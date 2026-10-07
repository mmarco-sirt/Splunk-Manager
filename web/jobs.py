"""
Ejecución de tareas en segundo plano para la interfaz web.

Las funciones de options/ informan de su progreso con print(). Aquí se redirige
sys.stdout/sys.stderr de forma que lo que imprime cada hilo de tarea acaba en el
log de esa tarea, mientras el resto de hilos siguen escribiendo en la consola.
"""
import builtins
import os
import re
import sys
import threading
import time
import traceback
import uuid
from contextlib import contextmanager

from core.paths import BACKUP_ROOT

MAX_JOBS = 200
ERROR_PATTERN = re.compile(r"❌|\[ERROR\]|\bError\b|\bERROR\b|No se pudo")
WARNING_PATTERN = re.compile(r"⚠️|ADVERTENCIA|\bAviso\b")

_local = threading.local()


class _ThreadRoutedStream:
    """Sustituto de sys.stdout/sys.stderr que envía la salida al 'sink' del hilo actual, si lo tiene."""

    def __init__(self, fallback):
        self._fallback = fallback

    def write(self, text):
        sink = getattr(_local, "sink", None)
        if sink is not None:
            sink(text)
            return len(text)
        try:
            return self._fallback.write(text)
        except (UnicodeEncodeError, ValueError, AttributeError):
            return len(text)

    def flush(self):
        try:
            self._fallback.flush()
        except (ValueError, AttributeError):
            pass

    def isatty(self):
        return False

    def __getattr__(self, name):
        return getattr(self._fallback, name)


def install_output_router():
    """Instala la redirección de salida y un input() que no bloquea en hilos de tarea. Idempotente."""
    if not isinstance(sys.stdout, _ThreadRoutedStream):
        sys.stdout = _ThreadRoutedStream(sys.stdout)
    if not isinstance(sys.stderr, _ThreadRoutedStream):
        sys.stderr = _ThreadRoutedStream(sys.stderr)

    original_input = builtins.input
    if getattr(original_input, "_web_safe", False):
        return

    def web_safe_input(prompt=""):
        # En la web nadie puede responder a un input(): se registra y se continúa con respuesta vacía.
        if getattr(_local, "sink", None) is not None:
            print(f"{prompt}")
            print("⚠️ Esta operación pedía un dato por consola; en la web se responde vacío (cancelar).")
            return ""
        return original_input(prompt)

    web_safe_input._web_safe = True
    builtins.input = web_safe_input


@contextmanager
def capture_output():
    """Captura en una lista todo lo que se imprima en el hilo actual mientras dure el bloque."""
    chunks = []
    previous = getattr(_local, "sink", None)
    _local.sink = chunks.append
    try:
        yield chunks
    finally:
        _local.sink = previous


def _snapshot_backups():
    """Devuelve {carpeta/archivo: mtime} de los .json de backups/<carpeta>/ (sin el historial)."""
    snapshot = {}
    if not os.path.isdir(BACKUP_ROOT):
        return snapshot
    for folder in os.listdir(BACKUP_ROOT):
        folder_path = os.path.join(BACKUP_ROOT, folder)
        if not os.path.isdir(folder_path):
            continue
        for name in os.listdir(folder_path):
            path = os.path.join(folder_path, name)
            if name.endswith(".json") and os.path.isfile(path):
                try:
                    snapshot[f"{folder}/{name}"] = os.path.getmtime(path)
                except OSError:
                    pass
    return snapshot


class Job:
    def __init__(self, title, kind, clients):
        self.id = uuid.uuid4().hex[:12]
        self.title = title
        self.kind = kind
        self.clients = clients
        self.status = "running"
        self.started = time.time()
        self.finished = None
        self.lines = []
        self.errors = 0
        self.warnings = 0
        self.files = []
        self._partial = ""
        self._lock = threading.Lock()

    def write(self, text):
        with self._lock:
            data = self._partial + text
            *complete, self._partial = data.split("\n")
            for line in complete:
                self._add_line(line)

    def _add_line(self, line):
        line = line.rstrip("\r")
        if ERROR_PATTERN.search(line):
            self.errors += 1
        elif WARNING_PATTERN.search(line):
            self.warnings += 1
        self.lines.append(line)

    def finish(self, status):
        with self._lock:
            if self._partial:
                self._add_line(self._partial)
                self._partial = ""
            self.finished = time.time()
            if status == "success" and self.errors:
                status = "partial"
            self.status = status

    def summary(self):
        return {
            "id": self.id,
            "title": self.title,
            "kind": self.kind,
            "clients": self.clients,
            "status": self.status,
            "started": self.started,
            "finished": self.finished,
            "errors": self.errors,
            "warnings": self.warnings,
            "files": self.files,
            "line_count": len(self.lines),
        }

    def detail(self, since=0):
        data = self.summary()
        with self._lock:
            data["lines"] = self.lines[since:]
        data["next"] = since + len(data["lines"])
        return data


class JobManager:
    def __init__(self):
        self._jobs = {}
        self._lock = threading.Lock()

    def start(self, title, kind, clients, target):
        """Lanza 'target()' en un hilo; todo lo que imprima queda en el log de la tarea."""
        job = Job(title, kind, clients)
        with self._lock:
            self._jobs[job.id] = job
            self._trim()

        def runner():
            _local.sink = job.write
            before = _snapshot_backups()
            status = "success"
            try:
                target()
            except Exception as e:
                print(f"❌ Error inesperado: {e}")
                print(traceback.format_exc())
                status = "error"
            finally:
                after = _snapshot_backups()
                job.files = sorted(p for p, mtime in after.items() if before.get(p) != mtime)
                _local.sink = None
                job.finish(status)

        threading.Thread(target=runner, name=f"job-{job.id}", daemon=True).start()
        return job

    def _trim(self):
        if len(self._jobs) <= MAX_JOBS:
            return
        finished = sorted((j for j in self._jobs.values() if j.status != "running"), key=lambda j: j.started)
        for job in finished[: len(self._jobs) - MAX_JOBS]:
            del self._jobs[job.id]

    def get(self, job_id):
        return self._jobs.get(job_id)

    def list(self):
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.started, reverse=True)
        return [j.summary() for j in jobs]

    def clear_finished(self):
        with self._lock:
            for job_id in [j.id for j in self._jobs.values() if j.status != "running"]:
                del self._jobs[job_id]


jobs = JobManager()
