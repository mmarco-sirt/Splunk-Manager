import base64
import contextlib
import io
import json
import os
import shutil
import tempfile
import types
import unittest
from unittest import mock

import core.paths
from core import acs, clients, splunkbase_account, vault
from options.addons import update_addons as addons
from options.batch.non_interactive import UsageError, resolve_splunkbase_account

CLIENT = {"name": "CLI", "host": "cliente.splunkcloud.com", "splunkToken": "jwt-token"}
LOGIN_XML = ('<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom">'
             '<title>Splunk Login</title><id>SESSION123</id></feed>')


def entry(name, version, new=None, appid=None, **extra):
    content = {"label": f"Label {name}", "version": version, **extra}
    if new:
        content.update({"update.version": new, "update.homepage": f"https://splunkbase.splunk.com/app/{appid}/",
                        "update.appurl": f"https://splunkbase.splunk.com/app/{appid}/release/{new}/download/"})
    return {"name": name, "content": content}


class FakeService:
    """service.get('apps/local') con la forma de la respuesta JSON de splunkd."""

    def __init__(self, entries):
        self.entries, self.host, self.calls = entries, "cliente.splunkcloud.com", []

    def get(self, path, **query):
        self.calls.append((path, query))
        return types.SimpleNamespace(body=io.BytesIO(json.dumps({"entry": self.entries}).encode("utf-8")))


class FakeHttp:
    """Sustituto de acs._http: responde según método y URL y registra cada petición."""

    def __init__(self, acs_apps=(), patch_responses=None, describe=None, login_status=200, license_url="https://lic"):
        self.calls = []
        self.acs_apps = {a["name"]: a for a in acs_apps}
        self.patch_responses = list(patch_responses or [])
        self.describe = list(describe or [])
        self.login_status = login_status
        self.license_url = license_url

    def __call__(self, method, url, headers=None, data=None, timeout=60, json_body=None, raw=False):
        self.calls.append({"method": method, "url": url, "headers": dict(headers or {}), "data": data, "json": json_body})
        if "account:login" in url:
            return self.login_status, LOGIN_XML if self.login_status == 200 else "denied"
        if "splunkbase.splunk.com/api/v1/app/" in url:
            return 200, json.dumps({"uid": 742, "appid": "x", "title": "X", "license_name": "sgt",
                                    "license_url": self.license_url})
        if method == "GET" and "apps/victoria?" in url:
            return 200, json.dumps({"apps": list(self.acs_apps.values())})
        if method == "GET" and "/apps/victoria/" in url:
            if self.describe:
                return 200, json.dumps(self.describe.pop(0))
            return 200, json.dumps(self.acs_apps.get(url.rsplit("/", 1)[1], {}))
        if method == "PATCH":
            if self.patch_responses:
                return self.patch_responses.pop(0)
            return 200, json.dumps({"appID": "x", "status": "installed", "version": (data or {}).get("version", "")})
        return 404, "{}"

    def patches(self):
        return [c for c in self.calls if c["method"] == "PATCH"]


def acs_app(name, version, splunkbase_id=None):
    app = {"appID": name, "name": name, "label": name, "status": "installed", "version": version}
    if splunkbase_id:
        app["splunkbaseID"] = str(splunkbase_id)
    return app


