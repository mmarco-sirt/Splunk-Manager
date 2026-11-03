"""
Admin Config Service (ACS) de Splunk Cloud y API de Splunkbase: lo necesario para ver y actualizar las apps y add-ons
de Splunkbase instalados en un cliente (Victoria Experience).

ACS usa el token JWT del cliente (el "splunkToken" de config.py) de un usuario con el rol sc_admin. Para actualizar una
app de Splunkbase hace falta, además, una sesión de Splunkbase (usuario y contraseña de splunk.com) y aceptar la
licencia de la app (cabecera ACS-Licensing-Ack con la URL de su licencia).
Además: estado del stack y reinicio, ventanas de mantenimiento, tokens HEC, listas de IP permitidas y puertos de salida,
tokens de autenticación y exportación de apps (todo ello solo en Splunk Cloud, con el token de sc_admin).
Documentación: Admin Config Service Manual (help.splunk.com) y https://admin.splunk.com/service/info/specs/v2/openapi.json
"""
import base64
import binascii
import ipaddress
import json
import re
import time
from datetime import datetime, timezone
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ElementTree

ACS_URL = "https://admin.splunk.com"
SPLUNKBASE_URL = "https://splunkbase.splunk.com"
CLOUD_SUFFIX = ".splunkcloud.com"
PAGE_SIZE = 100          # máximo que admite ACS por página
UPDATE_TIMEOUT = 900     # segundos de espera a que ACS termine de actualizar una app
POLL_INTERVAL = 15
USER_AGENT = "SplunkManager"
DOC_HOSTS = ("docs.splunk.com", "help.splunk.com")
_URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+")
_SPLUNKBASE_ID_PATTERN = re.compile(r"splunkbase\.splunk\.com/app/(\d+)")
_HINTS = {
    401: "el token no es válido o ha caducado",
    403: "el usuario del token no tiene el rol sc_admin o ACS no está habilitado en el stack",
    404: "el stack o la app no existen en ACS",
    409: "hay otra operación de apps en curso en el stack; espere a que termine",
    429: "ACS ha limitado las peticiones; espere un momento",
}


class AcsError(Exception):
    """Error de ACS o de Splunkbase con un mensaje apto para mostrar."""


# --- Cliente y stack ----------------------------------------------------------------------------

def stack_name(host):
    """Stack de ACS a partir del host (es.cliente.splunkcloud.com -> es.cliente), o None si no es Splunk Cloud."""
    host = str(host or "").strip().lower().split(":")[0]
    if host.endswith(CLOUD_SUFFIX) and len(host) > len(CLOUD_SUFFIX):
        return host[:-len(CLOUD_SUFFIX)]
    return None


def token_expiry(token):
    """Caducidad (epoch) de un token JWT de Splunk Cloud, o None si no se puede leer (no es JWT o no caduca)."""
    parts = str(token or "").split(".")
    if len(parts) != 3:
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
        return int(payload["exp"]) if payload.get("exp") else None
    except (ValueError, TypeError, KeyError, binascii.Error, UnicodeDecodeError):
        return None


def unavailable_reason(client, now=None):
    """Por qué no se puede usar ACS con este cliente, o None si se puede."""
    if not stack_name(client.get("host")):
        return "no es un stack de Splunk Cloud (ACS solo existe en *.splunkcloud.com)"
    if not client.get("splunkToken"):
        return "no tiene token: ACS necesita el token JWT de un usuario con el rol sc_admin"
    expiry = token_expiry(client["splunkToken"])
    if expiry and expiry < (time.time() if now is None else now):
        return (f"el token caducó el {time.strftime('%d/%m/%Y', time.localtime(expiry))}: hay que crear uno nuevo en "
                "Splunk Cloud y guardarlo en el cliente")
    return None


# --- HTTP ---------------------------------------------------------------------------------------

def _http(method, url, headers=None, data=None, timeout=60, json_body=None, raw=False):
    """
    Petición HTTP. Devuelve (código, cuerpo como texto); los errores HTTP también se devuelven, no se lanzan.
    'data' se envía como formulario; 'json_body' como JSON. Con raw=True el cuerpo se devuelve en bytes (descargas).
    """
    headers = {"User-Agent": USER_AGENT, **(headers or {})}
    if json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    else:
        body = urllib.parse.urlencode(data).encode("utf-8") if data else None
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    host = urllib.parse.urlsplit(url).netloc
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content = response.read()
            return response.status, content if raw else content.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except urllib.error.URLError as e:
        raise AcsError(f"no se pudo conectar con {host}: {e.reason}")
    except OSError as e:   # TimeoutError al leer, conexión cortada...
        raise AcsError(f"error de red con {host}: {e}")


