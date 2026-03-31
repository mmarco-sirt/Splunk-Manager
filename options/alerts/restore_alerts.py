import json
import os
import re
from splunklib.binding import HTTPError

BACKUP_DIR = "backups/alertas"

def list_backup_files():
    """Lista los archivos de backup disponibles en la carpeta 'backups/'."""
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)  # Crear la carpeta si no existe
        return []

    files = [f for f in os.listdir(BACKUP_DIR) if f.startswith("alertas_") if f.endswith(".json")]
    return files

def choose_backup_file():
    """Permite al usuario elegir un archivo de backup para restaurar."""
    files = list_backup_files()
    
    if not files:
        print("No hay archivos de backup disponibles.")
        return None

    print("Seleccione un archivo de backup para restaurar:")
    for i, file in enumerate(files, start=1):
        print(f"{i}. {file}")

    while True:
        try:
            choice = int(input("Ingrese el número del archivo: ")) - 1
            if 0 <= choice < len(files):
                return f"{BACKUP_DIR}/{files[choice]}"
            print("Número fuera de rango, intente nuevamente.")
        except ValueError:
            print("Entrada no válida, ingrese un número.")

def restore_alerts(service):
    """Restaura alertas desde un archivo JSON seleccionado por el usuario."""
    existing_alert_names = {s.name for s in service.saved_searches}
    failed_alerts = []
    file_path = choose_backup_file()
    if not file_path:
        return

    try:
        with open(file_path, "r") as file:
            alerts = json.load(file)

        for alert in alerts:
            if "name" in alert and alert["name"]:
                alert_name = alert["name"]
            else:
                alert_name = f"SIN_NOMBRE_{unnamed_counter}"
                unnamed_counter += 1
                print(f"[ADVERTENCIA] No se encontró el campo 'name' en una alerta. "
                      f"Se creará con el nombre '{alert_name}'. Revisa el JSON.")

            alert_content = alert.get("content", {})

            if "search" not in alert_content or alert_content["search"] is None:
                print(f"No se puede crear/actualizar la alerta '{alert_name}': falta 'search' o es None.")
                failed_alerts.append(alert_name)
                continue

            filtered_content = {
                k: v for k, v in alert_content.items() if v is not None
            }

            try:
                if alert_name in existing_alert_names:
                    print(f"Actualizando alerta: {alert_name}")
                    create_or_update_alert(service, alert_name, filtered_content, is_update=True, sharing="global")
                    print(f"Alerta '{alert_name}' actualizada correctamente.\n")
                else:
                    print(f"Creando alerta: {alert_name}")
                    create_or_update_alert(service, alert_name, filtered_content, is_update=False, sharing="global")
                    print(f"Alerta '{alert_name}' creada correctamente.\n")
                    existing_alert_names.add(alert_name)
            except Exception as e:
                print(f"Error al procesar la alerta '{alert_name}': {e}\n")
                failed_alerts.append(alert_name)

    except Exception as e:
        print(f"Error al restaurar alertas: {e}")


def restore_alerts_from_file(service, backup_file):
    """Restaura alertas desde un archivo JSON concreto"""
    with open(backup_file, "r", encoding="utf-8") as f:
        alerts = json.load(f)

    for alert in alerts:
        name = alert["name"]
        content = alert["content"]

        if name in service.saved_searches:
            print(f"🔄 Actualizando alerta: {name}")
            service.saved_searches[name].update(**content)
        else:
            print(f"➕ Creando alerta: {name}")
            service.saved_searches.create(name, **content)

    print(f"Restore completado desde {os.path.basename(backup_file)}")


