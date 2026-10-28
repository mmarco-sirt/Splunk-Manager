import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

from core.backup_files import merge_backup
from core.knowledge import ALL_NAMESPACES, all_saved_searches, find_saved_search, pick_existing, same_entry
from options.alerts.restore_alerts import restore_alerts_from_file
from tests.fakes import FakeSavedSearch, FakeService


def acl(app, owner, sharing):
    return {"app": app, "owner": owner, "sharing": sharing}


class TestAllNamespaces(unittest.TestCase):
    def test_lists_every_app_and_user(self):
        service = FakeService([FakeSavedSearch("CU-1")])
        all_saved_searches(service)
        self.assertEqual(service.saved_searches.last_namespace, ALL_NAMESPACES)


class TestFindSavedSearch(unittest.TestCase):
    def setUp(self):
        self.shared = FakeSavedSearch("CU-1", app="sirt", owner="admin", sharing="global")
        self.private = FakeSavedSearch("CU-1", app="sirt", owner="admin", sharing="user")
        self.service = FakeService([self.shared, self.private])

    def test_distinguishes_private_and_shared_copies(self):
        self.assertIs(find_saved_search(self.service, "CU-1", "sirt", "admin", "user"), self.private)
        self.assertIs(find_saved_search(self.service, "CU-1", "sirt", "admin", "global"), self.shared)
        # Sin compartición, la compartida
        self.assertIs(find_saved_search(self.service, "CU-1", "sirt", "admin"), self.shared)

    def test_missing(self):
        with self.assertRaises(KeyError):
            find_saved_search(self.service, "CU-1", "otra", "admin", "global")
        with self.assertRaises(KeyError):
            find_saved_search(self.service, "CU-2", "sirt", "admin")


class TestPickExisting(unittest.TestCase):
    def setUp(self):
        self.shared = FakeSavedSearch("TEST - CU-1", app="sirt", owner="nobody", sharing="app")
        self.juan = FakeSavedSearch("TEST - CU-1", app="search", owner="juan", sharing="user")
        self.mine = FakeSavedSearch("TEST - CU-1", app="search", owner="admin", sharing="user")

    def test_exact_acl(self):
        self.assertIs(pick_existing([self.shared, self.juan], acl("search", "juan", "user")), self.juan)
        self.assertIs(pick_existing([self.shared, self.juan], acl("sirt", "nobody", "app")), self.shared)

    def test_shared_backup_never_overwrites_someone_elses_private_copy(self):
        self.assertIsNone(pick_existing([self.juan], acl("sirt", "nobody", "global")))
        # Pero sí otra compartida con ese nombre (p. ej. en otra app)
        self.assertIs(pick_existing([self.juan, self.shared], acl("other", "nobody", "global")), self.shared)

    def test_private_backup_only_matches_the_same_owner(self):
        self.assertIsNone(pick_existing([self.shared], acl("search", "juan", "user")))
        moved = FakeSavedSearch("TEST - CU-1", app="otra", owner="juan", sharing="user")
        self.assertIs(pick_existing([self.shared, moved], acl("search", "juan", "user")), moved)

    def test_old_backups_without_acl(self):
        self.assertIs(pick_existing([self.juan, self.shared]), self.shared)
        self.assertIs(pick_existing([self.mine], user="admin"), self.mine)
        self.assertIsNone(pick_existing([self.juan], user="admin"))
        self.assertIsNone(pick_existing([]))

    def test_es_entries_prefer_their_app(self):
        es = FakeSavedSearch("TEST - CU-1", app="SplunkEnterpriseSecuritySuite", owner="nobody", sharing="global")
        self.assertIs(pick_existing([self.shared, es], {"app": "SplunkEnterpriseSecuritySuite"}), es)


