import os
import json

from splunklib.binding import HTTPError

from core.backup_files import choose_backup_file

BACKUP_FOLDER = "dashboards"

def restore_dashboard(service, file_path=None):
    """Restaura un dashboard desde un archivo de backup (si no se indica, se pide por consola)."""
    file_path = file_path or choose_backup_file(BACKUP_FOLDER, "", "backup de dashboards")
    if not file_path:
        return

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = json.load(f)

        # Usamos el nombre del archivo para obtener el ID del dashboard
        dashboard_id = os.path.splitext(os.path.basename(file_path))[0].replace("_backup", "")

        # Verificamos si ya existe el dashboard
        exists = True
        try:
            service.get(f"/services/data/ui/views/{dashboard_id}", output_mode="json")
        except HTTPError as he:
            if he.status != 404:
                raise
            exists = False  # No existe, lo crearemos

        # Filtrar solo los campos válidos para POST
        allowed_keys = {"eai:data"}
        filtered_content = {k: v for k, v in content.items() if k in allowed_keys}

        if exists:
            service.post(f"/services/data/ui/views/{dashboard_id}", **filtered_content)
            print(f"✅ Dashboard '{dashboard_id}' actualizado.")
        else:
            service.post("/services/data/ui/views", name=dashboard_id, **filtered_content)
            print(f"✅ Dashboard '{dashboard_id}' creado.")

    except Exception as e:
        print(f"❌ Error al restaurar el dashboard: {e}")
