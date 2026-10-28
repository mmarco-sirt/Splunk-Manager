import unittest

from core.splunk_conf import (
    app_info, decode_text, effective_stanza, is_text_path, merge_layers, normalize_value, parse_conf,
)


def messages(issues, level=None):
    return [i["message"] for i in issues if level is None or i["level"] == level]


class TestParseConf(unittest.TestCase):
    def test_stanzas_keys_comments_and_default(self):
        conf = parse_conf("# comentario\nglobal = 1\n\n[CU-1 A]\nsearch = index=a\ncron_schedule=*/5 * * * *\n  # otro\n[m]\ndefinition = x = y\n")
        self.assertEqual(conf.stanzas, {"default": {"global": "1"}, "CU-1 A": {"search": "index=a", "cron_schedule": "*/5 * * * *"},
                                        "m": {"definition": "x = y"}})
        self.assertEqual(conf.stanza_lines["CU-1 A"], 4)
        self.assertEqual(conf.key_lines[("CU-1 A", "cron_schedule")], 6)
        self.assertEqual(conf.issues, [])

    def test_continuation_lines(self):
        conf = parse_conf("[s]\nsearch = index=a \\\n| stats count \\\n| where count>1\ncron_schedule = 0 * * * *\n")
        self.assertEqual(conf.stanzas["s"]["search"], "index=a \n| stats count \n| where count>1")
        self.assertEqual(conf.stanzas["s"]["cron_schedule"], "0 * * * *")
        self.assertEqual(conf.issues, [])

    def test_backslash_followed_by_spaces_does_not_continue(self):
        conf = parse_conf("[s]\nsearch = index=a \\   \n| stats count\n")
        self.assertEqual(len(messages(conf.issues, "error")), 2)   # la barra con espacios y la línea huérfana
        self.assertIn("seguida de espacios", conf.issues[0]["message"])
        self.assertEqual(conf.issues[0]["line"], 2)
        self.assertEqual(conf.issues[1]["line"], 3)

    def test_continuation_at_end_of_file(self):
        conf = parse_conf("[s]\nsearch = index=a \\")
        self.assertEqual(conf.stanzas["s"]["search"], "index=a")
        self.assertIn("final del archivo", messages(conf.issues, "warning")[0])

    def test_duplicates_and_malformed_lines(self):
        conf = parse_conf("[s]\na = 1\na = 1\nb = 1\nb = 2\n[s]\nc = 3\n[sin cerrar\n[]\nlinea suelta\n= valor\n")
        errors, warnings = messages(conf.issues, "error"), messages(conf.issues, "warning")
        self.assertEqual(conf.stanzas["s"], {"a": "1", "b": "2", "c": "3"})
        self.assertTrue(any("«b» repetida" in m and "otro valor" in m for m in errors))
        self.assertTrue(any("«a» repetida" in m and "mismo valor" in m for m in warnings))
        self.assertTrue(any("Stanza [s] repetida" in m for m in warnings))
        self.assertTrue(any("sin cerrar" in m for m in errors))
        self.assertTrue(any("Stanza sin nombre" in m for m in errors))
        self.assertTrue(any("Línea sin «clave = valor»" in m for m in errors))
        self.assertTrue(any("Valor sin clave" in m for m in errors))

    def test_meta_global_stanza(self):
        # En los .meta, «[]» son los permisos de toda la app; en un .conf no es válido
        conf = parse_conf("[]\naccess = read : [ * ], write : [ admin ]\nexport = system\n", "metadata/default.meta")
        self.assertEqual(conf.issues, [])
        self.assertEqual(conf.stanzas[""]["export"], "system")
        self.assertTrue(parse_conf("[]\na = 1\n", "default/x.conf").issues)

    def test_merge_and_effective(self):
        default = parse_conf("[default]\ndispatch.earliest_time = -24h\n[s]\nsearch = a\ncron_schedule = 0 * * * *\n").stanzas
        local = parse_conf("[s]\ncron_schedule = */5 * * * *\n[t]\nsearch = b\n").stanzas
        merged = merge_layers(default, local)
        self.assertEqual(merged["s"], {"search": "a", "cron_schedule": "*/5 * * * *"})
        self.assertEqual(effective_stanza(merged, "t"), {"dispatch.earliest_time": "-24h", "search": "b"})

    def test_app_info(self):
        stanzas = parse_conf("[launcher]\nversion = 1.4.0\nauthor = SIRT\n[ui]\nlabel = SIRT SOC App for Splunk\n"
                             "[package]\nid = sirt_soc_app_for_splunk\n[id]\nversion = 1.4.0\n[install]\nbuild = 23\n").stanzas
        info = app_info(stanzas)
        self.assertEqual((info["id"], info["version"], info["label"], info["build"]), ("sirt_soc_app_for_splunk", "1.4.0", "SIRT SOC App for Splunk", "23"))
        self.assertEqual(app_info(parse_conf("[id]\nname = x\nversion = 2\n").stanzas)["version"], "2")

    def test_normalize_value(self):
        self.assertEqual(normalize_value("index=a \\\n| stats count  \r\n"), "index=a\n| stats count")
        self.assertEqual(normalize_value(None), "")


class TestDecodeText(unittest.TestCase):
    def test_utf8_is_fine(self):
        text, issues = decode_text("descripción = Acceso desde ubicación imposible\n".encode("utf-8"), "default/savedsearches.conf")
        self.assertEqual(issues, [])
        self.assertIn("ubicación", text)

    def test_bom(self):
        _, conf_issues = decode_text(b"\xef\xbb\xbf[s]\n", "default/macros.conf")
        _, csv_issues = decode_text(b"\xef\xbb\xbfuser,vip\n", "lookups/vip.csv")
        _, md_issues = decode_text(b"\xef\xbb\xbf# Leeme\n", "README.md")
        self.assertEqual([i["level"] for i in conf_issues + csv_issues + md_issues], ["error", "error", "warning"])

    def test_latin1_file(self):
        text, issues = decode_text("[s]\ndescription = Conexión\n".encode("cp1252"), "default/savedsearches.conf")
        self.assertEqual(issues[0]["level"], "error")
        self.assertEqual(issues[0]["line"], 2)
        self.assertIn("Conexión", text)

    def test_double_encoded_accents(self):
        broken = "description = DescripciÃ³n de la alerta Â¿Ok? â€“ fin\n"
        _, issues = decode_text(broken.encode("utf-8"), "default/savedsearches.conf")
        self.assertEqual([i["level"] for i in issues], ["warning"])
        self.assertIn("mal codificado", issues[0]["message"])

    def test_line_endings_and_control_chars(self):
        text, issues = decode_text(b"a = 1\r\nb = 2\nc = \x07\n", "default/x.conf")
        self.assertEqual(text, "a = 1\nb = 2\nc = \x07\n")
        self.assertTrue(any("mezclados" in m for m in messages(issues)))
        self.assertTrue(any("control" in m for m in messages(issues)))

    def test_is_text_path(self):
        self.assertTrue(is_text_path("default/app.conf"))
        self.assertTrue(is_text_path("README"))
        self.assertTrue(is_text_path("lookups/a.CSV"))
        self.assertFalse(is_text_path("static/appIcon.png"))


if __name__ == "__main__":
    unittest.main()
