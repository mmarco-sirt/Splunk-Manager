"""Funciones puras de la parte nueva de ACS: HEC, listas de IP, tokens, mantenimiento, auditoría y avisos de salud."""
import json
import os
import shutil
import tempfile
import time
import unittest
from datetime import date
from unittest import mock

import core.audit
from core import acs, audit
from web import access, hec
from web.health import platform_problems
from web.platform import token_level

CLIENT = {"name": "CLI", "host": "es.cliente.splunkcloud.com", "splunkToken": "jwt"}


class FakeHttp:
    def __init__(self, responses):
        self.calls = []
        self.responses = list(responses)

    def __call__(self, method, url, headers=None, data=None, timeout=60, json_body=None, raw=False):
        self.calls.append({"method": method, "url": url, "headers": headers or {}, "json": json_body, "raw": raw})
        status, body = self.responses.pop(0)
        return status, body if isinstance(body, (str, bytes)) else json.dumps(body)


class TestSubnetsAndHec(unittest.TestCase):
    def test_normalize_subnets(self):
        self.assertEqual(acs.normalize_subnets("10.0.0.1, 10.0.0.0/24 10.0.0.1"), ["10.0.0.1/32", "10.0.0.0/24"])
        self.assertEqual(acs.normalize_subnets(["fe80::1"], v6=True), ["fe80::1/128"])
        with self.assertRaises(acs.AcsError):
            acs.normalize_subnets(["no-es-ip"])
        with self.assertRaises(acs.AcsError):
            acs.normalize_subnets(["10.0.0.0/24"], v6=True)

    def test_hec_row_and_spec_roundtrip(self):
        item = {"spec": {"name": "t1", "defaultIndex": "main", "allowedIndexes": ["b", "a"], "disabled": "true", "useACK": True,
                         "_meta": "k::v"}, "token": "abc"}
        row = acs.hec_row(item)
        self.assertEqual((row["name"], row["token"], row["disabled"], row["use_ack"], row["allowed_indexes"], row["meta"]),
                         ("t1", "abc", True, True, ["a", "b"], "k::v"))
        spec = acs.hec_spec(row, include_token=True)
        self.assertEqual(spec, {"name": "t1", "defaultIndex": "main", "defaultHost": "", "defaultSource": "", "defaultSourcetype": "",
                                "_meta": "k::v", "allowedIndexes": ["a", "b"], "disabled": True, "useACK": True, "token": "abc"})
        # Solo los campos presentes (PATCH)
        self.assertEqual(acs.hec_spec({"name": "t1", "disabled": False}), {"name": "t1", "disabled": False})

    def test_hec_annotate_warnings(self):
        rows = [acs.hec_row({"spec": {"name": "a", "defaultIndex": "falta", "allowedIndexes": ["main", "nope"], "disabled": True}}),
                acs.hec_row({"spec": {"name": "b", "defaultIndex": "off"}}),
                acs.hec_row({"spec": {"name": "c", "defaultIndex": "main", "allowedIndexes": ["other"]}})]
        hec.annotate(rows, {"main": False, "off": True, "other": False})
        self.assertEqual(rows[0]["warnings"], ["el índice por defecto «falta» no existe", "índices permitidos que no existen: nope",
                                               "el índice por defecto no está entre los permitidos", "token deshabilitado"])
        self.assertEqual(rows[1]["warnings"], ["el índice por defecto «off» está deshabilitado"])
        self.assertEqual(rows[2]["warnings"], ["el índice por defecto no está entre los permitidos"])

    def test_create_hec_sends_json_and_dry_run_sends_nothing(self):
        http = FakeHttp([(202, {"http-event-collector": {"spec": {"name": "t"}}})])
        with mock.patch.object(acs, "_http", http):
            acs.create_hec(CLIENT, {"name": "t", "default_index": "main", "token": "xyz"})
            dry = acs.create_hec(CLIENT, {"name": "t", "default_index": "main"}, dry_run=True)
        self.assertEqual(len(http.calls), 1)
        call = http.calls[0]
        self.assertEqual((call["method"], call["json"]), ("POST", {"name": "t", "defaultIndex": "main", "token": "xyz"}))
        self.assertTrue(call["url"].endswith("/es.cliente/adminconfig/v2/inputs/http-event-collectors"))
        self.assertEqual(call["headers"]["Authorization"], "Bearer jwt")
        self.assertTrue(dry["dry_run"])
        self.assertEqual(dry["request"]["body"]["name"], "t")
        with self.assertRaises(acs.AcsError):
            acs.create_hec(CLIENT, {"name": "con espacio", "default_index": "main"})

    def test_wait_for_hec(self):
        answers = iter([(404, {"code": "404-hec-not-found"}), (200, {"http-event-collector": {"spec": {"name": "t", "disabled": False}, "token": "v"}})])
        http = FakeHttp([])
        http.responses = list(answers)
        sleeps = []
        with mock.patch.object(acs, "_http", http):
            row = acs.wait_for_hec(CLIENT, "t", sleep=sleeps.append, interval=1)
        self.assertEqual(row["token"], "v")
        self.assertEqual(sleeps, [1])

    def test_list_tokens_accepts_list_or_object(self):
        token = {"id": "1", "user": "u", "audience": "a", "status": "enabled", "expiresOn": "2030-01-01T00:00:00Z"}
        for body in ([token], {"tokens": [token]}):
            with mock.patch.object(acs, "_http", FakeHttp([(200, body)])):
                rows = acs.list_tokens(CLIENT)
            self.assertEqual(rows[0]["user"], "u")
            self.assertEqual(rows[0]["expires_on"], acs.parse_time("2030-01-01T00:00:00Z"))

    def test_create_token_uses_basic_auth(self):
        http = FakeHttp([(200, {"tokeninfo": {"id": "9", "token": "SECRET", "user": "u", "audience": "a", "expiresOn": "2030-01-01T00:00:00Z",
                                              "status": "enabled"}})])
        with mock.patch.object(acs, "_http", http):
            row = acs.create_token({"name": "C", "host": "c.splunkcloud.com"}, "admin", "pw", "u", "a", "+90d")
        self.assertEqual(row["token"], "SECRET")
        self.assertTrue(http.calls[0]["headers"]["Authorization"].startswith("Basic "))
        self.assertEqual(http.calls[0]["json"], {"user": "u", "audience": "a", "expiresOn": "+90d", "type": "static"})
        with self.assertRaises(acs.AcsError):
            acs.create_token(CLIENT, "admin", "pw", "u", "a", "mañana")

    def test_allowlist_feature_checks(self):
        with self.assertRaises(acs.AcsError):
            acs.list_allowlist(CLIENT, "federated-search", v6=False)
        with self.assertRaises(acs.AcsError):
            acs.list_allowlist(CLIENT, "inventada")
        with mock.patch.object(acs, "_http", FakeHttp([(200, {"subnets": ["10.0.0.0/8", "1.1.1.1/32"]})])):
            self.assertEqual(acs.list_allowlist(CLIENT, "s2s"), ["1.1.1.1/32", "10.0.0.0/8"])

    def test_export_app_requires_gzip(self):
        with mock.patch.object(acs, "_http", FakeHttp([(200, b"\x1f\x8b\x08rest")])):
            self.assertEqual(acs.export_app(CLIENT, "app")[:2], b"\x1f\x8b")
        with mock.patch.object(acs, "_http", FakeHttp([(200, b'{"code":"400"}')])):
            with self.assertRaises(acs.AcsError):
                acs.export_app(CLIENT, "app")