class TestPendingUpdates(unittest.TestCase):
    def test_stack_name(self):
        self.assertEqual(acs.stack_name("cliente.splunkcloud.com"), "cliente")
        self.assertEqual(acs.stack_name("ES.Cliente.splunkcloud.com:8089"), "es.cliente")
        self.assertIsNone(acs.stack_name("172.16.110.177"))
        self.assertIsNone(acs.stack_name(""))
        self.assertEqual(acs.unavailable_reason({"host": "x.splunkcloud.com"}),
                         "no tiene token: ACS necesita el token JWT de un usuario con el rol sc_admin")
        self.assertIsNone(acs.unavailable_reason(CLIENT))

    def test_expired_token_is_reported(self):
        def jwt(exp):
            payload = base64.urlsafe_b64encode(json.dumps({"sub": "u", "exp": exp}).encode()).decode().rstrip("=")
            return f"eyJhbGciOiJIUzI1NiJ9.{payload}.firma"
        self.assertEqual(acs.token_expiry(jwt(1700000000)), 1700000000)
        self.assertIsNone(acs.token_expiry("no-es-jwt"))
        self.assertIsNone(acs.token_expiry("a.b.c"))
        expired = {"host": "x.splunkcloud.com", "splunkToken": jwt(1700000000)}
        self.assertIn("el token caducó el", acs.unavailable_reason(expired, now=1800000000))
        self.assertIsNone(acs.unavailable_reason(expired, now=1600000000))
        self.assertIsNone(acs.unavailable_reason({"host": "x.splunkcloud.com", "splunkToken": "opaco"}))

    def test_reads_update_fields_from_apps_local(self):
        service = FakeService([entry("search", "1.0"), entry("Splunk_TA_windows", "11.0.3", "12.0.0", 742),
                               entry("TA-cyences", "1.1.1", "1.1.4", 5659, disabled="1")])
        pending = acs.pending_updates(service)
        self.assertEqual([(a["name"], a["version"], a["new_version"], a["splunkbase_id"], a["major"], a["disabled"])
                          for a in pending],
                         [("Splunk_TA_windows", "11.0.3", "12.0.0", "742", True, False),
                          ("TA-cyences", "1.1.1", "1.1.4", "5659", False, True)])
        self.assertEqual(service.calls, [("apps/local", {"output_mode": "json", "count": 0})])

    def test_select_by_name_label_wildcard_or_all(self):
        pending = [{"name": "Splunk_TA_windows", "label": "Splunk Add-on for Microsoft Windows"},
                   {"name": "TA-cyences", "label": "Cyences Add-on"}, {"name": "Splunk_SA_CIM", "label": "CIM"}]
        chosen, missing = addons.select_pending(pending, "splunk_ta_windows, TA-*, otra")
        self.assertEqual([a["name"] for a in chosen], ["Splunk_TA_windows", "TA-cyences"])
        self.assertEqual(missing, ["otra"])
        self.assertEqual(len(addons.select_pending(pending, ["todos"])[0]), 3)
        self.assertEqual([a["name"] for a in addons.select_pending(pending, "cim")[0]], ["Splunk_SA_CIM"])

    def test_splunkbase_login(self):
        with mock.patch.object(acs, "_http", FakeHttp()):
            self.assertEqual(acs.splunkbase_login("u", "p"), "SESSION123")
        with mock.patch.object(acs, "_http", FakeHttp(login_status=401)):
            with self.assertRaises(acs.AcsError):
                acs.splunkbase_login("u", "p")
        with self.assertRaises(acs.AcsError):
            acs.splunkbase_login("u", "")

    def test_license_url_from_acs_error(self):
        message = ('ACS PATCH apps/victoria/x: HTTP 400. Invalid license. Expected "https://opensource.org/licenses/ISC". '
                   'See https://docs.splunk.com/x')
        self.assertEqual(acs._license_from_error(message, "https://lic"), "https://opensource.org/licenses/ISC")
        self.assertIsNone(acs._license_from_error("HTTP 400. bad request", "https://lic"))