def _acs_error(status, text, context):
    try:
        payload = json.loads(text)
        detail = payload.get("message") or payload.get("code") or text
    except (ValueError, AttributeError):
        detail = text
    hint = f" ({_HINTS[status]})" if status in _HINTS else ""
    return AcsError(f"{context}: HTTP {status}{hint}. {str(detail).strip()[:300]}".rstrip(". ") + ".")


def _acs(client, method, path, data=None, headers=None, timeout=60, json_body=None, auth=None, raw=False,
         dry_run=False):
    """
    Petición a ACS en el stack del cliente. 'json_body' va como JSON; 'auth' = (usuario, contraseña) usa HTTP Basic en
    lugar del token (solo para crear tokens). Con raw=True devuelve los bytes de la respuesta (descargas). Con dry_run
    no se envía nada: se devuelve {"dry_run": True, "request": {...}} con lo que se habría pedido.
    """
    reason = unavailable_reason(client)
    if reason and not auth:
        raise AcsError(f"{client.get('name') or client.get('host')}: {reason}")
    if not stack_name(client.get("host")):
        raise AcsError(f"{client.get('name') or client.get('host')}: no es un stack de Splunk Cloud")
    url = f"{ACS_URL}/{stack_name(client['host'])}/adminconfig/v2/{path}"
    if auth:
        credentials = base64.b64encode(f"{auth[0]}:{auth[1]}".encode("utf-8")).decode("ascii")
        authorization = f"Basic {credentials}"
    else:
        authorization = f"Bearer {client['splunkToken']}"
    if dry_run:
        return {"dry_run": True, "request": {"method": method, "url": url, "body": json_body if json_body is not None else data}}
    status, text = _http(method, url, {"Authorization": authorization, **(headers or {})}, data, timeout,
                         json_body=json_body, raw=raw)
    if status >= 400:
        raise _acs_error(status, text, f"ACS {method} {path.split('?')[0]}")
    if raw:
        return text
    if not text.strip():
        return {}
    try:
        return json.loads(text)
    except ValueError:
        raise AcsError(f"ACS {method} {path}: la respuesta no es JSON: {text[:200]}")


def _quote(name):
    return urllib.parse.quote(str(name), safe="")


# --- Apps según ACS -----------------------------------------------------------------------------

def list_apps(client):
    """Apps instaladas según ACS (Victoria): [{name, label, version, splunkbaseID, status, ...}]."""
    apps, offset = [], 0
    while True:
        page = _acs(client, "GET", f"apps/victoria?count={PAGE_SIZE}&offset={offset}").get("apps") or []
        apps.extend(page)
        if len(page) < PAGE_SIZE:
            return apps
        offset += PAGE_SIZE


def describe_app(client, name):
    """Estado de una app según ACS: {name, status ('installed' o 'processing'), version, splunkbaseID...}."""
    data = _acs(client, "GET", f"apps/victoria/{urllib.parse.quote(name, safe='')}")
    if isinstance(data, dict) and isinstance(data.get("apps"), list):
        apps = data["apps"]
        return next((a for a in apps if a.get("name") == name), apps[0] if apps else {})
    return data if isinstance(data, dict) else {}


# --- Actualizaciones que Splunk ya ha detectado ---------------------------------------------------

def major_change(version, new_version):
    """True si cambia el primer número de la versión (11.0.3 -> 12.0.0)."""
    def major(text):
        match = re.match(r"\s*(\d+)", str(text or ""))
        return int(match.group(1)) if match else None
    old, new = major(version), major(new_version)
    return old is not None and new is not None and old != new


