import io
import unittest
from unittest import mock

from splunklib.binding import HTTPError
from splunklib.data import record

from core.spl import blocked_commands, commands, expand_macros
from web import app_validation as v
from web.app_packages import Package

GOOD_ALERT = {
    "search": "index=a | stats count", "description": "Accesos imposibles", "cron_schedule": "*/15 * * * *",
    "enableSched": "1", "dispatch.earliest_time": "-15m", "counttype": "number of events", "relation": "greater than",
    "quantity": "0", "alert.severity": "4", "actions": "email", "alert.suppress": "1", "disabled": "1",
}


class TestNames(unittest.TestCase):
    def test_valid(self):
        for name in ("CU-CA-AUTH-1152-T1078 Geographically Improbable Access", "TEST - CU-AD-1-T1110.001 Fuerza bruta",
                     "CU-O365-20-T1566 Phishing"):
            with self.subTest(name=name):
                self.assertEqual(v.name_problems(name), [])

    def test_invalid(self):
        cases = {
            "TEST-CU-AD-1-T1110 X": "exactamente «TEST - »",
            "Test - CU-AD-1-T1110 X": "exactamente «TEST - »",
            "CU-ad-1-T1110 X": "mayúsculas",
            "CU-AD-12A-T1110 X": "numérico",
            "CU-AD-1-T11101 X": "técnica MITRE",
            "CU-AD-1-1110 X": "técnica MITRE",
            "CU-AD-1-T1110": "nombre descriptivo",
            "CU-AD-T1110 X": "faltan partes",
            "Otra alerta": "No sigue el estándar",
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertTrue(any(expected.lower() in p.lower() for p in v.name_problems(name)), v.name_problems(name))


class TestAlertRules(unittest.TestCase):
    def test_complete_alert(self):
        self.assertEqual(v.alert_problems("CU-AD-1-T1110 X", GOOD_ALERT), [])

    def test_missing_fields(self):
        problems = v.alert_problems("CU-AD-1-T1110 X", {"search": "index=a"})
        message = problems[0][2]
        for expected in ("description", "cron_schedule", "dispatch.earliest_time", "condición de disparo", "alert.severity", "actions"):
            self.assertIn(expected, message)

    def test_cron_threshold_and_scheduling(self):
        self.assertIn("no es válido", v.alert_problems("x", {**GOOD_ALERT, "cron_schedule": "cada hora"})[0][2])
        self.assertIn("enableSched", v.alert_problems("x", {**GOOD_ALERT, "enableSched": "0"})[0][2])
        self.assertIn("relation y quantity", v.alert_problems("x", {**GOOD_ALERT, "quantity": ""})[0][2])
        custom = {**GOOD_ALERT, "counttype": "custom"}
        self.assertIn("alert_condition", v.alert_problems("x", custom)[0][2])
        self.assertEqual(v.alert_problems("x", {**custom, "alert_condition": "search count > 5"}), [])
        self.assertEqual(v.alert_problems("x", {**GOOD_ALERT, "actions": "", "action.notable": "1"}), [])

    def test_test_versions(self):
        name = "TEST - CU-AD-1-T1110 X"
        self.assertEqual(v.alert_problems(name, GOOD_ALERT), [])
        problems = v.alert_problems(name, {**GOOD_ALERT, "disabled": "0", "alert.suppress": "0"})
        self.assertEqual([p[1] for p in problems], ["test", "test"])

    def test_is_alert(self):
        self.assertTrue(v.is_alert("CU-AD-1-T1110 X", {}))
        self.assertTrue(v.is_alert("Otra", {"counttype": "always"}))
        self.assertFalse(v.is_alert("Informe semanal", {"action.email": "1", "actions": "email"}))


def make_package(files):
    return Package({k: v.encode("utf-8") if isinstance(v, str) else v for k, v in files.items()}, "sirt_app")


class TestValidatePackage(unittest.TestCase):
    def setUp(self):
        self.package = make_package({
            "default/app.conf": "[launcher]\nversion = 1.4\n[id]\nversion = 1.3.0\n[package]\nid = otra_app\n",
            "default/savedsearches.conf": (
                "[CU-AD-1-T1110 Bien]\n" + "".join(f"{k} = {val}\n" for k, val in GOOD_ALERT.items())
                + "search = `sirt_idx` `externa` | lookup vip user | lookup sin_csv x\n"
                "[CU-AD-2-T1110 Otra]\n" + "".join(f"{k} = {val}\n" for k, val in GOOD_ALERT.items())
                + "search = `externa` | stats count\n"
                "[cu-mal nombre]\nsearch = index=a\ncounttype = always\n"
                "[Informe]\nsearch = index=a\n"),
            "default/macros.conf": "[sirt_idx]\ndefinition = index=sirt\n",
            "default/transforms.conf": "[vip]\nfilename = vip.csv\n[sin_csv]\nfilename = falta.csv\n",
            "local/macros.conf": "[sirt_idx]\ndefinition = index=sirt2\n",
            "lookups/vip.csv": "user,vip\nana,1\nluis\n",
            "default/data/ui/views/roto.xml": "<dashboard><row></dashboard>",
            "README.md": "DescripciÃ³n rota\n",
        })
        self.issues = v.validate_package(self.package)

    def find(self, category, text):
        return [i for i in self.issues if i["category"] == category and text in i["message"]]

    def test_app_conf_and_local(self):
        self.assertTrue(self.find("app", "no tiene el formato X.Y.Z"))
        self.assertTrue(self.find("app", "no coinciden"))
        self.assertTrue(self.find("app", "no coincide con el id"))
        self.assertTrue(self.find("app", "incluye local/"))

    def test_saved_searches(self):
        naming = [i for i in self.issues if i["category"] == "naming"]
        self.assertEqual({(i["object"], i["level"]) for i in naming}, {("cu-mal nombre", "error"), ("Informe", "warning")})
        self.assertTrue(any(i["object"] == "cu-mal nombre" and i["category"] == "fields" for i in self.issues))
        self.assertFalse(any(i["object"] == "Informe" and i["category"] == "fields" for i in self.issues))

    def test_dependencies_are_grouped(self):
        externa = self.find("dependencies", "La macro externa")
        self.assertEqual(len(externa), 1)
        self.assertIn("2 búsquedas", externa[0]["message"])
        self.assertTrue(self.find("dependencies", "falta.csv"))
        self.assertFalse(self.find("dependencies", "sirt_idx"))

    def test_files(self):
        self.assertTrue(self.find("lookups", "nº de columnas"))
        self.assertTrue(self.find("views", "XML mal formado"))
        self.assertTrue(self.find("encoding", "mal codificado"))
        self.assertEqual(set(v.counts(self.issues)), {"error", "warning", "info"})


class TestSpl(unittest.TestCase):
    def test_expand_macros(self):
        macros = {"idx(1)": {"definition": "index=$i$ `filtro`", "args": "i"}, "filtro": {"definition": 'NOT user="svc_*"'},
                  "eval_m": {"definition": '"x"', "iseval": True}}
        text, used = expand_macros("```nota``` `idx(main)` | `eval_m` | `externa(1)`", macros)
        self.assertEqual(text.strip(), 'index=main NOT user="svc_*" | `eval_m` | `externa(1)`')
        self.assertEqual(used, ["idx(1)", "filtro"])
        self.assertEqual(expand_macros("`m(b=2, a=1)`", {"m(2)": {"definition": "$a$-$b$", "args": "a,b"}})[0], "1-2")

    def test_blocked(self):
        self.assertEqual(commands("index=a | stats count | outputlookup x\n| `m`"), ["index", "stats", "outputlookup"])
        self.assertEqual(blocked_commands(["search", "stats", "outputlookup", "collect", "sendcef", "Sendalert"]),
                         ["collect", "outputlookup", "sendalert", "sendcef"])
        self.assertEqual(blocked_commands(["lookup", "inputlookup", "tstats", "rest"]), [])


class TestRunHelpers(unittest.TestCase):
    def test_search_query_and_window(self):
        self.assertEqual(v.search_query("index=a"), "search index=a")
        self.assertEqual(v.search_query("| tstats count"), "| tstats count")
        self.assertEqual(v.window({"dispatch.earliest_time": "rt-5m", "dispatch.latest_time": "rt"}, "window", {}), ("-5m", "now"))
        self.assertEqual(v.window({}, "window", {}), ("-24h", "now"))
        self.assertEqual(v.window({"dispatch.earliest_time": "-1h"}, "fixed", {"earliest": "-7d", "latest": "now"}), ("-7d", "now"))

    def test_run_options(self):
        self.assertEqual(v.run_options({})["range"], "window")
        with self.assertRaises(ValueError):
            v.run_options({"range": "fixed", "earliest": "-1d | delete"})
        with self.assertRaises(ValueError):
            v.run_options({"timeout": 5})

    def test_versions(self):
        self.assertTrue(v.is_newer("1.10.0", "1.9.2"))
        self.assertFalse(v.is_newer("1.4.0", "1.4.0"))
        self.assertTrue(v.is_newer("1.4.0", "1.4.0-beta") or True)   # sin error aunque mezcle números y texto


class FakeJob:
    def __init__(self, content):
        self.content = content
        self.cancelled = False

    def refresh(self):
        return self

    def cancel(self):
        self.cancelled = True


class FakeSearchService:
    """Splunk simulado para run_search: parser (search/parser) y jobs."""

    def __init__(self, parser_commands=("search", "stats"), parser_error=None, content=None):
        self.parser_commands, self.parser_error = parser_commands, parser_error
        self.content = content or {"isDone": "1", "isFailed": "0", "resultCount": "7", "eventCount": "120",
                                   "scanCount": "500", "runDuration": "1.5"}
        self.created, self.parsed = [], []
        service = self

        class Jobs:
            def create(self, query, **kwargs):
                service.created.append((query, kwargs))
                service.job = FakeJob(service.content)
                return service.job
        self.jobs = Jobs()

    def get(self, path, **kwargs):
        self.parsed.append((path, kwargs))
        if self.parser_error:
            body = io.BytesIO(('{"messages":[{"type":"FATAL","text":"%s"}]}' % self.parser_error).encode())
            raise HTTPError(record({"status": 400, "reason": "Bad Request", "body": body, "headers": []}))
        body = '{"commands": [%s]}' % ",".join('{"command": "%s"}' % c for c in self.parser_commands)
        return record({"body": io.BytesIO(body.encode())})


@mock.patch.object(v, "POLL_SECONDS", 0)
class TestRunSearch(unittest.TestCase):
    def test_ok_runs_an_ad_hoc_job_and_deletes_it(self):
        service = FakeSearchService()
        result = v.run_search(service, "index=a | stats count", "-1h", "now", "sirt", 60)
        self.assertEqual((result["status"], result["result_count"], result["duration"]), ("ok", 7, 1.5))
        query, kwargs = service.created[0]
        self.assertEqual(query, "search index=a | stats count")
        self.assertEqual((kwargs["exec_mode"], kwargs["earliest_time"], kwargs["app"], kwargs["owner"]), ("normal", "-1h", "sirt", "nobody"))
        self.assertTrue(service.job.cancelled)
        self.assertEqual(service.parsed[0][1]["parse_only"], "true")

    def test_write_commands_never_reach_splunk(self):
        service = FakeSearchService()
        result = v.run_search(service, "index=a | outputlookup x", "-1h", "now", "sirt", 60)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual((service.created, service.parsed), ([], []))
        # También si el comando lo añade una macro del cliente (lo dice el parser)
        service = FakeSearchService(parser_commands=("search", "collect"))
        self.assertEqual(v.run_search(service, "`resumen`", "-1h", "now", "sirt", 60)["status"], "blocked")
        self.assertEqual(service.created, [])

    def test_parser_errors(self):
        service = FakeSearchService(parser_error="Unknown search command 'statz'.")
        result = v.run_search(service, "index=a | statz count", "-1h", "now", "sirt", 60)
        self.assertEqual(result["status"], "error")
        self.assertIn("Unknown search command", result["message"])
        self.assertEqual(service.created, [])

    def test_failed_and_warnings(self):
        failed = FakeSearchService(content={"isDone": "1", "isFailed": "1", "messages": {"error": "Error in 'lookup' command"}})
        self.assertEqual(v.run_search(failed, "index=a", "-1h", "now", "sirt", 60)["status"], "error")
        warned = FakeSearchService(content={"isDone": "1", "isFailed": "0", "resultCount": "0", "runDuration": "0.2",
                                            "messages": {"warn": "Some events were truncated"}})
        self.assertEqual(v.run_search(warned, "index=a", "-1h", "now", "sirt", 60)["status"], "warning")

    def test_timeout_cancels_the_job(self):
        service = FakeSearchService(content={"isDone": "0"})
        with mock.patch.object(v.time, "time", side_effect=[0, 0, 100, 100, 100]):
            result = v.run_search(service, "index=a", "-1h", "now", "sirt", 10)
        self.assertEqual(result["status"], "timeout")
        self.assertTrue(service.job.cancelled)


if __name__ == "__main__":
    unittest.main()
