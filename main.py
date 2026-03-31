from config import clientes, clientes_token
from utils import select_client, connect_to_splunk, ensure_backup_dir
from utils_token import select_client_token, connect_to_splunk_token
from options.alerts.backup_alerts import backup_alerts, backup_alerts_especific, backup_alerts_custom, backup_es_use_cases, backup_es_use_cases_custom
from options.alerts.restore_alerts import restore_alerts, restore_alerts_es
from options.alerts.list_alerts import list_active_cases
from options.macros.backup_macros import backup_macros
from options.macros.restore_macros import restore_macros
from options.users.backup_users import backup_users
from options.users.restore_users import restore_users, restore_users2
from options.dashboards.backup_dashboard import backup_dashboard
from options.dashboards.restore_dashboard import restore_dashboard
from options.reports.backup_reports import backup_reports


def main():
    ensure_backup_dir()  # Asegurar que la carpeta 'backups/' existe

    current_client = select_client(clientes)
    service = connect_to_splunk(current_client)
    #current_client = select_client_token(clientes_token)
    #service = connect_to_splunk_token(current_client)

    if not service:
        print("No se pudo conectar a Splunk. Saliendo...")
        return

    while True:
        print("\nSeleccione una opción:")
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
                tecnologias = ["AD", "FW", "AV", "PX", "MAIL", "O365", "Monitoring"]
                print("Seleccione la tecnología:")
                for i, tech in enumerate(tecnologias, 1):
                    print(f"{i}) {tech}")
                tech_choice = int(input("Ingrese una opción: "))
                if 1 <= tech_choice <= len(tecnologias):
                    especific = tecnologias[tech_choice - 1]
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
        #elif choice == "2":
        #    app = input("La alerta es para: 1) General 2) Enterprise Security (ES): ")
        #    if app == "1":
        #        restore_alerts(service)
        #    elif app == "2":
        #        restore_alerts_es(service)
        elif choice == "3":
            backup_macros(service)
        elif choice == "4":
            restore_macros(service)
        elif choice == "5":
            backup_users(service)
        elif choice == "6":
            restore_users(service)
        elif choice == "7":
            current_client = select_client(clientes)
            service = connect_to_splunk(current_client)
        elif choice == "8":
            dash_name = input("Ingrese el nombre del dashboard a respaldar: ")
            backup_dashboard(service, dash_name)
        elif choice == "9":
            restore_dashboard(service)
        elif choice == "10":
            list_active_cases(service)
        elif choice == "11":
            backup_reports(service)
        elif choice == "0":
            print("Saliendo...")
            break
        else:
            print("Opción no válida, intente nuevamente.")

if __name__ == "__main__":
    main()