def pending_updates(service):
    """
    Apps y add-ons para los que Splunk ya ha detectado una versión más nueva en Splunkbase (campos update.* de
    apps/local, los mismos del aviso «Update» de Apps → Manage Apps), ordenadas por nombre:
    [{name, label, version, new_version, splunkbase_id, homepage, major, disabled}].
    """
    response = service.get("apps/local", output_mode="json", count=0)
    data = json.loads(response.body.read().decode("utf-8"))
    result = []
    for entry in data.get("entry", []):
        content = entry.get("content") or {}
        new_version = str(content.get("update.version") or "").strip()
        if not new_version:
            continue
        links = f"{content.get('update.homepage') or ''} {content.get('update.appurl') or ''}"
        match = _SPLUNKBASE_ID_PATTERN.search(links)
        version = str(content.get("version") or "").strip()
        result.append({
            "name": entry.get("name") or "",
            "label": content.get("label") or entry.get("name") or "",
            "version": version,
            "new_version": new_version,
            "splunkbase_id": match.group(1) if match else "",
            "homepage": content.get("update.homepage") or "",
            "major": major_change(version, new_version),
            "disabled": str(content.get("disabled")).strip().lower() in ("1", "true"),
        })
    return sorted(result, key=lambda a: a["name"].lower())


# --- Splunkbase ---------------------------------------------------------------------------------

def splunkbase_login(username, password):
    """Inicia sesión en Splunkbase y devuelve el id de sesión (cabecera X-Splunkbase-Authorization de ACS)."""
    if not (username and password):
        raise AcsError("faltan el usuario o la contraseña de Splunkbase")
    status, text = _http("POST", f"{SPLUNKBASE_URL}/api/account:login/",
                         data={"username": username, "password": password})
    if status != 200:
        raise AcsError(f"Splunkbase rechazó el inicio de sesión de {username} (HTTP {status}). "
                       "Compruebe el usuario y la contraseña de splunk.com.")
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        raise AcsError("Splunkbase devolvió una respuesta inesperada al iniciar sesión.")
    session = next((el.text for el in root.iter() if el.tag.split("}")[-1] == "id" and (el.text or "").strip()), None)
    if not session:
        raise AcsError("Splunkbase no devolvió el id de sesión.")
    return session.strip()


def splunkbase_app(splunkbase_id):
    """Datos públicos de una app de Splunkbase: {id, appid, title, license_name, license_url}."""
    status, text = _http("GET", f"{SPLUNKBASE_URL}/api/v1/app/{int(splunkbase_id)}/")
    if status != 200:
        raise AcsError(f"Splunkbase no devolvió la app {splunkbase_id} (HTTP {status}).")
    try:
        data = json.loads(text)
    except ValueError:
        raise AcsError(f"Splunkbase devolvió una respuesta inesperada para la app {splunkbase_id}.")
    return {"id": data.get("uid"), "appid": data.get("appid") or "", "title": data.get("title") or "",
            "license_name": data.get("license_name") or "", "license_url": data.get("license_url") or ""}


# --- Actualizar ---------------------------------------------------------------------------------

def _license_from_error(message, sent):
    """URL de licencia que ACS indica en un error 400 por licencia incorrecta (None si no la hay)."""
    for url in _URL_PATTERN.findall(message):
        url = url.rstrip(".,;)'\"")
        if url != sent and not any(host in url for host in DOC_HOSTS):
            return url
    return None


def update_app(client, name, version, splunkbase_session, license_url, log=print):
    """
    Pide a ACS que actualice una app de Splunkbase a 'version' (vacía = la última). Devuelve la respuesta de ACS
    ({appID, status, version...}). Si la URL de licencia no es la que espera ACS, repite una vez con la que indica ACS.
    """
    headers = {"X-Splunkbase-Authorization": splunkbase_session, "ACS-Licensing-Ack": license_url or "",
               "Content-Type": "application/x-www-form-urlencoded"}
    data = {"version": version} if version else None
    path = f"apps/victoria/{urllib.parse.quote(name, safe='')}"
    try:
        return _acs(client, "PATCH", path, data=data, headers=headers, timeout=UPDATE_TIMEOUT)
    except AcsError as e:
        corrected = _license_from_error(str(e), license_url) if "HTTP 400" in str(e) else None
        if not corrected:
            raise
        log(f"   ACS pide aceptar la licencia {corrected} (la de Splunkbase era {license_url or 'desconocida'}).")
        headers["ACS-Licensing-Ack"] = corrected
        return _acs(client, "PATCH", path, data=data, headers=headers, timeout=UPDATE_TIMEOUT)


