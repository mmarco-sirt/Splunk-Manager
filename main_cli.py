"""
Splunk Manager por consola.

Sin argumentos abre el menú interactivo. Con argumentos se ejecuta sin preguntar nada, para programarlo con el
Programador de tareas de Windows (ver options/batch/non_interactive.py y python main_cli.py --help), p. ej.:
    python main_cli.py --backup alertas macros --clientes todos
    python main_cli.py --informe --clientes todos --dias 7
"""
import sys

from core.clients import ensure_config_exists, get_clients_from_config, manage_clients_cli, migrate_config_cli
from core.connection import select_client, connect_to_splunk
from core.paths import backup_dir
from options.alerts.backup_alerts import TECNOLOGIAS, backup_alerts, backup_alerts_especific, backup_alerts_custom, backup_es_use_cases, backup_es_use_cases_custom
from options.alerts.restore_alerts import restore_alerts
from options.alerts.list_alerts import list_active_cases
from options.alerts.toggle_alerts import toggle_alerts_cli
from options.macros.backup_macros import backup_macros
from options.macros.restore_macros import restore_macros
from options.users.backup_users import backup_users
from options.users.restore_users import restore_users
from options.dashboards.backup_dashboard import backup_dashboard
from options.dashboards.restore_dashboard import restore_dashboard
from options.reports.backup_reports import backup_reports
from options.roles.backup_roles import backup_roles
from options.roles.restore_roles import restore_roles
from options.batch.batch_mode import batch_backup_alerts, batch_restore_alerts, multi_alert_backup_same_client, batch_restore_multiple_alerts_same_client

def get_multiple_clients_selection(all_clients):
    """Abstracción para seleccionar uno o varios clientes de la lista."""
    print("\nClientes disponibles:")
    for i, c in enumerate(all_clients, 1):
        print(f"{i}) {c['name']}")

    seleccion = input("Ingrese los NÚMEROS de los clientes separados por coma (ej: 1,3,5): ")
    try:
        indices = [int(x.strip()) - 1 for x in seleccion.split(",")]
        selected = [all_clients[i] for i in indices if 0 <= i < len(all_clients)]
        return selected
    except (ValueError, IndexError):
        print("Error: Selección de clientes inválida.")
        return []

def alert_analysis_cli(service):
    """Muestra las alertas ruidosas, muertas y con problemas de planificador del cliente."""
    from web.alert_analysis import ANALYSIS_DAYS, analyze_alerts, report_lists
    choice = input(f"Periodo en días ({'/'.join(map(str, ANALYSIS_DAYS))}) [7]: ").strip() or "7"
    if not choice.isdigit() or int(choice) not in ANALYSIS_DAYS:
        print("Periodo no válido.")
        return
    print("Analizando el log del planificador (puede tardar un poco)...")
    try:
        result = analyze_alerts(service, int(choice))
    except Exception as e:
        print(f"❌ Error en el análisis de alertas: {e}")
        return
    summary, lists = result["summary"], report_lists(result)
    print(f"\n{summary['active']} alertas activas · {summary['fired']} disparos ({summary['fired_avg_day']:.1f} al día) · "
          f"{summary['skipped']} ejecuciones saltadas · {summary['errors']} con error")
    for title, key in (("Ruidosas", "noisy"), ("Muertas", "dead"), ("Problemas del planificador", "scheduler")):
        items = lists[key]
        print(f"\n{title} ({len(items)}):")
        for item in items[:20]:
            trend = f" · tendencia: {item['trend']}" if key == "noisy" and item["trend"] else ""
            print(f"  - {item['name']}: {item['reason']}{trend}")
        if len(items) > 20:
            print(f"  ... y {len(items) - 20} más (el informe Excel las incluye todas)")


