"""
Lookups del cliente, de todas las apps y usuarios: definiciones (transforms.conf: archivo CSV, KV store, externos y
geoespaciales), archivos de lookup y lookups automáticos (props.conf). También dónde se usan y una vista previa de su
contenido (| inputlookup, solo lectura).
"""
import re
from concurrent.futures import ThreadPoolExecutor

from splunklib.binding import HTTPError

from core.dependencies import visible
from core.knowledge import ALL_NAMESPACES
from core.spl import lookup_refs
from web.explorer import _flag, _read_json, oneshot_results

DEFINITIONS_PATH = "data/transforms/lookups"
FILES_PATH = "data/lookup-table-files"
AUTOMATIC_PATH = "data/props/lookups"
COLLECTIONS_PATH = "storage/collections/config"
# Opciones de una definición que se muestran en el detalle (si tienen valor)
DEFINITION_OPTIONS = ("match_type", "max_matches", "min_matches", "default_match", "case_sensitive_match",
                      "time_field", "time_format", "max_offset_secs", "min_offset_secs", "filter", "external_type")
PREVIEW_ROWS = 100
# Caracteres que no puede tener un nombre de lookup para la vista previa (evitan inyectar SPL o macros)
UNSAFE_NAME = re.compile(r"[`$|\[\]\"\\\r\n]")


def _entries(service, path, **query):
    return _read_json(service, path, count=0, **ALL_NAMESPACES, **query).get("entry", [])


def _optional_entries(service, path):
    """Entradas de un endpoint que puede no estar disponible (p. ej. sin permisos): None si no se puede leer."""
    try:
        return _entries(service, path)
    except HTTPError:
        return None


def _acl(entry):
    acl = entry.get("acl") or {}
    return {"app": acl.get("app"), "owner": acl.get("owner"), "sharing": acl.get("sharing")}


def definition_type(content):
    """file, kvstore, external o geo: el 'type' que da Splunk o, si no lo da, el que se deduce de la configuración."""
    kind = str(content.get("type") or "").strip().lower()
    if kind:
        return kind
    if content.get("collection"):
        return "kvstore"
    if content.get("external_cmd"):
        return "external"
    return "file"


def _prefixed(content, prefix):
    return {k[len(prefix):]: v for k, v in content.items() if k.startswith(prefix)}


def build_rows(definitions, files, automatic, collections=None):
    """
    Filas del listado a partir de las entradas JSON de Splunk (sin hacer peticiones). Cada fila tiene 'kind'
    (definition, file o automatic), su app, propietario y compartición, y en 'problems' lo que está mal:
    file_missing / file_not_visible (la definición apunta a un CSV que no existe o no se ve desde su app),
    collection_missing (colección del KV store que no existe) o definition_missing (lookup automático sin su definición).
    'collections' es None si no se pudieron leer (no se comprueba).
    """
    file_rows = []
    for entry in files:
        name = entry.get("name") or ""
        file_rows.append({
            "kind": "file", "name": name, "type": "geo" if name.lower().endswith((".kmz", ".kml")) else "csv",
            "source": (entry.get("content") or {}).get("eai:data") or "", **_acl(entry), "disabled": False,
            "updated": entry.get("updated") or "", "definitions": [], "problems": [],
        })
    files_by_name = {}
    for row in file_rows:
        files_by_name.setdefault(row["name"], []).append(row)

    rows = []
    for entry in definitions:
        c = entry.get("content") or {}
        kind = definition_type(c)
        row = {
            "kind": "definition", "name": entry.get("name") or "", "type": kind,
            "source": c.get("filename") or c.get("collection") or c.get("external_cmd") or "",
            **_acl(entry), "disabled": _flag(c.get("disabled")), "updated": entry.get("updated") or "",
            "fields": c.get("fields_list") or "", "filename": c.get("filename") or "", "collection": c.get("collection") or "",
            "options": {k: c.get(k) for k in DEFINITION_OPTIONS if c.get(k) not in (None, "")}, "problems": [],
        }
        if row["filename"]:
            same = files_by_name.get(row["filename"], [])
            for file_row in same:
                file_row["definitions"].append(row["name"])
            if not same:
                row["problems"].append("file_missing")
            elif not any(visible(f, row) for f in same):
                row["problems"].append("file_not_visible")
        if kind == "kvstore" and row["collection"] and collections is not None:
            if not any(col.get("name") == row["collection"] and visible(_acl(col), row) for col in collections):
                row["problems"].append("collection_missing")
        rows.append(row)

    definition_names = {row["name"] for row in rows}
    for entry in automatic:
        c = entry.get("content") or {}
        transform = c.get("transform") or ""
        rows.append({
            "kind": "automatic", "name": c.get("attribute") or entry.get("name") or "", "type": "automatic",
            "source": transform, "stanza": c.get("stanza") or "", **_acl(entry), "disabled": _flag(c.get("disabled")),
            "updated": entry.get("updated") or "", "inputs": _prefixed(c, "lookup.field.input."),
            "outputs": _prefixed(c, "lookup.field.output."), "overwrite": _flag(c.get("overwrite")),
            "problems": [] if not transform or transform in definition_names else ["definition_missing"],
        })
    return rows + file_rows


