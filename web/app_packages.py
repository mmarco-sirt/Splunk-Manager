"""
Paquetes de app de Splunk subidos para validarlos antes de desplegarlos (.tgz, .tar.gz, .spl o .zip).

Cada versión subida se guarda tal cual en apps_subidas/<app>/<AAAAMMDD-HHMMSS>_<versión>/ junto a meta.json, y ahí mismo
las validaciones contra un cliente (checks/) y las ejecuciones de búsquedas (runs/). Se conservan las MAX_UPLOADS
versiones más recientes de cada app.

Los archivos del paquete nunca se extraen a disco: se leen en memoria con las comprobaciones de una extracción segura
(sin rutas absolutas ni «..», sin enlaces ni dispositivos, sin nombres reservados de Windows y con límites de tamaño y de
número de archivos).
"""
import hashlib
import io
import json
import os
import re
import shutil
import stat
import tarfile
import threading
import time
import zipfile
import zlib

from core.paths import uploads_dir
from core.splunk_conf import app_info, decode_text, merge_layers, parse_conf

MAX_PACKAGE_BYTES = 100 * 1024 * 1024
MAX_EXTRACTED_BYTES = 200 * 1024 * 1024
MAX_FILES = 10000
MAX_UPLOADS = 30
EXTENSIONS = (".tar.gz", ".tgz", ".spl", ".zip")
PACKAGE_FILE = "paquete"
RESULT_KINDS = ("checks", "runs")
PACKAGE_ID = re.compile(r"^([A-Za-z0-9_.-]+)@(\d{8}-\d{6}(?:_[A-Za-z0-9_.-]+)?)$")
RESULT_ID = re.compile(r"^\d{8}-\d{6}(?:_\d+)?$")
_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]+")
_WINDOWS_RESERVED = re.compile(r"^(con|prn|aux|nul|com\d|lpt\d)(\..*)?$", re.IGNORECASE)
_IGNORED_PREFIXES = ("__MACOSX/",)


class PackageError(ValueError):
    """Paquete no válido o no encontrado (mensaje apto para la web)."""


def safe_relpath(name):
    """Ruta relativa segura (con «/») de un archivo del paquete; PackageError si podría salirse de su carpeta."""
    path = str(name or "").replace("\\", "/")
    if not path or path.startswith("/") or re.match(r"^[A-Za-z]:", path) or "\x00" in path:
        raise PackageError(f"Ruta no permitida en el paquete: «{name}».")
    parts = [p for p in path.split("/") if p not in ("", ".")]
    if not parts:
        raise PackageError(f"Ruta no permitida en el paquete: «{name}».")
    for part in parts:
        if (part == ".." or ":" in part or _WINDOWS_RESERVED.match(part) or part != part.rstrip(". ")
                or any(ord(c) < 32 for c in part)):
            raise PackageError(f"Ruta no permitida en el paquete: «{name}».")
    return "/".join(parts)


class _Budget:
    """Límites de la lectura: nº de archivos y bytes descomprimidos."""

    def __init__(self):
        self.files = 0
        self.bytes = 0

    def read(self, stream, name):
        self.files += 1
        if self.files > MAX_FILES:
            raise PackageError(f"El paquete tiene más de {MAX_FILES} archivos.")
        data = stream.read(MAX_EXTRACTED_BYTES - self.bytes + 1)
        self.bytes += len(data)
        if self.bytes > MAX_EXTRACTED_BYTES:
            raise PackageError(f"El paquete descomprimido ocupa más de {MAX_EXTRACTED_BYTES // (1024 * 1024)} MB.")
        return data


def _tar_members(data):
    budget = _Budget()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
        for member in tar:
            if member.issym() or member.islnk():
                raise PackageError(f"El paquete contiene enlaces ({member.name}): no se admiten.")
            if member.isdir():
                safe_relpath(member.name)
                continue
            if not member.isfile():
                raise PackageError(f"El paquete contiene un archivo especial ({member.name}): no se admite.")
            path = safe_relpath(member.name)
            stream = tar.extractfile(member)
            yield path, budget.read(stream, path) if stream else b""


