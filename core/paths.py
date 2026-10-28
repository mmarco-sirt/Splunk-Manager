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
REPORTS_ROOT = os.path.join(BASE_DIR, "informes")
LOGS_ROOT = os.path.join(BASE_DIR, "logs")
UPLOADS_ROOT = os.path.join(BASE_DIR, "apps_subidas")


def backup_dir(name=""):
    """Devuelve la ruta absoluta de backups/<name> y la crea si no existe."""
    path = os.path.join(BACKUP_ROOT, name)
    os.makedirs(path, exist_ok=True)
    return path


def reports_dir():
    """Devuelve la ruta absoluta de informes/ y la crea si no existe."""
    os.makedirs(REPORTS_ROOT, exist_ok=True)
    return REPORTS_ROOT


def logs_dir():
    """Devuelve la ruta absoluta de logs/ (logs del modo no interactivo del CLI) y la crea si no existe."""
    os.makedirs(LOGS_ROOT, exist_ok=True)
    return LOGS_ROOT


def uploads_dir():
    """Devuelve la ruta absoluta de apps_subidas/ (paquetes de app subidos para validarlos) y la crea si no existe."""
    os.makedirs(UPLOADS_ROOT, exist_ok=True)
    return UPLOADS_ROOT


def host_tag(service):
    """Identificador corto del host para nombrar los backups (ej: 'cliente' de cliente.splunkcloud.com)."""
    return service.host.split('.')[0]
