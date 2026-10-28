import unittest
from unittest import mock

from tests.fakes import FakeSavedSearch, FakeService
from web import compare, multi_edit


def item(name, search="index=a", is_test=False):
    return {"entry": {"name": name}, "compare": {"search": search}, "summary": {"is_test": is_test}}


class TestPairTestVersions(unittest.TestCase):
    def test_reference_only_in_test(self):
        data = {"REF": {"TEST - CU-1 X": item("TEST - CU-1 X", is_test=True)}, "B": {"CU-1 X": item("CU-1 X")}}
        paired = compare.pair_test_versions(data, "REF")
        self.assertEqual(paired["REF"]["CU-1 X"]["key"], "TEST - CU-1 X")
        self.assertEqual(paired["REF"]["CU-1 X"]["phases"], ["test"])
        self.assertEqual(paired["B"]["CU-1 X"]["key"], "CU-1 X")
        self.assertEqual(paired["B"]["CU-1 X"]["phases"], ["prod"])

    def test_same_phase_as_reference_when_available(self):
        data = {
            "REF": {"CU-1": item("CU-1"), "TEST - CU-1": item("TEST - CU-1")},
            "B": {"CU-1": item("CU-1"), "TEST - CU-1": item("TEST - CU-1")},
            "C": {"TEST - CU-1": item("TEST - CU-1")},
        }
        paired = compare.pair_test_versions(data, "REF")
        self.assertEqual(paired["REF"]["CU-1"]["key"], "CU-1")
        self.assertEqual(paired["B"]["CU-1"]["key"], "CU-1")
        self.assertEqual(paired["B"]["CU-1"]["phases"], ["prod", "test"])
        self.assertEqual(paired["C"]["CU-1"]["key"], "TEST - CU-1")
        # Si la referencia solo tiene la TEST, en B (que tiene las dos) se compara también la TEST
        data["REF"] = {"TEST - CU-1": item("TEST - CU-1")}
        self.assertEqual(compare.pair_test_versions(data, "REF")["B"]["CU-1"]["key"], "TEST - CU-1")

    def test_compare_clients_with_pairing(self):
        data = {
            "REF": {"TEST - CU-1": item("TEST - CU-1", "index=a", True), "CU-2": item("CU-2")},
            "B": {"CU-1": item("CU-1", "index=a"), "CU-2": item("CU-2", "index=z")},
        }
        with mock.patch.object(compare, "_fetch_all", return_value=(data, {})):
            result = compare.compare_clients("alerts", "REF", ["B"], pair_test=True)
            unpaired = compare.compare_clients("alerts", "REF", ["B"], pair_test=False)
        items = {i["name"]: i for i in result["items"]}
        self.assertTrue(result["pair_test"])
        self.assertEqual(items["CU-1"]["status"]["B"], "same")
        self.assertEqual(items["CU-1"]["keys"], {"REF": "TEST - CU-1", "B": "CU-1"})
        self.assertEqual(items["CU-2"]["status"]["B"], "different")
        self.assertEqual({i["name"]: i["status"]["B"] for i in unpaired["items"]},
                         {"CU-1": "extra", "CU-2": "different", "TEST - CU-1": "missing"})
        self.assertNotIn("keys", unpaired["items"][0])

    def test_pairing_is_ignored_for_other_types(self):
        with mock.patch.object(compare, "_fetch_all", return_value=({"REF": {"m": item("m")}, "B": {}}, {})):
            self.assertFalse(compare.compare_clients("macros", "REF", ["B"], pair_test=True)["pair_test"])


class TestSavedSearchKeys(unittest.TestCase):
    def test_private_copies_get_their_owner(self):
        service = FakeService([
            FakeSavedSearch("CU-1", app="sirt", owner="nobody", sharing="global", alert_type="number of events"),
            FakeSavedSearch("CU-1", app="search", owner="juan", sharing="user", alert_type="number of events"),
            FakeSavedSearch("CU-1", app="otra", owner="nobody", sharing="app", alert_type="number of events"),
        ])
        items = compare._saved_searches(service, True)
        self.assertEqual(sorted(items), ["CU-1", "CU-1 [otra]", "CU-1 [privada de juan]"])
        self.assertEqual(items["CU-1 [privada de juan]"]["entry"]["acl"], {"app": "search", "owner": "juan", "sharing": "user"})


class TestMultiEditCounterparts(unittest.TestCase):
    def test_counterparts(self):
        existing = {"CU-1 X", "TEST - CU-1 X", "TEST-CU-1 X", "CU-2", "TEST - CU-3"}
        self.assertEqual(multi_edit.counterparts(["CU-1 X", "CU-2", "TEST - CU-3"], existing),
                         {"CU-1 X": ["TEST - CU-1 X", "TEST-CU-1 X"], "CU-2": [], "TEST - CU-3": []})
        # Si ya se ha pedido la pareja, no se vuelve a ofrecer
        self.assertEqual(multi_edit.counterparts(["CU-1 X", "TEST - CU-1 X"], existing)["CU-1 X"], ["TEST-CU-1 X"])

    def test_lookup_reports_pairs_per_client(self):
        entries = {
            "A": [{"name": "CU-1"}, {"name": "TEST - CU-1"}],
            "B": [{"name": "TEST - CU-1"}],
        }
        with mock.patch.object(multi_edit.services, "get_service", side_effect=lambda c: c), \
                mock.patch.object(multi_edit, "_entries", side_effect=lambda svc: entries[svc]), \
                mock.patch.object(multi_edit, "find_by_name", side_effect=lambda svc, names, e: {n: [] for n in names}):
            result = multi_edit.lookup(["CU-1"], ["A", "B"])
        self.assertEqual(result["pairs"], {"CU-1": [{"name": "TEST - CU-1", "clients": ["A", "B"]}]})
        self.assertNotIn("pairs", result["clients"][0])


if __name__ == "__main__":
    unittest.main()
