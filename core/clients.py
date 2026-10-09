"""
Clientes de config.py.

Cada cliente puede tener usuario/contraseña, token o ambos; 'auth' indica el método preferido ("password" o
"token") y, si falla por credenciales, se prueba el otro. Las contraseñas y tokens se guardan cifrados con DPAPI
de Windows (ver core/vault.py): get_clients_from_config() los devuelve ya descifrados (solo en memoria) y
save_clients_to_config() los cifra al escribir.
"""
import ast
import copy
import getpass
import json
import os
import tempfile

from core import vault
from core.paths import CONFIG_PATH

CLIENTS_VAR = "clientes"
LEGACY_TOKEN_VAR = "clientes_token"  # Formato antiguo: lista aparte con los clientes que usan token
SECRET_FIELDS = ("password", "splunkToken")
AUTH_METHODS = {"password": "usuario/contraseña", "token": "token"}
FIELD_ORDER = ("name", "host", "username", "password", "splunkToken", "auth")

_cache = {"key": None, "clients": None, "notes": None}


def ensure_config_exists():
    """Crea un config.py vacío si no existe. Devuelve True si lo ha creado."""
    if os.path.exists(CONFIG_PATH):
        return False
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        f.write("clientes = []\n")
    return True


# --- Lectura -----------------------------------------------------------------

def _read_source():
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return f.read()


def _find_lists(source):
    """{nombre_variable: (nodo AST, valor)} de 'clientes' y 'clientes_token' en el código de config.py."""
    found = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id in (CLIENTS_VAR, LEGACY_TOKEN_VAR):
            try:
                value = ast.literal_eval(node.value)
            except ValueError:
                print(f"❌ Error: No se pudo evaluar la lista '{node.targets[0].id}' en config.py.")
                value = []
            found[node.targets[0].id] = (node, value if isinstance(value, list) else [])
    return found


def _norm(text):
    return str(text or "").strip().lower()


def merge_token_clients(clients, token_clients):
    """
    Une la lista antigua 'clientes_token' con 'clientes': mismo nombre o mismo host = mismo cliente (se le añade el
    token); el resto se añaden como clientes nuevos. Devuelve (clientes, notas sobre lo que se ha unido).
    """
    merged = [dict(c) for c in clients if isinstance(c, dict)]
    notes = []
    for token_client in token_clients:
        if not isinstance(token_client, dict) or not token_client.get("name"):
            continue
        target = next((c for c in merged if _norm(c.get("name")) == _norm(token_client["name"])), None)
        if target is None and token_client.get("host"):
            target = next((c for c in merged if _norm(c.get("host")) == _norm(token_client["host"])), None)
            if target is not None:
                notes.append(f"'{token_client['name']}' (token) tiene el mismo host que '{target['name']}': "
                             f"se han unido en '{target['name']}'.")
        if target is None:
            merged.append(dict(token_client))
            continue
        if token_client.get("splunkToken") and not target.get("splunkToken"):
            target["splunkToken"] = token_client["splunkToken"]
        if not target.get("host"):
            target["host"] = token_client.get("host")
        elif token_client.get("host") and _norm(token_client["host"]) != _norm(target["host"]):
            notes.append(f"'{target['name']}': el host de la lista de tokens ({token_client['host']}) no coincide; "
                         f"se mantiene {target['host']}.")
    return merged, notes


def _decrypt_client(client):
    """Copia del cliente con las credenciales descifradas. Las que no se pueden descifrar se dejan cifradas."""
    result = dict(client)
    for field in SECRET_FIELDS:
        value = result.get(field)
        if vault.is_encrypted(value):
            try:
                result[field] = vault.decrypt(value)
            except vault.VaultError:
                pass  # Se conserva cifrada: así no se pierde al guardar y credential_problems() la detecta
    return result


def _load():
    """Lee config.py (con caché mientras el archivo no cambie). Devuelve (clientes descifrados, notas de unión)."""
    if not os.path.exists(CONFIG_PATH):
        return [], []
    stat = os.stat(CONFIG_PATH)
    key = (stat.st_mtime_ns, stat.st_size)
    if _cache["key"] != key:
        lists = _find_lists(_read_source())
        clients = lists.get(CLIENTS_VAR, (None, []))[1]
        token_clients = lists.get(LEGACY_TOKEN_VAR, (None, []))[1]
        merged, notes = merge_token_clients(clients, token_clients)
        _cache.update(key=key, clients=[_decrypt_client(c) for c in merged], notes=notes)
    return copy.deepcopy(_cache["clients"]), list(_cache["notes"])


def get_clients_from_config():
    """Lista de clientes de config.py con las credenciales descifradas (solo en memoria)."""
    return _load()[0]


def credential_problems(client):
    """Campos de credenciales del cliente que no se han podido descifrar con este usuario de Windows."""
    return [field for field in SECRET_FIELDS if vault.is_encrypted(client.get(field))]