def create_or_update_alert(service, alert_name, attributes, is_update=False, max_retries=5, sharing="global"):
    """
    Intenta crear o actualizar una alerta con los 'attributes' proporcionados.
    Si Splunk rechaza algún campo, lo elimina y reintenta hasta max_retries veces.
    """
    attempt = 0
    while attempt < max_retries:
        attempt += 1
        try:
            if is_update:
                saved_search = service.saved_searches[alert_name]
                saved_search.update(**attributes)
                return 
            else:
                service.saved_searches.create(alert_name, **attributes)
                return 
        except HTTPError as he:
            error_msg = str(he)
            #print(f"Splunk devolvió error: {error_msg}")

            pattern = r'Argument\s+"([^"]+)"\s+is not supported'
            match = re.search(pattern, error_msg)

            if match:
                bad_field = match.group(1)

                if bad_field in attributes:
                    #print(f" - Eliminando el campo no soportado: {bad_field} y reintentando...")
                    del attributes[bad_field]
                    continue 
                else:
                    print(f" - El campo '{bad_field}' no se encuentra en los atributos. Abortando.")
                    raise he
            else:
                print(" - No se pudo identificar el campo conflictivo. Abortando.")
                raise he

    raise Exception(f"No se pudo crear/actualizar la alerta '{alert_name}' tras {max_retries} reintentos.")

def restore_alerts_es(service):
    """Restaura alertas desde un archivo JSON seleccionado por el usuario."""
    existing_alert_names = {s.name for s in service.saved_searches}
    failed_alerts = []
    file_path = choose_backup_file()
    if not file_path:
        return

    try:
        with open(file_path, "r") as file:
            alerts = json.load(file)

        for alert in alerts:
            if "name" in alert and alert["name"]:
                alert_name = alert["name"]
            else:
                alert_name = f"SIN_NOMBRE_{unnamed_counter}"
                unnamed_counter += 1
                print(f"[ADVERTENCIA] No se encontró el campo 'name' en una alerta. "
                      f"Se creará con el nombre '{alert_name}'. Revisa el JSON.")

            alert_app = alert["app"]
            alert_content = alert.get("content", {})

            if "search" not in alert_content or alert_content["search"] is None:
                print(f"No se puede crear/actualizar la alerta '{alert_name}': falta 'search' o es None.")
                failed_alerts.append(alert_name)
                continue

            filtered_content = {
                k: v for k, v in alert_content.items() if v is not None
            }

            try:
                if alert_name in existing_alert_names:
                    print(f"Actualizando alerta: {alert_name}")
                    create_or_update_alert_es(service, alert_name, alert_app, filtered_content, is_update=True, sharing="global")
                    print(f"Alerta '{alert_name}' actualizada correctamente.\n")
                else:
                    print(f"Creando alerta: {alert_name}")
                    create_or_update_alert_es(service, alert_name, alert_app, filtered_content, is_update=False, sharing="global")
                    print(f"Alerta '{alert_name}' creada correctamente.\n")
                    existing_alert_names.add(alert_name)
            except Exception as e:
                print(f"Error al procesar la alerta '{alert_name}': {e}\n")
                failed_alerts.append(alert_name)

    except Exception as e:
        print(f"Error al restaurar alertas: {e}")

def create_or_update_alert_es(service, alert_name, alert_app, attributes, is_update=False, max_retries=5, sharing="global"):
    """
    Intenta crear o actualizar una alerta con los 'attributes' proporcionados.
    Si Splunk rechaza algún campo, lo elimina y reintenta hasta max_retries veces.
    """
    attempt = 0
    while attempt < max_retries:
        attempt += 1
        try:
            if is_update:
                saved_search = service.saved_searches[alert_name]
                saved_search.update(**attributes)
                return 
            else:
                service.saved_searches.create(alert_name, alert_app, **attributes)
                return 
        except HTTPError as he:
            error_msg = str(he)
            #print(f"Splunk devolvió error: {error_msg}")

            pattern = r'Argument\s+"([^"]+)"\s+is not supported'
            match = re.search(pattern, error_msg)

            if match:
                bad_field = match.group(1)

                if bad_field in attributes:
                    #print(f" - Eliminando el campo no soportado: {bad_field} y reintentando...")
                    del attributes[bad_field]
                    continue 
                else:
                    print(f" - El campo '{bad_field}' no se encuentra en los atributos. Abortando.")
                    raise he
            else:
                print(" - No se pudo identificar el campo conflictivo. Abortando.")
                raise he

    raise Exception(f"No se pudo crear/actualizar la alerta '{alert_name}' tras {max_retries} reintentos.")