def wait_for_update(client, name, version, timeout=UPDATE_TIMEOUT, interval=POLL_INTERVAL, log=print,
                    sleep=time.sleep, clock=time.monotonic):
    """Espera a que ACS deje la app en estado 'installed' con la versión pedida. Devuelve la app según ACS."""
    deadline = clock() + timeout
    while True:
        app = describe_app(client, name)
        status, current = str(app.get("status") or "").lower(), str(app.get("version") or "")
        if status == "installed" and (not version or current == version):
            return app
        if status not in ("installed", "processing", "pending", ""):
            raise AcsError(f"ACS dejó '{name}' en estado '{status}' (versión {current or '?'}).")
        if clock() >= deadline:
            raise AcsError(f"ACS sigue procesando '{name}' tras {timeout // 60} min (estado {status or '?'}, versión "
                           f"{current or '?'}). Compruébelo más tarde en Apps → Manage Apps.")
        log(f"   … ACS sigue procesando (estado {status or '?'}, versión {current or '?'}); "
            f"nueva comprobación en {interval} s")
        sleep(interval)


# --- Fechas de ACS ------------------------------------------------------------------------------

def parse_time(text):
    """'2024-08-21T04:00:00Z' (RFC 3339) -> epoch, o None si está vacío o no se entiende."""
    if not text:
        return None
    try:
        value = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def _flag(value):
    return str(value).strip().lower() in ("1", "true", "yes")


# --- Plataforma del cliente ---------------------------------------------------------------------

def client_platform(client, info=None):
    """
    Qué es este cliente y qué se puede hacer con ACS: {kind ('cloud', 'enterprise' o 'unknown'), stack, acs (bool),
    reason (por qué no hay ACS), token_expiry (epoch del token JWT o None)}. 'info' es server/info si ya se conoce.
    """
    stack = stack_name(client.get("host"))
    kind = "cloud" if stack else "unknown"
    if info:
        instance = str(info.get("instance_type") or "").lower()
        if instance == "cloud":
            kind = "cloud"
        elif info.get("version") and not stack:
            kind = "enterprise"
    reason = unavailable_reason(client)
    return {"kind": kind, "stack": stack, "acs": reason is None, "reason": reason,
            "token_expiry": token_expiry(client.get("splunkToken"))}


# --- Estado del stack, reinicio y ventanas de mantenimiento --------------------------------------

def stack_status(client):
    """Estado del stack: {experience ('victoria'/'classic'/None), version, status, restart_required}."""
    data = _acs(client, "GET", "status")
    infra = data.get("infrastructure") or {}
    experience = str(infra.get("stackType") or "").strip().lower() or None
    return {
        "experience": experience,
        "version": infra.get("stackVersion") or "",
        "status": infra.get("status") or "",
        "restart_required": _flag((data.get("messages") or {}).get("restartRequired")),
    }


def restart(client, dry_run=False):
    """Pide a ACS reiniciar el search head (o un rolling restart del cluster). Devuelve el mensaje de ACS."""
    data = _acs(client, "POST", "restart-now", timeout=120, dry_run=dry_run)
    if data.get("dry_run"):
        return data
    return data.get("message") or "Reinicio solicitado."


def restart_status(client):
    """Estado del rolling restart de un search head cluster: [{captain, rolling_restart, ready}] (vacío si no es cluster)."""
    try:
        data = _acs(client, "GET", "restart/status")
    except AcsError as e:
        if "HTTP 500" in str(e):   # mientras reinicia, ACS responde 500 con el estado dentro
            return [{"captain": "", "rolling_restart": True, "ready": False}]
        raise
    return [{"captain": m.get("captain") or "", "rolling_restart": _flag(m.get("rollingRestartInitiated")),
             "ready": _flag(m.get("serviceReady"))} for m in data.get("shcStatus") or []]


