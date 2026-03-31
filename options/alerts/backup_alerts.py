import json
import os

BACKUP_DIR = "backups/alertas"

def backup_alerts(service):
    """Realiza un backup de las alertas en un archivo JSON dentro de la carpeta 'backups/'."""
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)  # Crear la carpeta si no existe

    alert_backup = []

    # Descargar todas las alertas configuradas
    for saved_search in service.saved_searches:
        if saved_search.content.get("alert_type") and saved_search.content["alert_type"] != "none":
            if saved_search.name.startswith("CU-"):
                alert_backup.append({
                    "name": saved_search.name,
                    "content": saved_search.content
                })

    # Guardar el backup en la carpeta 'backups/'
    file_name = f"{BACKUP_DIR}/alertas_{service.host.split('.')[0]}_backup.json"
    with open(file_name, "w") as file:
        json.dump(alert_backup, file, indent=4)

    print(f"Backup completado: alertas guardadas en {file_name}")

def backup_alerts_especific(service, especific):
    """Realiza un backup de las alertas en un archivo JSON dentro de la carpeta 'backups/'."""
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)  # Crear la carpeta si no existe

    alert_backup = []

    # Descargar todas las alertas configuradas
    for saved_search in service.saved_searches:
        if saved_search.content.get("alert_type") and saved_search.content["alert_type"] != "none":
            #if f"CU-*{especific}" in saved_search.name:
            if saved_search.name.startswith("CU-") and especific in saved_search.name:
                alert_backup.append({
                    "name": saved_search.name,
                    "content": saved_search.content
                })

    # Guardar el backup en la carpeta 'backups/'
    file_name = f"{BACKUP_DIR}/alertas_{especific}_{service.host.split('.')[0]}_backup.json"
    with open(file_name, "w") as file:
        json.dump(alert_backup, file, indent=4)

    print(f"Backup completado: alertas guardadas en {file_name}")

def backup_alerts_custom(service, custom):
    """Realiza un backup de las alertas en un archivo JSON dentro de la carpeta 'backups/'."""
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)  # Crear la carpeta si no existe

    alert_backup = []

    # Descargar todas las alertas configuradas
    for saved_search in service.saved_searches:
        if saved_search.content.get("alert_type") and saved_search.content["alert_type"] != "none":
            if saved_search.name.startswith("CU-") and custom in saved_search.name:
                alert_backup.append({
                    "name": saved_search.name,
                    "content": saved_search.content
                })

    # Guardar el backup en la carpeta 'backups/'
    file_name = f"{BACKUP_DIR}/alertas_{custom}_{service.host.split('.')[0]}_backup.json"
    with open(file_name, "w") as file:
        json.dump(alert_backup, file, indent=4)

    print(f"Backup completado: alertas guardadas en {file_name}")

def backup_es_use_cases(service, prefixes=None):
    """
    Realiza un backup de los casos de uso (saved searches) de Splunk Enterprise Security
    en un archivo JSON dentro de la carpeta 'backups/'.

    Args:
        service: Conexión al servicio Splunk.
        prefixes (str or list of str, optional): Prefijo o lista de prefijos para filtrar los nombres de los casos de uso.
    """
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)

    es_use_cases = []

    # Normalizar prefixes a lista
    if prefixes and isinstance(prefixes, str):
        prefixes = [prefixes]

    for saved_search in service.saved_searches:
        app = saved_search.access.get('app')
        tags = saved_search.content.get('tags', '')

        # Filtrar solo los casos de uso de Splunk ES
        if app == "SplunkEnterpriseSecuritySuite" or "ESCU" in tags:
            # Si se pasan prefijos, filtrar también por nombre
            if prefixes:
                if not any(saved_search.name.startswith(prefix) for prefix in prefixes):
                    continue  # Saltar si no coincide ningún prefijo

            es_use_cases.append({
                "name": saved_search.name,
                "app": app,
                "content": saved_search.content
            })

    # Guardar el backup
    file_name = f"{BACKUP_DIR}/alertas_es_use_cases_{service.host.split('.')[0]}_backup.json"
    with open(file_name, "w", encoding="utf-8") as file:
        json.dump(es_use_cases, file, indent=4)

    print(f"Backup completado: {len(es_use_cases)} casos de uso de ES guardados en {file_name}")

def backup_es_use_cases_custom(service, custom):
    """
    Realiza un backup de los casos de uso (saved searches) de Splunk Enterprise Security
    en un archivo JSON dentro de la carpeta 'backups/', filtrando por un texto específico
    en el nombre (custom).

    Args:
        service: Conexión al servicio Splunk.
        custom (str): Texto a buscar dentro del nombre de los casos de uso.
    """
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)

    es_use_cases = []

    # Descargar todas las búsquedas (saved searches)
    for saved_search in service.saved_searches:
        app = saved_search.access.get('app')
        tags = saved_search.content.get('tags', '')

        # Filtrar solo los casos de uso de Splunk ES
        if app == "SplunkEnterpriseSecuritySuite" or "ESCU" in tags:
            # Filtrar por texto en el nombre
            if custom in saved_search.name:
                es_use_cases.append({
                    "name": saved_search.name,
                    "app": app,
                    "content": saved_search.content
                })

    # Guardar el backup con el nombre del custom
    file_name = f"{BACKUP_DIR}/alertas_es_use_cases_{custom}_{service.host.split('.')[0]}_backup.json"
    with open(file_name, "w", encoding="utf-8") as file:
        json.dump(es_use_cases, file, indent=4)

    print(f"Backup completado: {len(es_use_cases)} casos de uso ES guardados en {file_name}")
