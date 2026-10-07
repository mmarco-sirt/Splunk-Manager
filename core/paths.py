import os
import sys

if getattr(sys, 'frozen', False):
    # Ejecutable PyInstaller: config.py y backups/ junto al .exe
    BASE_DIR = os.path.dirname(sys.executable)
else:
    # Desarrollo: raíz del proyecto (carpeta padre de core/)
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CONFIG_PATH = os.path.join(BASE_DIR, "config.py")
BACKUP_ROOT = os.path.join(BASE_DIR, "backups")


def backup_dir(name=""):
    """Devuelve la ruta absoluta de backups/<name> y la crea si no existe."""
    path = os.path.join(BACKUP_ROOT, name)
    os.makedirs(path, exist_ok=True)
    return path


def host_tag(service):
    """Identificador corto del host para nombrar los backups (ej: 'cliente' de cliente.splunkcloud.com)."""
    return service.host.split('.')[0]
