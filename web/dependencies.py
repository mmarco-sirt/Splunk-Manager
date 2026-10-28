"""Dependencias de una saved search en un cliente: lee sus macros y lookups y las resuelve con core.dependencies."""
from concurrent.futures import ThreadPoolExecutor

from core.dependencies import resolve_dependencies
from core.knowledge import ALL_NAMESPACES, acl_of, find_saved_search
from web.explorer import _flag, _read_json
from web.lookups import lookup_records


def macro_records(service):
    """Macros de todas las apps y usuarios, solo con lo necesario para resolver dependencias."""
    data = _read_json(service, "configs/conf-macros", count=0, f=["definition", "args", "iseval", "disabled"],
                      **ALL_NAMESPACES)
    records = []
    for entry in data.get("entry", []):
        c = entry.get("content") or {}
        acl = entry.get("acl") or {}
        records.append({"name": entry.get("name"), "app": acl.get("app"), "owner": acl.get("owner"),
                        "sharing": acl.get("sharing"), "definition": c.get("definition") or "", "args": c.get("args") or "",
                        "iseval": _flag(c.get("iseval")), "disabled": _flag(c.get("disabled"))})
    return records


def saved_search_dependencies(service, name, app, owner, sharing=None):
    """Macros (también las anidadas) y lookups que necesita una saved search, vistos desde su app y su propietario."""
    s = find_saved_search(service, name, app, owner, sharing)
    with ThreadPoolExecutor(max_workers=2) as pool:
        macros = pool.submit(macro_records, service)
        lookups = pool.submit(lookup_records, service)
        return resolve_dependencies(s.content.get("search") or "", acl_of(s), macros.result(), lookups.result())
