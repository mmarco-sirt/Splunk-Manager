"""
Vigilancia de alertas: consulta cada pocos minutos todos los Splunks y avisa en el escritorio de Windows de cada alerta
que se dispara. Pensado para cuando no llega el correo (incidencias de Microsoft).

Fuente: el log del planificador (index=_internal sourcetype=scheduler), donde aparece toda búsqueda programada que ha
ejecutado acciones. Solo se avisan las que han enviado el correo y lo envían a soc@sirt.com (REQUIRED_RECIPIENT;
destinatarios leídos de la configuración de cada alerta), salvo los nombres de IGNORE_NAMES / IGNORE_BY_CLIENT.

Uso (desde la raíz del proyecto):
    python -m options.alerts.watch_alerts --excluir GRANOLLERS
    python -m options.alerts.watch_alerts --excluir GRANOLLERS --teams     # también al canal de Teams del SOC
    python -m options.alerts.watch_alerts --excluir GRANOLLERS --intervalo 5 --desde 60
Ctrl+C para parar.
"""
import argparse
import base64
import datetime
import fnmatch
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from core.clients import get_clients_from_config
from core.connection import _connect, auth_methods
from core import teams, vault
from core.paths import logs_dir
from web.explorer import oneshot_results

# Solo se avisan las alertas cuyo correo (action.email.to/cc/bcc) incluye esta dirección
REQUIRED_RECIPIENT = "soc@sirt.com"
RECIPIENTS_TTL = 30 * 60       # cada cuánto se vuelven a leer los destinatarios de las alertas de cada Splunk
RECIPIENTS_RETRY = 5 * 60      # si salta una alerta desconocida, se releen como mucho con esta frecuencia
# Búsquedas que se descartan aunque cumplan lo anterior. Comodines fnmatch, sin mayúsculas.
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

def parse_emails(*values):
    """Direcciones de correo (en minúsculas) de los campos action.email.to/cc/bcc, separadas por comas, ; o espacios."""
    found = set()
    for value in values:
        for part in re.split(r"[,;\s]+", str(value or "")):
            if "@" in part:
                found.add(part.strip().strip("<>\"'").lower())
    return found


def recipients_map(entries):
    """
    {(app, nombre): destinatarios} de las búsquedas guardadas (entradas JSON de /servicesNS/-/-/saved/searches), más
    {("", nombre): destinatarios} uniendo todas las apps, por si la app del log no coincide.
    """
    result = {}
    for entry in entries:
        content = entry.get("content") or {}
        emails = parse_emails(content.get("action.email.to"), content.get("action.email.cc"),
                              content.get("action.email.bcc"))
        name, app = entry.get("name") or "", (entry.get("acl") or {}).get("app") or ""
        result.setdefault((app, name), set()).update(emails)
        result.setdefault(("", name), set()).update(emails)
    return result


def recipients_of(row, recipients):
    name = row.get("savedsearch_name") or ""
    found = recipients.get((row.get("app") or "", name))
    return found if found is not None else recipients.get(("", name))


def _actions(row):
    raw = row.get("alert_actions") or ""
    raw = ",".join(raw) if isinstance(raw, list) else raw
    return {a.strip() for a in raw.split(",") if a.strip()}


def is_alert(row, client_name, recipients, required=REQUIRED_RECIPIENT):
    """
    True si la ejecución del planificador es una alerta productiva que hay que avisar: ha enviado el correo (acción
    email) y entre sus destinatarios (to, cc o bcc) está 'required'. Así quedan fuera Cyences, notables sin correo,
    pruebas que solo van a una persona, etc.
    """
    if "email" not in _actions(row):
        return False
    name = (row.get("savedsearch_name") or "").strip().lower()
    patterns = IGNORE_NAMES + IGNORE_BY_CLIENT.get(client_name.upper(), [])
    if any(fnmatch.fnmatch(name, p) for p in patterns):
        return False
    return required.lower() in (recipients_of(row, recipients) or set())


def new_alerts(rows, client_name, seen, recipients, required=REQUIRED_RECIPIENT):
    """Filtra las filas y devuelve las alertas cuyo sid no se ha avisado ya (y las marca como vistas)."""
    found = []
    for row in sorted(rows, key=lambda r: float(r.get("t") or 0)):
        if not is_alert(row, client_name, recipients, required):
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
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        print(line.encode(encoding, errors="replace").decode(encoding), flush=True)
    if _log_file:
        _log_file.write(line + "\n")
        _log_file.flush()


def _fmt(epoch):
    return datetime.datetime.fromtimestamp(float(epoch)).strftime("%d/%m %H:%M")


