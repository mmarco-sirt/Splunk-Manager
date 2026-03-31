import os
import json

BACKUP_DIR = "backups/dashboards"

def backup_dashboard(service, dashboard_name):
    """Realiza un backup de un dashboard específico desde Splunk."""
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)

    try:
        # Accedemos al dashboard (usando output_mode como argumento separado)
        response = service.get(f"/services/data/ui/views/{dashboard_name}", output_mode="json")

        # Leemos y parseamos la respuesta
        data = json.loads(response.body.read().decode("utf-8"))
        content = data["entry"][0]["content"]

        file_path = os.path.join(BACKUP_DIR, f"{dashboard_name}_backup.json")
        with open(file_path, "w") as f:
            json.dump(content, f, indent=4)

        print(f"✅ Dashboard '{dashboard_name}' respaldado en '{file_path}'")

    except Exception as e:
        print(f"❌ Error al hacer backup del dashboard '{dashboard_name}': {e}")
