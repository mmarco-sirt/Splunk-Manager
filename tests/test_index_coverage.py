import json
import os
import tempfile
import unittest
from unittest import mock

from core import index_coverage as ic
from core.index_coverage import Knowledge, analyze, coverage, extract_filters, resolve_search

CTX = {"app": "sirt", "owner": "nobody"}

AUTH_MODEL = json.dumps({"modelName": "Authentication", "objects": [
    {"objectName": "Authentication", "parentName": "BaseEvent",
     "constraints": [{"search": "(`cim_Authentication_indexes`) tag=authentication NOT (action=success user=*$)"}]},
    {"objectName": "Failed_Authentication", "parentName": "Authentication", "constraints": [{"search": "action=\"failure\""}]},
]})


def alert(name, search, disabled=False, app="sirt", owner="nobody", sharing="app"):
    return {"name": name, "search": search, "disabled": disabled, "app": app, "owner": owner, "sharing": sharing}


def macro(name, definition, app="sirt", sharing="app", owner="nobody"):
    return {"name": name, "definition": definition, "app": app, "owner": owner, "sharing": sharing, "args": "",
            "iseval": False, "disabled": False}


def data(*rows):
    return [{"index": i, "sourcetype": st, "count": n, "last": 1700000000} for i, st, n in rows]


def indexes(*names):
    return [{"name": n, "datatype": "event", "disabled": False} for n in names]


def by_name(result):
    return {r["name"]: r for r in result["indexes"]}


class TestExtract(unittest.TestCase):
    def test_basic_filters(self):
        f = extract_filters('index=WinEventLog sourcetype="XmlWinEventLog:Security" EventCode=4625')
        self.assertEqual(f["index"], ["wineventlog"])
        self.assertEqual(f["sourcetype"], ["xmlwineventlog:security"])

    def test_negations_and_write_commands_do_not_count(self):
        f = extract_filters("index=fw index!=test NOT index=old | stats count | collect index=resumen")
        self.assertEqual(f["index"], ["fw"])

    def test_in_and_double_colon(self):
        f = extract_filters('index IN ("a", b) OR index::c | tstats count where index=d by host')
        self.assertEqual(sorted(f["index"]), ["a", "b", "c", "d"])

    def test_tokens_are_unresolved(self):
        f = extract_filters("index=$idx$ sourcetype=x")
        self.assertEqual(f["index"], [])
        self.assertEqual(f["unresolved"], ["index=$idx$"])

    def test_similar_field_names_are_ignored(self):
        f = extract_filters("src_index=a myindex=b | where orig_sourcetype=c")
        self.assertEqual((f["index"], f["sourcetype"]), ([], []))

    def test_datamodels(self):
        f = extract_filters("| tstats count from datamodel=Authentication.Failed_Authentication by user"
                            " | append [| datamodel Network_Traffic All_Traffic search] | append [| from datamodel:\"Web\"]")
        self.assertEqual(f["datamodel"], [("Authentication", "Failed_Authentication"), ("Web", None),
                                          ("Network_Traffic", "All_Traffic")])

    def test_datamodel_command_mode_is_not_a_dataset(self):
        self.assertEqual(extract_filters("| datamodel Authentication search")["datamodel"], [("Authentication", None)])