class TestMergeBackup(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="sm_test_")
        self.path = os.path.join(self.folder, "alertas_antes_de_editar_x_backup.json")

    def tearDown(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def read(self):
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)

    def test_private_and_shared_copies_are_kept_apart(self):
        shared = {"name": "CU-1", "content": {"v": 1}, "acl": acl("sirt", "nobody", "global")}
        private = {"name": "CU-1", "content": {"v": 2}, "acl": acl("search", "juan", "user")}
        merge_backup(self.path, [shared], same=same_entry)
        merge_backup(self.path, [private], same=same_entry)
        self.assertEqual(len(self.read()), 2)
        merge_backup(self.path, [{**shared, "content": {"v": 3}}], same=same_entry)
        data = self.read()
        self.assertEqual(len(data), 2)
        self.assertIn({"v": 3}, [e["content"] for e in data])

    def test_old_entries_without_acl_are_replaced(self):
        merge_backup(self.path, [{"name": "CU-1", "content": {"v": 1}}], same=same_entry)
        merge_backup(self.path, [{"name": "CU-1", "content": {"v": 2}, "acl": acl("sirt", "nobody", "global")}], same=same_entry)
        self.assertEqual([e["content"] for e in self.read()], [{"v": 2}])


class TestRestore(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="sm_test_")

    def tearDown(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def restore(self, service, entries, sharing):
        path = os.path.join(self.folder, "alertas_x_backup.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entries, f)
        with contextlib.redirect_stdout(io.StringIO()):
            restore_alerts_from_file(service, path, sharing=sharing)

    def test_updates_the_same_private_alert_and_keeps_its_sharing(self):
        juan = FakeSavedSearch("TEST - CU-1", app="search", owner="juan", sharing="user")
        shared = FakeSavedSearch("TEST - CU-1", app="sirt", owner="nobody", sharing="global")
        service = FakeService([shared, juan])
        entry = {"name": "TEST - CU-1", "content": {"search": "index=a", "cron_schedule": "*/5 * * * *"},
                 "acl": acl("search", "juan", "user")}
        self.restore(service, [entry], "keep")
        self.assertEqual(juan.updates[-1]["search"], "index=a")
        self.assertEqual(shared.updates, [])
        self.assertEqual(juan.sharing_changes, [])
        self.assertEqual(service.saved_searches.created, [])

    def test_does_not_overwrite_another_users_private_copy(self):
        juan = FakeSavedSearch("CU-1", app="search", owner="juan", sharing="user")
        service = FakeService([juan])
        self.restore(service, [{"name": "CU-1", "content": {"search": "index=a"}}], "keep")
        self.assertEqual(juan.updates, [])
        self.assertEqual([name for name, _ in service.saved_searches.created], ["CU-1"])
        created = service.saved_searches.items[-1]
        self.assertEqual(created.sharing_changes, ["global"])   # backup antiguo: las nuevas, globales

    def test_new_alert_keeps_the_sharing_of_the_backup(self):
        service = FakeService([])
        self.restore(service, [{"name": "CU-1", "content": {"search": "index=a"}, "acl": acl("sirt", "nobody", "app")}], "keep")
        self.assertEqual(service.saved_searches.items[-1].access["sharing"], "app")

    def test_explicit_sharing_is_applied(self):
        shared = FakeSavedSearch("CU-1", app="sirt", owner="nobody", sharing="app")
        service = FakeService([shared])
        self.restore(service, [{"name": "CU-1", "content": {"search": "index=a"}}], "global")
        self.assertEqual(shared.sharing_changes, ["global"])

    def test_repeated_name_in_the_same_file_updates_the_created_one(self):
        service = FakeService([])
        entries = [{"name": "CU-1", "content": {"search": "index=a"}}, {"name": "CU-1", "content": {"search": "index=b"}}]
        self.restore(service, entries, "global")
        self.assertEqual(len(service.saved_searches.created), 1)
        self.assertEqual(service.saved_searches.items[-1].content["search"], "index=b")


if __name__ == "__main__":
    unittest.main()
