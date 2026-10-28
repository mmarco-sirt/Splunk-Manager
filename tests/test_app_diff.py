import unittest

from web.app_diff import diff_client, diff_packages, macro_closure
from web.app_packages import Package

APP_CONF = "[launcher]\nversion = {v}\n[package]\nid = sirt\n"


def package(version, files):
    data = {"default/app.conf": APP_CONF.format(v=version)}
    data.update(files)
    return Package({k: v.encode("utf-8") if isinstance(v, str) else v for k, v in data.items()}, "sirt")


OLD = package("1.0.0", {
    "default/savedsearches.conf": "[CU-1 A]\nsearch = `idx` | stats count\ncron_schedule = */5 * * * *\n"
                                  "[CU-2 B]\nsearch = index=b\n[CU-3 C]\nsearch = `otra`\n[Viejo]\nsearch = index=v\n",
    "default/macros.conf": "[idx]\ndefinition = index=a\n[otra]\ndefinition = index=o\n",
    "lookups/vip.csv": "user,vip\nana,1\n",
    "static/appIcon.png": b"\x89PNG1",
})
NEW = package("1.1.0", {
    "default/savedsearches.conf": "[CU-1 A]\nsearch = `idx` | stats count\ncron_schedule = */5 * * * *\n"
                                  "[CU-2 B]\nsearch = index=b\n[CU-3 C]\nsearch = `otra`\n[CU-4 D]\nsearch = index=d\n",
    "local/savedsearches.conf": "[CU-2 B]\nsearch = index=b2\n",
    "default/macros.conf": "[idx]\ndefinition = index=a OR index=a2\n[otra]\ndefinition = index=o\n",
    "lookups/vip.csv": "user,vip\nana,1\nluis,0\n",
    "default/data/ui/views/resumen.xml": "<dashboard/>",
    "static/appIcon.png": b"\x89PNG2",
})


def by_key(result):
    return {(i["type"], i["name"]): i for i in result["items"]}


class TestDiffPackages(unittest.TestCase):
    def setUp(self):
        self.result = diff_packages(NEW, OLD)
        self.items = by_key(self.result)

    def test_statuses(self):
        self.assertEqual(self.items[("savedsearches", "CU-4 D")]["status"], "added")
        self.assertEqual(self.items[("savedsearches", "Viejo")]["status"], "removed")
        self.assertEqual(self.items[("savedsearches", "CU-2 B")]["status"], "modified")
        self.assertNotIn(("savedsearches", "CU-3 C"), self.items)   # sin cambios
        self.assertEqual(self.items[("app", "launcher")]["fields"][0]["after"], "1.1.0")
        counts = {t["type"]: t["counts"] for t in self.result["types"]}
        self.assertEqual(counts["savedsearches"], {"added": 1, "removed": 1, "modified": 1, "affected": 1, "same": 1, "present": 0})

    def test_local_layer_is_marked(self):
        fields = self.items[("savedsearches", "CU-2 B")]["fields"]
        self.assertEqual([(f["field"], f["before"], f["after"]) for f in fields], [("search [local]", None, "index=b2")])

    def test_searches_affected_by_changed_macros(self):
        # CU-1 A no cambia, pero la macro idx que usa sí: aparece como «affected» para volver a ejecutarla
        self.assertEqual(self.items[("macros", "idx")]["status"], "modified")
        cu1 = self.items[("savedsearches", "CU-1 A")]
        self.assertEqual((cu1["status"], cu1["spl_changed"], cu1["affected_by"], cu1["fields"]), ("affected", False, ["idx"], []))
        self.assertNotIn(("savedsearches", "CU-3 C"), self.items)   # usa «otra», que no cambia
        counts = {t["type"]: t["counts"] for t in self.result["types"]}
        self.assertEqual(counts["savedsearches"]["affected"], 1)
        # Si además cambia otro campo sigue siendo «modified», con las macros que le afectan
        result = diff_packages(NEW, package("1.0.0", {**{k: v.decode() for k, v in OLD.files.items() if k.endswith(".conf")},
                                                      "default/savedsearches.conf": "[CU-1 A]\nsearch = `idx` | stats count\ncron_schedule = 0 * * * *\n"}))
        cu1 = by_key(result)[("savedsearches", "CU-1 A")]
        self.assertEqual((cu1["status"], cu1["spl_changed"], cu1["affected_by"]), ("modified", False, ["idx"]))
        self.assertTrue(self.items[("savedsearches", "CU-2 B")]["spl_changed"])

    def test_files(self):
        self.assertEqual(self.items[("lookups", "vip.csv")]["status"], "modified")
        self.assertIn("+luis,0", self.items[("lookups", "vip.csv")]["fields"][0]["diff"])
        self.assertEqual(self.items[("views", "default/data/ui/views/resumen.xml")]["status"], "added")
        self.assertEqual(self.items[("files", "static/appIcon.png")]["fields"][0]["field"], "tamaño")

    def test_macro_closure(self):
        macros = {"a": {"definition": "`b(1)` x"}, "b(1)": {"definition": "`c` $x$"}, "c": {"definition": "`a`"}}
        self.assertEqual(macro_closure("`a` | `externa`", macros), {"a", "b(1)", "c"})


