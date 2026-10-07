"""Clientes de config.py y conexiones a Splunk reutilizables entre peticiones."""
import threading
import time

from core.clients import get_clients_from_config, save_clients_to_config
from core.connection import connect_to_splunk
from web.jobs import capture_output

CLIENT_FIELDS = ("name", "host", "username", "password", "splunkToken")

_services = {}   # nombre de cliente -> {"service", "connected_at", "info"}
_connect_locks = {}
_lock = threading.Lock()


class ClientError(Exception):
    """Error de cliente/conexión con un mensaje apto para mostrar en la web."""


def public_client(client):
    """Datos del cliente aptos para el navegador: nunca incluye contraseña ni token."""
    name = client.get("name", "")
    conn = _services.get(name)
    if client.get("username") and client.get("password"):
        auth = "user"
    elif client.get("splunkToken"):
        auth = "token"
    else:
        auth = "none"
    return {
        "name": name,
        "host": client.get("host", ""),
        "username": client.get("username", ""),
        "has_password": bool(client.get("password")),
        "has_token": bool(client.get("splunkToken")),
        "auth": auth,
        "connected": conn is not None,
        "connected_at": conn["connected_at"] if conn else None,
        "info": conn["info"] if conn else None,
    }


def list_clients():
    return [public_client(c) for c in get_clients_from_config()]


def find_client(name):
    client = next((c for c in get_clients_from_config() if c.get("name") == name), None)
    if not client:
        raise ClientError(f"No existe el cliente '{name}'.")
    return client


def _server_info(service):
    info = service.info
    return {
        "serverName": info.get("serverName"),
        "version": info.get("version"),
        "build": info.get("build"),
        "product_type": info.get("product_type"),
        "instance_type": info.get("instance_type"),
        "os_name": info.get("os_name"),
        "server_roles": info.get("server_roles") or [],
        "health": info.get("health_info"),
    }


def _client_lock(name):
    with _lock:
        return _connect_locks.setdefault(name, threading.Lock())


def connect(name, force=False):
    """Conecta (o reutiliza la conexión) con el cliente. Lanza ClientError con el motivo si falla."""
    if not force:
        conn = _services.get(name)
        if conn:
            return conn["service"]

    # Evita varios logins simultáneos al mismo cliente (p. ej. peticiones en paralelo del navegador)
    with _client_lock(name):
        conn = _services.get(name)
        if conn and not force:
            return conn["service"]
        return _do_connect(name)


def _do_connect(name):
    client = find_client(name)
    with capture_output() as output:
        service = connect_to_splunk(client)
    if not service:
        message = "".join(output).strip() or "No se pudo conectar."
        raise ClientError(f"{name}: {message}")

    try:
        info = _server_info(service)
    except Exception as e:
        raise ClientError(f"{name}: conectado, pero no se pudo leer la información del servidor: {e}")

    with _lock:
        _services[name] = {"service": service, "connected_at": time.time(), "info": info}
    return service


def disconnect(name):
    with _lock:
        conn = _services.pop(name, None)
    if conn:
        try:
            conn["service"].logout()
        except Exception:
            pass


def get_service(name):
    """Conexión al cliente: la reutiliza si ya existe y, si no, conecta."""
    if not name:
        raise ClientError("No se ha indicado ningún cliente.")
    return connect(name)


def _clean(data):
    return {k: str(data[k]).strip() for k in CLIENT_FIELDS if data.get(k) and str(data[k]).strip()}


def create_client(data):
    new_client = _clean(data)
    if not new_client.get("name") or not new_client.get("host"):
        raise ClientError("El nombre y el host son obligatorios.")
    clients = get_clients_from_config()
    if any(c.get("name") == new_client["name"] for c in clients):
        raise ClientError(f"Ya existe un cliente con el nombre '{new_client['name']}'.")
    clients.append(new_client)
    with capture_output():
        save_clients_to_config(clients)
    return public_client(new_client)


def update_client(name, data):
    """Actualiza un cliente. Contraseña/token vacíos mantienen el valor actual; clear_* los elimina."""
    clients = get_clients_from_config()
    index = next((i for i, c in enumerate(clients) if c.get("name") == name), None)
    if index is None:
        raise ClientError(f"No existe el cliente '{name}'.")

    current = dict(clients[index])
    changes = _clean(data)
    if not changes.get("name") or not changes.get("host"):
        raise ClientError("El nombre y el host son obligatorios.")
    if changes["name"] != name and any(c.get("name") == changes["name"] for c in clients):
        raise ClientError(f"Ya existe un cliente con el nombre '{changes['name']}'.")

    current["name"], current["host"] = changes["name"], changes["host"]
    if changes.get("username"):
        current["username"] = changes["username"]
    else:
        current.pop("username", None)
    for field, clear_flag in (("password", "clear_password"), ("splunkToken", "clear_token")):
        if data.get(clear_flag):
            current.pop(field, None)
        elif changes.get(field):
            current[field] = changes[field]

    clients[index] = current
    with capture_output():
        save_clients_to_config(clients)
    disconnect(name)
    return public_client(current)


def delete_client(name):
    clients = get_clients_from_config()
    remaining = [c for c in clients if c.get("name") != name]
    if len(remaining) == len(clients):
        raise ClientError(f"No existe el cliente '{name}'.")
    with capture_output():
        save_clients_to_config(remaining)
    disconnect(name)