class TestUpdateAddons(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="sm_addons_")
        self.patch_root = mock.patch.object(core.paths, "BACKUP_ROOT", self.folder)
        self.patch_root.start()
        self.output = []

    def tearDown(self):
        self.patch_root.stop()
        shutil.rmtree(self.folder, ignore_errors=True)

    def service(self):
        return FakeService([entry("search", "1.0"), entry("Splunk_TA_windows", "11.0.3", "12.0.0", 742),
                            entry("Splunk_SA_CIM", "8.7.0", "8.8.0", 1621), entry("phantom", "4.4.1", "8.7.1", 3411)])

    def acs_apps(self):
        return [acs_app("Splunk_TA_windows", "11.0.3", 742), acs_app("Splunk_SA_CIM", "8.7.0", 1621),
                acs_app("phantom", "4.4.1")]   # phantom: sin splunkbaseID, no gestionable

    def run_update(self, http, names, **kwargs):
        with mock.patch.object(acs, "_http", http):
            return addons.update_addons(self.service(), CLIENT, names, account={"username": "u", "password": "p"},
                                        log=self.output.append, sleep=lambda s: None, **kwargs)

    def test_dry_run_only_lists(self):
        http = FakeHttp(self.acs_apps())
        result = self.run_update(http, "todos", dry_run=True)
        self.assertEqual(result["updated"], ["Splunk_SA_CIM", "Splunk_TA_windows"])
        self.assertEqual(result["skipped"], ["phantom"])
        self.assertEqual(http.patches(), [])
        self.assertFalse(any("account:login" in c["url"] for c in http.calls))
        self.assertTrue(any(line.startswith("[SIMULACIÓN]") for line in self.output))
        self.assertTrue(any("cambio de versión mayor" in line for line in self.output))

    def test_updates_one_by_one_with_acs_headers_and_waits(self):
        http = FakeHttp(self.acs_apps(),
                        patch_responses=[(200, json.dumps({"status": "processing", "version": "8.7.0"}))],
                        describe=[{"name": "Splunk_SA_CIM", "status": "processing", "version": "8.7.0"},
                                  {"name": "Splunk_SA_CIM", "status": "installed", "version": "8.8.0"}])
        result = self.run_update(http, "Splunk_SA_CIM,Splunk_TA_windows")
        self.assertEqual(result, {"updated": ["Splunk_SA_CIM", "Splunk_TA_windows"], "failed": [], "skipped": [],
                                  "missing": [], "cancelled": []})
        patches = http.patches()
        self.assertEqual([p["url"] for p in patches],
                         ["https://admin.splunk.com/cliente/adminconfig/v2/apps/victoria/Splunk_SA_CIM",
                          "https://admin.splunk.com/cliente/adminconfig/v2/apps/victoria/Splunk_TA_windows"])
        self.assertEqual([p["data"] for p in patches], [{"version": "8.8.0"}, {"version": "12.0.0"}])
        for p in patches:
            self.assertEqual(p["headers"]["Authorization"], "Bearer jwt-token")
            self.assertEqual(p["headers"]["X-Splunkbase-Authorization"], "SESSION123")
            self.assertEqual(p["headers"]["ACS-Licensing-Ack"], "https://lic")
        self.assertEqual(sum(1 for c in http.calls if "account:login" in c["url"]), 1)
        self.assertEqual(http.describe, [])   # esperó hasta ver 'installed' con la versión nueva
        backup = os.path.join(self.folder, "addons", "addons_cliente_backup.json")
        with open(backup, encoding="utf-8") as f:
            saved = {a["name"]: a["version"] for a in json.load(f)}
        self.assertEqual(saved["Splunk_SA_CIM"], "8.7.0")

    def test_retries_with_the_license_acs_asks_for(self):
        http = FakeHttp(self.acs_apps(), patch_responses=[
            (400, json.dumps({"code": "400-bad-request",
                              "message": "License not acknowledged. Use https://www.splunk.com/legal/terms.html"}))])
        result = self.run_update(http, "Splunk_SA_CIM")
        self.assertEqual(result["updated"], ["Splunk_SA_CIM"])
        self.assertEqual([p["headers"]["ACS-Licensing-Ack"] for p in http.patches()],
                         ["https://lic", "https://www.splunk.com/legal/terms.html"])

    def test_token_problem_cancels_the_rest(self):
        http = FakeHttp(self.acs_apps(), patch_responses=[(403, json.dumps({"message": "forbidden"}))])
        result = self.run_update(http, "todos")
        self.assertEqual(result["failed"], ["Splunk_SA_CIM", "Splunk_TA_windows"])
        self.assertEqual(result["skipped"], ["phantom"])
        self.assertEqual(len(http.patches()), 1)
        self.assertTrue(any("sc_admin" in line for line in self.output))

    def test_other_errors_continue_with_the_next_app(self):
        http = FakeHttp(self.acs_apps(), patch_responses=[(409, json.dumps({"message": "busy"}))])
        result = self.run_update(http, "Splunk_SA_CIM,Splunk_TA_windows")
        self.assertEqual((result["failed"], result["updated"]), (["Splunk_SA_CIM"], ["Splunk_TA_windows"]))
        self.assertEqual(len(http.patches()), 2)

    def test_without_token_nothing_is_sent(self):
        http = FakeHttp(self.acs_apps())
        with mock.patch.object(acs, "_http", http):
            result = addons.update_addons(self.service(), {"name": "X", "host": "cliente.splunkcloud.com"}, "todos",
                                          account={"username": "u", "password": "p"}, log=self.output.append)
        self.assertEqual(result["failed"], ["phantom", "Splunk_SA_CIM", "Splunk_TA_windows"])
        self.assertEqual(http.calls, [])

    def test_backup_uses_the_full_stack_name(self):
        service = self.service()
        service.host = "es.cliente.splunkcloud.com"
        with mock.patch.object(acs, "_http", FakeHttp(self.acs_apps())):
            path = addons.backup_versions(service, log=self.output.append)
        self.assertEqual(os.path.basename(path), "addons_es.cliente_backup.json")

    def test_missing_names_are_reported(self):
        http = FakeHttp(self.acs_apps())
        result = self.run_update(http, "no_existe")
        self.assertEqual(result, {"updated": [], "failed": [], "skipped": [], "missing": ["no_existe"], "cancelled": []})
        self.assertEqual(http.patches(), [])

    def test_cancel_between_apps_and_progress(self):
        http = FakeHttp(self.acs_apps())
        progress, stops = [], iter([False, True])
        result = self.run_update(http, "Splunk_SA_CIM,Splunk_TA_windows", stop=lambda: next(stops),
                                 progress=lambda done, total, label: progress.append((done, total, label)))
        self.assertEqual((result["updated"], result["cancelled"]), (["Splunk_SA_CIM"], ["Splunk_TA_windows"]))
        self.assertEqual(len(http.patches()), 1)
        self.assertEqual(progress, [(0, 2, "Splunk_SA_CIM"), (1, 2, "")])
        self.assertTrue(any(line.startswith("⏹ Cancelado") for line in self.output))

    def test_pending_with_acs_reports_warnings_instead_of_printing(self):
        pending, warnings = addons.pending_with_acs(self.service(), {"name": "X", "host": "cliente.splunkcloud.com"})
        self.assertEqual([a["acs"] for a in pending], [None, None, None])
        self.assertEqual(len(warnings), 1)
        self.assertIn("no tiene token", warnings[0])