def _zip_members(data):
    budget = _Budget()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in archive.infolist():
            if stat.S_ISLNK(info.external_attr >> 16):
                raise PackageError(f"El paquete contiene enlaces ({info.filename}): no se admiten.")
            if info.flag_bits & 0x1:
                raise PackageError(f"El paquete tiene archivos cifrados ({info.filename}): no se admiten.")
            if info.is_dir():
                safe_relpath(info.filename)
                continue
            path = safe_relpath(info.filename)
            with archive.open(info) as stream:
                yield path, budget.read(stream, path)


def read_archive(data, filename=""):
    """
    Archivos de un paquete de app: ({ruta relativa a la app: bytes}, carpeta raíz, notas). Quita la carpeta raíz del
    paquete (la de la app) y comprueba que es una app (default/app.conf). PackageError si no se puede leer o no es seguro.
    """
    is_zip = data[:4] == b"PK\x03\x04" or str(filename).lower().endswith(".zip")
    notes, files, seen = [], {}, set()
    try:
        for path, content in (_zip_members(data) if is_zip else _tar_members(data)):
            if path.startswith(_IGNORED_PREFIXES):
                continue
            if path.lower() in seen:
                raise PackageError(f"Archivo repetido en el paquete (sin distinguir mayúsculas): «{path}».")
            seen.add(path.lower())
            files[path] = content
    except PackageError:
        raise
    except tarfile.ReadError:
        raise PackageError("No es un paquete .tgz, .tar.gz o .spl válido: no se pudo descomprimir.")
    except zipfile.BadZipFile:
        raise PackageError("No es un .zip válido.")
    except (tarfile.TarError, zlib.error, EOFError, OSError, RuntimeError) as e:
        reason = (str(e).splitlines() or [type(e).__name__])[0]
        raise PackageError(f"El paquete está dañado o incompleto: {reason}")
    if not files:
        raise PackageError("El paquete está vacío.")

    tops = {p.split("/", 1)[0] for p in files}
    root = ""
    if "default/app.conf" not in files and len(tops) == 1 and all("/" in p for p in files):
        root = tops.pop()
        files = {p.split("/", 1)[1]: content for p, content in files.items()}
    if "default/app.conf" not in files:
        raise PackageError("No es un paquete de app de Splunk: falta default/app.conf"
                           + (" (debe haber una sola carpeta raíz con la app)." if len(tops) > 1 else "."))
    if not root:
        notes.append("El paquete no tiene carpeta raíz: Splunk espera <id de la app>/default/app.conf.")
    return files, root, notes


# --- Modelo de un paquete -----------------------------------------------------------------

class Package:
    """Un paquete leído: sus archivos, los .conf de default/ y local/ y los datos de app.conf."""

    def __init__(self, files, root="", meta=None):
        self.files = files
        self.root = root
        self.meta = meta or {}
        self._texts = {}
        self._confs = {}

    def text(self, path):
        """(texto, problemas de codificación) de un archivo; se calcula una vez."""
        if path not in self._texts:
            self._texts[path] = decode_text(self.files[path], path)
        return self._texts[path]

    def conf(self, layer, name):
        """ConfFile de <layer>/<name>.conf (o metadata/<layer>.meta con name=None), o None si no está."""
        path = f"metadata/{layer}.meta" if name is None else f"{layer}/{name}.conf"
        if path not in self.files:
            return None
        if path not in self._confs:
            self._confs[path] = parse_conf(self.text(path)[0], path)
        return self._confs[path]

    def conf_names(self):
        """Nombres de los .conf que hay en default/ o local/ (sin extensión)."""
        names = set()
        for path in self.files:
            layer, _, rest = path.partition("/")
            if layer in ("default", "local") and rest.endswith(".conf") and "/" not in rest:
                names.add(rest[:-5])
        return sorted(names)

    def stanzas(self, name, layer=None):
        """Stanzas de un .conf: de una capa o, sin capa, default con local encima (lo que usa Splunk)."""
        if layer:
            conf = self.conf(layer, name)
            return conf.stanzas if conf else {}
        return merge_layers(*(self.conf(lay, name).stanzas for lay in ("default", "local") if self.conf(lay, name)))

    def has_local(self):
        return any(p.startswith("local/") or p == "metadata/local.meta" for p in self.files)

    def info(self):
        info = app_info(self.stanzas("app"))
        info["id"] = info["id"] or self.root
        return info


