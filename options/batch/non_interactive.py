"""
Modo no interactivo del CLI, pensado para el Programador de tareas de Windows: backups, informes, habilitar o
deshabilitar alertas, actualizar add-ons de Splunkbase, inventarios de la plataforma (estado, HEC, listas de IP,
tokens, índices), exportación de apps y reinicio vía ACS, en uno o varios clientes sin preguntar nada. Todo lo que se
muestra se guarda también en logs/AAAAMMDD-HHMMSS_<acción>.log (se conservan los MAX_LOGS más recientes).

Código de salida: 0 = todo correcto, 1 = alguna operación falló (ver el log), 2 = argumentos o configuración no válidos.
"""
import argparse
import os
import sys
import time
import traceback

from core import audit
from core.clients import ensure_config_exists, get_clients_from_config, migrate_config
from core.paths import LOGS_ROOT, logs_dir
from core.splunkbase_account import ENV_PASSWORD, ENV_USERNAME, encrypt_if_plain, get_account
from options.addons.update_addons import list_pending, parse_names, update_addons
from options.alerts.backup_alerts import backup_alerts, backup_es_use_cases
from options.alerts.toggle_alerts import parse_patterns, set_alerts_enabled
from options.platform import inventory as platform_cli
from options.macros.backup_macros import backup_macros
from options.reports.backup_reports import backup_reports
from options.roles.backup_roles import backup_roles
from options.users.backup_users import backup_users
from web import services
from web.jobs import ERROR_PATTERN, WARNING_PATTERN, install_output_router
from web.operations import run_for_clients

EXIT_OK, EXIT_FAILED, EXIT_USAGE = 0, 1, 2
MAX_LOGS = 200

BACKUP_KINDS = {
    "alertas": ("alertas CU-* y TEST - CU-*", backup_alerts),
    "es": ("casos de uso de Enterprise Security", backup_es_use_cases),
    "reportes": ("reportes", backup_reports),
    "macros": ("macros", backup_macros),
    "usuarios": ("usuarios", backup_users),
    "roles": ("roles", backup_roles),
}
REPORT_SECTIONS = {"salud": "health", "ingesta": "ingest", "alertas": "alerts", "analisis": "alert_analysis",
                   "indices": "indexes", "usuarios": "users", "plataforma": "platform"}
INVENTORIES = {   # --<flag>: (título, función(service, client))
    "estado": ("Estado del stack, mantenimiento y token", platform_cli.show_status),
    "hec": ("Tokens HEC", platform_cli.inventory_hec),
    "acceso": ("Listas de IP permitidas y puertos de salida", platform_cli.inventory_access),
    "tokens": ("Tokens de autenticación", platform_cli.inventory_tokens),
    "indices": ("Índices", platform_cli.inventory_indexes),
}
ALL_CLIENTS = ("todos", "all", "*")

EXAMPLES = """
Ejemplos:
  main_cli.py --backup alertas macros --clientes todos
  main_cli.py --backup todo --clientes todos --excluir PRUEBAS
  main_cli.py --informe --clientes todos --dias 7 --formatos xlsx,pdf
  main_cli.py --informe --por-cliente --clientes CLIENTE1,CLIENTE2 --secciones salud,analisis
  main_cli.py --deshabilitar "CU-AD-001,CU-FW-*" --clientes todos --simular
  main_cli.py --addons --clientes todos
  main_cli.py --actualizar-addons "Splunk_TA_windows,Splunk_SA_CIM" --clientes CLIENTE1 --simular
  main_cli.py --actualizar-addons todos --sin-mayores --clientes todos
  main_cli.py --estado --hec --acceso --tokens --indices --clientes todos
  main_cli.py --exportar-apps "sirt_soc_app_for_splunk" --clientes todos
  main_cli.py --reiniciar --clientes CLIENTE1 --simular

Sin argumentos se abre el menú interactivo. Código de salida: 0 correcto, 1 algo falló (ver logs/), 2 argumentos no válidos.
"""


