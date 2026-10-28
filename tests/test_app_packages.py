import io
import shutil
import stat
import tarfile
import tempfile
import unittest
import zipfile
from unittest import mock

from web import app_packages
from web.app_packages import PackageError, package_from_bytes, read_archive, safe_relpath

APP_CONF = "[launcher]\nversion = 1.4.0\n[ui]\nlabel = SIRT SOC App\n[package]\nid = sirt_soc_app_for_splunk\n"


def make_tgz(files, links=(), special=()):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, content in files.items():
            data = content.encode("utf-8") if isinstance(content, str) else content
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for name, target, kind in links:
            info = tarfile.TarInfo(name)
            info.type = kind
            info.linkname = target
            tar.addfile(info)
        for name in special:
            info = tarfile.TarInfo(name)
            info.type = tarfile.FIFOTYPE
            tar.addfile(info)
    return buffer.getvalue()


def make_zip(files, symlinks=()):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
        for name in symlinks:
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, "/etc/passwd")
    return buffer.getvalue()


APP = {"sirt_soc_app_for_splunk/default/app.conf": APP_CONF,
       "sirt_soc_app_for_splunk/default/savedsearches.conf": "[CU-AD-1-T1110 Fuerza bruta]\nsearch = index=a\n",
       "sirt_soc_app_for_splunk/lookups/vip.csv": "user,vip\nana,1\n"}


class TestSafeRelpath(unittest.TestCase):
    def test_accepts_normal_paths(self):
        self.assertEqual(safe_relpath("app/default/app.conf"), "app/default/app.conf")
        self.assertEqual(safe_relpath("./app//default\\app.conf"), "app/default/app.conf")

    def test_rejects_dangerous_paths(self):
        for name in ("../evil", "app/../../evil", "/etc/passwd", "C:/Windows/x", "app/file:stream", "app/aux.conf",
                     "app/CON", "app/nul.txt", "app/fin. ", "", ".", "app/\x01x"):
            with self.subTest(name=name), self.assertRaises(PackageError):
                safe_relpath(name)


