"""
Edición de una o varias alertas (o reportes) en varios clientes a la vez: búsqueda por nombre, vista previa de los cambios
en cada cliente y aplicación como tarea en segundo plano. Antes de cambiar nada en un cliente se guarda la configuración
anterior en su backup «antes_de_editar» (el mismo que usa la edición normal), así que se puede deshacer desde Backups.

Cada operación es {"field", "mode": "set", "value"} (mismo valor en todos los clientes) o
{"field", "mode": "replace", "find", "replace"} (sustituye un texto en el valor actual de cada cliente, p. ej. en el SPL,
que puede ser distinto en cada uno).

Al aplicar solo se modifican las saved searches de la vista previa y solo si los campos afectados siguen igual que en la
vista previa (huella 'fingerprint'); si alguien los ha cambiado entretanto, se omiten.

La versión TEST y la de producción de un caso de uso («TEST - CU-X» y «CU-X») son saved searches distintas: se buscan
por su nombre exacto, y la búsqueda indica además en qué clientes existe la otra versión para poder añadirla.
"""
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor

from core.conventions import is_counterpart
from core.knowledge import ALL_NAMESPACES
from options.alerts.backup_alerts import backup_before_edit
from web import services
from web.alert_editor import (
    BOOLEAN_FIELDS, EditError, apply_changes, as_text, check_field, effective_changes, validate_changes,
)
from web.explorer import _read_json, saved_search, saved_search_row
from web.operations import OperationError, diff_fields

FETCH_WORKERS = 6
MAX_NAMES = 50
MODES = ("set", "replace")


def _names(names):
    names = [str(n).strip() for n in dict.fromkeys(names or []) if str(n).strip()]
    if not names:
        raise OperationError("Indique al menos una alerta.")
    if len(names) > MAX_NAMES:
        raise OperationError(f"Como máximo {MAX_NAMES} alertas a la vez.")
    return names


def validate_ops(ops):
    """Normaliza la lista de operaciones; EditError si alguna no es válida."""
    if not isinstance(ops, list) or not ops:
        raise EditError("No hay cambios que aplicar.")
    clean, seen = [], set()
    for op in ops:
        if not isinstance(op, dict):
            raise EditError("Operación no válida.")
        field = check_field(op.get("field"))
        if field in seen:
            raise EditError(f"El campo {field} está repetido.")
        seen.add(field)
        mode = op.get("mode") or "set"
        if mode not in MODES:
            raise EditError(f"Modo no válido: {mode}.")
        if mode == "set":
            clean.append({"field": field, "mode": "set", "value": validate_changes({field: op.get("value")})[field]})
            continue
        if field in BOOLEAN_FIELDS:
            raise EditError(f"«{field}» es verdadero o falso: use «Establecer».")
        find = as_text(op.get("find"))
        if not find:
            raise EditError(f"Indique el texto a buscar en {field}.")
        clean.append({"field": field, "mode": "replace", "find": find, "replace": as_text(op.get("replace"))})
    fixed = {op["field"]: op["value"] for op in clean if op["mode"] == "set"}
    if fixed:
        validate_changes(fixed)   # comprobaciones entre campos (p. ej. programada sin cron)
    return clean


def item_key(client, s):
    access = s.access
    return "|".join([client, access.get("app") or "", access.get("owner") or "", access.get("sharing") or "", s.name])


def _key_label(key):
    """'cliente|app|propietario|compartición|nombre' -> «nombre» (app) o «nombre» (app, privada de propietario)."""
    _client, app, owner, sharing, name = key.split("|", 4)
    where = app or "sin app"
    if sharing == "user":
        where += f", privada de {owner}"
    return f"«{name}» ({where})"