class TestResolve(unittest.TestCase):
    def test_macro_visible_from_alert_context(self):
        k = Knowledge(macros=[macro("idx_fw", "index=firewall"), macro("idx_otra", "index=otra", app="otra")])
        r = resolve_search("`idx_fw` `idx_otra` action=blocked", CTX, k)
        self.assertEqual(list(r["index"]), ["firewall"])
        self.assertIn("Macro sin resolver: `idx_otra`", r["issues"])

    def test_datamodel_with_mixed_eventtypes(self):
        """El dataset llega a dos eventtypes: uno con índice y otro solo con sourcetype. Cubre los dos índices."""
        k = Knowledge(
            macros=[macro("cim_Authentication_indexes", "()", app="Splunk_SA_CIM", sharing="global")],
            eventtypes=[{"name": "win_auth", "app": "TA-win", "owner": "nobody", "sharing": "global",
                         "search": "index=wineventlog sourcetype=WinEventLog EventCode=4625"},
                        {"name": "linux_auth", "app": "TA-nix", "owner": "nobody", "sharing": "global",
                         "search": "sourcetype=linux_secure"}],
            tags=[{"name": "eventtype=win_auth", "tags": ["authentication"]},
                  {"name": "eventtype=linux_auth", "tags": ["authentication"]}],
            datamodels={"Authentication": ic.parse_datamodel(AUTH_MODEL)})
        r = resolve_search("| tstats count from datamodel=Authentication.Failed_Authentication by user", CTX, k)
        mode, covered = coverage(r, ["wineventlog", "os", "main"], {"linux_secure": ["os"], "wineventlog": ["wineventlog"]})
        self.assertEqual(mode, "index")
        self.assertEqual(sorted(covered), ["os", "wineventlog"])
        self.assertIn("data model Authentication.Failed_Authentication", covered["os"])
        self.assertIn("sourcetype linux_secure", covered["os"])

    def test_cim_macro_with_indexes(self):
        k = Knowledge(macros=[macro("cim_Authentication_indexes", "(index=ad OR index=vpn)", sharing="global")],
                      datamodels={"Authentication": ic.parse_datamodel(AUTH_MODEL)})
        r = resolve_search("| tstats count from datamodel=Authentication by user", CTX, k)
        self.assertEqual(sorted(coverage(r, ["ad", "vpn", "fw"], {})[1]), ["ad", "vpn"])

    def test_missing_objects_are_reported(self):
        r = resolve_search("eventtype=nope tag=nada | tstats count from datamodel=Falta", CTX, Knowledge())
        self.assertEqual(r["issues"], ["Eventtype no encontrado: nope", "Ningún eventtype ni sourcetype tiene el tag «nada»",
                                       "Data model no encontrado: Falta"])

    def test_eventtype_cycle_does_not_hang(self):
        k = Knowledge(eventtypes=[{"name": "a", "app": "sirt", "owner": "nobody", "sharing": "app", "search": "eventtype=b index=x"},
                                  {"name": "b", "app": "sirt", "owner": "nobody", "sharing": "app", "search": "eventtype=a"}])
        r = resolve_search("eventtype=a", CTX, k)
        self.assertEqual(coverage(r, ["x"], {})[1], {"x": "eventtype a"})