class UsageError(Exception):
    """Argumentos o configuración no válidos."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"Error: {message}\n")


def build_parser():
    parser = _Parser(
        prog="main_cli.py", add_help=False, epilog=EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Splunk Manager por consola. Con argumentos se ejecuta sin preguntar nada (para tareas programadas).")
    actions = parser.add_argument_group("Acciones (se pueden combinar y se ejecutan en este orden)")
    actions.add_argument("--backup", nargs="+", metavar="TIPO", help=f"backup de: {', '.join(BACKUP_KINDS)} o todo")
    actions.add_argument("--habilitar", metavar="NOMBRES", help="habilita alertas (separadas por comas; admite comodines: CU-AD-*)")
    actions.add_argument("--deshabilitar", metavar="NOMBRES", help="deshabilita alertas (igual que --habilitar)")
    actions.add_argument("--informe", action="store_true", help="genera un informe en informes/")
    actions.add_argument("--addons", action="store_true",
                         help="muestra las apps y add-ons con actualización pendiente en Splunkbase")
    actions.add_argument("--actualizar-addons", metavar="NOMBRES",
                         help="actualiza apps/add-ons de Splunkbase vía ACS: 'todos' o nombres separados por comas "
                              "(admite comodines)")
    actions.add_argument("--estado", action="store_true",
                         help="estado del stack (ACS): experiencia, versión, reinicio pendiente, mantenimiento y caducidad del token")
    actions.add_argument("--hec", action="store_true", help="inventario de tokens HEC (ACS) con backup en backups/hec/")
    actions.add_argument("--acceso", action="store_true",
                         help="inventario de listas de IP permitidas y puertos de salida (ACS) con backup en backups/acceso/")
    actions.add_argument("--tokens", action="store_true", help="tokens de autenticación del stack (ACS) con backup en backups/tokens/")
    actions.add_argument("--indices", action="store_true", help="inventario de índices con backup en backups/indices/")
    actions.add_argument("--exportar-apps", metavar="NOMBRES",
                         help="exporta apps como .tgz vía ACS a backups/apps/: 'todos' o nombres separados por comas")
    actions.add_argument("--reiniciar", action="store_true", help="reinicia el stack vía ACS (rolling restart en clusters)")
    actions.add_argument("--listar-clientes", action="store_true", help="muestra los clientes configurados y termina")
    options = parser.add_argument_group("Clientes")
    options.add_argument("-c", "--clientes", metavar="CLIENTES", help="'todos' o nombres separados por comas")
    options.add_argument("--excluir", metavar="CLIENTES", help="clientes a excluir, separados por comas (útil con 'todos')")
    report = parser.add_argument_group("Informe")
    report.add_argument("--secciones", metavar="LISTA", help=f"{', '.join(REPORT_SECTIONS)} (por defecto, todas)")
    report.add_argument("--formatos", metavar="LISTA", help="xlsx, html, pdf (por defecto xlsx y pdf; html si no hay Edge)")
    report.add_argument("--dias", type=int, choices=(7, 30, 90), default=7,
                        help="días de ingesta; el análisis de alertas usa como mucho 30 (por defecto 7)")
    report.add_argument("--umbral", type=int, default=24, metavar="HORAS",
                        help="horas sin datos para considerar que una fuente ha dejado de enviar (24)")
    report.add_argument("--por-cliente", action="store_true", help="un informe por cliente en lugar de uno conjunto")
    other = parser.add_argument_group("Otros")
    other.add_argument("--simular", action="store_true",
                       help="con --habilitar/--deshabilitar/--actualizar-addons/--exportar-apps/--reiniciar: muestra qué "
                            "cambiaría o la petición que se enviaría a ACS sin tocar nada")
    other.add_argument("--sin-mayores", action="store_true",
                       help="con --actualizar-addons: no actualiza las apps que cambian de versión mayor")
    other.add_argument("--sin-local", action="store_true", help="con --exportar-apps: no incluir la carpeta local/ de la app")
    other.add_argument("--sin-usuarios", action="store_true", help="con --exportar-apps: no incluir las carpetas de usuarios")
    other.add_argument("--splunkbase-usuario", metavar="USUARIO",
                       help=f"cuenta de Splunkbase para --actualizar-addons (contraseña en la variable de entorno "
                            f"{ENV_PASSWORD}); si no se indica, se usa la guardada en config.py")
    other.add_argument("--sin-log", action="store_true", help="no guardar el log en logs/")
    other.add_argument("-h", "--help", action="help", help="muestra esta ayuda y termina")
    return parser


def _split(text):
    return [part.strip() for part in str(text or "").split(",") if part.strip()]


def resolve_clients(spec, exclude, all_clients):
    """Clientes de --clientes ('todos' o nombres, sin distinguir mayúsculas) menos los de --excluir."""
    if not spec:
        raise UsageError("Indique los clientes con --clientes (todos o nombres separados por comas).")
    by_name = {c["name"].lower(): c for c in all_clients}

    def lookup(names):
        unknown = [n for n in names if n.lower() not in by_name]
        if unknown:
            raise UsageError(f"Clientes desconocidos: {', '.join(unknown)}. Disponibles: "
                             f"{', '.join(c['name'] for c in all_clients) or '(ninguno)'}")
        return [by_name[n.lower()]["name"] for n in names]

    names = [c["name"] for c in all_clients] if spec.strip().lower() in ALL_CLIENTS else lookup(_split(spec))
    excluded = set(lookup(_split(exclude)))
    selected = [n for n in dict.fromkeys(names) if n not in excluded]
    if not selected:
        raise UsageError("No queda ningún cliente seleccionado.")
    return selected


def backup_kinds(values):
    kinds = []
    for value in values or []:
        for kind in _split(value.lower()):
            if kind == "todo":
                kinds += list(BACKUP_KINDS)
            elif kind in BACKUP_KINDS:
                kinds.append(kind)
            else:
                raise UsageError(f"Tipo de backup desconocido: {kind}. Tipos: {', '.join(BACKUP_KINDS)}, todo.")
    return list(dict.fromkeys(kinds))


def report_options(args):
    """Secciones, formatos y opciones del informe; los avisos (p. ej. PDF no disponible) se devuelven aparte."""
    from web.reports import SECTIONS, formats
    sections = []
    for name in _split(args.secciones) or list(SECTIONS):
        key = REPORT_SECTIONS.get(name.lower(), name.lower())
        if key not in SECTIONS:
            raise UsageError(f"Sección desconocida: {name}. Secciones: {', '.join(REPORT_SECTIONS)}.")
        sections.append(key)
    available = formats()
    requested = [f.lower() for f in _split(args.formatos)] or (["xlsx", "pdf"] if "pdf" in available else ["xlsx", "html"])
    unknown = [f for f in requested if f not in ("xlsx", "html", "pdf")]
    if unknown:
        raise UsageError(f"Formato desconocido: {', '.join(unknown)}. Formatos: xlsx, html, pdf.")
    warnings = []
    if "pdf" in requested and "pdf" not in available:
        warnings.append("⚠️ No se encontró Microsoft Edge ni Chrome: el informe no se generará en PDF.")
    output = [f for f in dict.fromkeys(requested) if f in available]
    if not output:
        raise UsageError("Ninguno de los formatos pedidos está disponible.")
    if not 1 <= args.umbral <= 24 * 30:
        raise UsageError("El umbral debe estar entre 1 y 720 horas.")
    return {"sections": list(dict.fromkeys(sections)), "formats": output, "days": args.dias,
            "threshold": args.umbral, "per_client": args.por_cliente}, warnings


def resolve_splunkbase_account(args, environ=None):
    """Cuenta de Splunkbase para --actualizar-addons: --splunkbase-usuario + SPLUNKBASE_PASSWORD o la guardada/entorno."""
    environ = os.environ if environ is None else environ
    if args.splunkbase_usuario:
        password = environ.get(ENV_PASSWORD, "")
        if not password:
            raise UsageError(f"Con --splunkbase-usuario la contraseña va en la variable de entorno {ENV_PASSWORD}.")
        return {"username": args.splunkbase_usuario.strip(), "password": password, "source": "argumentos"}
    account = get_account()
    if not account:
        raise UsageError("No hay cuenta de Splunkbase: guárdela desde el menú interactivo (opción 21), indique "
                         f"--splunkbase-usuario y {ENV_PASSWORD}, o las variables {ENV_USERNAME} y {ENV_PASSWORD}.")
    return account


# --- Salida: consola + log ------------------------------------------------------------------

class _Tee:
    """Escribe en la consola y en el log a la vez y cuenta las líneas de error y de aviso (como las tareas de la web)."""

    def __init__(self, console, log_file):
        self.console = console
        self.log = log_file
        self.errors = 0
        self.warnings = 0
        self._partial = ""

    def write(self, text):
        if self.log:
            self.log.write(text)
        if self.console:
            try:
                self.console.write(text)
            except (OSError, ValueError, UnicodeError):
                pass
        *lines, self._partial = (self._partial + text).split("\n")
        for line in lines:
            if ERROR_PATTERN.search(line):
                self.errors += 1
            elif WARNING_PATTERN.search(line):
                self.warnings += 1
        return len(text)

    def flush(self):
        for stream in (self.log, self.console):
            try:
                if stream:
                    stream.flush()
            except (OSError, ValueError):
                pass

    def isatty(self):
        return False


def _new_log_path(label):
    """logs/AAAAMMDD-HHMMSS_<acción>.log, sin pisar el de otra ejecución en el mismo segundo."""
    base = os.path.join(logs_dir(), f"{time.strftime('%Y%m%d-%H%M%S')}_{label}")
    path, counter = f"{base}.log", 1
    while os.path.exists(path):
        path, counter = f"{base}_{counter}.log", counter + 1
    return path


def _prune_logs():
    try:
        files = sorted(f for f in os.listdir(LOGS_ROOT) if f.endswith(".log"))
        for name in files[:-MAX_LOGS]:
            os.remove(os.path.join(LOGS_ROOT, name))
    except OSError:
        pass


def _duration(seconds):
    return f"{seconds:.0f} s" if seconds < 90 else f"{seconds // 60:.0f} min {seconds % 60:.0f} s"


# --- Acciones ------------------------------------------------------------------------------------

def run_backups(names, kinds):
    def action(service):
        for kind in kinds:
            label, backup = BACKUP_KINDS[kind]
            print(f"▶ Backup de {label}")
            try:
                backup(service)
            except Exception as e:
                print(f"❌ Error en el backup de {label}: {e}")
    print(f"\n=== Backup de {', '.join(kinds)} ===")
    run_for_clients(names, action)()


def run_toggle(names, patterns, enabled, dry_run):
    verb = "Habilitar" if enabled else "Deshabilitar"
    print(f"\n=== {verb} {', '.join(patterns)}{' (simulación)' if dry_run else ''} ===")

    def action(service, client):
        result = set_alerts_enabled(service, patterns, enabled, dry_run)
        audit.record(f"alert.{'enable' if enabled else 'disable'}", f"{verb} {', '.join(patterns)}", client.get("name"),
                     {"changed": result["changed"], "failed": result["failed"]}, result="error" if result["failed"] else "ok", dry_run=dry_run)

    run_for_clients(names, action, with_client=True)()


def run_report(names, options):
    from web.reports import build_report
    groups = [[name] for name in names] if options["per_client"] else [names]
    for group in groups:
        title, run = build_report(group, options["sections"], options["formats"],
                                  {"days": options["days"], "threshold": options["threshold"]})
        print(f"\n=== {title} ===")
        run()


def run_pending_addons(names):
    print("\n=== Apps y add-ons con actualización pendiente ===")
    run_for_clients(names, lambda service, client: list_pending(service, client), with_client=True)()


def run_update_addons(names, patterns, dry_run, account, exclude_major=False):
    print(f"\n=== Actualizar add-ons: {', '.join(patterns)}{' sin versiones mayores' if exclude_major else ''}"
          f"{' (simulación)' if dry_run else ''} ===")

    def action(service, client):
        chosen = patterns
        if exclude_major:
            from options.addons.update_addons import pending_with_acs, select_pending
            pending, _ = pending_with_acs(service, client)
            wanted, _ = select_pending(pending, patterns)
            skipped = [a["name"] for a in wanted if a["major"]]
            chosen = [a["name"] for a in wanted if not a["major"]]
            if skipped:
                print(f"· Se dejan fuera por cambiar de versión mayor: {', '.join(skipped)}")
            if not chosen:
                print("· No queda nada que actualizar en este cliente.")
                return
        result = update_addons(service, client, chosen, account=account, dry_run=dry_run)
        audit.record("addons.update", f"Actualizar add-ons: {', '.join(chosen)}", client.get("name"),
                     {"updated": result["updated"], "failed": result["failed"]}, result="error" if result["failed"] else "ok", dry_run=dry_run)

    run_for_clients(names, action, with_client=True)()


def run_inventories(names, keys):
    for key in keys:
        title, fn = INVENTORIES[key]
        print(f"\n=== {title} ===")
        run_for_clients(names, fn, with_client=True)()


def run_export_apps(names, apps, options, dry_run):
    print(f"\n=== Exportar apps: {', '.join(apps)}{' (simulación)' if dry_run else ''} ===")
    run_for_clients(names, lambda service, client: platform_cli.export_apps(service, client, apps, options, dry_run=dry_run),
                    with_client=True)()


def run_restart(names, dry_run):
    print(f"\n=== Reiniciar el stack{' (simulación)' if dry_run else ''} ===")
    run_for_clients(names, lambda service, client: platform_cli.restart(service, client, dry_run=dry_run), with_client=True)()


def _list_clients(clients):
    if not clients:
        print("No hay clientes configurados.")
    for c in clients:
        print(f"{c['name']}\t{c.get('host', '')}")


def main(argv):
    """Punto de entrada del modo no interactivo. Devuelve el código de salida."""
    args = build_parser().parse_args(argv)
    if getattr(sys.stdout, "reconfigure", None):
        sys.stdout.reconfigure(errors="replace")   # consola o redirección sin UTF-8 (Programador de tareas)

    ensure_config_exists()
    try:
        notes = migrate_config()   # Cifra las credenciales que sigan en claro en config.py
    except Exception as e:
        notes = [f"❌ No se pudieron cifrar las credenciales de config.py: {e}"]
    try:
        notes += [n for n in [encrypt_if_plain()] if n]
    except Exception as e:
        notes.append(f"⚠️ No se pudo cifrar la contraseña de Splunkbase de config.py: {e}")
    all_clients = get_clients_from_config()
    if args.listar_clientes:
        for note in notes:
            print(note)
        _list_clients(all_clients)
        return EXIT_OK

    try:
        if args.habilitar and args.deshabilitar:
            raise UsageError("Use --habilitar o --deshabilitar, no los dos a la vez.")
        kinds = backup_kinds(args.backup)
        enable = parse_patterns(args.habilitar) if args.habilitar else []
        disable = parse_patterns(args.deshabilitar) if args.deshabilitar else []
        report, report_warnings = report_options(args) if args.informe else (None, [])
        addons = parse_names(args.actualizar_addons) if args.actualizar_addons else []
        if args.actualizar_addons and not addons:
            raise UsageError("Indique qué actualizar con --actualizar-addons: todos o nombres separados por comas.")
        inventories = [k for k in INVENTORIES if getattr(args, k)]
        export_apps = parse_names(args.exportar_apps) if args.exportar_apps else []
        if args.exportar_apps and not export_apps:
            raise UsageError("Indique qué exportar con --exportar-apps: todos o nombres separados por comas.")
        if not (kinds or enable or disable or report or args.addons or addons or inventories or export_apps or args.reiniciar):
            raise UsageError("Indique al menos una acción: --backup, --habilitar, --deshabilitar, --informe, --addons, "
                             "--actualizar-addons, --estado, --hec, --acceso, --tokens, --indices, --exportar-apps o "
                             "--reiniciar (ver --help).")
        if args.simular and not (enable or disable or addons or export_apps or args.reiniciar):
            raise UsageError("--simular solo se usa con --habilitar, --deshabilitar, --actualizar-addons, --exportar-apps o --reiniciar.")
        if args.sin_mayores and not addons:
            raise UsageError("--sin-mayores solo se usa con --actualizar-addons.")
        export_options = {"default": True, "local": not args.sin_local, "users": not args.sin_usuarios, "confs_only": False}
        account = resolve_splunkbase_account(args) if addons and not args.simular else None
        names = resolve_clients(args.clientes, args.excluir, all_clients)
    except UsageError as e:
        print(f"Error: {e}", file=sys.stderr)
        return EXIT_USAGE

    label = "+".join(a for a, on in (("backup", kinds), ("habilitar", enable), ("deshabilitar", disable),
                                     ("informe", report), ("addons", args.addons), ("actualizar-addons", addons),
                                     *((k, True) for k in inventories), ("exportar-apps", export_apps),
                                     ("reiniciar", args.reiniciar)) if on)
    log_path = None if args.sin_log else _new_log_path(label)
    log_file = open(log_path, "w", encoding="utf-8") if log_path else None
    original = sys.stdout, sys.stderr
    tee = _Tee(sys.stdout, log_file)
    sys.stdout = sys.stderr = tee
    install_output_router()   # los mensajes de conexión acaban en el motivo del error de cada cliente
    started = time.time()
    try:
        print(f"Splunk Manager · {time.strftime('%d/%m/%Y %H:%M:%S')} · {label} · "
              f"{len(names)} {'cliente' if len(names) == 1 else 'clientes'}: {', '.join(names)}")
        for note in notes + report_warnings:
            print(note)
        if kinds:
            run_backups(names, kinds)
        if enable or disable:
            run_toggle(names, enable or disable, bool(enable), args.simular)
        if report:
            run_report(names, report)
        if args.addons:
            run_pending_addons(names)
        if addons:
            run_update_addons(names, addons, args.simular, account, args.sin_mayores)
        if inventories:
            run_inventories(names, inventories)
        if export_apps:
            run_export_apps(names, export_apps, export_options, args.simular)
        if args.reiniciar:
            run_restart(names, args.simular)
    except KeyboardInterrupt:
        print("\n❌ Interrumpido por el usuario.")
    except Exception as e:
        print(f"❌ Error inesperado: {e}")
        print(traceback.format_exc())
    finally:
        for name in names:
            services.disconnect(name)
        print(f"\nTerminado en {_duration(time.time() - started)}: {tee.errors} errores, {tee.warnings} avisos."
              + (f" Log: {log_path}" if log_path else ""))
        sys.stdout, sys.stderr = original
        if log_file:
            log_file.close()
            _prune_logs()
    return EXIT_FAILED if tee.errors else EXIT_OK