def _fingerprint(s, ops):
    """Huella de los valores actuales de los campos que se van a tocar."""
    values = {op["field"]: as_text(s.content.get(op["field"])) for op in ops}
    return hashlib.sha1(json.dumps(values, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def plan(s, ops):
    """Cambios efectivos para una saved search: ({campo: valor nuevo}, [campos donde no se encontró el texto])."""
    changes, not_found = {}, []
    for op in ops:
        if op["mode"] == "set":
            changes[op["field"]] = op["value"]
            continue
        current = as_text(s.content.get(op["field"]))
        if op["find"] not in current:
            # Si ya está el texto nuevo (p. ej. al comprobar tras aplicar), no es que falte: ya está reemplazado
            if not (op["replace"] and op["replace"] in current):
                not_found.append(op["field"])
            continue
        changes[op["field"]] = current.replace(op["find"], op["replace"])
    return (effective_changes(s, validate_changes(changes)) if changes else {}), not_found


def _entries(service):
    """Nombre y ACL de todas las saved searches (de todas las apps y usuarios), sin su configuración."""
    return _read_json(service, "saved/searches", count=0, f=["disabled"], **ALL_NAMESPACES).get("entry", [])


def find_by_name(service, names, entries=None):
    """
    {nombre: [saved searches con ese nombre exacto]}; puede haber varias del mismo nombre en distintas apps o de distintos
    usuarios. 'entries' evita volver a pedir la lista si ya se tiene.
    """
    found = {name: [] for name in names}
    for entry in _entries(service) if entries is None else entries:
        name = entry.get("name")
        if name in found:
            acl = entry.get("acl", {})
            found[name].append(saved_search(service, name, acl.get("app"), acl.get("owner"), acl.get("sharing")))
    return found


def counterparts(names, existing):
    """
    {nombre: [su otra versión entre los nombres 'existing']}: para «CU-X», «TEST - CU-X» y viceversa. No incluye las que ya
    están en 'names'.
    """
    return {name: sorted({other for other in existing if is_counterpart(name, other)} - set(names)) for name in names}


def _per_client(clients, fn):
    with ThreadPoolExecutor(max_workers=min(FETCH_WORKERS, len(clients))) as pool:
        return list(pool.map(fn, clients))


def lookup(names, clients):
    """
    En qué clientes existe cada alerta y con qué configuración: {names, clients: [{client, error, items}], pairs}.
    'pairs' indica, para cada nombre, su otra versión (TEST o producción) y en qué clientes existe:
    {nombre: [{name, clients}]}.
    """
    names = _names(names)

    def one(client):
        try:
            service = services.get_service(client)
            entries = _entries(service)
            found = find_by_name(service, names, entries)
        except Exception as e:
            return {"client": client, "error": str(e), "items": [], "pairs": {}}
        return {"client": client, "error": None,
                "items": [{"name": n, "rows": [saved_search_row(s) for s in found[n]]} for n in names],
                "pairs": counterparts(names, {e.get("name") for e in entries})}

    results = _per_client(clients, one)
    pairs = {}
    for result in results:
        for name, others in result.pop("pairs").items():
            for other in others:
                pairs.setdefault(name, {}).setdefault(other, []).append(result["client"])
    return {"names": names, "clients": results,
            "pairs": {name: [{"name": other, "clients": found_in} for other, found_in in sorted(found.items())]
                      for name, found in pairs.items()}}


def preview(names, clients, ops):
    """
    Lo que cambiaría en cada cliente. Estado de cada elemento: change, same (ya tiene esos valores), not_found (no
    aparece el texto a reemplazar), missing (no existe en el cliente), invalid (el resultado no es válido) o error.
    """
    names, ops = _names(names), validate_ops(ops)

    def one(client):
        try:
            found = find_by_name(services.get_service(client), names)
        except Exception as e:
            return [{"client": client, "status": "error", "error": str(e)}]
        items = []
        for name in names:
            if not found[name]:
                items.append({"client": client, "name": name, "status": "missing"})
            for s in found[name]:
                item = {"client": client, "name": s.name, "app": s.access.get("app"), "owner": s.access.get("owner"),
                        "sharing": s.access.get("sharing"), "key": item_key(client, s), "row": saved_search_row(s)}
                try:
                    effective, not_found = plan(s, ops)
                except EditError as e:
                    items.append({**item, "status": "invalid", "error": str(e)})
                    continue
                before = {k: as_text(s.content[k]) for k in effective if k in s.content}
                item.update(not_found=not_found, fingerprint=_fingerprint(s, ops), changed=sorted(effective),
                            fields=diff_fields(before, effective),
                            status="change" if effective else "not_found" if not_found else "same")
                items.append(item)
        return items

    items = [item for group in _per_client(clients, one) for item in group]
    counts = {}
    for item in items:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return {"names": names, "ops": ops, "items": items, "counts": counts}


def build_apply(names, ops, targets):
    """
    Devuelve (título, clientes, función) para aplicar en una tarea los cambios de la vista previa.
    targets: [{key, fingerprint}] de los elementos con estado 'change'.
    """
    names, ops = _names(names), validate_ops(ops)
    expected = {}
    for target in targets or []:
        key, fingerprint = str(target.get("key") or ""), str(target.get("fingerprint") or "")
        if key.count("|") >= 4 and fingerprint:
            expected.setdefault(key.split("|", 1)[0], {})[key] = fingerprint
    if not expected:
        raise OperationError("No hay nada que aplicar: genere antes la vista previa.")
    clients = list(expected)

    def run():
        done = skipped = failed = 0
        for client in clients:
            print(f"\n━━━━━━━━━━ {client} ━━━━━━━━━━")
            wanted = expected[client]
            try:
                service = services.get_service(client)
                found = find_by_name(service, names)
            except Exception as e:
                print(f"❌ {e}")
                failed += len(wanted)
                continue
            present = {item_key(client, s): s for name in names for s in found[name]}
            pending = []
            for key, fingerprint in wanted.items():
                s = present.get(key)
                label = _key_label(key)
                if s is None:
                    print(f"⚠️ {label} ya no existe en {client}: se omite.")
                    skipped += 1
                elif _fingerprint(s, ops) != fingerprint:
                    print(f"⚠️ {label} ha cambiado desde la vista previa: no se modifica. Vuelva a generar la vista previa.")
                    skipped += 1
                else:
                    try:
                        effective, _ = plan(s, ops)
                    except EditError as e:
                        print(f"❌ {label}: {e}")
                        failed += 1
                        continue
                    if effective:
                        pending.append((label, s, effective))
                    else:
                        print(f"· {label} ya tenía esos valores.")
            if not pending:
                continue
            paths = backup_before_edit(service, [s for _, s, _ in pending])
            print(f"Configuración anterior guardada en {', '.join(os.path.basename(p) for p in paths)}")
            for label, s, effective in pending:
                try:
                    apply_changes(s, effective)
                    print(f"✅ {label}: {', '.join(sorted(effective))}")
                    done += 1
                except Exception as e:
                    print(f"❌ {label}: {e}")
                    failed += 1
        print(f"\nResumen: {done} modificadas, {skipped} omitidas, {failed} con fallos.")

    what = f"«{names[0]}»" if len(names) == 1 else f"{len(names)} alertas"
    where = clients[0] if len(clients) == 1 else f"{len(clients)} clientes"
    return f"Edición de {what} en {where}", clients, run