def detail_lines(alerts):
    """Una línea por alerta distinta: nombre, veces, resultados, horas y acciones."""
    names = {}
    for a in alerts:
        names.setdefault(a.get("savedsearch_name", "?"), []).append(a)
    out = []
    for name, items in names.items():
        results = sum(int(float(a.get("result_count") or 0)) for a in items)
        first, last = _fmt(items[0]["t"]), _fmt(items[-1]["t"])
        when = first if first == last else f"{first} - {last}"
        times = f"{len(items)} veces" if len(items) > 1 else "1 vez"
        out.append(f"{name}  ({times}, {results} resultados, {when}) [{items[-1].get('alert_actions', '')}]")
    return out


def report(client_name, alerts):
    """Escribe en consola y log el detalle de las alertas nuevas de un cliente."""
    for line in detail_lines(alerts):
        log(f"\U0001F6A8 {client_name}: {line}")


def summary_lines(by_client):
    """Dos lineas para la notificacion: clientes con su numero de alertas y la alerta mas reciente."""
    clients = ", ".join(f"{name} ({len(alerts)})" for name, alerts in by_client.items())
    latest = sorted(((float(a.get("t") or 0), name, a.get("savedsearch_name", "?"))
                     for name, alerts in by_client.items() for a in alerts), reverse=True)
    last = f"Ultima: {latest[0][1]} - {latest[0][2]}" if latest else ""
    return [clients, last]


class TeamsChannel:
    """Envío al canal de Teams del SOC. Si falla, lo dice en consola y en el escritorio (una vez hasta que se recupere)."""
    MAX_LINES = 60

    def __init__(self, url):
        self.url = url
        self.failing = False

    def send(self, title, blocks, color="attention"):
        try:
            teams.post(self.url, teams.card(title, blocks, color))
        except Exception as e:  # noqa: BLE001
            log(f"⚠️ Teams: no se ha podido enviar el mensaje ({e})")
            if not self.failing:
                toast("⚠️ Vigilancia: falla el envío a Teams", [str(e)[:200]])
            self.failing = True
            return False
        if self.failing:
            log("✅ Teams: el envío vuelve a funcionar")
        self.failing = False
        return True

    def alerts(self, by_client):
        total = sum(len(a) for a in by_client.values())
        blocks, used = [], 0
        for name, alerts in by_client.items():
            lines = [f"- {line}" for line in detail_lines(alerts)]
            room = max(0, self.MAX_LINES - used)
            if len(lines) > room:
                lines = lines[:room] + [f"- … y {len(lines) - room} más (ver el log de la vigilancia)"]
            used += len(lines)
            blocks.append((f"{name} ({len(alerts)})", lines))
        self.send(f"\U0001F6A8 {total} alerta(s) nueva(s) en {len(by_client)} cliente(s)", blocks)


_teams = None


def notify(by_client):
    total = sum(len(a) for a in by_client.values())
    toast(f"\U0001F6A8 {total} alerta(s) nueva(s) en {len(by_client)} cliente(s)", summary_lines(by_client))
    if _teams:
        _teams.alerts(by_client)


# --- Consulta ---

