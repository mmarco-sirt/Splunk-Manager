"""Clientes de config.py y conexiones a Splunk reutilizables entre peticiones."""
import threading
import time

from core import vault
from core.clients import (
    AUTH_METHODS, credential_problems, get_clients_from_config, preferred_auth, save_clients_to_config,
)
from core.connection import auth_methods, connect_to_splunk
from web.jobs import capture_output

CLIENT_FIELDS = ("name", "host", "username", "password", "splunkToken")

_services = {}   # nombre de cliente -> {"service", "connected_at", "info", "method"}
_connect_locks = {}
_lock = threading.Lock()


class ClientError(Exception):
    """Error de cliente/conexión con un mensaje apto para mostrar en la web."""


def public_client(client):
    """Datos del cliente aptos para el navegador: nunca incluye contraseña ni token."""
    name = client.get("name", "")
    conn = _services.get(name)
    methods = auth_methods(client)
    return {
        "name": name,
        "host": client.get("host", ""),
        "username": client.get("username", ""),
        "has_password": bool(client.get("password")),
        "has_token": bool(client.get("splunkToken")),
        "auth": preferred_auth(client) if methods else "none",   # método preferido
        "methods": methods,                                       # métodos configurados, el preferido primero
        "credential_problems": credential_problems(client),      # credenciales que no se pueden descifrar
        "connected": conn is not None,
        "connected_at": conn["connected_at"] if conn else None,
        "connected_with": conn["method"] if conn else None,
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


def _open(client, method=None):
    """Conecta con el cliente (con un método concreto o con el preferido y, si falla, el otro)."""
    with capture_output() as output:
        service = connect_to_splunk(client, method=method)
    if not service:
        message = "".join(output).strip() or "No se pudo conectar."
        raise ClientError(f"{client.get('name') or client.get('host')}: {message}")
    return service


def _do_connect(name):
    client = find_client(name)
    service = _open(client)
    try:
        info = _server_info(service)
    except Exception as e:
        raise ClientError(f"{name}: conectado, pero no se pudo leer la información del servidor: {e}")

    with _lock:
        _services[name] = {"service": service, "connected_at": time.time(), "info": info,
                           "method": getattr(service, "auth_method", None)}
    return service


def server_info(name):
    """Información del servidor guardada al conectar (versión, nombre...), o None si no hay conexión."""
    conn = _services.get(name)
    return conn["info"] if conn else None


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


def _apply_auth(client, auth):
    """Guarda el método preferido solo si el cliente tiene los dos tipos de credenciales."""
    if client.get("username") and client.get("password") and client.get("splunkToken") and auth in AUTH_METHODS:
        client["auth"] = auth
    else:
        client.pop("auth", None)


def _save(clients):
    try:
        with capture_output():
            save_clients_to_config(clients)
    except vault.VaultError as e:
        raise ClientError(str(e))


def create_client(data):
    new_client = _clean(data)
    if not new_client.get("name") or not new_client.get("host"):
        raise ClientError("El nombre y el host son obligatorios.")
    clients = get_clients_from_config()
    if any(c.get("name") == new_client["name"] for c in clients):
        raise ClientError(f"Ya existe un cliente con el nombre '{new_client['name']}'.")
    _apply_auth(new_client, data.get("auth"))
    clients.append(new_client)
    _save(clients)
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
    _apply_auth(current, data.get("auth") or current.get("auth"))

    clients[index] = current
    _save(clients)
    disconnect(name)
    return public_client(current)


def delete_client(name):
    clients = get_clients_from_config()
    remaining = [c for c in clients if c.get("name") != name]
    if len(remaining) == len(clients):
        raise ClientError(f"No existe el cliente '{name}'.")
    _save(remaining)
    disconnect(name)


def test_credentials(data):
    """
    Prueba unas credenciales con un método concreto sin guardarlas. Si se indica 'original' (cliente existente),
    los campos vacíos usan sus valores guardados. Devuelve {ok, method, message, version, serverName}.
    """
    method = data.get("method")
    if method not in AUTH_METHODS:
        raise ClientError("Método de autenticación no válido.")
    client = dict(find_client(data["original"])) if data.get("original") else {}
    changes = _clean(data)
    secret = "password" if method == "password" else "splunkToken"
    if client and changes.get("host") and changes["host"].lower() != client.get("host", "").lower() and not changes.get(secret):
        # Nunca se envía una credencial guardada a un host distinto del suyo
        raise ClientError(f"Para probar con otro host, vuelva a escribir {'la contraseña' if secret == 'password' else 'el token'}.")
    for field in ("name", "host", "username"):
        if field in changes:
            client[field] = changes[field]
    for field, clear_flag in (("password", "clear_password"), ("splunkToken", "clear_token")):
        if data.get(clear_flag):
            client.pop(field, None)
        if changes.get(field):
            client[field] = changes[field]
    if not client.get("host"):
        raise ClientError("Indique el host.")
    client.setdefault("name", client["host"])

    label = AUTH_METHODS[method]
    try:
        service = _open(client, method=method)
    except ClientError as e:
        return {"ok": False, "method": method, "message": str(e)}
    try:
        info = _server_info(service)
    finally:
        try:
            if method == "password":
                service.logout()
        except Exception:
            pass
    return {"ok": True, "method": method, "message": f"Conexión correcta con {label}",
            "version": info.get("version"), "serverName": info.get("serverName")}