def maintenance_schedules(client, from_time=None, to_time=None):
    """
    Ventanas de mantenimiento programadas por Splunk (ACS solo lista las futuras y las completadas, no las canceladas):
    [{id, type, status, start, end (epoch), duration, zero_downtime, operations: [{description, status, target_version}]}].
    Sin fechas, ACS devuelve los 30 días siguientes; 'from_time'/'to_time' en AAAA-MM-DD.
    """
    query = {"count": 100}
    if from_time:
        query["fromTime"] = from_time
    if to_time:
        query["toTime"] = to_time
    schedules, next_link = [], None
    while True:
        if next_link:
            query["nextLink"] = next_link
        data = _acs(client, "GET", f"maintenance-windows/schedules?{urllib.parse.urlencode(query)}")
        for item in data.get("schedules") or []:
            schedules.append({
                "id": item.get("scheduleId") or "",
                "type": item.get("mwType") or "",
                "status": item.get("status") or "",
                "start": parse_time(item.get("scheduleStartTimestamp")),
                "end": parse_time(item.get("scheduleEndTimestamp")),
                "duration": item.get("duration") or "",
                "zero_downtime": _flag(item.get("zeroDowntime")),
                "requested_by": item.get("requestedEntity") or "",
                "operations": [{"description": op.get("operationDescription") or "", "status": op.get("operationStatus") or "",
                                "target_version": op.get("targetVersion") or ""} for op in item.get("operations") or []],
            })
        next_link = data.get("nextLink")
        if not next_link or not data.get("schedules"):
            break
    return sorted(schedules, key=lambda s: s["start"] or 0)


def maintenance_preferences(client):
    """Preferencias de mantenimiento: {freezes: [{id, start, end, applies_to, reason, by}], preferred, warning}."""
    data = _acs(client, "GET", "maintenance-windows/preferences")
    freezes = []
    change = data.get("changeFreezes") or {}
    for by, key in (("cliente", "customerInitiatedFreezes"), ("Splunk", "splunkInitiatedFreezes")):
        for item in change.get(key) or []:
            freezes.append({"id": item.get("id") or "", "start": item.get("startDate") or "", "end": item.get("endDate") or "",
                            "applies_to": item.get("appliesTo") or "", "reason": item.get("reason") or "", "by": by})
    preferred = (data.get("preferredSchedule") or {}).get("preferredWindow") or {}
    slot = preferred.get("timeSlot") or {}
    return {
        "freezes": freezes,
        "preferred": {"start": slot.get("start") or "", "end": slot.get("end") or "", "day": preferred.get("preferredDay") or "",
                      "alternate_days": preferred.get("alternateDays") or []} if preferred else None,
        "warning": data.get("warning") or "",
    }


def _freeze_date(text):
    try:
        return datetime.strptime(str(text), "%Y/%m/%d").date()
    except ValueError:
        return None


def active_freezes(freezes, today=None):
    """Change freezes vigentes hoy (fechas AAAA/MM/DD, UTC)."""
    today = today or datetime.now(timezone.utc).date()
    result = []
    for f in freezes:
        start, end = _freeze_date(f.get("start")), _freeze_date(f.get("end"))
        if start and end and start <= today <= end:
            result.append(f)
    return result


def next_window(schedules, now=None):
    """La primera ventana de mantenimiento que no ha terminado, o None."""
    now = time.time() if now is None else now
    for s in schedules:
        if (s.get("end") or s.get("start") or 0) >= now:
            return s
    return None


# --- Tokens HEC ---------------------------------------------------------------------------------

HEC_FIELDS = ("default_index", "default_host", "default_source", "default_sourcetype", "allowed_indexes", "disabled",
              "use_ack", "meta")


def hec_row(item):
    """Token HEC de ACS normalizado: {name, token, disabled, use_ack, default_index, ..., allowed_indexes, meta}."""
    spec = item.get("spec") if isinstance(item, dict) and "spec" in item else item
    spec = spec or {}
    use_ack = spec.get("useAck") if "useAck" in spec else spec.get("useACK")
    return {
        "name": spec.get("name") or "",
        "token": item.get("token") if isinstance(item, dict) else "",
        "disabled": _flag(spec.get("disabled")),
        "use_ack": _flag(use_ack),
        "default_index": spec.get("defaultIndex") or "",
        "default_host": spec.get("defaultHost") or "",
        "default_source": spec.get("defaultSource") or "",
        "default_sourcetype": spec.get("defaultSourcetype") or "",
        "allowed_indexes": sorted(str(i) for i in spec.get("allowedIndexes") or []),
        "meta": spec.get("_meta") or "",
    }


