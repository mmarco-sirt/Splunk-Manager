"""
Vigilancia de alertas: consulta cada pocos minutos todos los Splunks y avisa en el escritorio de Windows de cada alerta
que se dispara. Pensado para cuando no llega el correo (incidencias de Microsoft).

Fuente: el log del planificador (index=_internal sourcetype=scheduler). Ahí aparece toda búsqueda programada que ha
ejecutado acciones, escriba o no en el índice alerts (Cyences, notables de ES, alertas por correo...). Se descartan las
que solo hacen mantenimiento (lookups, summary index, telemetría, ITSI, risk) y los nombres de IGNORE_NAMES.

Uso (desde la raíz del proyecto):
    python -m options.alerts.watch_alerts --excluir GRANOLLERS
    python -m options.alerts.watch_alerts --excluir GRANOLLERS --intervalo 5 --desde 60
Ctrl+C para parar.
"""
import argparse
import base64
import datetime
import fnmatch
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from core.clients import get_clients_from_config
from core.connection import _connect, auth_methods
from core.paths import logs_dir
from web.explorer import oneshot_results

# Acciones que no suponen avisar a nadie: si una ejecución solo tiene estas, no es una alerta
MAINTENANCE_ACTIONS = {
    "lookup", "outputlookup", "summary_index", "outputtelemetry", "itsi_import_objects", "itsi_summary_metrics_collect",
    "indicator", "risk", "output_message", "cyences_send_digest_email_action",
}
# Búsquedas que disparan acciones de alerta pero son ruido (generadores, KPIs...). Comodines fnmatch, sin mayúsculas.
IGNORE_NAMES = [
    "lookup generator*",
    "alimentar brs",
    "cyences digest email",
    "cu-mi-siem-0045-t0000 use case change log",
]
# Por cliente (nombre en config.py): KPIs de VEUCTTI que se ejecutan cada 5 minutos con logevent
IGNORE_BY_CLIENT = {
    "VEUCTTI": ["cap1 *", "cfin1 *", "cfin2 *", "disp4 *", "sneg1 *", "sneg2 *", "sost2 *", "suser6 *"],
}

FIRED_SPL = (
    'search index=_internal sourcetype=scheduler earliest={earliest} latest=now status=success alert_actions=* '
    'NOT suppressed=1 savedsearch_name=* '
    '| search alert_actions!="" '
    '| eval t=_time '
    '| fields t sid savedsearch_name app alert_actions result_count '
    '| fields - _raw _time'
)

OVERLAP = 15 * 60          # segundos que se vuelven a mirar en cada ciclo (retraso de indexación de _internal)
SEEN_TTL = 26 * 3600       # tiempo que se recuerdan los sid ya avisados
STATE_FILE = "vigilancia_alertas_estado.json"
POWERSHELL_AUMID = "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe"


# --- Filtro ---

def is_alert(row, client_name):
    """True si la ejecución del planificador es una alerta que hay que avisar."""
    raw = row.get("alert_actions") or ""
    raw = ",".join(raw) if isinstance(raw, list) else raw
    # Las alertas de Cyences (acciones cyences_*) no se avisan
    actions = {a.strip() for a in raw.split(",") if a.strip() and not a.strip().startswith("cyences_")}
    if not actions or actions <= MAINTENANCE_ACTIONS:
        return False
    name = (row.get("savedsearch_name") or "").strip().lower()
    patterns = IGNORE_NAMES + IGNORE_BY_CLIENT.get(client_name.upper(), [])
    return not any(fnmatch.fnmatch(name, p) for p in patterns)


def new_alerts(rows, client_name, seen):
    """Filtra las filas y devuelve las alertas cuyo sid no se ha avisado ya (y las marca como vistas)."""
    found = []
    for row in sorted(rows, key=lambda r: float(r.get("t") or 0)):
        if not is_alert(row, client_name):
            continue
        key = f"{client_name}|{row.get('sid') or row.get('savedsearch_name')}|{row.get('t')}"
        if key in seen:
            continue
        seen[key] = time.time()
        found.append(row)
    return found


# --- Estado ---

