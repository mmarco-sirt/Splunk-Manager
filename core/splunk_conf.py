"""
Lectura de los archivos de una app de Splunk sin Splunk: codificación de los archivos de texto y sintaxis de los .conf y
.meta, con los problemas que se encuentran (cada uno con su nivel, archivo y línea).

Sintaxis de un .conf (como la lee Splunk):
  - [stanza] abre una stanza; las claves anteriores a la primera stanza son de [default].
  - clave = valor (se separa por el primer «=»); una línea que termina en «\\» continúa en la siguiente.
  - Los comentarios son líneas que empiezan por «#» (no hay comentarios al final de una línea).
"""
import re

TEXT_EXTENSIONS = {
    ".conf", ".meta", ".xml", ".csv", ".txt", ".md", ".json", ".py", ".js", ".html", ".htm", ".css", ".yml", ".yaml",
    ".manifest", ".rst", ".svg", ".sh", ".ps1", ".bat", ".cfg", ".ini", ".spec", ".xsl",
}
BOM = b"\xef\xbb\xbf"
# Restos de UTF-8 leído como Latin-1/Windows-1252 y vuelto a guardar («Ã³» por «ó», «Â¿» por «¿», «â€“» por «–») y el
# carácter de sustitución (�), que aparece cuando un texto no se pudo leer
_CP1252_HIGH = "€‚ƒ„…†‡ˆ‰Š‹ŒŽ‘’“”•–—˜™š›œžŸ"
MOJIBAKE = re.compile(f"Ã[\u0080-¿{_CP1252_HIGH}]|Â[ -¿]|â€[\u0080-¿{_CP1252_HIGH}]|�")
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
MAX_REPEATED = 5   # como mucho, cuántas líneas se citan de un mismo problema en un archivo


def issue(level, category, message, file="", line=None, obj=""):
    """Problema encontrado: level es error, warning o info."""
    return {"level": level, "category": category, "file": file, "line": line, "object": obj, "message": message}


def is_text_path(path):
    name = path.rsplit("/", 1)[-1].lower()
    return "." not in name or "." + name.rsplit(".", 1)[-1] in TEXT_EXTENSIONS


def _lines_with(text, pattern):
    return [(n, m) for n, line in enumerate(text.split("\n"), 1) for m in [pattern.search(line)] if m]


def decode_text(data, path=""):
    """
    Texto de un archivo (UTF-8, con los finales de línea normalizados a \\n) y sus problemas de codificación: BOM, bytes
    que no son UTF-8, tildes y eñes rotas, finales de línea mezclados y caracteres de control.
    """
    issues = []
    lower = path.lower()
    if data.startswith(BOM):
        if lower.endswith((".conf", ".meta")):
            issues.append(issue("error", "encoding", "Empieza con BOM UTF-8: Splunk lo lee como parte de la primera línea "
                                "(la primera stanza o clave no funcionará). Guárdelo como UTF-8 sin BOM.", path, 1))
        elif lower.endswith(".csv"):
            issues.append(issue("error", "encoding", "Empieza con BOM UTF-8: la primera columna del lookup tendrá un nombre "
                                "distinto. Guárdelo como UTF-8 sin BOM.", path, 1))
        else:
            issues.append(issue("warning", "encoding", "Empieza con BOM UTF-8 (mejor UTF-8 sin BOM).", path, 1))
        data = data[len(BOM):]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        line = data[:e.start].count(b"\n") + 1
        text = data.decode("cp1252", errors="replace")
        issues.append(issue("error", "encoding", f"No es UTF-8 válido (byte 0x{data[e.start]:02X} en la línea {line}): parece "
                            "Windows-1252/Latin-1 y las tildes y eñes se verán mal en Splunk. Guárdelo como UTF-8.", path, line))
    crlf = text.count("\r\n")
    lone_cr = text.count("\r") - crlf
    lf = text.count("\n") - crlf
    if crlf and lf:
        issues.append(issue("warning", "encoding", f"Finales de línea mezclados: {crlf} Windows (CRLF) y {lf} Unix (LF).", path))
    if lone_cr:
        issues.append(issue("warning", "encoding", f"{lone_cr} retornos de carro sueltos (finales de línea de Mac antiguo).", path))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    broken = _lines_with(text, MOJIBAKE)
    for n, match in broken[:MAX_REPEATED]:
        fragment = text.split("\n")[n - 1].strip()
        start = max(fragment.find(match.group(0)) - 20, 0)
        issues.append(issue("warning", "encoding", f"Texto mal codificado (tildes o eñes rotas): «…{fragment[start:start + 60]}…».",
                            path, n))
    if len(broken) > MAX_REPEATED:
        issues.append(issue("warning", "encoding", f"Y {len(broken) - MAX_REPEATED} líneas más con texto mal codificado.", path))
    controls = _lines_with(text, CONTROL)
    if controls:
        lines = ", ".join(str(n) for n, _ in controls[:MAX_REPEATED])
        issues.append(issue("warning", "encoding", f"Caracteres de control invisibles en las líneas {lines}"
                            f"{' y más' if len(controls) > MAX_REPEATED else ''}.", path, controls[0][0]))
    return text, issues


