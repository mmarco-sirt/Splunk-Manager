"""
Edición de alertas y reportes (saved searches): configuración completa, cambio de valores y cambios en bloque.

Antes de modificar nada se guarda la configuración anterior de cada saved search en
backups/<alertas|reportes>/<prefijo>antes_de_editar_<host>_backup.json (con su historial de versiones),
así que cualquier cambio se puede deshacer restaurando ese archivo (o solo algunos de sus elementos).
"""
import json
import os
import re
import threading

from core.backup_files import write_backup
from core.paths import backup_dir, host_tag
from options.alerts.backup_alerts import _is_alert, _is_es_use_case
from options.alerts.restore_alerts import READ_ONLY_FIELDS
from web.explorer import saved_search, saved_search_row

EDIT_LABEL = "antes_de_editar"
MAX_VALUE_LENGTH = 200_000
KEY_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.:\-]*$")
BOOLEAN_FIELDS = {"disabled", "is_scheduled", "is_visible", "alert.digest_mode", "alert.suppress", "alert.track",
                  "realtime_schedule", "run_on_startup"}

_backup_lock = threading.Lock()


class EditError(ValueError):
    """Cambio no válido (se muestra tal cual en la web)."""


def _hidden(key):
    """Campos internos o de solo lectura que no se pueden editar."""
    return key.startswith("eai:") or key in READ_ONLY_FIELDS


def as_text(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, list):
        return ",".join(str(v) for v in value)
    return str(value)


