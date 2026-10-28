import unittest

from web.lookups import build_rows, definition_type, preview_spl, usage_in


def entry(name, app="sirt", sharing="app", owner="nobody", **content):
    return {"name": name, "acl": {"app": app, "owner": owner, "sharing": sharing}, "content": content,
            "updated": "2026-10-28T10:00:00+01:00"}


class TestBuildRows(unittest.TestCase):
    def setUp(self):
        definitions = [
            entry("con_csv", filename="datos.csv", fields_list="user,name", match_type="WILDCARD(user)"),
            entry("sin_csv", filename="falta.csv"),
            entry("csv_lejano", filename="lejos.csv"),
            entry("kv", collection="assets", type="kvstore"),
            entry("kv_sin_coleccion", collection="no_existe"),
            entry("externo", external_cmd="dnslookup.py clienthost clientip", disabled="1"),
        ]
        files = [entry("datos.csv", **{"eai:data": "/opt/splunk/etc/apps/sirt/lookups/datos.csv"}),
                 entry("lejos.csv", app="otra"), entry("suelto.csv", sharing="global")]
        automatic = [entry("access : LOOKUP-dns", attribute="LOOKUP-dns", stanza="access_combined", transform="externo",
                           **{"lookup.field.input.clientip": "clientip", "lookup.field.output.clienthost": "host"}),
                     entry("x : LOOKUP-roto", attribute="LOOKUP-roto", stanza="x", transform="borrado")]
        collections = [entry("assets")]
        self.rows = {(r["kind"], r["name"]): r for r in build_rows(definitions, files, automatic, collections)}

    def test_definitions(self):
        self.assertEqual(self.rows[("definition", "con_csv")]["problems"], [])
        self.assertEqual(self.rows[("definition", "con_csv")]["options"], {"match_type": "WILDCARD(user)"})
        self.assertEqual(self.rows[("definition", "sin_csv")]["problems"], ["file_missing"])
        self.assertEqual(self.rows[("definition", "csv_lejano")]["problems"], ["file_not_visible"])
        self.assertEqual(self.rows[("definition", "kv")]["type"], "kvstore")
        self.assertEqual(self.rows[("definition", "kv")]["problems"], [])
        self.assertEqual(self.rows[("definition", "kv_sin_coleccion")]["problems"], ["collection_missing"])
        self.assertEqual(self.rows[("definition", "externo")]["type"], "external")
        self.assertTrue(self.rows[("definition", "externo")]["disabled"])

    def test_files_know_their_definitions(self):
        self.assertEqual(self.rows[("file", "datos.csv")]["definitions"], ["con_csv"])
        self.assertEqual(self.rows[("file", "suelto.csv")]["definitions"], [])
        self.assertEqual(self.rows[("file", "datos.csv")]["source"], "/opt/splunk/etc/apps/sirt/lookups/datos.csv")

    def test_automatic(self):
        dns = self.rows[("automatic", "LOOKUP-dns")]
        self.assertEqual((dns["stanza"], dns["source"]), ("access_combined", "externo"))
        self.assertEqual((dns["inputs"], dns["outputs"]), ({"clientip": "clientip"}, {"clienthost": "host"}))
        self.assertEqual(self.rows[("automatic", "LOOKUP-roto")]["problems"], ["definition_missing"])

    def test_collections_not_checked_when_unknown(self):
        rows = build_rows([entry("kv", collection="x", type="kvstore")], [], [], None)
        self.assertEqual(rows[0]["problems"], [])

    def test_definition_type(self):
        self.assertEqual(definition_type({"filename": "a.csv"}), "file")
        self.assertEqual(definition_type({"collection": "c"}), "kvstore")
        self.assertEqual(definition_type({"external_cmd": "x.py"}), "external")
        self.assertEqual(definition_type({"type": "Geo", "filename": "a.kmz"}), "geo")


class TestUsage(unittest.TestCase):
    def test_file_is_used_directly_and_through_its_definitions(self):
        searches = [
            entry("CU-1", search="index=a | lookup con_csv user", alert_type="number of events"),
            entry("Informe", search="| inputlookup datos.csv | stats count"),
            entry("Otra", search="index=a | lookup otra x"),
        ]
        macros = [entry("m", definition="lookup con_csv user OUTPUT name")]
        definitions = [entry("con_csv", filename="datos.csv")]
        automatic = [entry("x : LOOKUP-a", attribute="LOOKUP-a", stanza="x", transform="con_csv")]
        usage = usage_in("datos.csv", "file", searches, macros, definitions, automatic)
        self.assertEqual(usage["names"], ["con_csv", "datos.csv"])
        self.assertEqual([(s["name"], s["is_alert"], s["via"]) for s in usage["searches"]],
                         [("CU-1", True, ["con_csv"]), ("Informe", False, [])])
        self.assertEqual([(m["name"], m["via"]) for m in usage["macros"]], [("m", ["con_csv"])])
        self.assertEqual([(a["name"], a["via"]) for a in usage["automatic"]], [("LOOKUP-a", ["con_csv"])])
        # Una definición solo se usa por su nombre
        self.assertEqual([s["name"] for s in usage_in("con_csv", "definition", searches, macros, definitions, [])["searches"]], ["CU-1"])


class TestPreview(unittest.TestCase):
    def test_spl(self):
        self.assertEqual(preview_spl("datos.csv"), ("| inputlookup datos.csv | head 100", "| inputlookup datos.csv | stats count"))
        self.assertEqual(preview_spl("mi lookup.csv", rows=5)[0], '| inputlookup "mi lookup.csv" | head 5')

    def test_unsafe_names_are_rejected(self):
        for name in ("x | delete", "`macro`", "$tok$", 'a"b', "", "[sub]", "a\nb"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                preview_spl(name)


if __name__ == "__main__":
    unittest.main()
