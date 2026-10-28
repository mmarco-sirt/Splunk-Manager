"""Objetos falsos con la forma de los de splunklib, para probar sin conexión a Splunk."""


class FakeEntity:
    def __init__(self, name, app="search", owner="nobody", sharing="global", **content):
        self.name = name
        self.access = {"app": app, "owner": owner, "sharing": sharing}
        self.content = dict(content)
        self.service = None
        self.updates = []
        self.sharing_changes = []

    def update(self, **kwargs):
        # En splunklib, 'sharing' en update() no se envía: cambia el namespace de la petición (nobody/system)
        assert "sharing" not in kwargs, "update() con 'sharing' escribiría en otro namespace"
        self.updates.append(kwargs)
        self.content.update(kwargs)
        return self

    def acl_update(self, sharing=None, owner=None):
        self.sharing_changes.append(sharing)
        self.access["sharing"] = sharing

    def __repr__(self):
        return f"<{self.name} {self.access}>"


class FakeSavedSearch(FakeEntity):
    def __init__(self, name, app="search", owner="nobody", sharing="global", **content):
        super().__init__(name, app, owner, sharing, **{"search": "index=main", **content})


class FakeMacro(FakeEntity):
    def __init__(self, name, app="search", owner="nobody", sharing="global", **content):
        super().__init__(name, app, owner, sharing, **{"definition": "index=main", **content})


class FakeCollection:
    """Colección con la visibilidad de Splunk: los privados solo se ven en el namespace de su propietario y su app."""

    def __init__(self, service, items, factory):
        self.service = service
        self.items = list(items)
        self.factory = factory
        self.created = []

    def list(self, **namespace):
        self.last_namespace = namespace
        return list(self.items)

    def create(self, name, search=None, **params):
        app = params.pop("app", None) or "search"
        owner = params.pop("owner", None) or self.service.username
        sharing = params.pop("sharing", None) or "user"
        content = dict(params) if search is None else {"search": search, **params}
        entity = self.factory(name, app=app, owner=owner, sharing=sharing, **content)
        entity.service = self.service
        self.items.append(entity)
        self.created.append((name, {"app": app, "owner": owner, "sharing": sharing, **params}))
        return entity

    def get(self, name, owner=None, app=None):
        def visible(s):
            if s.access["sharing"] == "user":
                return s.access["owner"] == owner and s.access["app"] == app
            return s.access["sharing"] == "global" or s.access["app"] == app
        return [s for s in self.items if s.name == name and visible(s)]

    def _load_list(self, response):
        return response


class FakeService:
    def __init__(self, items=(), macros=(), username="admin", host="cliente.splunkcloud.com"):
        self.username = username
        self.host = host
        self.saved_searches = FakeCollection(self, items, FakeSavedSearch)
        self.macros = FakeCollection(self, macros, FakeMacro)
        for entity in self.saved_searches.items + self.macros.items:
            entity.service = self