def hec_spec(row, include_token=False):
    """Spec de ACS a partir de una fila normalizada (solo los campos presentes y no None)."""
    spec = {"name": row["name"]}
    mapping = (("default_index", "defaultIndex"), ("default_host", "defaultHost"), ("default_source", "defaultSource"),
               ("default_sourcetype", "defaultSourcetype"), ("meta", "_meta"))
    for key, acs_key in mapping:
        if row.get(key) is not None and key in row:
            spec[acs_key] = str(row[key])
    if row.get("allowed_indexes") is not None and "allowed_indexes" in row:
        spec["allowedIndexes"] = [str(i).strip() for i in row["allowed_indexes"] if str(i).strip()]
    if "disabled" in row and row["disabled"] is not None:
        spec["disabled"] = bool(row["disabled"])
    if "use_ack" in row and row["use_ack"] is not None:
        spec["useACK"] = bool(row["use_ack"])
    if include_token and row.get("token"):
        spec["token"] = str(row["token"])
    return spec


def list_hec(client):
    """Tokens HEC del stack (normalizados), ordenados por nombre."""
    data = _acs(client, "GET", "inputs/http-event-collectors?count=0")
    items = data.get("http-event-collectors")
    if items is None:
        items = data.get("http_event_collectors") or []
    return sorted((hec_row(i) for i in items), key=lambda r: r["name"].lower())


def describe_hec(client, name):
    """Un token HEC, o None si ACS aún no lo tiene (404 mientras se crea)."""
    try:
        data = _acs(client, "GET", f"inputs/http-event-collectors/{_quote(name)}")
    except AcsError as e:
        if "HTTP 404" in str(e):
            return None
        raise
    item = data.get("http-event-collector") or data.get("http_event_collector") or data
    return hec_row(item)


def _check_hec_name(name):
    name = str(name or "").strip()
    if not name or re.search(r"[\s/\\]", name):
        raise AcsError("el nombre del token HEC es obligatorio y no puede llevar espacios ni barras")
    return name


def create_hec(client, row, dry_run=False):
    """Crea un token HEC (asíncrono: ACS responde 202). 'row' es una fila normalizada; con 'token' se reutiliza ese valor."""
    row = {**row, "name": _check_hec_name(row.get("name"))}
    return _acs(client, "POST", "inputs/http-event-collectors", json_body=hec_spec(row, include_token=True), dry_run=dry_run)


def update_hec(client, name, changes, dry_run=False):
    """Modifica solo los campos indicados de un token HEC (PATCH, asíncrono)."""
    spec = hec_spec({**changes, "name": _check_hec_name(name)})
    return _acs(client, "PATCH", f"inputs/http-event-collectors/{_quote(name)}", json_body=spec, dry_run=dry_run)


def delete_hec(client, name, dry_run=False):
    return _acs(client, "DELETE", f"inputs/http-event-collectors/{_quote(_check_hec_name(name))}", dry_run=dry_run)


def wait_for_hec(client, name, expected=None, timeout=180, interval=5, sleep=time.sleep, clock=time.monotonic,
                 deleted=False):
    """
    Espera a que un token HEC exista (y tenga los valores de 'expected', si se indican) o, con deleted=True, a que
    desaparezca. Devuelve la fila (o None si se ha borrado).
    """
    deadline = clock() + timeout
    while True:
        row = describe_hec(client, name)
        if deleted and row is None:
            return None
        if not deleted and row is not None and all(row.get(k) == v for k, v in (expected or {}).items()):
            return row
        if clock() >= deadline:
            what = "desaparezca" if deleted else "quede creado o actualizado"
            raise AcsError(f"ACS sigue procesando el token HEC '{name}' tras {timeout} s (esperando a que {what})")
        sleep(interval)


# --- Listas de IP permitidas y puertos de salida ------------------------------------------------

ALLOWLIST_FEATURES = {
    "search-api": "API REST de búsqueda (puerto 8089)",
    "search-ui": "Splunk Web",
    "hec": "HTTP Event Collector",
    "s2s": "Forwarders (S2S, puerto 9997)",
    "idm-api": "Inputs Data Manager: API",
    "idm-ui": "Inputs Data Manager: Splunk Web",
    "acs": "Admin Config Service",
    "federated-search": "Federated search (salida)",
}
V6_ONLY_FEATURES = ("federated-search",)   # ACS solo tiene lista IPv6 (de salida) para federated search


