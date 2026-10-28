import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

from core.conventions import SIRT_APP, sirt_app
from options.macros.restore_macros import restore_macros
from tests.fakes import FakeMacro, FakeService


class TestRestoreMacros(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="sm_test_")

    def tearDown(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def restore(self, service, entries):
        path = os.path.join(self.folder, "macros_x_backup.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entries, f)
        with contextlib.redirect_stdout(io.StringIO()):
            restore_macros(service, path)

    def test_updates_the_same_macro_in_its_app_and_keeps_its_sharing(self):
        sirt = FakeMacro("m(1)", app="sirt", sharing="app")
        other = FakeMacro("m(1)", app="search", sharing="global")
        service = FakeService(macros=[other, sirt])
        acl = {"app": "sirt", "owner": "nobody", "sharing": "app"}
        self.restore(service, [{"name": "m(1)", "definition": "index=x $a$", "args": "a", "acl": acl}])
        self.assertEqual(sirt.updates, [{"definition": "index=x $a$", "args": "a"}])   # sin 'sharing'
        self.assertEqual((other.updates, sirt.sharing_changes), ([], []))

    def test_old_backups_make_the_shared_macro_global_as_before(self):
        app_level = FakeMacro("m", app="sirt", sharing="app")
        service = FakeService(macros=[app_level])
        self.restore(service, [{"name": "m", "definition": "index=y", "args": ""}])
        self.assertEqual(app_level.content["definition"], "index=y")
        self.assertEqual(app_level.sharing_changes, ["global"])

    def test_never_overwrites_another_users_private_macro(self):
        private = FakeMacro("m", app="search", owner="juan", sharing="user")
        service = FakeService(macros=[private])
        self.restore(service, [{"name": "m", "definition": "index=y", "args": ""}])
        self.assertEqual(private.updates, [])
        self.assertEqual([(name, params["sharing"]) for name, params in service.macros.created], [("m", "global")])


class TestSirtApp(unittest.TestCase):
    def test_default_and_per_client(self):
        self.assertEqual(sirt_app(None), SIRT_APP)
        self.assertEqual(sirt_app({"name": "X"}), "sirt_soc_app_for_splunk")
        self.assertEqual(sirt_app({"sirt_app": "  sirt_app_cliente  "}), "sirt_app_cliente")
        self.assertEqual(sirt_app({"sirt_app": " "}), SIRT_APP)


if __name__ == "__main__":
    unittest.main()