class TestReadArchive(unittest.TestCase):
    def test_tgz_with_root_folder(self):
        files, root, notes = read_archive(make_tgz(APP), "app.tgz")
        self.assertEqual(root, "sirt_soc_app_for_splunk")
        self.assertEqual(sorted(files), ["default/app.conf", "default/savedsearches.conf", "lookups/vip.csv"])
        self.assertEqual(notes, [])
        package = package_from_bytes(make_tgz(APP), "app.spl")
        self.assertEqual((package.info()["id"], package.info()["version"]), ("sirt_soc_app_for_splunk", "1.4.0"))
        self.assertEqual(list(package.stanzas("savedsearches")), ["CU-AD-1-T1110 Fuerza bruta"])

    def test_zip_without_root_folder(self):
        files, root, notes = read_archive(make_zip({"default/app.conf": APP_CONF, "__MACOSX/._app.conf": "x"}), "app.zip")
        self.assertEqual((sorted(files), root), (["default/app.conf"], ""))
        self.assertEqual(len(notes), 1)

    def test_only_default_folder_is_not_mistaken_for_the_root(self):
        files, root, _ = read_archive(make_tgz({"default/app.conf": APP_CONF, "default/macros.conf": "[m]\ndefinition=1\n"}))
        self.assertEqual((sorted(files), root), (["default/app.conf", "default/macros.conf"], ""))

    def test_rejects_traversal_links_and_special_files(self):
        cases = {   # caso: (paquete, nombre, motivo esperado)
            "traversal": (make_tgz({**APP, "sirt_soc_app_for_splunk/../../evil.py": "x"}), "app.tgz", "Ruta no permitida"),
            "absoluta": (make_tgz({**APP, "/tmp/evil": "x"}), "app.tgz", "Ruta no permitida"),
            "symlink": (make_tgz(APP, links=[("sirt_soc_app_for_splunk/bin/x", "/etc/passwd", tarfile.SYMTYPE)]), "app.tgz", "enlaces"),
            "hardlink": (make_tgz(APP, links=[("sirt_soc_app_for_splunk/bin/x", "/etc/passwd", tarfile.LNKTYPE)]), "app.tgz", "enlaces"),
            "fifo": (make_tgz(APP, special=["sirt_soc_app_for_splunk/bin/fifo"]), "app.tgz", "archivo especial"),
            "zip symlink": (make_zip({"default/app.conf": APP_CONF}, symlinks=["default/x"]), "app.zip", "enlaces"),
            "zip traversal": (make_zip({"default/app.conf": APP_CONF, "../evil": "x"}), "app.zip", "Ruta no permitida"),
            "repetido": (make_zip({"default/app.conf": APP_CONF, "Default/App.conf": APP_CONF}), "app.zip", "repetido"),
            "sin app.conf": (make_tgz({"app/README": "x"}), "app.tgz", "falta default/app.conf"),
            "no es un paquete": (b"esto no es un tar", "app.tgz", "no se pudo descomprimir"),
            "zip dañado": (b"PK\x03\x04 roto", "app.zip", "No es un .zip válido"),
        }
        for label, (data, name, reason) in cases.items():
            with self.subTest(case=label), self.assertRaisesRegex(PackageError, reason):
                read_archive(data, name)

    def test_limits(self):
        with mock.patch.object(app_packages, "MAX_EXTRACTED_BYTES", 1000):
            with self.assertRaises(PackageError):
                read_archive(make_zip({"default/app.conf": APP_CONF, "lookups/grande.csv": "a," * 1000}), "x.zip")
        with mock.patch.object(app_packages, "MAX_FILES", 2):
            with self.assertRaises(PackageError):
                read_archive(make_tgz(APP), "x.tgz")


class TestStore(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="sm_apps_")
        self.patch = mock.patch.object(app_packages, "uploads_dir", return_value=self.folder)
        self.patch.start()
        app_packages._cache.clear()

    def tearDown(self):
        self.patch.stop()
        shutil.rmtree(self.folder, ignore_errors=True)

    def test_save_list_load_results_and_delete(self):
        meta = app_packages.save_package("sirt-1.4.0.tgz", make_tgz(APP), summarize=lambda p: {"errors": 0})
        self.assertRegex(meta["id"], r"^sirt_soc_app_for_splunk@\d{8}-\d{6}_1\.4\.0$")
        self.assertEqual((meta["counts"]["saved_searches"], meta["counts"]["lookups"], meta["summary"]), (1, 1, {"errors": 0}))
        again = app_packages.save_package("copia.tgz", make_tgz(APP))
        self.assertTrue(again["duplicate"])
        self.assertEqual([m["id"] for m in app_packages.list_packages()], [meta["id"]])

        package = app_packages.load_package(meta["id"])
        self.assertEqual(package.info()["label"], "SIRT SOC App")
        result_id = app_packages.save_result(meta["id"], "runs", {"client": "X", "results": []})
        self.assertEqual(app_packages.read_result(meta["id"], "runs", result_id)["client"], "X")
        self.assertEqual([r["id"] for r in app_packages.list_results(meta["id"], "runs")], [result_id])

        app_packages.delete_package(meta["id"])
        self.assertEqual(app_packages.list_packages(), [])
        with self.assertRaises(PackageError):
            app_packages.load_package(meta["id"])

    def test_invalid_ids_and_extensions(self):
        for bad in ("../x@20261028-120000", "app@..", "app", "app@20261028-120000/../../x"):
            with self.subTest(id=bad), self.assertRaises(PackageError):
                app_packages.package_dir(bad)
        with self.assertRaises(PackageError):
            app_packages.save_package("app.exe", make_tgz(APP))


if __name__ == "__main__":
    unittest.main()
