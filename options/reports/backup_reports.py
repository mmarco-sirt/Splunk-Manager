import json
import os

from core.paths import backup_dir, host_tag

BACKUP_FOLDER = "reportes"

def backup_reports(service):
    """Realiza un backup de los reportes (saved searches sin alert_type) en un archivo JSON."""
    report_backup = []

    for saved_search in service.saved_searches:
        # Filtrar reportes: saved searches sin alert_type o con alert_type = 'none'
        if not saved_search.content.get("alert_type") or saved_search.content["alert_type"] == "none":
            report_backup.append({
                "name": saved_search.name,
                "content": saved_search.content
            })

    file_name = os.path.join(backup_dir(BACKUP_FOLDER), f"reportes_{host_tag(service)}_backup.json")
    with open(file_name, "w", encoding="utf-8") as file:
        json.dump(report_backup, file, indent=4)

    print(f"Backup completado: {len(report_backup)} reportes guardados en {file_name}")
