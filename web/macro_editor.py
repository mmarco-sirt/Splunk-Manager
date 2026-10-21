"""
Edición de macros: definición, argumentos, descripción, eval y validación, y habilitar/deshabilitar una o varias a la vez.

Antes de modificar nada se guarda la configuración anterior de cada macro en
backups/macros/macros_antes_de_editar_<host>_backup.json (con su historial de versiones), así que cualquier cambio se
puede deshacer restaurando ese archivo (o solo algunas de sus macros).
"""
import os
import re

from splunklib.binding import namespace

from options.macros.backup_macros import backup_before_edit
from web.alert_editor import EditError, as_text
from web.explorer import macro_row

EDITABLE = ("definition", "args", "description", "iseval", "validation", "errormsg", "disabled")
BOOLEAN_FIELDS = {"iseval", "disabled"}
MAX_VALUE_LENGTH = 200_000
ARG_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
ARGS_IN_NAME = re.compile(r"\((\d+)\)$")


def get_macro(service, name, app=None, owner=None):
    """Macro por nombre (en su app/propietario si se indican). KeyError si no existe."""
    if app and owner:
        return service.macros[name, namespace(owner=owner, app=app)]
    return service.macros[name]


def expected_args(name):
    """Número de argumentos que indica el nombre: 'mi_macro(2)' -> 2, 'mi_macro' -> 0."""
    match = ARGS_IN_NAME.search(name or "")
    return int(match.group(1)) if match else 0


def _bool_text(key, value):
    text = as_text(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return "1"
    if text in ("0", "false", "no", "off", ""):
        return "0"
    raise EditError(f"'{key}' debe ser verdadero o falso.")


def _current(m, key):
    value = m.content.get(key)
    return _bool_text(key, value) if key in BOOLEAN_FIELDS else as_text(value)


def validate_changes(name, changes):
    """Normaliza {campo: valor} a texto y rechaza campos no editables o valores no válidos para esa macro."""
    if not isinstance(changes, dict) or not changes:
        raise EditError("No hay cambios que aplicar.")
    clean = {}
    for key, value in changes.items():
        if key not in EDITABLE:
            raise EditError(f"El campo '{key}' no se puede modificar.")
        if isinstance(value, (dict, list)):
            raise EditError(f"Valor no válido para '{key}'.")
        text = _bool_text(key, value) if key in BOOLEAN_FIELDS else as_text(value)
        if len(text) > MAX_VALUE_LENGTH:
            raise EditError(f"El valor de '{key}' es demasiado largo.")
        clean[key] = text
    if "definition" in clean and not clean["definition"].strip():
        raise EditError("La definición no puede quedar vacía.")
    if "args" in clean:
        args = [a.strip() for a in clean["args"].split(",") if a.strip()]
        bad = next((a for a in args if not ARG_PATTERN.match(a)), None)
        if bad:
            raise EditError(f"Argumento no válido: «{bad}» (solo letras, números, _ y -).")
        if len(set(args)) != len(args):
            raise EditError("Hay argumentos repetidos.")
        expected = expected_args(name)
        if len(args) != expected:
            raise EditError(f"«{name}» debe tener exactamente {expected} {'argumento' if expected == 1 else 'argumentos'}: "
                            f"el número forma parte del nombre de la macro." if expected else
                            f"«{name}» no lleva argumentos (para tenerlos, su nombre tendría que terminar en (N)).")
        clean["args"] = ",".join(args)
    return clean


def effective_changes(m, changes):
    """Cambios que de verdad modifican algo."""
    return {k: v for k, v in changes.items() if _current(m, k) != v}


def apply_changes(m, effective):
    values = dict(effective)
    definition = values.pop("definition", None)
    m.update(definition=definition, **values)   # Splunk exige la definición: sin ella se envía la actual
    m.refresh()


def macro_detail(service, name, app, owner):
    """Configuración editable de una macro."""
    m = get_macro(service, name, app, owner)
    return {"row": macro_row(m), "content": {k: _current(m, k) for k in EDITABLE}, "expected_args": expected_args(m.name)}


def update_macro(service, name, app, owner, changes):
    """Modifica los campos indicados de una macro guardando antes su configuración anterior."""
    m = get_macro(service, name, app, owner)
    effective = effective_changes(m, validate_changes(m.name, changes))
    if not effective:
        return {"changed": [], "row": macro_row(m), "message": "No había nada que cambiar."}
    path = backup_before_edit(service, [m])
    apply_changes(m, effective)
    return {
        "changed": sorted(effective),
        "row": macro_row(m),
        "backup": [os.path.basename(path)],
        "message": f"'{m.name}' actualizada ({len(effective)} {'campo' if len(effective) == 1 else 'campos'}).",
    }


def bulk_update(service, items, changes):
    """Aplica los mismos cambios (p. ej. habilitar o deshabilitar) a varias macros ([{name, app, owner}])."""
    if not items:
        raise EditError("No se ha seleccionado ninguna macro.")
    results, pending = [], []
    for item in items:
        try:
            m = get_macro(service, item.get("name"), item.get("app"), item.get("owner"))
        except KeyError:
            results.append({"name": item.get("name"), "ok": False, "error": "No existe."})
            continue
        try:
            effective = effective_changes(m, validate_changes(m.name, changes))
        except EditError as e:
            results.append({"name": m.name, "ok": False, "error": str(e)})
            continue
        if effective:
            pending.append((m, effective))
        else:
            results.append({"name": m.name, "ok": True, "changed": [], "row": macro_row(m)})

    path = backup_before_edit(service, [m for m, _ in pending]) if pending else None
    for m, effective in pending:
        try:
            apply_changes(m, effective)
            results.append({"name": m.name, "ok": True, "changed": sorted(effective), "row": macro_row(m)})
        except Exception as e:
            results.append({"name": m.name, "ok": False, "error": str(e)})
    return {
        "results": results,
        "updated": sum(1 for r in results if r["ok"] and r.get("changed")),
        "unchanged": sum(1 for r in results if r["ok"] and not r.get("changed")),
        "failed": sum(1 for r in results if not r["ok"]),
        "backup": [os.path.basename(path)] if path else [],
    }