def load_state(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data.get("last", {}), data.get("seen", {})
    except (OSError, ValueError):
        return {}, {}


def save_state(path, last, seen):
    cutoff = time.time() - SEEN_TTL
    seen = {k: v for k, v in seen.items() if v >= cutoff}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"last": last, "seen": seen}, fh)
    os.replace(tmp, path)
    return seen


# --- Avisos ---

def _xml(text):
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


def toast(title, lines):
    """Notificación de Windows que se queda en pantalla hasta cerrarla. Si falla, ventana emergente."""
    body = "".join(f"<text>{_xml(line)}</text>" for line in lines[:2])
    xml = (f'<toast scenario="reminder"><visual><binding template="ToastGeneric"><text>{_xml(title)}</text>{body}'
           f'</binding></visual><audio src="ms-winsoundevent:Notification.Looping.Alarm2" loop="false"/>'
           f'<actions><action content="Cerrar" arguments="dismiss" activationType="system"/></actions></toast>')
    script = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] "
        "| Out-Null\n"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null\n"
        "$x = New-Object Windows.Data.Xml.Dom.XmlDocument\n"
        f"$x.LoadXml(@'\n{xml}\n'@)\n"
        "$t = New-Object Windows.UI.Notifications.ToastNotification $x\n"
        f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{POWERSHELL_AUMID}').Show($t)\n"
    )
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                                capture_output=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode == 0:
            return
        log(f"   (la notificación de Windows ha fallado: {result.stderr.decode(errors='ignore').strip()[:200]})")
    except Exception as e:  # noqa: BLE001
        log(f"   (la notificación de Windows ha fallado: {e})")
    popup(title, lines)


def popup(title, lines):
    """Ventana emergente siempre encima, en un hilo para no bloquear la vigilancia."""
    def show():
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, "\n".join(lines), title, 0x40 | 0x1000 | 0x40000)  # info|system|topmost
    threading.Thread(target=show, daemon=True).start()


_log_file = None


def log(text):
    line = f"[{datetime.datetime.now():%d/%m %H:%M:%S}] {text}"
    print(line, flush=True)
    if _log_file:
        _log_file.write(line + "\n")
        _log_file.flush()


def _fmt(epoch):
    return datetime.datetime.fromtimestamp(float(epoch)).strftime("%d/%m %H:%M")


def report(client_name, alerts):
    """Escribe en consola y log el detalle de las alertas nuevas de un cliente."""
    names = {}
    for a in alerts:
        names.setdefault(a.get("savedsearch_name", "?"), []).append(a)
    for name, items in names.items():
        results = sum(int(float(a.get("result_count") or 0)) for a in items)
        log(f"🚨 {client_name}: {name}  ({len(items)} vez/veces, {results} resultados, "
            f"{_fmt(items[0]['t'])} - {_fmt(items[-1]['t'])}) [{items[-1].get('alert_actions', '')}]")


def summary_lines(by_client):
    """Dos lineas para la notificacion: clientes con su numero de alertas y la alerta mas reciente."""
    clients = ", ".join(f"{name} ({len(alerts)})" for name, alerts in by_client.items())
    latest = sorted(((float(a.get("t") or 0), name, a.get("savedsearch_name", "?"))
                     for name, alerts in by_client.items() for a in alerts), reverse=True)
    last = f"Ultima: {latest[0][1]} - {latest[0][2]}" if latest else ""
    return [clients, last]


def notify(by_client):
    total = sum(len(a) for a in by_client.values())
    toast(f"🚨 {total} alerta(s) nueva(s) en {len(by_client)} cliente(s)", summary_lines(by_client))


# --- Consulta ---

