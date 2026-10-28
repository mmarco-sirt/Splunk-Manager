"""
Análisis estático de SPL: macros que llama una búsqueda y lookups que usa (sin ejecutarla).

- Macros: `nombre` o `nombre(arg1, arg2)`. En macros.conf la de N argumentos se llama «nombre(N)».
  Los comentarios ```…``` se ignoran.
- Lookups: comandos lookup, inputlookup y outputlookup (con sus opciones clave=valor antes del nombre).
- Comandos que escriben o envían datos (outputlookup, collect, sendemail, sendalert, delete…): una búsqueda que los use
  no se ejecuta al validar.
"""
import re

# Comandos con efectos (escriben en Splunk, envían avisos o datos fuera, borran o ejecutan scripts). También se bloquea
# cualquier comando cuyo nombre empiece por send/output/notify/post/push/write/ingest (comandos de apps como sendcef).
WRITE_COMMANDS = {
    "outputlookup", "outputcsv", "outputtext", "collect", "summaryindex", "mcollect", "meventcollect", "tscollect",
    "sendemail", "sendalert", "delete", "script", "run", "runshellscript", "dbxoutput", "crawl", "dump",
}
WRITE_PREFIXES = re.compile(r"^(send|output|notify|post|push|write|ingest)")
_COMMAND = re.compile(r"(?:^|[|\[])\s*([A-Za-z_][\w-]*)")
_COMMENT = re.compile(r"```.*?```", re.DOTALL)
_MACRO = re.compile(r"`([^`]*)`")
_MACRO_CALL = re.compile(r"\s*([A-Za-z0-9_][\w.:-]*)\s*(?:\((.*)\))?\s*", re.DOTALL)
_LOOKUP = re.compile(
    r"(?:^|[|\[])\s*(inputlookup|outputlookup|lookup)\b"
    r"((?:\s+[\w.]+\s*=\s*(?:\"[^\"]*\"|[^\s|\]\[]+))*)"
    r"\s+(\"[^\"]+\"|[^\s|\]\[]+)",
    re.IGNORECASE)


def strip_comments(spl):
    """El SPL sin sus comentarios (```…```)."""
    return _COMMENT.sub(" ", spl or "")


def split_args(text):
    """Argumentos de una llamada a macro: separados por comas de primer nivel, respetando comillas y paréntesis."""
    args, current, depth, quote = [], [], 0, None
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            current.append(ch)
            if ch == "\\" and i + 1 < len(text):
                current.append(text[i + 1])
                i += 1
            elif ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            current.append(ch)
        elif ch in "([{":
            depth += 1
            current.append(ch)
        elif ch in ")]}":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            args.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
        i += 1
    last = "".join(current).strip()
    if last or args:
        args.append(last)
    return args


def macro_stanza(name, arity):
    """Nombre de la macro en macros.conf: 'nombre' sin argumentos y 'nombre(N)' con N argumentos."""
    return f"{name}({arity})" if arity else name


def macro_calls(text):
    """
    Macros que llama un texto SPL, en orden y sin repetir: [{name, args, arity, stanza, call}].
    'call' es la llamada tal cual aparece (`nombre(...)`).
    """
    calls, seen = [], set()
    for match in _MACRO.finditer(strip_comments(text)):
        parsed = _MACRO_CALL.fullmatch(match.group(1))
        if not parsed:
            continue
        name, inner = parsed.group(1), parsed.group(2)
        args = split_args(inner) if inner and inner.strip() else []
        stanza = macro_stanza(name, len(args))
        if stanza in seen:
            continue
        seen.add(stanza)
        calls.append({"name": name, "args": args, "arity": len(args), "stanza": stanza, "call": match.group(0)})
    return calls


def _bind(args, names):
    """{nombre del argumento: valor} de una llamada (por posición, o nombre=valor si coincide con un argumento)."""
    values = {}
    for position, arg in enumerate(args):
        key, sep, value = arg.partition("=")
        if sep and key.strip() in names:
            values[key.strip()] = value.strip()
        elif position < len(names):
            values[names[position]] = arg
    return values


def expand_macros(text, macros, max_depth=20):
    """
    SPL con las macros de 'macros' ({stanza: {definition, args, iseval}}) sustituidas por su definición, con sus
    argumentos ($arg$) y también las anidadas. Las que no están en 'macros' y las de tipo eval se dejan tal cual: las
    resuelve Splunk. Devuelve (texto sin comentarios, [macros sustituidas]).
    """
    used = []

    def expand(fragment, depth):
        def replace(match):
            parsed = _MACRO_CALL.fullmatch(match.group(1))
            if not parsed or depth >= max_depth:
                return match.group(0)
            name, inner = parsed.group(1), parsed.group(2)
            args = split_args(inner) if inner and inner.strip() else []
            stanza = macro_stanza(name, len(args))
            macro = macros.get(stanza)
            if not macro or macro.get("iseval"):
                return match.group(0)
            definition = macro.get("definition") or ""
            names = [a.strip() for a in str(macro.get("args") or "").split(",") if a.strip()]
            for arg, value in _bind(args, names).items():
                definition = definition.replace(f"${arg}$", value)
            if stanza not in used:
                used.append(stanza)
            return expand(definition, depth + 1)
        return _MACRO.sub(replace, fragment)

    return expand(strip_comments(text), 0), used


def commands(text):
    """Comandos que usa un texto SPL (en minúsculas, en orden y sin repetir), sin contar los comentarios."""
    return list(dict.fromkeys(m.group(1).lower() for m in _COMMAND.finditer(strip_comments(text))))


def blocked_commands(names):
    """De una lista de comandos, los que escriben o envían datos (no se ejecutan al validar)."""
    return sorted({c.lower() for c in names if c.lower() in WRITE_COMMANDS or WRITE_PREFIXES.match(c.lower())})


def lookup_refs(text):
    """
    Lookups que usa un texto SPL, en orden y sin repetir: [{name, commands}] (commands: lookup, inputlookup,
    outputlookup). Se omiten los nombres que dependen de una macro o de un token ($…$), que no se pueden resolver aquí.
    """
    refs = {}
    for match in _LOOKUP.finditer(strip_comments(text)):
        command, name = match.group(1).lower(), match.group(3).strip('"')
        if not name or "`" in name or "$" in name:
            continue
        ref = refs.setdefault(name, {"name": name, "commands": []})
        if command not in ref["commands"]:
            ref["commands"].append(command)
    return list(refs.values())
