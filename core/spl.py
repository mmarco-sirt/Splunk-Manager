"""
Análisis estático de SPL: macros que llama una búsqueda y lookups que usa (sin ejecutarla).

- Macros: `nombre` o `nombre(arg1, arg2)`. En macros.conf la de N argumentos se llama «nombre(N)».
  Los comentarios ```…``` se ignoran.
- Lookups: comandos lookup, inputlookup y outputlookup (con sus opciones clave=valor antes del nombre).
"""
import re

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
