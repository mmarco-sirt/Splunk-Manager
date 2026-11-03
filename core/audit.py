"""
Registro de auditoría: qué se ha cambiado, en qué cliente, cuándo, desde dónde (web o CLI) y con qué resultado.

Cada entrada es una línea JSON en logs/auditoria.jsonl, para poder filtrarla y exportarla. Lo escriben las operaciones
que modifican algo (ediciones, restores, copias, add-ons, ACS...) tanto desde la web como desde el CLI; las lecturas no
se registran. Los backups «antes de editar» siguen guardando el contenido anterior; aquí solo queda el rastro.
"""
import csv
import getpass
import io
import json
import os
import threading
import time

from core.paths import LOGS_ROOT, logs_dir

FILE_NAME = "auditoria.jsonl"
MAX_BYTES = 20 * 1024 * 1024    # al superarlo se renombra con la fecha y se empieza otro
MAX_READ = 5000

_lock = threading.Lock()
_source = "cli"


def set_source(name):
    """Desde dónde se registran las operaciones a partir de ahora: 'web' o 'cli'."""
    global _source
    _source = name


def path():
    return os.path.join(logs_dir(), FILE_NAME)


def _user():
    try:
        return getpass.getuser()
    except Exception:
        return ""


def record(action, title, client=None, detail=None, result="ok", job=None, dry_run=False):
    """
    Añade una entrada. 'action' es una clave estable (p. ej. 'alert.update'), 'title' el texto para una persona,
    'client' un nombre o lista de nombres, 'detail' datos breves (nunca credenciales) y 'result' ok/error/iniciado...
    Con dry_run el resultado es 'simulado'. Nunca lanza excepciones: un fallo al escribir no debe parar la operación.
    """
    entry = {
        "time": time.time(),
        "source": _source,
        "user": _user(),
        "client": client if isinstance(client, str) else list(client or []),
        "action": action,
        "title": str(title or "")[:300],
        "detail": _safe(detail),
        "result": "simulado" if dry_run else result,
    }
    if job:
        entry["job"] = job
    try:
        line = json.dumps(entry, ensure_ascii=False)
        with _lock:
            target = path()
            if os.path.exists(target) and os.path.getsize(target) > MAX_BYTES:
                os.replace(target, os.path.join(LOGS_ROOT, f"auditoria_{time.strftime('%Y%m%d-%H%M%S')}.jsonl"))
            with open(target, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass
    return entry


def _safe(value, depth=0):
    """Deja 'detail' serializable y corto; quita cualquier campo que parezca una credencial."""
    if depth > 4:
        return str(value)[:200]
    if isinstance(value, dict):
        return {str(k): ("***" if _secret(k) else _safe(v, depth + 1)) for k, v in list(value.items())[:60]}
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        return [_safe(v, depth + 1) for v in items[:100]] + ([f"… y {len(items) - 100} más"] if len(items) > 100 else [])
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:500]


def _secret(key):
    key = str(key).lower()
    return any(word in key for word in ("password", "token", "secret", "contraseña"))


def read(limit=500, client=None, action=None, text=None, since=None):
    """Últimas entradas (la más reciente primero) con filtros opcionales por cliente, acción, texto o fecha (epoch)."""
    target = path()
    if not os.path.exists(target):
        return []
    entries = []
    with open(target, "r", encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            entries.append(entry)
    entries = entries[-MAX_READ:]
    needle = (text or "").lower()

    def keep(e):
        if since and e.get("time", 0) < since:
            return False
        if client:
            clients = e.get("client")
            if client != clients and not (isinstance(clients, list) and client in clients):
                return False
        if action and not str(e.get("action", "")).startswith(action):
            return False
        if needle and needle not in json.dumps(e, ensure_ascii=False).lower():
            return False
        return True

    result = [e for e in reversed(entries) if keep(e)]
    return result[:limit] if limit else result


def actions():
    """Acciones distintas que hay en el registro (para los filtros)."""
    return sorted({e.get("action") for e in read(limit=0) if e.get("action")})


def to_csv(entries):
    """Entradas como CSV (texto) con ; como separador, listo para Excel."""
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\n")
    writer.writerow(["Fecha", "Origen", "Usuario", "Cliente", "Acción", "Descripción", "Resultado", "Detalle"])
    for e in entries:
        clients = e.get("client")
        writer.writerow([
            time.strftime("%d/%m/%Y %H:%M:%S", time.localtime(e.get("time", 0))),
            e.get("source", ""), e.get("user", ""),
            ", ".join(clients) if isinstance(clients, list) else clients or "",
            e.get("action", ""), e.get("title", ""), e.get("result", ""),
            json.dumps(e.get("detail"), ensure_ascii=False) if e.get("detail") not in (None, {}, []) else "",
        ])
    return out.getvalue()
