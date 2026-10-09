import splunklib.client
from splunklib.binding import HTTPError

from core import vault
from core.clients import AUTH_METHODS, preferred_auth


def select_client(clients):
    """Permite seleccionar un cliente de la lista."""
    print("Seleccione un cliente para conectarse a Splunk:")
    for i, client in enumerate(clients, start=1):
        print(f"{i}. {client['name']}")

    while True:
        try:
            choice = int(input("Ingrese el número del cliente: ")) - 1
            if 0 <= choice < len(clients):
                return clients[choice]
            print("Número fuera de rango, intente nuevamente.")
        except ValueError:
            print("Entrada no válida, ingrese un número.")


class CredentialError(Exception):
    """El cliente no tiene credenciales utilizables para un método de autenticación."""


def auth_methods(client):
    """Métodos con credenciales configuradas, empezando por el preferido ('auth')."""
    available = []
    if client.get("username") and client.get("password"):
        available.append("password")
    if client.get("splunkToken"):
        available.append("token")
    preferred = preferred_auth(client)
    return sorted(available, key=lambda m: m != preferred)


def _check_secret(client, field):
    if vault.is_encrypted(client.get(field)):
        raise CredentialError(f"no se puede descifrar ({'contraseña' if field == 'password' else 'token'} guardado "
                              f"con otro usuario de Windows o en otro equipo; vuelva a introducirlo)")


def _connect(client, method):
    """Conecta con un método concreto y comprueba que Splunk acepta las credenciales."""
    if method == "password":
        if not (client.get("username") and client.get("password")):
            raise CredentialError("no hay usuario y contraseña")
        _check_secret(client, "password")
        service = splunklib.client.connect(host=client["host"], username=client["username"],
                                           password=client["password"], autologin=True)
    elif method == "token":
        if not client.get("splunkToken"):
            raise CredentialError("no hay token")
        _check_secret(client, "splunkToken")
        service = splunklib.client.connect(host=client["host"], splunkToken=client["splunkToken"], autologin=True)
    else:
        raise CredentialError(f"método de autenticación desconocido: {method}")
    # Con token no hay login: una petición sencilla comprueba que el token es válido
    service.get("server/info")
    service.auth_method = method
    return service


def _is_credential_failure(error):
    return isinstance(error, CredentialError) or (isinstance(error, HTTPError) and error.status in (401, 403))


def connect_to_splunk(client, method=None):
    """
    Establece la conexión a Splunk. Usa el método preferido del cliente (usuario/contraseña o token) y, si Splunk
    rechaza esas credenciales, prueba con el otro. Con 'method' solo se prueba ese método.
    """
    methods = [method] if method else auth_methods(client)
    if not methods:
        print("Error: No se proporcionaron credenciales válidas (usuario/contraseña o token).")
        return None

    for i, current in enumerate(methods):
        label = AUTH_METHODS.get(current, current)
        try:
            service = _connect(client, current)
        except Exception as e:
            print(f"Error al conectar a Splunk con {label}: {e}")
            if i + 1 < len(methods) and _is_credential_failure(e):
                print(f"Probando con {AUTH_METHODS[methods[i + 1]]}...")
                continue
            return None
        if current == "password":
            print(f"Conectado a Splunk como: {client['username']}")
        else:
            print(f"Conectado a {client['name']} usando token.")
        if i > 0:
            print(f"⚠️ Aviso: se ha conectado con {label} porque falló el método preferido. Revise sus credenciales.")
        return service
    return None