def _bool_text(key, value):
    text = as_text(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return "1"
    if text in ("0", "false", "no", "off", ""):
        return "0"
    raise EditError(f"'{key}' debe ser verdadero o falso.")


def validate_changes(changes):
    """Normaliza {campo: valor} a texto y rechaza campos de solo lectura o valores no válidos."""
    if not isinstance(changes, dict) or not changes:
        raise EditError("No hay cambios que aplicar.")
    clean = {}
    for key, value in changes.items():
        key = str(key).strip()
        if not KEY_PATTERN.match(key) or len(key) > 200:
            raise EditError(f"Nombre de campo no válido: '{key}'.")
        if _hidden(key) or key == "name":
            raise EditError(f"El campo '{key}' no se puede modificar.")
        if isinstance(value, dict):
            raise EditError(f"Valor no válido para '{key}'.")
        text = _bool_text(key, value) if key in BOOLEAN_FIELDS else as_text(value)
        if len(text) > MAX_VALUE_LENGTH:
            raise EditError(f"El valor de '{key}' es demasiado largo.")
        clean[key] = text
    if "search" in clean and not clean["search"].strip():
        raise EditError("La búsqueda SPL no puede quedar vacía.")
    if clean.get("is_scheduled") == "1" and "cron_schedule" in clean and not clean["cron_schedule"].strip():
        raise EditError("Indique la programación (cron) o desactive «Programada».")
    return clean


def _action_list(value):
    return [a.strip() for a in as_text(value).split(",") if a.strip()]


def _effective_changes(s, changes):
    """Cambios que de verdad modifican algo; si cambia 'actions', activa/desactiva también action.<nombre>."""
    current = s.content
    effective = {k: v for k, v in changes.items()
                 if (_bool_text(k, current.get(k)) if k in BOOLEAN_FIELDS else as_text(current.get(k))) != v}
    if "actions" in effective:
        before, after = set(_action_list(current.get("actions"))), set(_action_list(effective["actions"]))
        for name in after - before:
            key = f"action.{name}"
            if key not in changes and as_text(current.get(key)) != "1":
                effective[key] = "1"
        for name in before - after:
            key = f"action.{name}"
            if key not in changes and as_text(current.get(key)) not in ("", "0"):
                effective[key] = "0"
    return effective


def _backup_entry(s):
    entry = {"name": s.name, "content": s.content}
    if _is_es_use_case(s):
        entry["app"] = s.access.get("app")
    return entry


def backup_before_edit(service, saved_searches):
    """
    Guarda la configuración actual de esas saved searches en <prefijo>antes_de_editar_<host>_backup.json,
    sustituyendo las entradas anteriores con el mismo nombre. Devuelve las rutas de los archivos escritos.
    """
    by_folder = {}
    for s in saved_searches:
        folder = "alertas" if (_is_alert(s) or _is_es_use_case(s)) else "reportes"
        by_folder.setdefault(folder, []).append(_backup_entry(s))

    paths = []
    with _backup_lock:
        for folder, entries in by_folder.items():
            path = os.path.join(backup_dir(folder), f"{folder}_{EDIT_LABEL}_{host_tag(service)}_backup.json")
            existing = []
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        existing = json.load(f)
                except (OSError, ValueError):
                    existing = []
            names = {e["name"] for e in entries}
            data = [e for e in existing if isinstance(e, dict) and e.get("name") not in names] + entries
            write_backup(path, data)
            paths.append(path)
    return paths


def _apply(s, effective):
    """Aplica los cambios en Splunk (el estado habilitado/deshabilitado tiene su propia llamada)."""
    values = dict(effective)
    disabled = values.pop("disabled", None)
    if values:
        s.update(**values)
    if disabled is not None:
        if disabled == "1":
            s.disable()
        else:
            s.enable()
    s.refresh()


def saved_search_detail(service, name, app, owner):
    """Configuración completa (editable) de una saved search."""
    s = saved_search(service, name, app, owner)
    content = {k: as_text(v) for k, v in s.content.items() if not _hidden(k)}
    return {
        "row": saved_search_row(s),
        "content": dict(sorted(content.items())),
        "readonly": sorted(k for k in s.content if k in READ_ONLY_FIELDS),
    }


def update_saved_search(service, name, app, owner, changes):
    """Modifica los campos indicados de una saved search guardando antes su configuración anterior."""
    changes = validate_changes(changes)
    s = saved_search(service, name, app, owner)
    effective = _effective_changes(s, changes)
    if not effective:
        return {"changed": [], "row": saved_search_row(s), "message": "No había nada que cambiar."}
    backups = backup_before_edit(service, [s])
    _apply(s, effective)
    return {
        "changed": sorted(effective),
        "row": saved_search_row(s),
        "backup": [os.path.basename(p) for p in backups],
        "message": f"'{name}' actualizada ({len(effective)} {'campo' if len(effective) == 1 else 'campos'}).",
    }


def bulk_update(service, items, changes):
    """Aplica los mismos cambios a varias saved searches ([{name, app, owner}]). Devuelve el resultado de cada una."""
    changes = validate_changes(changes)
    if not items:
        raise EditError("No se ha seleccionado ninguna saved search.")
    results, pending = [], []
    for item in items:
        try:
            s = saved_search(service, item.get("name"), item.get("app"), item.get("owner"))
        except KeyError:
            results.append({"name": item.get("name"), "ok": False, "error": "No existe."})
            continue
        effective = _effective_changes(s, changes)
        if effective:
            pending.append((s, effective))
        else:
            results.append({"name": s.name, "ok": True, "changed": [], "row": saved_search_row(s)})

    backups = backup_before_edit(service, [s for s, _ in pending]) if pending else []
    for s, effective in pending:
        try:
            _apply(s, effective)
            results.append({"name": s.name, "ok": True, "changed": sorted(effective), "row": saved_search_row(s)})
        except Exception as e:
            results.append({"name": s.name, "ok": False, "error": str(e)})
    return {
        "results": results,
        "updated": sum(1 for r in results if r["ok"] and r.get("changed")),
        "unchanged": sum(1 for r in results if r["ok"] and not r.get("changed")),
        "failed": sum(1 for r in results if not r["ok"]),
        "backup": [os.path.basename(p) for p in backups],
    }