def normalize_subnets(subnets, v6=False):
    """Lista de subredes en CIDR válidas (sin repetidas), o AcsError. Una IP suelta se convierte en /32 (o /128)."""
    result = []
    for raw in subnets if isinstance(subnets, (list, tuple, set)) else str(subnets or "").replace(",", " ").split():
        text = str(raw).strip()
        if not text:
            continue
        try:
            network = ipaddress.ip_network(text, strict=False)
        except ValueError:
            raise AcsError(f"'{text}' no es una subred válida (use notación CIDR, p. ej. 10.0.0.0/24)")
        if (network.version == 6) != bool(v6):
            raise AcsError(f"'{text}' no es IPv{6 if v6 else 4}")
        cidr = network.with_prefixlen
        if cidr not in result:
            result.append(cidr)
    return result


def _allowlist_path(feature, v6):
    if feature not in ALLOWLIST_FEATURES:
        raise AcsError(f"funcionalidad desconocida: {feature} (válidas: {', '.join(ALLOWLIST_FEATURES)})")
    if feature in V6_ONLY_FEATURES and not v6:
        raise AcsError(f"{feature} solo tiene lista IPv6 en ACS")
    return f"access/{feature}/ipallowlists{'-v6' if v6 else ''}"


def list_allowlist(client, feature, v6=False):
    """Subredes permitidas para una funcionalidad, ordenadas."""
    data = _acs(client, "GET", _allowlist_path(feature, v6))
    return sorted(str(s) for s in data.get("subnets") or [])


def add_allowlist(client, feature, subnets, v6=False, dry_run=False):
    """Añade subredes a la lista (ACS devuelve avisos en 'warnings')."""
    subnets = normalize_subnets(subnets, v6)
    if not subnets:
        raise AcsError("indique al menos una subred")
    return _acs(client, "POST", _allowlist_path(feature, v6), json_body={"subnets": subnets}, dry_run=dry_run)


def remove_allowlist(client, feature, subnets, v6=False, dry_run=False):
    subnets = normalize_subnets(subnets, v6)
    if not subnets:
        raise AcsError("indique al menos una subred")
    return _acs(client, "DELETE", _allowlist_path(feature, v6), json_body={"subnets": subnets}, dry_run=dry_run)


def _outbound_path(v6, port=None):
    base = f"access/outbound-ports{'-v6' if v6 else ''}"
    return f"{base}/{int(port)}" if port is not None else base


def _check_port(port):
    try:
        port = int(port)
    except (TypeError, ValueError):
        raise AcsError("el puerto debe ser un número")
    if not 1 <= port <= 65535:
        raise AcsError("el puerto debe estar entre 1 y 65535")
    return port


def list_outbound_ports(client, v6=False):
    """Puertos de salida abiertos con sus destinos: [{port, subnets}] ordenados por puerto."""
    data = _acs(client, "GET", _outbound_path(v6))
    items = data if isinstance(data, list) else data.get("outboundPorts") or data.get("ports") or []
    result = []
    for item in items:
        try:
            port = int(item.get("port"))
        except (TypeError, ValueError):
            continue
        result.append({"port": port, "subnets": sorted(str(s) for s in item.get("destinationRanges") or item.get("subnets") or [])})
    return sorted(result, key=lambda p: p["port"])


def add_outbound_port(client, port, subnets, reason, v6=False, dry_run=False):
    """Abre un puerto de salida hacia las subredes indicadas (se añaden a las que ya hubiera)."""
    port = _check_port(port)
    subnets = normalize_subnets(subnets, v6)
    if not subnets:
        raise AcsError("indique al menos una subred de destino")
    body = {"outboundPorts": [{"port": port, "subnets": subnets}], "reason": str(reason or "").strip() or "Splunk Manager"}
    return _acs(client, "POST", _outbound_path(v6), json_body=body, timeout=120, dry_run=dry_run)


def remove_outbound_subnets(client, port, subnets, v6=False, dry_run=False):
    port = _check_port(port)
    subnets = normalize_subnets(subnets, v6)
    if not subnets:
        raise AcsError("indique al menos una subred")
    return _acs(client, "DELETE", _outbound_path(v6, port), json_body={"subnets": subnets}, timeout=120, dry_run=dry_run)


# --- Tokens de autenticación (JWT) --------------------------------------------------------------