class TestMaintenanceAndTokens(unittest.TestCase):
    def test_parse_time_and_windows(self):
        self.assertEqual(acs.parse_time("2024-08-21T04:00:00Z"), 1724212800.0)
        self.assertIsNone(acs.parse_time("ayer"))
        schedules = [{"start": 100, "end": 200}, {"start": 300, "end": 400}]
        self.assertEqual(acs.next_window(schedules, now=250), schedules[1])
        self.assertIsNone(acs.next_window(schedules, now=500))

    def test_active_freezes(self):
        freezes = [{"start": "2026/11/01", "end": "2026/11/30"}, {"start": "2026/12/01", "end": "2026/12/02"}, {"start": "x", "end": "y"}]
        self.assertEqual(acs.active_freezes(freezes, today=date(2026, 11, 15)), [freezes[0]])

    def test_token_claims_and_level(self):
        import base64
        payload = base64.urlsafe_b64encode(json.dumps({"sub": "u", "aud": "API", "exp": 2000, "iat": 1000, "jti": "id1"}).encode()).decode().rstrip("=")
        token = f"h.{payload}.s"
        self.assertEqual(acs.token_claims(token), {"sub": "u", "aud": "API", "exp": 2000, "iat": 1000, "jti": "id1"})
        self.assertEqual(acs.token_claims("no-jwt"), {})
        now = 1_000_000
        self.assertEqual(token_level(now - 1, now), "expired")
        self.assertEqual(token_level(now + 3 * 86400, now), "serious")
        self.assertEqual(token_level(now + 20 * 86400, now), "warning")
        self.assertEqual(token_level(now + 60 * 86400, now), "ok")
        self.assertIsNone(token_level(None, now))

    def test_client_platform(self):
        info = acs.client_platform(CLIENT, {"instance_type": "cloud", "version": "10"})
        self.assertEqual((info["kind"], info["stack"], info["acs"]), ("cloud", "es.cliente", True))
        info = acs.client_platform({"name": "E", "host": "splunk.local", "username": "a", "password": "b"}, {"version": "9.2", "instance_type": ""})
        self.assertEqual((info["kind"], info["acs"]), ("enterprise", False))
        self.assertIn("no es un stack", info["reason"])