class TestCoverageModes(unittest.TestCase):
    def mode(self, spl, names=("fw", "win"), st=None):
        return coverage(resolve_search(spl, CTX, Knowledge()), list(names), st or {})

    def test_wildcard_index(self):
        self.assertEqual(self.mode("index=w* x", ("win", "wsus", "fw")), ("index", {"win": None, "wsus": None}))

    def test_sourcetype_only(self):
        self.assertEqual(self.mode("sourcetype=pan:*", st={"pan:traffic": ["fw"], "linux": ["os"]}),
                         ("sourcetype", {"fw": "sourcetype pan:traffic"}))

    def test_index_star_with_sourcetype_uses_sourcetype(self):
        self.assertEqual(self.mode("index=* sourcetype=linux", st={"linux": ["os"]})[1], {"os": "sourcetype linux"})

    def test_sourcetype_star_does_not_widen_datamodel(self):
        # Caso real: «tstats … from datamodel=Malware.Malware_Attacks where sourcetype=*» no cubre todos los índices
        k = Knowledge(datamodels={"Malware": {"Malware_Attacks": {"parent": None, "constraints": ["index=cortex_xdr tag=malware"]}}})
        r = resolve_search("| tstats count from datamodel=Malware.Malware_Attacks where sourcetype=* by sourcetype", CTX, k)
        mode, covered = coverage(r, ["cortex_xdr", "hornet"], {"hornet:json": ["hornet"], "pan:xdr": ["cortex_xdr"]})
        self.assertEqual((mode, sorted(covered)), ("index", ["cortex_xdr"]))
        self.assertEqual(self.mode("sourcetype=* error"), ("default", {}))

    def test_filters_in_the_same_search_must_all_match(self):
        """index/sourcetype del CU y el data model se cumplen a la vez: no se suman."""
        k = Knowledge(datamodels={"Malware": {"Malware_Attacks": {"parent": None, "constraints": ["index=cortex_xdr"]}}})
        st = {"hornet:json": ["hornet"], "pan:xdr": ["cortex_xdr"]}
        names = ["cortex_xdr", "hornet"]
        for spl in ("| tstats count from datamodel=Malware where sourcetype=hornet:json",
                    "| tstats count from datamodel=Malware where index=hornet"):
            self.assertEqual(coverage(resolve_search(spl, CTX, k), names, st)[1], {}, spl)
        self.assertEqual(sorted(self.mode("index=fw sourcetype=pan:traffic",
                                          st={"pan:traffic": ["fw"], "linux": ["os"]})[1]), ["fw"])
        self.assertEqual(self.mode("index=fw sourcetype=sin_datos", st={"pan:traffic": ["fw"]})[1], {})

    def test_two_tags_must_both_match(self):
        k = Knowledge(eventtypes=[{"name": "fw", "app": "sirt", "owner": "nobody", "sharing": "app", "search": "index=fw"},
                                  {"name": "av", "app": "sirt", "owner": "nobody", "sharing": "app", "search": "index=av"}],
                      tags=[{"name": "eventtype=fw", "tags": ["network", "communicate"]},
                            {"name": "eventtype=av", "tags": ["malware"]}])
        r = resolve_search("tag=network tag=communicate", CTX, k)
        self.assertEqual(sorted(coverage(r, ["fw", "av"], {})[1]), ["fw"])
        self.assertEqual(sorted(coverage(resolve_search("tag=network tag=malware", CTX, k), ["fw", "av"], {})[1]), [])

    def test_or_and_in_add_up_and_are_reported(self):
        k = Knowledge(eventtypes=[{"name": "fw", "app": "sirt", "owner": "nobody", "sharing": "app", "search": "index=fw"}],
                      tags=[{"name": "eventtype=fw", "tags": ["network"]}, {"name": "sourcetype=av:log", "tags": ["malware"]}])
        st = {"linux": ["os"], "av:log": ["av"]}
        for spl in ("eventtype=fw OR sourcetype=linux", "(index=fw) OR (sourcetype=linux)", "tag IN (network, malware)"):
            r = resolve_search(spl, CTX, k)
            self.assertTrue(any("OR" in i for i in r["issues"]), spl)
            self.assertIn("fw", coverage(r, ["fw", "os", "av"], st)[1], spl)
        r = resolve_search("index=fw OR index=os sourcetype=linux", CTX, k)
        self.assertEqual(r["issues"], [])

    def test_broad_and_no_events(self):
        self.assertEqual(self.mode("index=* error"), ("all", {}))
        self.assertEqual(self.mode("error OR fail"), ("default", {}))
        self.assertEqual(self.mode("| inputlookup usuarios.csv | where x=1"), ("no_events", {}))


class TestAnalyze(unittest.TestCase):
    def test_statuses(self):
        searches = [
            alert("CU-FW-001-T1001 Firewall", "index=fw action=blocked"),
            alert("CU-AD-002-T1078 AD", "index=ad EventCode=4625", disabled=True),
            alert("TEST - CU-DNS-003-T1071 DNS", "index=dns"),
            alert("CU-ALL-004-T1000 Todo", "index=* error"),
            alert("Otra alerta", "index=proxy"),             # no es un CU: no cuenta
        ]
        rows = data(("fw", "pan", 10), ("ad", "win", 5), ("dns", "bind", 7), ("proxy", "squid", 3), ("backup", "veeam", 2),
                    ("summary", "stash", 9), ("legal", "x", 1))
        result = analyze(searches, indexes("fw", "ad", "dns", "proxy", "backup", "summary", "vacio", "legal"), rows, Knowledge(),
                         ignored=[{"index": "leg*", "reason": "Retención", "scope": "global"}])
        idx = by_name(result)
        self.assertEqual(idx["fw"]["status"], "used")
        self.assertEqual(idx["ad"]["status"], "inactive_only")
        self.assertEqual(idx["dns"]["status"], "inactive_only")
        self.assertEqual(idx["proxy"]["status"], "unused")
        self.assertEqual(idx["backup"]["status"], "unused")
        self.assertEqual(idx["legal"]["status"], "ignored")
        self.assertEqual(idx["legal"]["ignored"]["reason"], "Retención")
        self.assertNotIn("summary", idx)
        self.assertEqual(result["excluded"], ["summary"])
        self.assertEqual(result["without_data"], ["vacio"])
        s = result["summary"]
        self.assertEqual((s["unused"], s["inactive_only"], s["used"], s["ignored"], s["broad_alerts"]), (2, 2, 1, 1, 1))
        self.assertEqual(s["alerts"], 4)
        # Los no cubiertos van primero, de más a menos eventos
        self.assertEqual([r["name"] for r in result["indexes"]][:2], ["proxy", "backup"])


class TestIgnoredFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(ic, "ignored_path", lambda: os.path.join(self.tmp.name, ic.IGNORED_FILE))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_roundtrip_and_rules(self):
        self.assertEqual(ic.load_ignored("A"), {"global": [], "client": []})
        ic.save_ignored("*", ["archivo_*", {"index": "ARCHIVO_*"}])      # duplicado sin distinguir mayúsculas
        ic.save_ignored("A", [{"index": "legal", "reason": "Cumplimiento"}])
        self.assertEqual(ic.ignored_rules("A"), [{"index": "legal", "reason": "Cumplimiento", "scope": "client"},
                                                 {"index": "archivo_*", "reason": "", "scope": "global"}])
        ic.rename_client("A", "B")
        self.assertEqual(ic.load_ignored("A")["client"], [])
        self.assertEqual(ic.load_ignored("B")["client"][0]["index"], "legal")
        ic.save_ignored("B", [])
        with open(ic.ignored_path(), encoding="utf-8") as f:
            self.assertEqual(list(json.load(f)), ["*"])

    def test_invalid_pattern(self):
        with self.assertRaises(ValueError):
            ic.save_ignored("A", ["index=x"])


if __name__ == "__main__":
    unittest.main()


# --- Extremo a extremo con un Splunk simulado (web/index_coverage.py, API y CLI) -------------------------------

import io
from contextlib import redirect_stdout

from tests.fakes import FakeSavedSearch, FakeService


class _Body:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class _Response:
    def __init__(self, payload):
        self.body = _Body(payload)


class SplunkStub(FakeService):
    """FakeService con los endpoints REST que usa la cobertura de índices y un tstats simulado."""

    def __init__(self, searches, rest, data_rows):
        super().__init__(items=searches)
        self.rest = rest
        self.oneshots = []
        stub = self

        class Jobs:
            def oneshot(self, spl, **params):
                stub.oneshots.append((spl, params))
                return _Body({"results": data_rows})

        class Indexes:
            def list(self, **kw):
                return []

        self.jobs, self.indexes = Jobs(), Indexes()

    def get(self, path, **query):
        if path not in self.rest:
            from splunklib.binding import HTTPError
            error = HTTPError.__new__(HTTPError)
            error.status = 404
            raise error
        return _Response({"entry": self.rest[path]})


def _entry(name, app="search", sharing="global", **content):
    return {"name": name, "acl": {"app": app, "owner": "nobody", "sharing": sharing}, "content": content}