def token_row(item):
    return {
        "id": item.get("id") or "",
        "user": item.get("user") or "",
        "audience": item.get("audience") or "",
        "status": item.get("status") or "",
        "type": item.get("type") or "",
        "expires_on": parse_time(item.get("expiresOn")),
        "not_before": parse_time(item.get("notBefore")),
        "last_used": parse_time(item.get("lastUsed")),
        "last_used_ip": item.get("lastUsedIP") or "",
    }


def list_tokens(client):
    """Tokens de autenticación del stack (sin su valor), los que caducan antes primero."""
    data = _acs(client, "GET", "tokens?count=0")
    items = data if isinstance(data, list) else data.get("tokens") or []
    rows = [token_row(t) for t in items if isinstance(t, dict)]
    return sorted(rows, key=lambda t: (t["expires_on"] is None, t["expires_on"] or 0, t["user"]))


EXPIRES_PATTERN = re.compile(r"^(\+\d+[smhd]?|\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2}([+-]\d{2}:\d{2})?)?)$")


def create_token(client, username, password, user, audience, expires_on="+30d", token_type="static", dry_run=False):
    """
    Crea un token JWT con usuario y contraseña del stack (HTTP Basic). 'expires_on' relativo (+90d) o absoluto
    (AAAA-MM-DDTHH:MM:SS). Devuelve {id, token (solo se muestra esta vez), user, audience, expires_on, status}.
    """
    if not (username and password):
        raise AcsError("hacen falta el usuario y la contraseña del stack para crear un token")
    user, audience = str(user or "").strip(), str(audience or "").strip()
    if not user or not audience:
        raise AcsError("indique el usuario del token y la audiencia (texto libre, p. ej. 'Splunk Manager')")
    expires_on = str(expires_on or "+30d").strip()
    if not EXPIRES_PATTERN.match(expires_on):
        raise AcsError("caducidad no válida: use +<n>d (p. ej. +90d) o una fecha AAAA-MM-DDTHH:MM:SS")
    body = {"user": user, "audience": audience, "expiresOn": expires_on, "type": token_type or "static"}
    data = _acs(client, "POST", "tokens", json_body=body, auth=(username, password), dry_run=dry_run)
    if data.get("dry_run"):
        return data
    info = data.get("tokeninfo") or data
    row = token_row(info)
    row["token"] = info.get("token") or ""
    if not row["token"]:
        raise AcsError("ACS no devolvió el valor del token")
    return row


def delete_token(client, token_id, dry_run=False):
    token_id = str(token_id or "").strip()
    if not token_id:
        raise AcsError("indique el id del token")
    return _acs(client, "DELETE", f"tokens/{_quote(token_id)}", dry_run=dry_run)


def token_claims(token):
    """Campos legibles del token JWT del cliente: {sub (usuario), aud, exp, iat, jti} o {} si no es JWT."""
    parts = str(token or "").split(".")
    if len(parts) != 3:
        return {}
    try:
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    except (ValueError, TypeError, binascii.Error, UnicodeDecodeError):
        return {}
    return {k: payload.get(k) for k in ("sub", "aud", "exp", "iat", "jti") if payload.get(k) is not None}


# --- Exportar apps ------------------------------------------------------------------------------

EXPORT_TIMEOUT = 600


def export_app(client, name, default=True, local=True, users=True, confs_only=False):
    """Exporta una app (Victoria) como .tgz y devuelve sus bytes. Requiere la capacidad export_apps."""
    name = str(name or "").strip()
    if not name or re.search(r"[\s/\\]", name):
        raise AcsError("nombre de app no válido")
    query = urllib.parse.urlencode({"default": str(bool(default)).lower(), "local": str(bool(local)).lower(),
                                    "users": str(bool(users)).lower(), "confs_only": str(bool(confs_only)).lower()})
    data = _acs(client, "GET", f"apps/victoria/export/download/{_quote(name)}?{query}", raw=True, timeout=EXPORT_TIMEOUT)
    if not data:
        raise AcsError(f"ACS devolvió una exportación vacía de '{name}'")
    if data[:2] != b"\x1f\x8b":   # no es gzip: suele ser un JSON de error con código 200
        text = data[:300].decode("utf-8", "replace")
        raise AcsError(f"ACS no devolvió un .tgz para '{name}': {text}")
    return data
