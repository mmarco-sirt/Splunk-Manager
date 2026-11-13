"""
Webhook de Teams (flujo de Workflows / Power Automate "Publicar en un canal cuando se recibe una solicitud de webhook")
guardado en config.py y cifrado con DPAPI como las credenciales de los clientes (ver core/vault.py):

    teams = {"webhook": "dpapi:..."}

La URL lleva la firma (sig=...) que permite publicar en el canal: se trata como un secreto.
"""
import ast
import json
import os
import urllib.request

from core import vault
from core.clients import _read_source, _write_atomic
from core.paths import CONFIG_PATH

VAR = "teams"


def _find(source):
    """(nodo AST, valor dict) de la asignación 'teams = {...}' en config.py, o (None, {})."""
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id == VAR:
            try:
                value = ast.literal_eval(node.value)
            except ValueError:
                value = {}
            return node, value if isinstance(value, dict) else {}
    return None, {}


def _render(source, value):
    """Contenido de config.py con la asignación 'teams' sustituida (o añadida al final) y el resto intacto."""
    lines = source.splitlines()
    block = f"{VAR} = {json.dumps(value, ensure_ascii=False, indent=4)}" if value else None
    node, _ = _find(source) if source.strip() else (None, {})
    if node is not None:
        lines[node.lineno - 1:node.end_lineno] = [block] if block else []
    elif block:
        lines += ["", "# Webhook de Teams (Workflows) para la vigilancia de alertas", block]
    return "\n".join(lines).strip("\n") + "\n"


def save_webhook(url):
    """Guarda la URL del webhook en config.py, cifrada."""
    url = str(url or "").strip()
    if not url.lower().startswith("https://"):
        raise ValueError("La URL del webhook de Teams debe empezar por https://")
    source = _read_source() if os.path.exists(CONFIG_PATH) else ""
    _write_atomic(_render(source, {"webhook": vault.encrypt(url) if vault.available() else url}))


def get_webhook():
    """URL del webhook descifrada, o None si no hay. Lanza vault.VaultError si no se puede descifrar."""
    if not os.path.exists(CONFIG_PATH):
        return None
    value = _find(_read_source())[1].get("webhook") or ""
    if not value:
        return None
    return vault.decrypt(value) if vault.is_encrypted(value) else value


def card(title, blocks, color="attention"):
    """
    Mensaje con una Adaptive Card. 'blocks' es una lista de (encabezado o None, [líneas]). 'color' es el del título:
    attention (rojo), good (verde), warning (amarillo) o default.
    """
    body = [{"type": "TextBlock", "text": title, "weight": "Bolder", "size": "Medium", "color": color, "wrap": True}]
    for heading, lines in blocks:
        if heading:
            body.append({"type": "TextBlock", "text": heading, "weight": "Bolder", "spacing": "Medium", "wrap": True})
        if lines:
            body.append({"type": "TextBlock", "text": "\n\n".join(lines), "spacing": "Small", "wrap": True})
    return {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "contentUrl": None,
            "content": {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard",
                        "version": "1.4", "msteams": {"width": "Full"}, "body": body},
        }],
    }


def post(url, message, timeout=30):
    """Envía el mensaje al webhook. Lanza excepción si Teams no lo acepta."""
    request = urllib.request.Request(url, data=json.dumps(message).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status >= 300:
            raise RuntimeError(f"Teams respondió {response.status}")