class TestDiffClient(unittest.TestCase):
    def snapshot(self, **changes):
        base = {
            "installed": {"version": "1.0.0", "label": "SIRT", "disabled": False, "build": ""},
            "confs": {
                "savedsearches": {
                    "CU-1 A": {"search": "`idx` | stats count", "cron_schedule": "*/5 * * * *", "disabled": False, "otro": "x"},
                    "CU-2 B": {"search": "index=b"},
                    "CU-3 C": {"search": "`otra`  "},
                    "Creada en el cliente": {"search": "index=z"},
                },
                "macros": {"idx": {"definition": "index=a OR index=a2"}, "otra": {"definition": "index=o"}},
            },
            "lookups": {"vip.csv", "solo_cliente.csv"},
            "views": {"resumen": "<dashboard>\n</dashboard>"},
            "errors": {},
        }
        base.update(changes)
        return base

    def test_compares_only_the_package_fields(self):
        items = by_key(diff_client(NEW, self.snapshot()))
        self.assertNotIn(("savedsearches", "CU-1 A"), items)   # 'otro' y 'disabled' no los define el paquete; idx es igual
        self.assertNotIn(("savedsearches", "CU-3 C"), items)   # espacios de sobra: igual
        cu2 = items[("savedsearches", "CU-2 B")]
        self.assertEqual([(f["field"], f["before"], f["after"]) for f in cu2["fields"]], [("search [local]", "index=b", "index=b2")])
        self.assertEqual(items[("savedsearches", "CU-4 D")]["status"], "added")
        self.assertEqual(items[("savedsearches", "Creada en el cliente")]["status"], "removed")
        self.assertEqual(items[("lookups", "vip.csv")]["status"], "present")
        self.assertEqual(items[("lookups", "solo_cliente.csv")]["status"], "removed")
        self.assertEqual(items[("views", "resumen")]["status"], "modified")

    def test_booleans_and_numbers_are_normalized(self):
        pkg = package("1.0.0", {"default/savedsearches.conf": "[s]\nsearch = x\nenableSched = true\nalert.severity = 3\n"})
        snap = self.snapshot(confs={"savedsearches": {"s": {"search": "x", "enableSched": True, "alert.severity": 3}}},
                             lookups=set(), views={})
        self.assertEqual(diff_client(pkg, snap)["items"], [])

    def test_not_installed_means_everything_is_new(self):
        snap = self.snapshot(installed=None, confs={}, lookups=set(), views={})
        statuses = {i["status"] for i in diff_client(NEW, snap)["items"]}
        self.assertEqual(statuses, {"added"})

    def test_errors_per_type(self):
        result = diff_client(NEW, self.snapshot(errors={"macros": "Sin permiso"}))
        macros = next(t for t in result["types"] if t["type"] == "macros")
        self.assertEqual(macros["error"], "Sin permiso")
        self.assertFalse(any(i["type"] == "macros" for i in result["items"]))


if __name__ == "__main__":
    unittest.main()