class ConfFile:
    """Un .conf leído: stanzas {nombre: {clave: valor}} en orden, la línea de cada stanza y clave, y los problemas."""

    def __init__(self, path=""):
        self.path = path
        self.stanzas = {}
        self.stanza_lines = {}
        self.key_lines = {}
        self.issues = []

    def add(self, level, message, line, obj=""):
        self.issues.append(issue(level, "syntax", message, self.path, line, obj))


def parse_conf(text, path=""):
    """
    Lee el texto de un .conf o .meta con las reglas de Splunk. Devuelve un ConfFile (con sus problemas de sintaxis). En
    los .meta, «[]» es la stanza de los permisos de toda la app.
    """
    conf = ConfFile(path)
    allow_empty = path.endswith(".meta")
    lines = text.split("\n")
    current = "default"
    i = 0
    while i < len(lines):
        raw, lineno = lines[i], i + 1
        stripped = raw.strip()
        i += 1
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("["):
            if not stripped.endswith("]"):
                conf.add("error", f"Cabecera de stanza sin cerrar: «{stripped[:80]}».", lineno)
                continue
            name = stripped[1:-1]
            if not name.strip() and not (allow_empty and not name):
                conf.add("error", "Stanza sin nombre: «[]».", lineno)
                continue
            if name != name.strip():
                conf.add("warning", f"El nombre de la stanza «{name}» tiene espacios al principio o al final.", lineno, name)
            if name in conf.stanzas:
                conf.add("warning", f"Stanza [{name}] repetida (también en la línea {conf.stanza_lines[name]}): Splunk junta sus "
                         "claves.", lineno, name)
            conf.stanzas.setdefault(name, {})
            conf.stanza_lines.setdefault(name, lineno)
            current = name
            continue
        if "=" not in raw:
            conf.add("error", f"Línea sin «clave = valor»: «{stripped[:80]}».", lineno, current)
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        if not key:
            conf.add("error", "Valor sin clave (la línea empieza por «=»).", lineno, current)
            continue
        parts = [value]
        while True:
            last = parts[-1]
            if last.endswith("\\"):
                if i >= len(lines):
                    conf.add("warning", f"«{key}» termina en «\\» al final del archivo.", lineno, current)
                    parts[-1] = last[:-1]
                    break
                parts[-1] = last[:-1]
                parts.append(lines[i])
                i += 1
                continue
            if last.rstrip().endswith("\\") and last != last.rstrip():
                conf.add("error", f"«{key}»: la barra invertida «\\» va seguida de espacios, así que la línea no continúa y lo "
                         "que sigue se lee como otra clave.", lineno + len(parts) - 1, current)
            break
        value = "\n".join(parts).strip()
        stanza = conf.stanzas.setdefault(current, {})
        if current == "default" and current not in conf.stanza_lines:
            conf.stanza_lines[current] = lineno
        if key in stanza:
            previous = conf.key_lines[(current, key)]
            if stanza[key] == value:
                conf.add("warning", f"«{key}» repetida en [{current}] (también en la línea {previous}) con el mismo valor.",
                         lineno, current)
            else:
                conf.add("error", f"«{key}» repetida en [{current}] con otro valor (línea {previous}): Splunk usa el último.",
                         lineno, current)
        stanza[key] = value
        conf.key_lines[(current, key)] = lineno
    return conf


def merge_layers(*layers):
    """Stanzas de varias capas (p. ej. default y local, en ese orden): cada capa sobrescribe las claves de la anterior."""
    merged = {}
    for stanzas in layers:
        for name, fields in (stanzas or {}).items():
            merged.setdefault(name, {}).update(fields)
    return merged


def effective_stanza(stanzas, name):
    """Claves de una stanza con las de [default] del mismo archivo como valores por defecto (como hace Splunk)."""
    return {**stanzas.get("default", {}), **stanzas.get(name, {})}


def app_info(stanzas):
    """Datos de app.conf (default y local ya mezclados): id, versión, nombre visible, build, autor…"""
    def get(stanza, key):
        return (stanzas.get(stanza) or {}).get(key, "").strip()
    return {
        "id": get("package", "id") or get("id", "name"),
        "version": get("launcher", "version") or get("id", "version"),
        "launcher_version": get("launcher", "version"),
        "id_version": get("id", "version"),
        "label": get("ui", "label"),
        "build": get("install", "build"),
        "author": get("launcher", "author"),
        "description": get("launcher", "description"),
    }


def normalize_value(value):
    """Valor para comparar: sin «\\» de continuación, sin espacios al final de cada línea y con \\n como fin de línea."""
    text = str(value if value is not None else "").replace("\r\n", "\n").replace("\\\n", "\n")
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()
