import os
import json

BACKUP_DIR = "backups/dashboards"

def list_dashboard_backups():
    """Lista los archivos de backup disponibles para dashboards."""
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)
        return []
    return [f for f in os.listdir(BACKUP_DIR) if f.endswith("_backup.json")]

def restore_dashboard(service, file_path=None):
    """Restaura un dashboard desde un archivo de backup."""
    try:
        if not file_path:
            files = list_dashboard_backups()
            if not files:
                print("No hay backups de dashboards disponibles.")
                return
            print("Dashboards disponibles para restaurar:")
            for i, file in enumerate(files, 1):
                print(f"{i}. {file}")
            index = int(input("Seleccione el dashboard a restaurar: ")) - 1
            if not (0 <= index < len(files)):
                print("Opción inválida.")
                return
            file_path = os.path.join(BACKUP_DIR, files[index])
        
        with open(file_path, "r") as f:
            content = json.load(f)

        # Usamos el nombre del archivo para obtener el ID del dashboard
        dashboard_id = os.path.splitext(os.path.basename(file_path))[0].replace("_backup", "")

        # Verificamos si ya existe el dashboard
        existing = None
        try:
            existing = service.get(f"/services/data/ui/views/{dashboard_id}?output_mode=json")
        except:
            pass  # No existe, lo crearemos

        # Filtrar solo los campos válidos para POST
        allowed_keys = {"eai:data"}
        filtered_content = {k: v for k, v in content.items() if k in allowed_keys}

        if existing:
            service.post(f"/services/data/ui/views/{dashboard_id}", **filtered_content)
            print(f"✅ Dashboard '{dashboard_id}' actualizado.")
        else:
            service.post("/services/data/ui/views", name=dashboard_id, **filtered_content)
            print(f"✅ Dashboard '{dashboard_id}' creado.")

    except Exception as e:
        print(f"❌ Error al restaurar el dashboard: {e}")
