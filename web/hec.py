"""
Tokens HEC (HTTP Event Collector) de Splunk Cloud vía ACS: inventario por cliente y de todos los clientes, alta,
edición y borrado (con simulación), y backup/restore en backups/hec/.

Avisos de cada token: índice por defecto que no existe o está deshabilitado, índices permitidos que no existen y
token deshabilitado. Los backups guardan también el valor del token, porque ACS permite recrearlo con el mismo valor
(así los emisores no tienen que reconfigurarse); la carpeta backups/ no se versiona.
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor

from core import acs, audit
from core.backup_files import write_backup
from core.paths import backup_dir
from web import services
from web.explorer import _read_json
from web.jobs import cancel_requested, capture_output, set_progress
from web.services import ClientError

CLIENT_WORKERS = 6
FOLDER = "hec"
PREFIX = "hec_"
EDITABLE = ("default_index", "default_host", "default_source", "default_sourcetype", "allowed_indexes", "disabled",
            "use_ack", "meta")


def _acs_client(name):
    client = services.find_client(name)
    reason = acs.unavailable_reason(client)
    if reason:
        raise ClientError(f"{name}: {reason}.")
    return client


def index_names(service):
    """{índice: deshabilitado} de todos los índices del cliente (una petición ligera a data/indexes)."""
    data = _read_json(service, "data/indexes", count=0, f=["disabled"])
    return {e.get("name"): str((e.get("content") or {}).get("disabled")).lower() in ("1", "true") for e in data.get("entry", [])}


def annotate(rows, indexes):
    """Añade a cada token sus avisos ('warnings') comprobando sus índices contra los del cliente."""
    for row in rows:
        warnings = []
        default = row.get("default_index")
        if default == "default" and default not in indexes:
            warnings.append("sin índice por defecto propio («default»): los eventos sin índice van al índice por defecto de la instancia")
        elif default and default not in indexes:
            warnings.append(f"el índice por defecto «{default}» no existe")
        elif default and indexes.get(default):
            warnings.append(f"el índice por defecto «{default}» está deshabilitado")
        missing = [i for i in row.get("allowed_indexes") or [] if i not in indexes]
        if missing:
            warnings.append(f"índices permitidos que no existen: {', '.join(missing)}")
        if default and row.get("allowed_indexes") and default not in row["allowed_indexes"]:
            warnings.append("el índice por defecto no está entre los permitidos")
        if row.get("disabled"):
            warnings.append("token deshabilitado")
        row["warnings"] = warnings
    return rows


def list_tokens(client_name, with_values=False):
    """Tokens HEC del cliente con avisos: {client, tokens, indexes}. Sin with_values, el valor del token se oculta."""
    client = _acs_client(client_name)
    rows = acs.list_hec(client)
    try:
        indexes = index_names(services.get_service(client_name))
    except Exception as e:
        indexes = None
        warning = f"No se pudieron leer los índices del cliente: {e}"
    else:
        warning = None
    if indexes is not None:
        annotate(rows, indexes)
    else:
        for row in rows:
            row["warnings"] = ["token deshabilitado"] if row.get("disabled") else []
    if not with_values:
        for row in rows:
            row["has_token"] = bool(row.get("token"))
            row["token"] = ""
    return {"client": client_name, "tokens": rows, "indexes": sorted(indexes) if indexes else [], "warning": warning}


def token_value(client_name, name):
    """Valor de un token HEC (se pide aparte para no mandarlo en el listado)."""
    row = acs.describe_hec(_acs_client(client_name), name)
    if not row:
        raise ClientError(f"No existe el token HEC «{name}».")
    return {"name": row["name"], "token": row.get("token") or ""}


def overview(names=None):
    """Tokens HEC de varios clientes en paralelo; los que fallan llevan 'error'."""
    clients = services.list_clients()
    hosts = {c["name"]: c for c in clients}
    targets = [n for n in (names or list(hosts)) if n in hosts]

    def one(name):
        try:
            with capture_output():
                data = list_tokens(name)
            return {"client": name, "host": hosts[name]["host"], "tokens": data["tokens"], "warning": data["warning"]}
        except Exception as e:
            return {"client": name, "host": hosts[name]["host"], "tokens": [], "error": str(e)}

    with ThreadPoolExecutor(CLIENT_WORKERS) as pool:
        return list(pool.map(one, targets))


# --- Alta, edición y borrado ------------------------------------------------------------------------

def _clean_changes(data):
    """Campos editables de un token a partir del JSON del navegador."""
    changes = {}
    for key in EDITABLE:
        if key not in data:
            continue
        value = data[key]
        if key == "allowed_indexes":
            if isinstance(value, str):
                value = [v.strip() for v in value.replace(",", " ").split() if v.strip()]
            changes[key] = [str(v) for v in value or []]
        elif key in ("disabled", "use_ack"):
            changes[key] = bool(value)
        else:
            changes[key] = str(value or "").strip()
    return changes


def _dry(result, verb, name, client_name):
    return {"dry_run": True, "message": f"[SIMULACIÓN] {verb} el token HEC «{name}» en {client_name}", "request": result["request"]}


def create(client_name, data, dry_run=False):
    """Crea un token HEC y espera a que ACS lo tenga. Devuelve la fila (con avisos) o la simulación."""
    client = _acs_client(client_name)
    row = {**_clean_changes(data), "name": str(data.get("name") or "").strip()}
    if data.get("token"):
        row["token"] = str(data["token"]).strip()
    if not row.get("default_index"):
        raise ClientError("Indique el índice por defecto (ACS usaría «default», que normalmente no es lo que se quiere).")
    try:
        result = acs.create_hec(client, row, dry_run=dry_run)
        if dry_run:
            return _dry(result, "Se crearía", row["name"], client_name)
        created = acs.wait_for_hec(client, row["name"])
    except acs.AcsError as e:
        audit.record("hec.create", f"Crear token HEC «{row['name']}»", client_name, {"spec": acs.hec_spec(row)}, result="error")
        raise ClientError(str(e))
    audit.record("hec.create", f"Crear token HEC «{row['name']}»", client_name, {"spec": acs.hec_spec(row)})
    annotate([created], _safe_indexes(client_name))
    created["has_token"] = bool(created.get("token"))
    return created


def update(client_name, name, data, dry_run=False):
    """Modifica los campos indicados de un token HEC (PATCH) y espera a que ACS los aplique."""
    client = _acs_client(client_name)
    changes = _clean_changes(data)
    if not changes:
        raise ClientError("No hay ningún cambio que aplicar.")
    try:
        result = acs.update_hec(client, name, changes, dry_run=dry_run)
        if dry_run:
            return _dry(result, "Se modificaría", name, client_name)
        updated = acs.wait_for_hec(client, name, expected={k: v for k, v in changes.items() if k != "allowed_indexes"})
    except acs.AcsError as e:
        audit.record("hec.update", f"Modificar token HEC «{name}»", client_name, {"changes": changes}, result="error")
        raise ClientError(str(e))
    audit.record("hec.update", f"Modificar token HEC «{name}»", client_name, {"changes": changes})
    annotate([updated], _safe_indexes(client_name))
    updated["has_token"] = bool(updated.get("token"))
    updated["token"] = ""
    return updated


def delete(client_name, name, dry_run=False):
    client = _acs_client(client_name)
    try:
        if not dry_run:
            _backup_rows(client_name, client, [r for r in acs.list_hec(client) if r["name"] == name], label="antes_de_borrar")
        result = acs.delete_hec(client, name, dry_run=dry_run)
        if dry_run:
            return _dry(result, "Se borraría", name, client_name)
        acs.wait_for_hec(client, name, deleted=True)
    except acs.AcsError as e:
        audit.record("hec.delete", f"Borrar token HEC «{name}»", client_name, result="error")
        raise ClientError(str(e))
    audit.record("hec.delete", f"Borrar token HEC «{name}»", client_name)
    return {"ok": True}


def _safe_indexes(client_name):
    try:
        return index_names(services.get_service(client_name))
    except Exception:
        return {}


# --- Backup y restore --------------------------------------------------------------------------------

def _backup_rows(client_name, client, rows, label=""):
    """Guarda los tokens en backups/hec/hec_<stack>[_label]_backup.json (con historial). Devuelve la ruta."""
    stack = acs.stack_name(client.get("host")) or client_name
    suffix = f"_{label}" if label else ""
    path = os.path.join(backup_dir(FOLDER), f"{PREFIX}{stack}{suffix}_backup.json")
    write_backup(path, rows)
    return path


def backup(service, client=None, log=print):
    """Backup de todos los tokens HEC del cliente (con su valor). Pensado para run_for_clients(with_client=True)."""
    client = client or services.client_for_service(service)
    if not client:
        raise ClientError("No se encontró el cliente de esta conexión.")
    reason = acs.unavailable_reason(client)
    if reason:
        raise ClientError(f"{client.get('name')}: {reason}.")
    rows = acs.list_hec(client)
    path = _backup_rows(client.get("name"), client, rows)
    log(f"✅ {len(rows)} tokens HEC guardados en {os.path.basename(path)}")
    return path


def restore(service, path, opts=None, log=print):
    """
    Restaura tokens HEC desde un backup: los que existen se actualizan (PATCH) y los que no, se crean con el mismo valor
    de token si el backup lo tiene. Los avisos de ACS se muestran; cada token se espera antes de pasar al siguiente.
    """
    client = services.client_for_service(service)
    if not client:
        raise ClientError("No se encontró el cliente de esta conexión.")
    reason = acs.unavailable_reason(client)
    if reason:
        raise ClientError(f"{client.get('name')}: {reason}.")
    with open(path, "r", encoding="utf-8") as f:
        entries = json.load(f)
    if not isinstance(entries, list):
        raise ClientError("El backup de HEC debe ser una lista de tokens.")
    existing = {r["name"]: r for r in acs.list_hec(client)}
    if existing:
        _backup_rows(client.get("name"), client, list(existing.values()), label="antes_de_restaurar")
    created = updated = failed = 0
    for entry in entries:
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        if cancel_requested():
            log("⏹ Cancelado.")
            break
        row = {k: entry.get(k) for k in EDITABLE if k in entry}
        try:
            if name in existing:
                acs.update_hec(client, name, row)
                acs.wait_for_hec(client, name, expected={k: v for k, v in row.items() if k in ("disabled", "default_index")})
                log(f"✅ {name}: actualizado")
                updated += 1
            else:
                acs.create_hec(client, {**row, "name": name, "token": entry.get("token") or ""})
                acs.wait_for_hec(client, name)
                log(f"✅ {name}: creado{' con el mismo valor de token' if entry.get('token') else ''}")
                created += 1
        except acs.AcsError as e:
            log(f"❌ {name}: {e}")
            failed += 1
    audit.record("hec.restore", f"Restaurar tokens HEC desde {os.path.basename(path)}", client.get("name"),
                 {"created": created, "updated": updated, "failed": failed}, result="error" if failed else "ok")
    log(f"Resultado: {created} creados, {updated} actualizados, {failed} con error.")


def build_backup(client_names):
    """Tarea de backup de HEC en varios clientes. Devuelve (título, función)."""
    names = list(client_names)

    def run():
        total = len(names)
        for i, name in enumerate(names):
            set_progress(i, total, name)
            if total > 1:
                print(f"\n━━━━━━━━━━ {name} ━━━━━━━━━━")
            try:
                backup(services.get_service(name), services.find_client(name))
            except Exception as e:
                print(f"❌ {name}: {e}")
            if cancel_requested():
                break
        set_progress(total, total, "")

    return f"Backup de tokens HEC · {names[0] if len(names) == 1 else f'{len(names)} clientes'}", run