def stub_service():
    searches = [
        FakeSavedSearch("CU-AUTH-001-T1110 Fuerza bruta", app="sirt",
                        search="| tstats count from datamodel=Authentication.Failed_Authentication by user"),
        FakeSavedSearch("CU-FW-002-T1046 Escaneo", app="sirt", search="`fw_idx` action=blocked | stats dc(dest_port) by src"),
        FakeSavedSearch("TEST - CU-DNS-003-T1071 DNS", app="sirt", search="index=dns"),
        FakeSavedSearch("Informe semanal", app="sirt", search="index=proxy"),
    ]
    rest = {
        "configs/conf-macros": [_entry("fw_idx", app="sirt", sharing="app", definition="index=firewall"),
                                _entry("cim_Authentication_indexes", definition="()")],
        "saved/eventtypes": [_entry("win_auth", search="index=wineventlog EventCode=4625"),
                             _entry("nix_auth", search="sourcetype=linux_secure")],
        "configs/conf-tags": [_entry("eventtype=win_auth", authentication="enabled"),
                              _entry("eventtype=nix_auth", authentication="enabled", **{"eai:appName": "TA-nix"})],
        "datamodel/model": [_entry("Authentication", **{"eai:data": AUTH_MODEL})],
        "cluster_blaster_indexes/sh_indexes_manager": [
            _entry(n, datatype="event") for n in ("wineventlog", "os", "firewall", "dns", "proxy", "o365", "summary")
        ] + [_entry("metricas", datatype="metric")],
    }
    rows = [{"index": i, "sourcetype": st, "count": n, "last": 1763370000}
            for i, st, n in (("wineventlog", "WinEventLog", 900), ("os", "linux_secure", 50), ("firewall", "pan:traffic", 4000),
                             ("dns", "bind", 30), ("proxy", "squid", 70), ("o365", "o365:management:activity", 120),
                             ("summary", "stash", 5))]
    return SplunkStub(searches, rest, rows)


class TestEndToEnd(unittest.TestCase):
    def setUp(self):
        from web import explorer
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for patcher in (mock.patch.object(ic, "ignored_path", lambda: os.path.join(self.tmp.name, ic.IGNORED_FILE)),
                        mock.patch.object(explorer, "CLOUD_INDEXES_PATH", "cluster_blaster_indexes/sh_indexes_manager")):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.service = stub_service()

    def test_analyze_client(self):
        from web.index_coverage import analyze_client, report_data
        ic.save_ignored("CLI", [{"index": "o365", "reason": "Lo revisa otro equipo"}])
        result = analyze_client(self.service, "CLI")
        status = {r["name"]: r["status"] for r in result["indexes"]}
        self.assertEqual(status, {"proxy": "unused", "dns": "inactive_only", "o365": "ignored", "firewall": "used",
                                  "wineventlog": "used", "os": "used"})
        self.assertEqual(result["metrics"], ["metricas"])
        self.assertEqual(result["excluded"], ["summary"])
        self.assertEqual(result["warnings"], [])
        spl, params = self.service.oneshots[0]
        self.assertIn("tstats", spl)
        self.assertEqual(params["earliest_time"], "-7d")
        data = report_data(result)
        self.assertEqual([r["name"] for r in data["uncovered"]], ["proxy", "dns"])
        self.assertEqual(data["uncovered"][1]["users_text"], ["TEST - CU-DNS-003-T1071 DNS [pruebas (test)]"])

    def test_api_routes(self):
        from web import services
        from web.app import create_app
        app = create_app(restrict_host=False)
        http = app.test_client()
        with mock.patch.object(services, "get_service", lambda name: self.service), \
                mock.patch.object(services, "find_client", lambda name: {"name": name}), \
                mock.patch("core.audit.record", lambda *a, **k: None):
            r = http.get("/api/index-coverage?client=CLI")
            self.assertEqual(r.status_code, 200, r.get_json())
            self.assertEqual(r.get_json()["summary"]["unused"], 2)
            r = http.put("/api/index-coverage/ignored", json={"client": "CLI", "scope": "global", "entries": [{"index": "proxy"}]},
                         headers={"X-Splunk-Manager": "1"})
            self.assertEqual(r.get_json()["global"], [{"index": "proxy", "reason": ""}])
            r = http.put("/api/index-coverage/ignored", json={"client": "CLI", "entries": ["mal patrón"]}, headers={"X-Splunk-Manager": "1"})
            self.assertEqual(r.status_code, 400)
            r = http.get("/api/index-coverage?client=CLI")
            self.assertEqual(r.get_json()["summary"]["unused"], 1)   # queda o365

    def test_cli_output(self):
        from options.alerts.index_coverage import show_index_coverage
        out = io.StringIO()
        with redirect_stdout(out):
            show_index_coverage(self.service, {"name": "CLI"})
        text = out.getvalue()
        self.assertIn("Índices sin ningún CU (2)", text)
        self.assertIn("- proxy: 70 eventos", text)
        self.assertIn("solo consultan CU deshabilitados o de pruebas (1)", text)