class Watcher:
    def __init__(self, clients, interval, since_minutes, state_path, heartbeat=0, required=REQUIRED_RECIPIENT):
        self.clients = clients
        self.required = required
        self.recipients = {}
        self.heartbeat = heartbeat
        self.last_heartbeat = time.time()
        self.alerts_since_heartbeat = 0
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

    def _load_recipients(self, client):
        response = self._service(client).get("/servicesNS/-/-/saved/searches", output_mode="json", count=0,
                                             search="is_scheduled=1",
                                             f=["action.email.to", "action.email.cc", "action.email.bcc", "eai:acl"])
        entries = json.loads(response.body.read().decode("utf-8")).get("entry", [])
        self.recipients[client["name"]] = (time.time(), recipients_map(entries))

    def _recipients(self, client, rows):
        """Destinatarios de las alertas del cliente; se releen si caducan o si ha saltado una alerta desconocida."""
        name = client["name"]
        loaded, mapping = self.recipients.get(name, (0, None))
        age = time.time() - loaded
        unknown = mapping is not None and any(
            "email" in _actions(r) and recipients_of(r, mapping) is None for r in rows)
        if mapping is None or age >= RECIPIENTS_TTL or (unknown and age >= RECIPIENTS_RETRY):
            try:
                self._load_recipients(client)
            except Exception:
                self.services.pop(name, None)
                self._load_recipients(client)
        return self.recipients[name][1]

    def check(self, client):
        name = client["name"]
        started = time.time()
        try:
            rows = self._query(client, self.last[name] - OVERLAP)
            recipients = self._recipients(client, rows)
        except Exception as e:  # noqa: BLE001
            return name, None, None, str(e)[:300]
        self.last[name] = started
        return name, rows, recipients, None

    def cycle(self):
        with ThreadPoolExecutor(8) as pool:
            results = list(pool.map(self.check, self.clients))
        by_client = {}
        for name, rows, recipients, error in results:
            if error:
                if self.errors.get(name) != error:
                    log(f"⚠️ {name}: {error}")
                    toast(f"⚠️ Vigilancia: {name} no responde", [error[:200]])
                    if _teams and name not in self.errors:
                        _teams.send(f"⚠️ Vigilancia de alertas: {name} no responde",
                                    [(None, [f"No se pueden consultar sus alertas: {error[:300]}"])], "warning")
                self.errors[name] = error
                continue
            if name in self.errors:
                log(f"✅ {name}: vuelve a responder")
                self.errors.pop(name)
                if _teams:
                    _teams.send(f"✅ Vigilancia de alertas: {name} vuelve a responder", [], "good")
            try:
                fired = new_alerts(rows, name, self.seen, recipients, self.required)
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
        self.alerts_since_heartbeat += total
        if _teams and self.heartbeat and time.time() - self.last_heartbeat >= self.heartbeat * 60:
            _teams.send("💚 Vigilancia de alertas activa",
                        [(None, [f"{self.alerts_since_heartbeat} alerta(s) en los últimos {self.heartbeat:g} min; "
                                 f"{ok}/{len(results)} clientes respondiendo"
                                 + (f" (sin respuesta: {', '.join(sorted(self.errors))})" if self.errors else "")])],
                        "good")
            self.last_heartbeat, self.alerts_since_heartbeat = time.time(), 0
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
    parser.add_argument("--destinatario", default=REQUIRED_RECIPIENT, metavar="CORREO",
                        help=f"solo avisa de las alertas cuyo correo va a esta dirección (por defecto {REQUIRED_RECIPIENT})")
    parser.add_argument("--probar-aviso", action="store_true",
                        help="muestra una notificación de prueba (y la envía a Teams con --teams) y termina")
    parser.add_argument("--teams", action="store_true",
                        help="envía también las alertas al canal de Teams (webhook guardado con --guardar-teams)")
    parser.add_argument("--latido", type=float, default=60, metavar="MIN",
                        help="con --teams, mensaje de 'vigilancia activa' cada MIN minutos (0 = nunca; 60 por defecto)")
    parser.add_argument("--guardar-teams", action="store_true",
                        help="pide la URL del webhook de Teams, la guarda cifrada en config.py y termina")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    global _teams
    if args.guardar_teams:
        teams.save_webhook(input("URL del webhook de Teams: "))
        print("Webhook de Teams guardado (cifrado) en config.py.")
        return 0
    if args.teams:
        try:
            url = teams.get_webhook()
        except vault.VaultError as e:
            print(f"No se puede descifrar el webhook de Teams de config.py ({e}); vuelva a guardarlo con --guardar-teams.")
            return 1
        if not url:
            print("No hay webhook de Teams guardado: use --guardar-teams.")
            return 1
        _teams = TeamsChannel(url)
    if args.probar_aviso:
        toast("🚨 PRUEBA: 1 alerta(s)", ["Aviso de prueba de la vigilancia de alertas", "Si ves esto, funciona"])
        if _teams:
            ok = _teams.send("🧪 Prueba de la vigilancia de alertas",
                             [(None, ["Mensaje de prueba: si lo ves aquí, los avisos de alertas llegarán a este canal."])],
                             "default")
            print("Mensaje de prueba enviado a Teams." if ok else "No se ha podido enviar a Teams (ver arriba).")
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
    scope = f"{len(clients)} clientes cada {args.intervalo:g} min" + (
        f" (excluidos: {', '.join(sorted(excluded))})" if excluded else "") + f", alertas con correo a {args.destinatario}"
    log(f"Vigilando {scope}" + (" con avisos también en Teams" if _teams else "") + ". Ctrl+C para parar.")
    if _teams:
        _teams.send("▶️ Vigilancia de alertas iniciada", [(None, [f"Se vigilan {scope}."])], "good")
    try:
        Watcher(clients, args.intervalo, args.desde, state_path, args.latido if _teams else 0,
                args.destinatario.strip()).run()
    except KeyboardInterrupt:
        log("Vigilancia detenida.")
        if _teams:
            _teams.send("⏹️ Vigilancia de alertas detenida",
                        [(None, ["A partir de ahora no se avisará en este canal hasta que se vuelva a iniciar."])],
                        "warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
