"""
Admin Config Service (ACS) de Splunk Cloud y API de Splunkbase: lo necesario para ver y actualizar las apps y add-ons
de Splunkbase instalados en un cliente (Victoria Experience).

ACS usa el token JWT del cliente (el "splunkToken" de config.py) de un usuario con el rol sc_admin. Para actualizar una
app de Splunkbase hace falta, además, una sesión de Splunkbase (usuario y contraseña de splunk.com) y aceptar la
licencia de la app (cabecera ACS-Licensing-Ack con la URL de su licencia).
Documentación: Admin Config Service Manual → «Manage Splunkbase apps in Splunk Cloud Platform».
"""
import base64
import binascii
import json
import re
import time
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

def _http(method, url, headers=None, data=None, timeout=60):
    """Petición HTTP. Devuelve (código, cuerpo como texto); los errores HTTP también se devuelven, no se lanzan."""
    body = urllib.parse.urlencode(data).encode("utf-8") if data else None
    request = urllib.request.Request(url, data=body, method=method,
                                     headers={"User-Agent": USER_AGENT, **(headers or {})})
    host = urllib.parse.urlsplit(url).netloc
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
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


def _acs(client, method, path, data=None, headers=None, timeout=60):
    reason = unavailable_reason(client)
    if reason:
        raise AcsError(f"{client.get('name') or client.get('host')}: {reason}")
    url = f"{ACS_URL}/{stack_name(client['host'])}/adminconfig/v2/{path}"
    status, text = _http(method, url, {"Authorization": f"Bearer {client['splunkToken']}", **(headers or {})},
                         data, timeout)
    if status >= 400:
        raise _acs_error(status, text, f"ACS {method} {path.split('?')[0]}")
    if not text.strip():
        return {}
    try:
        return json.loads(text)
    except ValueError:
        raise AcsError(f"ACS {method} {path}: la respuesta no es JSON: {text[:200]}")


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