def preferred_auth(client):
    """Método de autenticación preferido del cliente: el indicado en 'auth' o, si no, el que tenga."""
    auth = client.get("auth")
    if auth in AUTH_METHODS:
        return auth
    if client.get("username") and client.get("password"):
        return "password"
    return "token" if client.get("splunkToken") else "password"


# --- Escritura ------------------------------------------------------------------

def _format_clients(clients_list):
    """Código Python de la lista de clientes, con las credenciales cifradas y un campo por línea."""
    lines = [f"{CLIENTS_VAR} = ["]
    for client in clients_list:
        fields = {k: v for k, v in client.items() if not k.startswith("_") and v not in (None, "")}
        for field in SECRET_FIELDS:
            if field in fields:
                fields[field] = vault.encrypt(str(fields[field]))
        ordered = [k for k in FIELD_ORDER if k in fields] + [k for k in fields if k not in FIELD_ORDER]
        lines.append("    {")
        for key in ordered:
            value = fields[key]
            literal = json.dumps(value, ensure_ascii=False) if isinstance(value, str) else repr(value)
            lines.append(f"        {json.dumps(key)}: {literal},")
        lines.append("    },")
    lines.append("]")
    return "\n".join(lines)


def _write_atomic(content):
    folder = os.path.dirname(CONFIG_PATH) or "."
    fd, temp_path = tempfile.mkstemp(prefix=".config_", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(temp_path, CONFIG_PATH)
    except BaseException:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise
    _cache["key"] = None


def _render_config(clients_list):
    """Contenido nuevo de config.py: sustituye 'clientes', quita la lista antigua 'clientes_token' y conserva el resto."""
    source = _read_source() if os.path.exists(CONFIG_PATH) else ""
    lines = source.splitlines()
    lists = _find_lists(source) if source.strip() else {}
    new_block = _format_clients(clients_list)

    edits = []  # (primera línea, última línea, texto nuevo) con índices desde 0
    if CLIENTS_VAR in lists:
        node = lists[CLIENTS_VAR][0]
        edits.append((node.lineno - 1, node.end_lineno - 1, new_block))
    if LEGACY_TOKEN_VAR in lists:
        node = lists[LEGACY_TOKEN_VAR][0]
        edits.append((node.lineno - 1, node.end_lineno - 1, None))
    for start, end, text in sorted(edits, reverse=True):
        lines[start:end + 1] = [text] if text is not None else []
    if CLIENTS_VAR not in lists:
        lines += ["", new_block]
    return "\n".join(lines).strip("\n") + "\n"


def save_clients_to_config(clients_list):
    """Guarda la lista de clientes en config.py (credenciales cifradas), preservando el resto del archivo."""
    _write_atomic(_render_config(clients_list))
    print("\n✅ `config.py` ha sido actualizado correctamente.")


# --- Migración de config.py antiguos ------------------------------------------------

def needs_migration():
    """True si config.py tiene credenciales sin cifrar o la lista antigua 'clientes_token'."""
    if not os.path.exists(CONFIG_PATH):
        return False
    lists = _find_lists(_read_source())
    if LEGACY_TOKEN_VAR in lists:
        return True
    clients = lists.get(CLIENTS_VAR, (None, []))[1]
    return any(c.get(f) and not vault.is_encrypted(c.get(f)) for c in clients if isinstance(c, dict) for f in SECRET_FIELDS)


def migrate_config():
    """
    Cifra las credenciales que estén en claro en config.py y une la lista antigua 'clientes_token' en 'clientes'.
    Comprueba el resultado y, si algo no cuadra, deja el archivo como estaba. Devuelve la lista de mensajes.
    """
    if not needs_migration():
        return []
    if not vault.available():
        return ["⚠️ config.py tiene credenciales sin cifrar, pero el cifrado (DPAPI) solo está disponible en Windows."]

    original = _read_source()
    lists = _find_lists(original)
    clients, notes = _load()
    plain = sum(1 for c in lists.get(CLIENTS_VAR, (None, []))[1] + lists.get(LEGACY_TOKEN_VAR, (None, []))[1]
                if isinstance(c, dict) for f in SECRET_FIELDS if c.get(f) and not vault.is_encrypted(c.get(f)))

    _write_atomic(_render_config(clients))
    saved = get_clients_from_config()
    expected = [{k: v for k, v in c.items() if v not in (None, "")} for c in clients]
    if saved != expected or any(credential_problems(c) for c in saved):
        _write_atomic(original)
        raise RuntimeError("La comprobación del config.py cifrado ha fallado; se ha dejado el archivo como estaba.")

    messages = [f"🔐 Se han cifrado {plain} credenciales de config.py con DPAPI de Windows (ya no están en claro)."]
    if LEGACY_TOKEN_VAR in lists:
        messages.append(f"🔗 La lista antigua '{LEGACY_TOKEN_VAR}' se ha unido a '{CLIENTS_VAR}': "
                        f"{len(saved)} clientes en total.")
    return messages + notes


def migrate_config_cli():
    """Ejecuta migrate_config() mostrando el resultado por consola (CLI y aplicación de escritorio)."""
    try:
        for message in migrate_config():
            print(message)
    except Exception as e:
        print(f"❌ No se pudieron cifrar las credenciales de config.py: {e}")


# --- Gestión por consola ---------------------------------------------------------------

def _ask_secret(label, has_value):
    """Pide una contraseña o token sin mostrarlo. Vacío = sin cambios; 'BORRAR' = eliminar."""
    hint = " [guardado; vacío = sin cambios, BORRAR = quitar]" if has_value else " (vacío si no aplica)"
    return getpass.getpass(f"  - {label}{hint}: ").strip()


def _ask_auth(client):
    """Si el cliente tiene usuario/contraseña y token, pregunta cuál usar primero."""
    if not (client.get("username") and client.get("password") and client.get("splunkToken")):
        client.pop("auth", None)
        return
    current = preferred_auth(client)
    choice = input(f"  - Método preferido: 1) usuario/contraseña  2) token  [{'1' if current == 'password' else '2'}]: ").strip()
    client["auth"] = {"1": "password", "2": "token"}.get(choice, current)


def modify_client():
    """Modifica un cliente existente en la configuración."""
    clients = get_clients_from_config()
    if not clients:
        print("No se encontraron clientes en `config.py`.")
        return

    print("\n--- Modificar Cliente Existente ---")
    for i, client in enumerate(clients):
        print(f"{i + 1}) {client['name']}")

    try:
        choice = int(input("\nSeleccione el cliente a modificar (número): ")) - 1
        if not (0 <= choice < len(clients)):
            print("Selección inválida.")
            return
    except ValueError:
        print("Entrada no válida.")
        return

    client_to_modify = clients[choice]
    print(f"\nEditando '{client_to_modify['name']}'. Deje en blanco para no cambiar.")
    print("Para credenciales, puede escribir 'BORRAR' para eliminar el campo.")

    # Campos de texto principales
    client_to_modify['name'] = input(f"  - Nombre [{client_to_modify.get('name', '')}]: ") or client_to_modify.get('name')
    client_to_modify['host'] = input(f"  - Host [{client_to_modify.get('host', '')}]: ") or client_to_modify.get('host')

    # Campos de credenciales con opción de borrado
    new_user = input(f"  - Username [{client_to_modify.get('username', '')}]: ")
    if new_user.upper() == 'BORRAR':
        client_to_modify.pop('username', None)
    elif new_user:
        client_to_modify['username'] = new_user

    for field, label in (("password", "Password"), ("splunkToken", "Token")):
        value = _ask_secret(label, bool(client_to_modify.get(field)))
        if value.upper() == 'BORRAR':
            client_to_modify.pop(field, None)
        elif value:
            client_to_modify[field] = value

    _ask_auth(client_to_modify)
    save_clients_to_config(clients)

def add_client():
    """Añade un nuevo cliente a la configuración."""
    clients = get_clients_from_config()

    print("\n--- Añadir Nuevo Cliente ---")
    new_client = {}
    new_client['name'] = input("  - Nombre del cliente: ")
    new_client['host'] = input("  - Host (ej: cliente.splunkcloud.com): ")

    print("\n  Introduzca credenciales: usuario/contraseña, token o ambos (deje en blanco si no aplica):")
    new_client['username'] = input("  - Username: ")
    new_client['password'] = _ask_secret("Password", False)
    new_client['splunkToken'] = _ask_secret("Token", False)

    # Limpiar valores vacíos para no guardarlos en el archivo
    new_client = {k: v for k, v in new_client.items() if v}

    if not new_client.get('name') or not new_client.get('host'):
        print("\n❌ Error: El nombre y el host son obligatorios. Cliente no añadido.")
        return
    if any(_norm(c.get('name')) == _norm(new_client['name']) for c in clients):
        print(f"\n❌ Error: Ya existe un cliente con el nombre '{new_client['name']}'.")
        return

    _ask_auth(new_client)
    clients.append(new_client)
    save_clients_to_config(clients)

def manage_clients_cli():
    """Menú de consola para añadir/modificar clientes."""
    if ensure_config_exists():
        print("Aviso: No se encontraba `config.py`. Se ha creado uno vacío.")

    while True:
        print("\n--- Gestión de Clientes de Splunk ---")
        print("1) Modificar un cliente existente")
        print("2) Añadir un nuevo cliente")
        print("0) Salir")
        choice = input("\nSeleccione una opción: ")

        if choice == '1':
            modify_client()
        elif choice == '2':
            add_client()
        elif choice == '0':
            break
        else:
            print("Opción no válida. Intente de nuevo.")

if __name__ == "__main__":
    manage_clients_cli()
