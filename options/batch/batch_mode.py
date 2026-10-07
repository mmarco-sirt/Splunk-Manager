import os

from core.backup_files import choose_multiple_backup_files
from core.connection import connect_to_splunk
from options.alerts.backup_alerts import backup_alerts, backup_alerts_custom
from options.alerts.restore_alerts import restore_alerts, restore_alerts_from_file


def batch_backup_alerts(clientes):
    print("\n=== Backup masivo de alertas ===")
    for client in clientes:
        print(f"\nConectando a {client['name']}...")
        service = connect_to_splunk(client)
        if not service:
            print(f"No se pudo conectar a {client['name']}. Continuando...")
            continue
        print(f"➡ Ejecutando backup en {client['name']}...")
        backup_alerts(service)


def batch_restore_alerts(clientes):
    print("\n=== Restore masivo de alertas ===")
    for client in clientes:
        print(f"\nConectando a {client['name']}...")
        service = connect_to_splunk(client)
        if not service:
            print(f"No se pudo conectar a {client['name']}. Continuando...")
            continue
        print(f"➡ Ejecutando restore en {client['name']}...")
        restore_alerts(service)


def multi_alert_backup_same_client(service, alertas=None):
    """Backup de varias alertas (por nombre) en un mismo cliente. Si no se indican, se piden por consola."""
    print("\n=== Backup múltiple en un solo cliente ===")
    if alertas is None:
        alertas = input("Ingrese los nombres separados por coma: ").split(",")
    alertas = [a.strip() for a in alertas if a.strip()]
    if not alertas:
        print("❌ No se indicó ninguna alerta. Operación cancelada.")
        return

    for alerta in alertas:
        print(f"➡ Backup de {alerta}...")
        try:
            backup_alerts_custom(service, alerta)
        except Exception as e:
            print(f"❌ Error en backup de {alerta}: {e}")


def batch_restore_multiple_alerts_same_client(service, backup_files=None):
    """Restaura varios archivos de alertas en un mismo cliente. Si no se indican, se piden por consola."""
    print("\n=== Restore múltiple de alertas en un cliente ===")
    if backup_files is None:
        backup_files = choose_multiple_backup_files("alertas", "alertas_", "backup de alertas")

    for backup_file in backup_files:
        print(f"\n➡ Procesando {os.path.basename(backup_file)}")
        try:
            restore_alerts_from_file(service, backup_file)
        except Exception as e:
            print(f"❌ Error en la restauración de {os.path.basename(backup_file)}: {e}")
