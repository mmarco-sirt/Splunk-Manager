import os

from core.backup_files import write_backup
from core.knowledge import acl_of, all_saved_searches
from core.paths import backup_dir, host_tag

BACKUP_FOLDER = "reportes"

def backup_reports(service):
    """Realiza un backup de los reportes (saved searches sin alert_type, de todas las apps y usuarios) en un archivo JSON."""
    report_backup = []

    for saved_search in all_saved_searches(service):
        # Filtrar reportes: saved searches sin alert_type o con alert_type = 'none'
        if not saved_search.content.get("alert_type") or saved_search.content["alert_type"] == "none":
            report_backup.append({
                "name": saved_search.name,
                "content": saved_search.content,
                "acl": acl_of(saved_search),
            })

    file_name = os.path.join(backup_dir(BACKUP_FOLDER), f"reportes_{host_tag(service)}_backup.json")
    write_backup(file_name, report_backup)

    print(f"Backup completado: {len(report_backup)} reportes guardados en {file_name}")