def list_lookups(service):
    """Definiciones, archivos y lookups automáticos de todas las apps y usuarios."""
    with ThreadPoolExecutor(max_workers=4) as pool:
        definitions = pool.submit(_entries, service, DEFINITIONS_PATH)
        files = pool.submit(_entries, service, FILES_PATH)
        automatic = pool.submit(_optional_entries, service, AUTOMATIC_PATH)
        collections = pool.submit(_optional_entries, service, COLLECTIONS_PATH)
        return build_rows(definitions.result(), files.result(), automatic.result() or [], collections.result())


def lookup_records(service):
    """Definiciones y archivos de lookup (lo que se necesita para resolver las dependencias de una búsqueda)."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        definitions = pool.submit(_entries, service, DEFINITIONS_PATH)
        files = pool.submit(_entries, service, FILES_PATH)
        return build_rows(definitions.result(), files.result(), [])


# --- Dónde se usa ---------------------------------------------------------------------

def _is_alert(entry):
    c = entry.get("content") or {}
    app = (entry.get("acl") or {}).get("app")
    return (c.get("alert_type") or "none") != "none" or app == "SplunkEnterpriseSecuritySuite" or "ESCU" in (c.get("tags") or "")


def usage_in(name, kind, searches, macros, definitions, automatic):
    """
    Dónde se usa un lookup (sin hacer peticiones): búsquedas y macros que lo llaman con lookup, inputlookup u outputlookup
    y lookups automáticos que lo aplican. Un archivo CSV también se usa a través de las definiciones que apuntan a él
    ('via' indica por cuál).
    """
    names = {name}
    if kind == "file":
        names |= {e.get("name") for e in definitions if (e.get("content") or {}).get("filename") == name}

    def refs(text):
        return [r for r in lookup_refs(text) if r["name"] in names]

    result = {"names": sorted(names), "searches": [], "macros": [], "automatic": []}
    for entry in searches:
        found = refs((entry.get("content") or {}).get("search"))
        if found:
            result["searches"].append({
                "name": entry.get("name"), **_acl(entry), "is_alert": _is_alert(entry),
                "disabled": _flag((entry.get("content") or {}).get("disabled")),
                "commands": sorted({cmd for r in found for cmd in r["commands"]}),
                "via": sorted({r["name"] for r in found if r["name"] != name}),
            })
    for entry in macros:
        found = refs((entry.get("content") or {}).get("definition"))
        if found:
            result["macros"].append({"name": entry.get("name"), **_acl(entry),
                                     "via": sorted({r["name"] for r in found if r["name"] != name})})
    for entry in automatic:
        c = entry.get("content") or {}
        if c.get("transform") in names:
            result["automatic"].append({"name": c.get("attribute") or entry.get("name"), "stanza": c.get("stanza") or "",
                                        **_acl(entry), "via": [] if c.get("transform") == name else [c.get("transform")]})
    return result


def lookup_usage(service, name, kind):
    """Búsquedas, macros y lookups automáticos del cliente que usan un lookup."""
    if not name:
        raise ValueError("Indique el lookup.")
    with ThreadPoolExecutor(max_workers=4) as pool:
        searches = pool.submit(_entries, service, "saved/searches", f=["search", "alert_type", "disabled", "tags"])
        macros = pool.submit(_entries, service, "configs/conf-macros", f=["definition"])
        definitions = pool.submit(_entries, service, DEFINITIONS_PATH, f=["filename"])
        automatic = pool.submit(_optional_entries, service, AUTOMATIC_PATH)
        return usage_in(name, kind, searches.result(), macros.result(), definitions.result(), automatic.result() or [])


# --- Vista previa ------------------------------------------------------------------------

def preview_spl(name, rows=PREVIEW_ROWS):
    """(SPL de las primeras filas, SPL del total) de un lookup; ValueError si el nombre no es seguro."""
    name = str(name or "").strip()
    if not name or len(name) > 512 or UNSAFE_NAME.search(name):
        raise ValueError("Nombre de lookup no válido.")
    target = name if re.fullmatch(r"[\w.:@+-]+", name) else f'"{name}"'
    return f"| inputlookup {target} | head {int(rows)}", f"| inputlookup {target} | stats count"


def lookup_preview(service, name, app, owner=None, sharing=None):
    """
    Primeras filas y nº total de filas de un lookup (| inputlookup, que solo lee) en el contexto de su app, que es donde
    se ve. Devuelve {fields, rows, total, truncated, count_error}.
    """
    rows_spl, count_spl = preview_spl(name)
    namespace = {"app": app or "search", "owner": owner if sharing == "user" and owner else "nobody"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        sample_job = pool.submit(oneshot_results, service, rows_spl, **namespace)
        count_job = pool.submit(oneshot_results, service, count_spl, **namespace)
        try:
            sample = sample_job.result()
        except RuntimeError as e:   # mensajes de error de Splunk (p. ej. el lookup no existe en esa app)
            raise ValueError(f"Splunk no pudo leer el lookup: {e}")
        total, count_error = None, None
        try:
            counted = count_job.result()
            total = int(float(counted[0].get("count"))) if counted else 0
        except Exception as e:
            count_error = str(e)
    fields = []
    for row in sample:
        for key in row:
            if key not in fields:
                fields.append(key)
    return {"fields": fields, "rows": sample, "total": total, "truncated": total is None or total > len(sample),
            "count_error": count_error}
