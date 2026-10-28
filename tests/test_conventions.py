import unittest

from core.conventions import is_counterpart, is_cu_alert, is_test_alert, strip_test_prefix


class TestPrefixes(unittest.TestCase):
    def test_strip_test_prefix(self):
        self.assertEqual(strip_test_prefix("TEST - CU-CA-AUTH-1152-T1078 Geographically Improbable Access"),
                         "CU-CA-AUTH-1152-T1078 Geographically Improbable Access")
        self.assertEqual(strip_test_prefix("CU-AD-001 X"), "CU-AD-001 X")

    def test_tolerant_variants(self):
        for name in ("TEST-CU-AD-001", "Test - CU-AD-001", "test -CU-AD-001", "TEST  -  CU-AD-001"):
            with self.subTest(name=name):
                self.assertTrue(is_test_alert(name))
                self.assertEqual(strip_test_prefix(name), "CU-AD-001")

    def test_only_use_cases(self):
        # TEST sin un caso de uso detrás no es la versión de pruebas de un CU
        for name in ("TEST - Otra alerta", "TESTCU-AD-001", "Mi TEST - CU-AD-001", "TEST - cu-ad-001", "", None):
            with self.subTest(name=name):
                self.assertFalse(is_test_alert(name))
                self.assertEqual(strip_test_prefix(name), str(name or ""))

    def test_is_cu_alert(self):
        self.assertTrue(is_cu_alert("CU-AD-001"))
        self.assertTrue(is_cu_alert("TEST - CU-AD-001"))
        self.assertFalse(is_cu_alert("TEST - CU-AD-001", include_test=False))
        self.assertTrue(is_cu_alert("CU-AD-001", include_test=False))
        self.assertFalse(is_cu_alert("Threat - Access - Rule"))
        self.assertFalse(is_cu_alert(None))

    def test_is_counterpart(self):
        self.assertTrue(is_counterpart("CU-X 1", "TEST - CU-X 1"))
        self.assertTrue(is_counterpart("TEST-CU-X 1", "CU-X 1"))
        self.assertFalse(is_counterpart("CU-X 1", "CU-X 1"))
        self.assertFalse(is_counterpart("TEST - CU-X 1", "TEST-CU-X 1"))   # las dos son de pruebas
        self.assertFalse(is_counterpart("CU-X 1", "TEST - CU-X 2"))
        self.assertFalse(is_counterpart("Otra", "TEST - Otra"))


if __name__ == "__main__":
    unittest.main()
