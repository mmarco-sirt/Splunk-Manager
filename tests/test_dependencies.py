import unittest

from core.dependencies import resolve_dependencies, visible

SIRT = {"app": "sirt", "owner": "nobody"}


def macro(name, definition="index=main", app="sirt", owner="nobody", sharing="app", disabled=False):
    return {"name": name, "definition": definition, "app": app, "owner": owner, "sharing": sharing, "disabled": disabled}


def definition(name, filename="", app="sirt", sharing="app", owner="nobody", disabled=False):
    return {"kind": "definition", "name": name, "type": "file", "filename": filename, "app": app, "owner": owner,
            "sharing": sharing, "disabled": disabled}


def csv(name, app="sirt", sharing="app", owner="nobody"):
    return {"kind": "file", "name": name, "type": "csv", "app": app, "owner": owner, "sharing": sharing, "disabled": False}


def by_name(items, key):
    return {item[key]: item for item in items}


class TestVisibility(unittest.TestCase):
    def test_rules(self):
        self.assertTrue(visible({"app": "otra", "sharing": "global"}, SIRT))
        self.assertTrue(visible({"app": "sirt", "sharing": "app"}, SIRT))
        self.assertFalse(visible({"app": "otra", "sharing": "app"}, SIRT))
        self.assertFalse(visible({"app": "sirt", "sharing": "user", "owner": "juan"}, SIRT))
        self.assertTrue(visible({"app": "sirt", "sharing": "user", "owner": "juan"}, {"app": "sirt", "owner": "juan"}))
        self.assertTrue(visible({"app": "system", "sharing": "app"}, SIRT))


class TestMacros(unittest.TestCase):
    def test_statuses(self):
        macros = [
            macro("m_ok"), macro("m_global", app="search", sharing="global"), macro("m_otra_app", app="otra"),
            macro("m_deshabilitada", disabled=True), macro("m_args(2)"),
        ]
        spl = "`m_ok` | `m_global` | `m_otra_app` | `m_deshabilitada` | `m_args(x)` | `no_existe`"
        result = by_name(resolve_dependencies(spl, SIRT, macros)["macros"], "stanza")
        self.assertEqual(result["m_ok"]["status"], "ok")
        self.assertEqual(result["m_global"]["status"], "ok")
        self.assertEqual(result["m_otra_app"]["status"], "not_visible")
        self.assertEqual(result["m_otra_app"]["not_visible_in"], [{"app": "otra", "owner": "nobody", "sharing": "app"}])
        self.assertEqual(result["m_deshabilitada"]["status"], "disabled")
        self.assertEqual(result["m_args(1)"]["status"], "missing")
        self.assertEqual(result["m_args(1)"]["other_arities"], ["m_args(2)"])
        self.assertEqual(result["no_existe"]["status"], "missing")
        self.assertEqual(resolve_dependencies(spl, SIRT, macros)["problems"], 4)

    def test_nested_macros_and_lookups_inside_them(self):
        macros = [macro("padre", "`hijo(1)` | lookup usuarios user"), macro("hijo(1)", "`nieto` | where x=$a$"), macro("nieto")]
        result = resolve_dependencies("`padre`", SIRT, macros, [definition("usuarios")])
        self.assertEqual([(m["stanza"], m["depth"], m["via"]) for m in result["macros"]],
                         [("padre", 0, None), ("hijo(1)", 1, "padre"), ("nieto", 2, "hijo(1)")])
        self.assertEqual([(lk["name"], lk["via"], lk["status"]) for lk in result["lookups"]], [("usuarios", "padre", "ok")])

    def test_cycles_do_not_loop(self):
        macros = [macro("a", "`b`"), macro("b", "`a` | `c`"), macro("c")]
        self.assertEqual([m["stanza"] for m in resolve_dependencies("`a`", SIRT, macros)["macros"]], ["a", "b", "c"])

    def test_precedence_private_then_app_then_global(self):
        macros = [macro("m", "global", app="search", sharing="global"), macro("m", "de la app"),
                  macro("m", "privada", owner="juan", sharing="user")]
        as_nobody = resolve_dependencies("`m`", SIRT, macros)["macros"][0]
        self.assertEqual(as_nobody["definition"], "de la app")
        self.assertEqual(as_nobody["also"], [{"app": "search", "owner": "nobody", "sharing": "global"}])
        as_juan = resolve_dependencies("`m`", {"app": "sirt", "owner": "juan"}, macros)["macros"][0]
        self.assertEqual(as_juan["definition"], "privada")
        self.assertEqual(len(as_juan["also"]), 2)


class TestLookups(unittest.TestCase):
    def test_statuses(self):
        lookups = [
            definition("con_csv", "datos.csv"), csv("datos.csv"),
            definition("sin_csv", "falta.csv"),
            definition("csv_lejano", "lejos.csv"), csv("lejos.csv", app="otra"),
            definition("otra_app", app="otra"),
            csv("global.csv", app="search", sharing="global"),
            definition("deshabilitado", disabled=True),
        ]
        spl = ("| lookup con_csv a | lookup sin_csv a | lookup csv_lejano a | lookup otra_app a "
               "| inputlookup global.csv | lookup deshabilitado a | lookup no_existe a")
        result = by_name(resolve_dependencies(spl, SIRT, [], lookups)["lookups"], "name")
        self.assertEqual({name: item["status"] for name, item in result.items()}, {
            "con_csv": "ok", "sin_csv": "file_missing", "csv_lejano": "file_not_visible", "otra_app": "not_visible",
            "global.csv": "ok", "deshabilitado": "disabled", "no_existe": "missing",
        })
        self.assertEqual(result["global.csv"]["kind"], "file")
        self.assertEqual(result["con_csv"]["commands"], ["lookup"])

    def test_outputlookup_creates_the_lookup(self):
        result = resolve_dependencies("| inputlookup a.csv | outputlookup nuevo.csv | outputlookup a.csv", SIRT, [], [csv("a.csv")])
        self.assertEqual({lk["name"]: lk["status"] for lk in result["lookups"]}, {"a.csv": "ok", "nuevo.csv": "created"})
        self.assertEqual(result["problems"], 0)

    def test_direct_use_is_reported_before_use_inside_macros(self):
        result = resolve_dependencies("| inputlookup x.csv | `m`", SIRT, [macro("m", "lookup x.csv a")], [csv("x.csv")])
        self.assertEqual([(lk["name"], lk["via"]) for lk in result["lookups"]], [("x.csv", None)])


if __name__ == "__main__":
    unittest.main()
