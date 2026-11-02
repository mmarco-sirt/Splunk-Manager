"""
Cuenta de Splunkbase (usuario y contraseña de splunk.com) guardada en config.py, con la contraseña cifrada con DPAPI
igual que las credenciales de los clientes (ver core/vault.py). Hace falta para actualizar apps de Splunkbase vía ACS.

    splunkbase = {"username": "usuario@empresa.com", "password": "dpapi:..."}

También se puede indicar sin guardarla con las variables de entorno SPLUNKBASE_USERNAME y SPLUNKBASE_PASSWORD.
"""
import ast
import json
import os

from core import vault
from core.clients import _read_source, _write_atomic
from core.paths import CONFIG_PATH

VAR = "splunkbase"
ENV_USERNAME, ENV_PASSWORD = "SPLUNKBASE_USERNAME", "SPLUNKBASE_PASSWORD"


def _find(source):
    """(nodo AST, valor dict) de la asignación 'splunkbase = {...}' en config.py, o (None, {})."""
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id == VAR:
            try:
                value = ast.literal_eval(node.value)
            except ValueError:
                value = {}
            return node, value if isinstance(value, dict) else {}
    return None, {}


def _stored():
    if not os.path.exists(CONFIG_PATH):
        return {}
    return _find(_read_source())[1]


def saved_account():
    """Cuenta guardada en config.py con la contraseña descifrada, o None. Si no se puede descifrar, 'problem' lo dice."""
    data = _stored()
    username, password = str(data.get("username") or "").strip(), data.get("password") or ""
    if not username:
        return None
    account = {"username": username, "password": password, "problem": None}
    if vault.is_encrypted(password):
        try:
            account["password"] = vault.decrypt(password)
        except vault.VaultError:
            account["password"] = ""
            account["problem"] = ("la contraseña de Splunkbase se guardó con otro usuario de Windows o en otro equipo; "
                                  "vuelva a introducirla")
    return account


def get_account():
    """
    Cuenta de Splunkbase a usar: las variables de entorno SPLUNKBASE_USERNAME/SPLUNKBASE_PASSWORD si están las dos y,
    si no, la guardada en config.py. Devuelve {"username", "password", "source"} o None.
    """
    username, password = os.environ.get(ENV_USERNAME, "").strip(), os.environ.get(ENV_PASSWORD, "")
    if username and password:
        return {"username": username, "password": password, "source": "entorno"}
    account = saved_account()
    if account and account["password"]:
        return {"username": account["username"], "password": account["password"], "source": "config.py"}
    return None


def _render(source, value):
    """Contenido de config.py con la asignación 'splunkbase' sustituida (o añadida al final) y el resto intacto."""
    lines = source.splitlines()
    block = f"{VAR} = {json.dumps(value, ensure_ascii=False, indent=4)}" if value else None
    node, _ = _find(source) if source.strip() else (None, {})
    if node is not None:
        lines[node.lineno - 1:node.end_lineno] = [block] if block else []
    elif block:
        lines += ["", "# Cuenta de Splunkbase (splunk.com) para actualizar apps y add-ons vía ACS", block]
    return "\n".join(lines).strip("\n") + "\n"


def save_account(username, password):
    """Guarda la cuenta en config.py con la contraseña cifrada."""
    username, password = str(username or "").strip(), str(password or "")
    if not username or not password:
        raise ValueError("Indique el usuario y la contraseña de Splunkbase.")
    source = _read_source() if os.path.exists(CONFIG_PATH) else ""
    _write_atomic(_render(source, {"username": username, "password": vault.encrypt(password)}))


def delete_account():
    """Quita la cuenta de config.py (si la había)."""
    if os.path.exists(CONFIG_PATH):
        _write_atomic(_render(_read_source(), None))


def encrypt_if_plain():
    """Si la contraseña de Splunkbase está en claro en config.py, la cifra. Devuelve un mensaje o None."""
    data = _stored()
    password = data.get("password") or ""
    if not password or vault.is_encrypted(password) or not vault.available():
        return None
    save_account(data.get("username"), password)
    return "🔐 Se ha cifrado la contraseña de Splunkbase de config.py con DPAPI de Windows."
