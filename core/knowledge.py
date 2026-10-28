"""
Objetos de conocimiento de Splunk (saved searches y macros) de todas las apps y usuarios.

Con /services/ (el contexto del usuario conectado) Splunk no devuelve los objetos privados de otros usuarios ni los
compartidos solo dentro de una app que no es la del usuario. Por eso los listados, backups y comparaciones leen
/servicesNS/-/-/. Como puede haber varios con el mismo nombre (p. ej. el compartido y la copia privada de un usuario),
cada uno se identifica por su nombre, app, propietario y compartición.
"""
from splunklib.binding import HTTPError

ALL_NAMESPACES = {"owner": "-", "app": "-"}
SHARED = ("app", "global", "system")


def all_saved_searches(service):
    """Todas las saved searches que ve la cuenta, de cualquier app y usuario (cada una sabe dónde está para modificarla)."""
    return service.saved_searches.list(**ALL_NAMESPACES)


def all_macros(service):
    """Todas las macros que ve la cuenta, de cualquier app y usuario."""
    return service.macros.list(**ALL_NAMESPACES)


def acl_of(entity):
    """App, propietario y compartición de una saved search o una macro."""
    access = entity.access
    return {"app": access.get("app"), "owner": access.get("owner"), "sharing": access.get("sharing")}


def _matches(entity, app, owner, sharing=None):
    access = entity.access
    return access.get("app") == app and access.get("owner") == owner and (not sharing or access.get("sharing") == sharing)


def find_object(collection, name, app, owner, sharing=None):
    """
    Objeto de una colección (saved searches, macros) por nombre en su app y propietario (y compartición, si se indica).
    Los compartidos se leen como 'nobody', que no ve las copias privadas con el mismo nombre. KeyError si no existe.
    """
    try:
        response = collection.get(name, owner="nobody" if sharing in SHARED else owner, app=app)
    except HTTPError as e:
        if e.status == 404:
            raise KeyError(name)
        raise
    found = [o for o in collection._load_list(response) if o.name == name and _matches(o, app, owner, sharing)]
    if not found:
        raise KeyError(name)
    # Sin compartición, si el mismo propietario tiene uno privado y otro compartido con ese nombre, el compartido
    return sorted(found, key=lambda o: o.access.get("sharing") == "user")[0]


def find_saved_search(service, name, app, owner, sharing=None):
    return find_object(service.saved_searches, name, app, owner, sharing)


def find_macro(service, name, app, owner, sharing=None):
    return find_object(service.macros, name, app, owner, sharing)


def reload_saved_search(saved_search):
    """La misma saved search leída de nuevo de Splunk (sin confundirla con otra del mismo nombre)."""
    acl = acl_of(saved_search)
    return find_saved_search(saved_search.service, saved_search.name, acl["app"], acl["owner"], acl["sharing"])


def reload_macro(macro):
    """La misma macro leída de nuevo de Splunk (sin confundirla con otra del mismo nombre)."""
    acl = acl_of(macro)
    return find_macro(macro.service, macro.name, acl["app"], acl["owner"], acl["sharing"])


def apply_sharing(entity, sharing):
    """Aplica el nivel de compartición (user/app/global) manteniendo el propietario actual. None: no lo cambia."""
    if not sharing or entity.access.get("sharing") == sharing:
        return
    owner = entity.access.get("owner") or "nobody"
    try:
        entity.acl_update(sharing=sharing, owner=owner)
    except Exception as e:
        print(f" - [ADVERTENCIA] No se pudo aplicar sharing='{sharing}' a '{entity.name}': {e}")


def pick_existing(candidates, acl=None, user=None):
    """
    De los objetos con el mismo nombre (de cualquier app o usuario), el que debe actualizar un restore, o None para
    crearlo. Con la app y el propietario del backup ('acl') se busca ese mismo; si no está, uno compartido si el del backup
    lo era: nunca se pisa la copia privada de otro usuario. Sin 'acl' (backups antiguos) vale uno compartido o uno privado
    del usuario conectado ('user'), como hacía el restore antes de leer todas las apps.
    """
    acl = acl or {}
    if acl.get("app") and acl.get("owner"):
        for s in candidates:
            if _matches(s, acl["app"], acl["owner"], acl.get("sharing")):
                return s
    if acl.get("sharing") == "user":
        own = [s for s in candidates if s.access.get("sharing") == "user" and s.access.get("owner") == acl.get("owner")]
        return own[0] if own else None
    shared = [s for s in candidates if s.access.get("sharing") != "user"]
    if not shared and not acl.get("sharing") and user:
        shared = [s for s in candidates if s.access.get("owner") == user]
    shared.sort(key=lambda s: s.access.get("app") != acl.get("app"))
    return shared[0] if shared else None


def same_entry(old, new):
    """Mismo elemento de backup: mismo nombre y, si los dos guardan dónde estaba ('acl'), la misma app y propietario."""
    if old.get("name") != new.get("name"):
        return False
    old_acl, new_acl = old.get("acl"), new.get("acl")
    return not old_acl or not new_acl or old_acl == new_acl