class TestSplunkbaseAccount(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="sm_cfg_")
        self.config = os.path.join(self.folder, "config.py")
        with open(self.config, "w", encoding="utf-8") as f:
            f.write('# comentario\nclientes = [\n    {"name": "A", "host": "a.splunkcloud.com", "splunkToken": "dpapi:tok"},\n]\n')
        self.patches = [mock.patch.object(splunkbase_account, "CONFIG_PATH", self.config),
                        mock.patch.object(clients, "CONFIG_PATH", self.config),
                        mock.patch.object(vault, "encrypt", lambda t: t if t.startswith("dpapi:") else "dpapi:" + t[::-1]),
                        mock.patch.object(vault, "decrypt", lambda v: v[len("dpapi:"):][::-1] if v.startswith("dpapi:") else v),
                        mock.patch.object(vault, "available", lambda: True),
                        mock.patch.dict(os.environ, {}, clear=False)]
        for p in self.patches:
            p.start()
        os.environ.pop(splunkbase_account.ENV_USERNAME, None)
        os.environ.pop(splunkbase_account.ENV_PASSWORD, None)
        clients._cache["key"] = None

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        clients._cache["key"] = None
        shutil.rmtree(self.folder, ignore_errors=True)

    def read(self):
        with open(self.config, encoding="utf-8") as f:
            return f.read()

    def test_save_get_replace_and_delete(self):
        self.assertIsNone(splunkbase_account.get_account())
        splunkbase_account.save_account("usuario@empresa.com", "secreta")
        text = self.read()
        self.assertIn("# comentario", text)
        self.assertIn('"name": "A"', text)
        self.assertEqual(text.count("splunkbase = {"), 1)
        self.assertNotIn("secreta", text)
        self.assertEqual(splunkbase_account.get_account(),
                         {"username": "usuario@empresa.com", "password": "secreta", "source": "config.py"})
        splunkbase_account.save_account("otro", "clave2")
        self.assertEqual(self.read().count("splunkbase = {"), 1)
        self.assertEqual(splunkbase_account.get_account()["username"], "otro")
        splunkbase_account.delete_account()
        self.assertNotIn("splunkbase", self.read())
        self.assertIsNone(splunkbase_account.get_account())

    def test_plain_password_is_encrypted_and_survives_client_saves(self):
        with open(self.config, "a", encoding="utf-8") as f:
            f.write('\nsplunkbase = {"username": "u", "password": "enclaro"}\n')
        self.assertTrue(splunkbase_account.encrypt_if_plain())
        self.assertNotIn("enclaro", self.read())
        self.assertIsNone(splunkbase_account.encrypt_if_plain())
        with contextlib.redirect_stdout(io.StringIO()):
            clients.save_clients_to_config(clients.get_clients_from_config() + [{"name": "B", "host": "b.splunkcloud.com"}])
        self.assertIn('"name": "B"', self.read())
        self.assertEqual(splunkbase_account.get_account()["password"], "enclaro")

    def test_environment_wins(self):
        os.environ[splunkbase_account.ENV_USERNAME] = "env-user"
        os.environ[splunkbase_account.ENV_PASSWORD] = "env-pass"
        self.assertEqual(splunkbase_account.get_account()["source"], "entorno")


class TestNonInteractiveAccount(unittest.TestCase):
    def test_user_argument_needs_the_password_variable(self):
        args = types.SimpleNamespace(splunkbase_usuario="user")
        with self.assertRaises(UsageError):
            resolve_splunkbase_account(args, environ={})
        self.assertEqual(resolve_splunkbase_account(args, environ={"SPLUNKBASE_PASSWORD": "p"}),
                         {"username": "user", "password": "p", "source": "argumentos"})

    def test_without_any_account_is_a_usage_error(self):
        args = types.SimpleNamespace(splunkbase_usuario=None)
        with mock.patch("options.batch.non_interactive.get_account", lambda: None):
            with self.assertRaises(UsageError):
                resolve_splunkbase_account(args, environ={})
        with mock.patch("options.batch.non_interactive.get_account", lambda: {"username": "u", "password": "p", "source": "config.py"}):
            self.assertEqual(resolve_splunkbase_account(args, environ={})["username"], "u")


if __name__ == "__main__":
    unittest.main()
