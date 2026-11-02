import unittest
from unittest import mock

from web import addons
from web.services import ClientError

CLIENT = {"name": "CLI", "host": "cliente.splunkcloud.com", "splunkToken": "jwt"}
ACCOUNT = {"username": "u", "password": "p", "source": "config.py"}


class TestAccountStatus(unittest.TestCase):
    def test_without_account(self):
        with mock.patch.object(addons, "get_account", lambda: None), mock.patch.object(addons, "saved_account", lambda: None):
            self.assertEqual(addons.account_status(),
                             {"configured": False, "username": "", "source": None, "saved": False, "problem": None})

    def test_saved_but_not_decryptable(self):
        saved = {"username": "u", "password": "", "problem": "otro usuario de Windows"}
        with mock.patch.object(addons, "get_account", lambda: None), mock.patch.object(addons, "saved_account", lambda: saved):
            status = addons.account_status()
        self.assertEqual((status["configured"], status["saved"], status["username"], status["problem"]),
                         (False, True, "u", "otro usuario de Windows"))

    def test_configured_never_includes_the_password(self):
        saved = {"username": "u", "password": "p", "problem": None}
        with mock.patch.object(addons, "get_account", lambda: ACCOUNT), mock.patch.object(addons, "saved_account", lambda: saved):
            status = addons.account_status()
        self.assertEqual(status, {"configured": True, "username": "u", "source": "config.py", "saved": True, "problem": None})
        self.assertNotIn("password", status)

    def test_store_checks_the_account_before_saving(self):
        saved = []
        with mock.patch.object(addons.acs, "splunkbase_login", lambda u, p: "SESSION"), \
                mock.patch.object(addons, "save_account", lambda u, p: saved.append((u, p))), \
                mock.patch.object(addons, "account_status", lambda: {"configured": True}):
            addons.store_account({"username": " u ", "password": "p"})
        self.assertEqual(saved, [("u", "p")])

        def refuse(u, p):
            raise addons.acs.AcsError("Splunkbase rechazó el inicio de sesión")
        with mock.patch.object(addons.acs, "splunkbase_login", refuse), \
                mock.patch.object(addons, "save_account", lambda u, p: saved.append((u, p))):
            with self.assertRaises(ClientError):
                addons.store_account({"username": "u", "password": "mal"})
            addons.store_account({"username": "u2", "password": "p2", "test": False})   # sin comprobar
        self.assertEqual(saved[1:], [("u2", "p2")])
        with self.assertRaises(ClientError):
            addons.store_account({"username": "", "password": "p"})


class TestBuildUpdate(unittest.TestCase):
    def test_requires_names_token_and_account(self):
        with mock.patch.object(addons.services, "find_client", lambda name: dict(CLIENT)):
            with self.assertRaises(ClientError):
                addons.build_update("CLI", [])
            with mock.patch.object(addons, "get_account", lambda: None):
                with self.assertRaises(ClientError):
                    addons.build_update("CLI", ["Splunk_TA_windows"])
        with mock.patch.object(addons.services, "find_client", lambda name: {"name": "CLI", "host": "cliente.splunkcloud.com"}):
            with self.assertRaises(ClientError):
                addons.build_update("CLI", ["Splunk_TA_windows"])

    def test_action_updates_with_cancel_and_progress_hooks(self):
        calls = []

        def fake_update(service, client, names, **kwargs):
            calls.append((service, client["name"], names, kwargs))
        with mock.patch.object(addons.services, "find_client", lambda name: dict(CLIENT)), \
                mock.patch.object(addons.services, "get_service", lambda name: "SERVICE"), \
                mock.patch.object(addons, "get_account", lambda: ACCOUNT), \
                mock.patch.object(addons, "update_addons", fake_update):
            title, action = addons.build_update("CLI", ["Splunk_TA_windows", " Splunk_SA_CIM "])
            action()
        self.assertEqual(title, "Actualizar add-ons (2 apps) · CLI")
        service, client, names, kwargs = calls[0]
        self.assertEqual((service, client, names), ("SERVICE", "CLI", ["Splunk_TA_windows", "Splunk_SA_CIM"]))
        self.assertEqual(kwargs["account"], ACCOUNT)
        self.assertIs(kwargs["stop"], addons.cancel_requested)
        self.assertIs(kwargs["progress"], addons.set_progress)


class TestOverview(unittest.TestCase):
    def test_collects_each_client_and_keeps_errors_apart(self):
        clients = [{"name": "A", "host": "a.splunkcloud.com"}, {"name": "B", "host": "b.splunkcloud.com"},
                   {"name": "C", "host": "c.splunkcloud.com"}]

        def fake_pending(name):
            if name == "B":
                raise ClientError("B: sin conexión")
            return {"client": name, "host": f"{name.lower()}.splunkcloud.com", "acs": {"available": True}, "pending": [{"name": "x"}],
                    "warnings": []}
        with mock.patch.object(addons.services, "list_clients", lambda: clients), \
                mock.patch.object(addons, "pending", fake_pending):
            rows = addons.overview()
            subset = addons.overview(["C", "desconocido"])
        self.assertEqual([r["client"] for r in rows], ["A", "B", "C"])
        self.assertEqual(rows[1], {"client": "B", "host": "b.splunkcloud.com", "acs": None, "pending": [], "warnings": [],
                                   "error": "B: sin conexión"})
        self.assertEqual([r["client"] for r in subset], ["C"])


if __name__ == "__main__":
    unittest.main()