def interactive():
    backup_dir()  # Asegurar que la carpeta 'backups/' existe
    ensure_config_exists()
    migrate_config_cli()  # Cifra las credenciales que sigan en claro en config.py

    clientes = get_clients_from_config()
    if not clientes:
        print("No hay clientes configurados. Añada uno a continuación.")
        manage_clients_cli()
        clientes = get_clients_from_config()
        if not clientes:
            print("Sin clientes no se puede continuar. Saliendo...")
            return

    current_client = select_client(clientes)
    service = connect_to_splunk(current_client)

    if not service:
        print("No se pudo conectar a Splunk. Saliendo...")
        return

    while True:
        print(f"\n[Cliente: {current_client['name']}] Seleccione una opción:")
        print("1) Backup Alerts")
        print("2) Restore Alerts")
        print("3) Backup Macros")
        print("4) Restore Macros")
        print("5) Backup Users")
        print("6) Restore Users")
        print("7) Change Client")
        print("8) Backup Dashboard")
        print("9) Restore Dashboard")
        print("10) List Alerts")
        print("11) Backup Reports")
        print("12) Batch Backup Alerts")
        print("13) Batch Restore Alerts")
        print("14) Multi Alert Backup Same Client")
        print("15) Backup Roles")
        print("16) Restore Roles")
        print("17) Gestionar Clientes (Añadir/Modificar)")
        print("18) Multi Alert Restore Same Client")
        print("19) Habilitar / Deshabilitar alertas")
        print("20) Análisis de alertas (ruidosas, muertas, planificador)")
        print("0) Exit")

        choice = input("Ingrese una opción: ")

        if choice == "1":
            backup_choice = input("Seleccione el tipo de backup: 1) General (Todas) 2) Específico (Por tecnologia) 3) Custom 4) Enterprise Security: 5) Enterprise Security Custom: ")
            if backup_choice == "1":
                backup_alerts(service)
            elif backup_choice == "4":
                backup_es_use_cases(service)
            elif backup_choice == "5":
                custom = input("Escriba el nombre de la alerta: ")
                backup_es_use_cases_custom(service, custom)
            elif backup_choice == "2":
                print("Seleccione la tecnología:")
                for i, tech in enumerate(TECNOLOGIAS, 1):
                    print(f"{i}) {tech}")
                tech_input = input("Ingrese una opción: ")
                if tech_input.isdigit() and 1 <= int(tech_input) <= len(TECNOLOGIAS):
                    especific = TECNOLOGIAS[int(tech_input) - 1]
                    backup_alerts_especific(service, especific)
                else:
                    print("Opción no válida, intente nuevamente.")
            elif backup_choice == "3":
                custom = input("Escriba el nombre de la alerta: ")
                backup_alerts_custom(service, custom)
            else:
                print("Opción no válida, intente nuevamente.")
        elif choice == "2":
            restore_alerts(service)
        elif choice == "3":
            backup_macros(service)
        elif choice == "4":
            restore_macros(service)
        elif choice == "5":
            backup_users(service)
        elif choice == "6":
            restore_users(service)
        elif choice == "7":
            new_client = select_client(clientes)
            new_service = connect_to_splunk(new_client)
            if new_service:
                current_client, service = new_client, new_service
            else:
                print(f"No se pudo conectar. Se mantiene la conexión con {current_client['name']}.")
        elif choice == "8":
            dash_name = input("Ingrese el nombre del dashboard a respaldar: ")
            backup_dashboard(service, dash_name)
        elif choice == "9":
            restore_dashboard(service)
        elif choice == "10":
            list_active_cases(service)
        elif choice == "11":
            backup_reports(service)
        elif choice == "12":
            targets = get_multiple_clients_selection(clientes)
            if targets:
                batch_backup_alerts(targets)
        elif choice == "13":
            targets = get_multiple_clients_selection(clientes)
            if targets:
                batch_restore_alerts(targets)
        elif choice == "14":
            multi_alert_backup_same_client(service)
        elif choice == "15":
            backup_roles(service)
        elif choice == "16":
            restore_roles(service)
        elif choice == "17":
            manage_clients_cli()
            clientes = get_clients_from_config() or clientes
            print("\nVolviendo al menú principal. Lista de clientes recargada.")
        elif choice == "18":
            batch_restore_multiple_alerts_same_client(service)
        elif choice == "19":
            toggle_alerts_cli(service)
        elif choice == "20":
            alert_analysis_cli(service)
        elif choice == "0":
            print("Saliendo...")
            break
        else:
            print("Opción no válida, intente nuevamente.")

def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        from options.batch.non_interactive import main as run_non_interactive
        sys.exit(run_non_interactive(argv))
    interactive()


if __name__ == "__main__":
    main()