class TestHealthPlatformProblems(unittest.TestCase):
    def test_levels(self):
        now = 1_000_000
        base = {"token_expiry": None, "restart_required": False, "stack_status": "Ready", "pending_addons": 0, "next_window": None,
                "active_freezes": [], "errors": {}}
        self.assertEqual(platform_problems(base, now), [])
        problems = platform_problems({**base, "token_expiry": now - 10, "restart_required": True, "pending_addons": 2,
                                      "next_window": {"start": now + 3600, "type": "Upgrade", "zero_downtime": True},
                                      "active_freezes": [{"end": "2026/12/01", "reason": "cierre"}], "stack_status": "Degraded",
                                      "errors": {"windows": "HTTP 403", "addons": "x"}}, now)
        levels = [p[0] for p in problems]
        self.assertEqual(levels, ["critical", "warning", "serious", "info", "info", "info", "info"])
        self.assertIn("caducó", problems[0][1])
        self.assertIn("ventanas de mantenimiento", problems[-1][1])
        soon = platform_problems({**base, "token_expiry": now + 2 * 86400}, now)
        self.assertEqual(soon[0][0], "serious")


class TestAccessChange(unittest.TestCase):
    def test_validate_and_describe(self):
        target, subnets, v6, reason = access._validate("allowlist", {"feature": "hec", "subnets": "10.1.1.1, 10.2.0.0/16"})
        self.assertEqual((target, subnets, v6), ("hec", ["10.1.1.1/32", "10.2.0.0/16"], False))
        port, subnets, v6, reason = access._validate("outbound", {"port": "443", "subnets": ["1.1.1.1"], "reason": "webhook", "v6": False})
        self.assertEqual((port, reason), (443, "webhook"))
        self.assertIn("Añadir 2 subredes a la lista de HTTP Event Collector", access._describe("allowlist", "add", "hec", subnets + ["x"], False))
        self.assertIn("Cerrar el puerto de salida 443 (IPv6)", access._describe("outbound", "remove", 443, subnets, True))
        with self.assertRaises(Exception):
            access._validate("allowlist", {"feature": "nada", "subnets": ["1.1.1.1"]})


class TestAudit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._logs = core.audit.LOGS_ROOT
        core.audit.LOGS_ROOT = self.tmp
        self._dir = core.audit.logs_dir
        core.audit.logs_dir = lambda: self.tmp

    def tearDown(self):
        core.audit.LOGS_ROOT = self._logs
        core.audit.logs_dir = self._dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_record_read_filter_and_csv(self):
        audit.set_source("web")
        audit.record("hec.create", "Crear token", "A", {"spec": {"token": "SECRET", "name": "t"}})
        audit.record("alert.update", "Editar «CU-1»", ["A", "B"], result="error")
        audit.record("job.backup", "Backup", "B", dry_run=True)
        entries = audit.read()
        self.assertEqual([e["action"] for e in entries], ["job.backup", "alert.update", "hec.create"])
        self.assertEqual(entries[2]["detail"]["spec"]["token"], "***")
        self.assertEqual(entries[0]["result"], "simulado")
        self.assertEqual([e["title"] for e in audit.read(client="A")], ["Editar «CU-1»", "Crear token"])
        self.assertEqual(len(audit.read(action="hec")), 1)
        self.assertEqual(len(audit.read(text="cu-1")), 1)
        self.assertEqual(audit.actions(), ["alert.update", "hec.create", "job.backup"])
        csv_text = audit.to_csv(entries)
        self.assertIn("Fecha;Origen;Usuario;Cliente", csv_text)
        self.assertIn("A, B", csv_text)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, audit.FILE_NAME)))


if __name__ == "__main__":
    unittest.main()
