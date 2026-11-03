"""
Acceso de red de Splunk Cloud vía ACS: listas de IP permitidas (IPv4 e IPv6) por funcionalidad y puertos de salida.

Inventario por cliente y de todos los clientes (matriz funcionalidad × cliente), alta y baja de subredes en uno o
varios clientes a la vez (tarea en segundo plano, con simulación) y backup en backups/acceso/.
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor

from core import acs, audit
from core.backup_files import write_backup
from core.paths import backup_dir
from web import services
from web.jobs import cancel_requested, capture_output, set_progress
from web.services import ClientError

CLIENT_WORKERS = 6
FEATURE_WORKERS = 6
FOLDER = "acceso"
PREFIX = "acceso_"


def features():
    return [{"id": k, "label": v} for k, v in acs.ALLOWLIST_FEATURES.items()]


def _acs_client(name):
    client = services.find_client(name)
    reason = acs.unavailable_reason(client)
    if reason:
        raise ClientError(f"{name}: {reason}.")
    return client


def inventory(client_name):
    """
    Listas del cliente: {client, allowlists: [{feature, label, version (4/6), subnets, error}], outbound: [{version, port,
    subnets}], errors}. Cada funcionalidad se consulta aparte (en paralelo); las que ACS no admite en el stack llevan 'error'.
    """
    client = _acs_client(client_name)
    jobs = [(feature, v6) for feature in acs.ALLOWLIST_FEATURES for v6 in (False, True)
            if not (feature in acs.V6_ONLY_FEATURES and not v6)]

    def one(job):
        feature, v6 = job
        try:
            return {"feature": feature, "label": acs.ALLOWLIST_FEATURES[feature], "version": 6 if v6 else 4,
                    "subnets": acs.list_allowlist(client, feature, v6), "error": None}
        except acs.AcsError as e:
            return {"feature": feature, "label": acs.ALLOWLIST_FEATURES[feature], "version": 6 if v6 else 4,
                    "subnets": [], "error": _short(e)}

    def ports(v6):
        try:
            return [{"version": 6 if v6 else 4, **p} for p in acs.list_outbound_ports(client, v6)], None
        except acs.AcsError as e:
            return [], _short(e)

    with ThreadPoolExecutor(FEATURE_WORKERS) as pool:
        lists = list(pool.map(one, jobs))
        ports4 = pool.submit(ports, False)
        ports6 = pool.submit(ports, True)
    outbound, errors = [], {}
    for version, future in ((4, ports4), (6, ports6)):
        rows, error = future.result()
        outbound += rows
        if error:
            errors[f"outbound{version}"] = error
    return {"client": client_name, "allowlists": lists, "outbound": outbound, "errors": errors}


def _short(error):
    text = str(error)
    return text if len(text) < 200 else text[:200] + "…"


def overview(names=None):
    """Inventario de varios clientes en paralelo; los que fallan llevan 'error'."""
    clients = services.list_clients()
    hosts = {c["name"]: c for c in clients}
    targets = [n for n in (names or list(hosts)) if n in hosts]

    def one(name):
        try:
            with capture_output():
                data = inventory(name)
            return {"client": name, "host": hosts[name]["host"], **data}
        except Exception as e:
            return {"client": name, "host": hosts[name]["host"], "allowlists": [], "outbound": [], "errors": {}, "error": str(e)}

    with ThreadPoolExecutor(CLIENT_WORKERS) as pool:
        return list(pool.map(one, targets))


# --- Cambios ------------------------------------------------------------------------------------------

def _validate(kind, data):
    """Normaliza una petición de cambio: devuelve (kind, feature/port, subnets, v6, reason)."""
    v6 = bool(data.get("v6"))
    subnets = acs.normalize_subnets(data.get("subnets") or [], v6)
    if not subnets:
        raise ClientError("Indique al menos una subred en notación CIDR.")
    if kind == "allowlist":
        feature = str(data.get("feature") or "")
        if feature not in acs.ALLOWLIST_FEATURES:
            raise ClientError("Funcionalidad no válida.")
        return feature, subnets, v6, ""
    if kind == "outbound":
        return acs._check_port(data.get("port")), subnets, v6, str(data.get("reason") or "").strip()
    raise ClientError("Tipo de lista no válido.")


def _describe(kind, action, target, subnets, v6):
    what = f"IPv6 de {target}" if v6 else str(target)
    if kind == "allowlist":
        label = acs.ALLOWLIST_FEATURES.get(target, target)
        return f"{'Añadir' if action == 'add' else 'Quitar'} {len(subnets)} subred{'es' if len(subnets) != 1 else ''} " \
               f"{'a' if action == 'add' else 'de'} la lista de {label}{' (IPv6)' if v6 else ''}"
    return f"{'Abrir' if action == 'add' else 'Cerrar'} el puerto de salida {target}{' (IPv6)' if v6 else ''} " \
           f"hacia {len(subnets)} subred{'es' if len(subnets) != 1 else ''}"


def build_change(kind, action, client_names, data, dry_run=False):
    """
    Tarea que añade ('add') o quita ('remove') subredes de una lista de IP o de un puerto de salida en varios clientes,
    de uno en uno. Con dry_run solo muestra la petición que se enviaría. Devuelve (título, función).
    """
    try:
        target, subnets, v6, reason = _validate(kind, data)
    except acs.AcsError as e:
        raise ClientError(str(e))
    if action not in ("add", "remove"):
        raise ClientError("Acción no válida.")
    names = [n for n in dict.fromkeys(client_names or []) if n]
    if not names:
        raise ClientError("Seleccione al menos un cliente.")
    title = _describe(kind, action, target, subnets, v6)
    audit_action = f"{'allowlist' if kind == 'allowlist' else 'outbound'}.{action}"

    def apply(client):
        if kind == "allowlist":
            fn = acs.add_allowlist if action == "add" else acs.remove_allowlist
            return fn(client, target, subnets, v6=v6, dry_run=dry_run)
        if action == "add":
            return acs.add_outbound_port(client, target, subnets, reason, v6=v6, dry_run=dry_run)
        return acs.remove_outbound_subnets(client, target, subnets, v6=v6, dry_run=dry_run)

    def run():
        total = len(names)
        ok = failed = 0
        for i, name in enumerate(names):
            set_progress(i, total, name)
            if total > 1:
                print(f"\n━━━━━━━━━━ {name} ━━━━━━━━━━")
            if cancel_requested():
                print("⏹ Cancelado.")
                break
            try:
                client = _acs_client(name)
                if not dry_run and action == "remove":
                    _backup_before(name, client)
                result = apply(client)
                if dry_run:
                    print(f"[SIMULACIÓN] {title} en {name}: {result['request']['method']} {result['request']['url']}")
                    print(f"   cuerpo: {json.dumps(result['request']['body'], ensure_ascii=False)}")
                else:
                    warnings = (result or {}).get("warnings") if isinstance(result, dict) else None
                    for warning in warnings or []:
                        print(f"⚠️ ACS avisa: {warning}")
                    print(f"✅ {name}: {title.lower()} ({', '.join(subnets)})")
                ok += 1
                audit.record(audit_action, title, name, {"target": target, "subnets": subnets, "v6": v6}, dry_run=dry_run)
            except (ClientError, acs.AcsError) as e:
                print(f"❌ {name}: {e}")
                failed += 1
                audit.record(audit_action, title, name, {"target": target, "subnets": subnets, "v6": v6}, result="error")
        set_progress(total, total, "")
        print(f"Resultado: {ok} clientes correctos, {failed} con error.")

    label = names[0] if len(names) == 1 else f"{len(names)} clientes"
    return f"{title} · {label}{' (simulación)' if dry_run else ''}", run


def _backup_before(client_name, client):
    """Guarda las listas actuales del cliente antes de quitar algo (para poder volver a añadirlo)."""
    try:
        data = inventory(client_name)
        rows = [{"kind": "allowlist", "feature": a["feature"], "version": a["version"], "subnets": a["subnets"]}
                for a in data["allowlists"] if not a["error"]]
        rows += [{"kind": "outbound", "port": p["port"], "version": p["version"], "subnets": p["subnets"]} for p in data["outbound"]]
        stack = acs.stack_name(client.get("host")) or client_name
        write_backup(os.path.join(backup_dir(FOLDER), f"{PREFIX}{stack}_antes_de_quitar_backup.json"), rows)
    except Exception as e:
        print(f"⚠️ No se pudo guardar el estado anterior de las listas: {e}")


def backup(service, client=None, log=print):
    """Backup de las listas de IP y puertos de salida del cliente en backups/acceso/."""
    client = client or services.client_for_service(service)
    if not client:
        raise ClientError("No se encontró el cliente de esta conexión.")
    name = client.get("name")
    data = inventory(name)
    rows = [{"kind": "allowlist", "feature": a["feature"], "label": a["label"], "version": a["version"], "subnets": a["subnets"]}
            for a in data["allowlists"] if not a["error"]]
    rows += [{"kind": "outbound", "port": p["port"], "version": p["version"], "subnets": p["subnets"]} for p in data["outbound"]]
    stack = acs.stack_name(client.get("host")) or name
    path = os.path.join(backup_dir(FOLDER), f"{PREFIX}{stack}_backup.json")
    write_backup(path, rows)
    skipped = [f"{a['feature']} IPv{a['version']}" for a in data["allowlists"] if a["error"]]
    if skipped:
        log(f"⚠️ Sin datos de: {', '.join(skipped)} (ACS no las devuelve en este stack)")
    log(f"✅ {len(rows)} listas guardadas en {os.path.basename(path)}")
    return path