class Watcher:
    def __init__(self, clients, interval, since_minutes, state_path):
        self.clients = clients
        self.interval = interval
        self.state_path = state_path
        self.services = {}
        self.errors = {}
        self.last, self.seen = load_state(state_path)
        start = time.time() - since_minutes * 60
        for c in clients:
            self.last.setdefault(c["name"], start)

    def _service(self, client):
        service = self.services.get(client["name"])
        if service is not None:
            return service
        error = None
        for method in auth_methods(client):
            try:
                service = _connect(client, method)
                self.services[client["name"]] = service
                return service
            except Exception as e:  # noqa: BLE001
                error = e
        raise RuntimeError(f"no conecta ({error or 'sin credenciales'})")

    def _query(self, client, earliest):
        try:
            return oneshot_results(self._service(client), FIRED_SPL.format(earliest=int(earliest)))
        except Exception:
            # Sesión caducada u otro fallo: se reconecta una vez
            self.services.pop(client["name"], None)
            return oneshot_results(self._service(client), FIRED_SPL.format(earliest=int(earliest)))

    def check(self, client):
        name = client["name"]
        started = time.time()
        try:
            rows = self._query(client, self.last[name] - OVERLAP)
        except Exception as e:  # noqa: BLE001
            return name, None, str(e)[:300]
        self.last[name] = started
        return name, rows, None

    def cycle(self):
        with ThreadPoolExecutor(8) as pool:
            results = list(pool.map(self.check, self.clients))
        by_client = {}
        for name, rows, error in results:
            if error:
                if self.errors.get(name) != error:
                    log(f"⚠️ {name}: {error}")
                    toast(f"⚠️ Vigilancia: {name} no responde", [error[:200]])
                self.errors[name] = error
                continue
            if name in self.errors:
                log(f"✅ {name}: vuelve a responder")
                self.errors.pop(name)
            try:
                fired = new_alerts(rows, name, self.seen)
                if fired:
                    by_client[name] = fired
                    report(name, fired)
            except Exception as e:  # noqa: BLE001
                log(f"❌ {name}: error procesando resultados: {e}")
        total = sum(len(a) for a in by_client.values())
        if by_client:
            notify(by_client)
        self.seen = save_state(self.state_path, self.last, self.seen)
        ok = len(results) - len(self.errors)
        log(f"Ciclo terminado: {ok}/{len(results)} clientes consultados, {total} alerta(s) nueva(s)"
            + (f"; sin respuesta: {', '.join(sorted(self.errors))}" if self.errors else ""))

    def run(self):
        while True:
            started = time.time()
            try:
                self.cycle()
            except Exception as e:  # noqa: BLE001
                log(f"❌ Error en el ciclo: {e}")
            time.sleep(max(10, self.interval * 60 - (time.time() - started)))


def main(argv=None):
    global _log_file
    parser = argparse.ArgumentParser(prog="python -m options.alerts.watch_alerts",
                                     description="Avisa en el escritorio de las alertas que se disparan en los Splunks.")
    parser.add_argument("-c", "--clientes", default="todos", help="'todos' o nombres separados por comas")
    parser.add_argument("--excluir", default="", help="clientes a excluir, separados por comas")
    parser.add_argument("--intervalo", type=float, default=5, help="minutos entre consultas (por defecto 5)")
    parser.add_argument("--desde", type=int, default=15,
                        help="al arrancar sin estado previo, minutos hacia atrás que se revisan (por defecto 15)")
    parser.add_argument("--reiniciar", action="store_true", help="ignora el estado guardado (vuelve a avisar de todo)")
    parser.add_argument("--probar-aviso", action="store_true", help="muestra una notificación de prueba y termina")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if args.probar_aviso:
        toast("🚨 PRUEBA: 1 alerta(s)", ["Aviso de prueba de la vigilancia de alertas", "Si ves esto, funciona"])
        return 0

    clients = get_clients_from_config()
    if args.clientes.lower() != "todos":
        wanted = {n.strip().upper() for n in args.clientes.split(",") if n.strip()}
        clients = [c for c in clients if c["name"].upper() in wanted]
    excluded = {n.strip().upper() for n in args.excluir.split(",") if n.strip()}
    clients = [c for c in clients if c["name"].upper() not in excluded]
    if not clients:
        print("No hay clientes que vigilar.")
        return 1

    state_path = os.path.join(logs_dir(), STATE_FILE)
    if args.reiniciar and os.path.exists(state_path):
        os.remove(state_path)
    _log_file = open(os.path.join(logs_dir(), f"{datetime.datetime.now():%Y%m%d}_vigilancia-alertas.log"), "a",
                     encoding="utf-8")
    log(f"Vigilando {len(clients)} clientes cada {args.intervalo:g} min"
        + (f" (excluidos: {', '.join(sorted(excluded))})" if excluded else "") + ". Ctrl+C para parar.")
    try:
        Watcher(clients, args.intervalo, args.desde, state_path).run()
    except KeyboardInterrupt:
        log("Vigilancia detenida.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
