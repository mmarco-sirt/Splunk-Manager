"""
Dependencias de una búsqueda: las macros que necesita (también las que llaman otras macros) y los lookups que usa, y si
el cliente los tiene, si están habilitados y si son visibles desde la app y el propietario de la búsqueda.

Visibilidad, como en Splunk: un objeto se ve desde la app A y el usuario U si es global, si está compartido en la app A o
si es privado de U en la app A. Si hay varios con el mismo nombre se usa el privado de U, luego el de la app A y por
último uno global de otra app.

Los objetos se pasan como diccionarios: {name, app, owner, sharing, disabled, ...}; las macros con 'definition' y los
lookups con 'kind' ('definition' o 'file') y, las definiciones de tipo archivo, con 'filename'.
"""
from core.spl import lookup_refs, macro_calls

MAX_DEPTH = 25
# ok: se usa y funciona · disabled: existe pero está deshabilitado · not_visible: existe, pero solo en otras apps o
# privado de otro usuario · missing: no existe · file_missing / file_not_visible: definición de lookup cuyo archivo CSV
# no existe o no se ve
PROBLEMS = ("disabled", "not_visible", "missing", "file_missing", "file_not_visible")


def visible(obj, context):
    """True si el objeto se ve desde la app y el propietario de 'context' ({app, owner})."""
    sharing = obj.get("sharing")
    if sharing in ("global", "system") or obj.get("app") == "system":
        return True
    if obj.get("app") != context.get("app"):
        return False
    return sharing != "user" or obj.get("owner") == context.get("owner")


def _rank(obj, context):
    if obj.get("sharing") == "user":
        return 0
    return 1 if obj.get("app") == context.get("app") else 2


def resolve(candidates, context):
    """(objeto que usaría Splunk o None, todos los visibles por orden de preferencia)."""
    seen = sorted((o for o in candidates if visible(o, context)), key=lambda o: (_rank(o, context), o.get("app") or ""))
    return (seen[0] if seen else None), seen


def _where(obj):
    return {"app": obj.get("app"), "owner": obj.get("owner"), "sharing": obj.get("sharing")}


def _group(objects):
    groups = {}
    for obj in objects:
        groups.setdefault(obj.get("name"), []).append(obj)
    return groups


def _base(stanza):
    return stanza.split("(", 1)[0]


def resolve_dependencies(spl, context, macros, lookups=()):
    """
    Árbol de dependencias de 'spl' visto desde 'context' ({app, owner}):
      macros: [{stanza, call, depth, via, status, app, owner, sharing, definition, iseval, args, also, other_arities,
                not_visible_in}]
      lookups: [{name, commands, via, status, kind, type, app, owner, sharing, filename}]
      problems: nº de macros y lookups con algún problema.
    'via' es la macro en cuya definición aparece (None si está en la propia búsqueda) y 'depth' su nivel de anidamiento.
    """
    macros_by_name = _group(macros)
    arities = {}
    for stanza in macros_by_name:
        arities.setdefault(_base(stanza), set()).add(stanza)
    lookups_by_name = _group(lookups)
    files = [lk for lk in lookups if lk.get("kind") == "file"]
    found_macros, found_lookups = [], []
    seen_macros, seen_lookups = set(), set()

    def lookup_status(ref, via):
        if ref["name"] in seen_lookups:
            return
        seen_lookups.add(ref["name"])
        candidates = lookups_by_name.get(ref["name"], [])
        chosen, _ = resolve(candidates, context)
        item = {"name": ref["name"], "commands": ref["commands"], "via": via}
        if chosen is None:
            item["status"] = "not_visible" if candidates else "missing"
            item["not_visible_in"] = [_where(o) for o in candidates]
        else:
            item.update(_where(chosen), kind=chosen.get("kind"), type=chosen.get("type"), filename=chosen.get("filename"))
            item["status"] = "disabled" if chosen.get("disabled") else "ok"
            if item["status"] == "ok" and chosen.get("kind") == "definition" and chosen.get("filename"):
                same_file = [f for f in files if f.get("name") == chosen["filename"]]
                if not same_file:
                    item["status"] = "file_missing"
                elif resolve(same_file, context)[0] is None:
                    item["status"] = "file_not_visible"
        found_lookups.append(item)

    def visit(text, depth, via):
        for ref in lookup_refs(text):
            lookup_status(ref, via)
        for call in macro_calls(text):
            stanza = call["stanza"]
            if stanza in seen_macros:
                continue
            seen_macros.add(stanza)
            candidates = macros_by_name.get(stanza, [])
            chosen, visible_ones = resolve(candidates, context)
            item = {"stanza": stanza, "call": call["call"], "depth": depth, "via": via}
            if chosen is None:
                item["status"] = "not_visible" if candidates else "missing"
                item["not_visible_in"] = [_where(o) for o in candidates]
                item["other_arities"] = sorted(arities.get(call["name"], set()) - {stanza})
                found_macros.append(item)
                continue
            item.update(_where(chosen), definition=chosen.get("definition") or "", iseval=bool(chosen.get("iseval")),
                        args=chosen.get("args") or "", status="disabled" if chosen.get("disabled") else "ok",
                        also=[_where(o) for o in visible_ones[1:]])   # otras definiciones que quedan tapadas
            found_macros.append(item)
            if depth < MAX_DEPTH:
                visit(item["definition"], depth + 1, stanza)

    visit(spl, 0, None)
    problems = sum(1 for item in found_macros + found_lookups if item["status"] in PROBLEMS)
    return {"context": {"app": context.get("app"), "owner": context.get("owner")}, "macros": found_macros,
            "lookups": found_lookups, "problems": problems}
