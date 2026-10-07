import os
import json

from core.backup_files import write_backup
from core.paths import backup_dir

BACKUP_FOLDER = "dashboards"

def backup_dashboard(service, dashboard_name):
    """Realiza un backup de un dashboard específico desde Splunk."""
    try:
        response = service.get(f"/services/data/ui/views/{dashboard_name}", output_mode="json")

        data = json.loads(response.body.read().decode("utf-8"))
        content = data["entry"][0]["content"]

        file_path = os.path.join(backup_dir(BACKUP_FOLDER), f"{dashboard_name}_backup.json")
        write_backup(file_path, content)

        print(f"✅ Dashboard '{dashboard_name}' respaldado en '{file_path}'")

    except Exception as e:
        print(f"❌ Error al hacer backup del dashboard '{dashboard_name}': {e}")