def package_from_bytes(data, filename=""):
    files, root, notes = read_archive(data, filename)
    package = Package(files, root)
    package.notes = notes
    return package


# --- Almacén ----------------------------------------------------------------------------------

_lock = threading.Lock()
_cache = {}   # id -> Package (los últimos abiertos)
CACHE_SIZE = 3


def _safe(text, default):
    return _UNSAFE.sub("_", str(text or "")).strip("._-") or default


def _parse_id(package_id):
    match = PACKAGE_ID.match(str(package_id or ""))
    if not match:
        raise PackageError("Identificador de paquete no válido.")
    return match.group(1), match.group(2)


def package_dir(package_id):
    app, folder = _parse_id(package_id)
    path = os.path.join(uploads_dir(), app, folder)
    if not os.path.isfile(os.path.join(path, "meta.json")):
        raise PackageError("No existe ese paquete (quizá se borró).")
    return path


def _read_meta(path):
    with open(os.path.join(path, "meta.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def _write_meta(path, meta):
    with open(os.path.join(path, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def list_packages():
    """Versiones subidas, de la más reciente a la más antigua."""
    root = uploads_dir()
    found = []
    for app in os.listdir(root):
        app_path = os.path.join(root, app)
        if not os.path.isdir(app_path):
            continue
        for folder in os.listdir(app_path):
            path = os.path.join(app_path, folder)
            if os.path.isfile(os.path.join(path, "meta.json")):
                try:
                    found.append(_read_meta(path))
                except (OSError, ValueError):
                    continue
    return sorted(found, key=lambda m: m.get("uploaded", 0), reverse=True)


def _counts(package):
    names = {"savedsearches": "saved_searches", "macros": "macros", "eventtypes": "eventtypes", "transforms": "transforms"}
    counts = {label: len([s for s in package.stanzas(conf) if s != "default"]) for conf, label in names.items()}
    counts["lookups"] = sum(1 for p in package.files if p.startswith("lookups/"))
    counts["views"] = sum(1 for p in package.files if re.match(r"^(default|local)/data/ui/views/[^/]+\.xml$", p))
    counts["files"] = len(package.files)
    return counts


def save_package(filename, data, summarize=None):
    """
    Guarda un paquete subido y devuelve su meta. Si ya se había subido el mismo archivo (mismo SHA-256) devuelve el
    existente con duplicate=True. 'summarize(package)' añade a meta el resumen de la validación.
    """
    filename = os.path.basename(str(filename or "").replace("\\", "/")) or "paquete.tgz"
    if not filename.lower().endswith(EXTENSIONS):
        raise PackageError("El paquete debe ser .tgz, .tar.gz, .spl o .zip.")
    if len(data) > MAX_PACKAGE_BYTES:
        raise PackageError(f"El paquete ocupa más de {MAX_PACKAGE_BYTES // (1024 * 1024)} MB.")
    sha256 = hashlib.sha256(data).hexdigest()
    for meta in list_packages():
        if meta.get("sha256") == sha256:
            return {**meta, "duplicate": True}

    package = package_from_bytes(data, filename)
    info = package.info()
    app = _safe(info["id"], "app")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    folder = f"{stamp}_{_safe(info['version'], 'sin_version')}"
    with _lock:
        app_path = os.path.join(uploads_dir(), app)
        path = os.path.join(app_path, folder)
        counter = 1
        while os.path.exists(path):
            path = os.path.join(app_path, f"{folder}_{counter}")
            counter += 1
        folder = os.path.basename(path)
        os.makedirs(path)
        ext = next(e for e in EXTENSIONS if filename.lower().endswith(e))
        with open(os.path.join(path, PACKAGE_FILE + ext), "wb") as f:
            f.write(data)
        meta = {
            "id": f"{app}@{folder}", "app": app, "version": info["version"], "label": info["label"], "build": info["build"],
            "filename": filename, "archive": PACKAGE_FILE + ext, "size": len(data), "sha256": sha256,
            "uploaded": time.time(), "root": package.root, "notes": package.notes, "counts": _counts(package),
        }
        if summarize:
            meta["summary"] = summarize(package)
        _write_meta(path, meta)
        _prune(app_path)
    return meta


def _prune(app_path):
    versions = sorted((f for f in os.listdir(app_path) if os.path.isfile(os.path.join(app_path, f, "meta.json"))), reverse=True)
    for old in versions[MAX_UPLOADS:]:
        shutil.rmtree(os.path.join(app_path, old), ignore_errors=True)


def get_meta(package_id):
    return _read_meta(package_dir(package_id))


def update_meta(package_id, **changes):
    path = package_dir(package_id)
    with _lock:
        meta = _read_meta(path)
        meta.update(changes)
        _write_meta(path, meta)
    return meta


def load_package(package_id):
    """Package de una versión subida (se guardan en memoria las últimas abiertas)."""
    path = package_dir(package_id)
    with _lock:
        if package_id in _cache:
            return _cache[package_id]
    meta = _read_meta(path)
    with open(os.path.join(path, meta["archive"]), "rb") as f:
        data = f.read()
    files, root, _ = read_archive(data, meta["archive"])
    package = Package(files, root, meta)
    with _lock:
        _cache[package_id] = package
        while len(_cache) > CACHE_SIZE:
            _cache.pop(next(iter(_cache)))
    return package


def delete_package(package_id):
    path = package_dir(package_id)
    with _lock:
        _cache.pop(package_id, None)
        shutil.rmtree(path, ignore_errors=True)
        parent = os.path.dirname(path)
        if os.path.isdir(parent) and not os.listdir(parent):
            os.rmdir(parent)


# --- Resultados guardados (validaciones contra un cliente y ejecuciones) -------------------------

def save_result(package_id, kind, data):
    """Guarda un resultado (kind: checks o runs) y devuelve su id."""
    if kind not in RESULT_KINDS:
        raise PackageError("Tipo de resultado no válido.")
    folder = os.path.join(package_dir(package_id), kind)
    os.makedirs(folder, exist_ok=True)
    result_id = time.strftime("%Y%m%d-%H%M%S")
    counter = 1
    while os.path.exists(os.path.join(folder, f"{result_id}.json")):
        result_id = f"{time.strftime('%Y%m%d-%H%M%S')}_{counter}"
        counter += 1
    with open(os.path.join(folder, f"{result_id}.json"), "w", encoding="utf-8") as f:
        json.dump({**data, "id": result_id}, f, ensure_ascii=False, indent=1)
    return result_id


def list_results(package_id, kind):
    """Resultados guardados de una versión (resumen), del más reciente al más antiguo."""
    if kind not in RESULT_KINDS:
        raise PackageError("Tipo de resultado no válido.")
    folder = os.path.join(package_dir(package_id), kind)
    if not os.path.isdir(folder):
        return []
    found = []
    for name in sorted(os.listdir(folder), reverse=True):
        if name.endswith(".json") and RESULT_ID.match(name[:-5]):
            try:
                data = read_result(package_id, kind, name[:-5])
            except (OSError, ValueError):
                continue
            found.append({k: data.get(k) for k in ("id", "client", "created", "finished", "status", "counts")})
    return found


def read_result(package_id, kind, result_id):
    if kind not in RESULT_KINDS or not RESULT_ID.match(str(result_id or "")):
        raise PackageError("Resultado no válido.")
    path = os.path.join(package_dir(package_id), kind, f"{result_id}.json")
    if not os.path.isfile(path):
        raise PackageError("No existe ese resultado.")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
