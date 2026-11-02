"""
Add-ons y apps de Splunkbase desde la web: actualizaciones pendientes por cliente (y de todos a la vez), actualización
vía ACS como tarea en segundo plano y cuenta de Splunkbase guardada en config.py.
"""
from concurrent.futures import ThreadPoolExecutor

from core import acs
from core.splunkbase_account import delete_account, get_account, save_account, saved_account
from options.addons.update_addons import pending_with_acs, update_addons
from web import services
from web.jobs import cancel_requested, capture_output, set_progress
from web.services import ClientError

CLIENT_WORKERS = 6


# --- Cuenta de Splunkbase ------------------------------------------------------------------------

def account_status():
    """Cuenta de Splunkbase que se usaría, sin la contraseña: {configured, username, source, saved, problem}."""
    account = get_account()
    saved = saved_account()
    return {
        "configured": account is not None,
        "username": account["username"] if account else (saved or {}).get("username", ""),
        "source": account["source"] if account else None,     # "config.py" o "entorno"
        "saved": saved is not None,                           # hay una en config.py (aunque no se pueda descifrar)
        "problem": (saved or {}).get("problem"),
    }


def store_account(data):
    """Guarda la cuenta en config.py (contraseña cifrada), comprobándola antes en Splunkbase salvo que test sea False."""
    username = str(data.get("username") or "").strip()
    password = str(data.get("password") or "")
    if not username or not password:
        raise ClientError("Indique el usuario y la contraseña de Splunkbase.")
    if data.get("test", True):
        try:
            acs.splunkbase_login(username, password)
        except acs.AcsError as e:
            raise ClientError(str(e))
    save_account(username, password)
    return account_status()


def remove_account():
    delete_account()
    return account_status()


# --- Pendientes ----------------------------------------------------------------------------------

def _acs_info(client):
    reason = acs.unavailable_reason(client)
    return {"available": reason is None, "reason": reason, "stack": acs.stack_name(client.get("host"))}


def pending(client_name):
    """Actualizaciones pendientes del cliente: {client, host, acs, pending, warnings}. Conecta si hace falta."""
    client = services.find_client(client_name)
    service = services.get_service(client_name)
    apps, warnings = pending_with_acs(service, client)
    return {"client": client_name, "host": client.get("host", ""), "acs": _acs_info(client), "pending": apps,
            "warnings": warnings}


def overview(names=None):
    """Pendientes de varios clientes en paralelo (todos los configurados si no se indican); los que fallan llevan 'error'."""
    clients = services.list_clients()
    hosts = {c["name"]: c["host"] for c in clients}
    targets = [n for n in (names or list(hosts)) if n in hosts]

    def one(name):
        try:
            with capture_output():   # los mensajes de conexión no interesan aquí
                return pending(name)
        except Exception as e:
            return {"client": name, "host": hosts.get(name, ""), "acs": None, "pending": [], "warnings": [],
                    "error": str(e)}

    with ThreadPoolExecutor(CLIENT_WORKERS) as pool:
        return list(pool.map(one, targets))


# --- Actualizar (tarea) ---------------------------------------------------------------------------

def build_update(client_name, names):
    """Tarea que actualiza en el cliente las apps pendientes indicadas. Devuelve (título, función)."""
    names = [str(n).strip() for n in (names or []) if str(n).strip()]
    if not names:
        raise ClientError("Seleccione al menos una app para actualizar.")
    client = services.find_client(client_name)
    reason = acs.unavailable_reason(client)
    if reason:
        raise ClientError(f"{client_name}: {reason}.")
    account = get_account()
    if not account:
        raise ClientError("No hay cuenta de Splunkbase: guárdela en la sección Add-ons antes de actualizar.")
    label = names[0] if len(names) == 1 else f"{len(names)} apps"
    title = f"Actualizar add-ons ({label}) · {client_name}"

    def action():
        service = services.get_service(client_name)
        update_addons(service, client, names, account=account, stop=cancel_requested, progress=set_progress)

    return title, action
