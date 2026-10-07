import os

from core.backup_files import write_backup
from core.paths import backup_dir, host_tag

BACKUP_FOLDER = "alertas"
TECNOLOGIAS = ["AD", "FW", "AV", "PX", "MAIL", "O365", "MI"]


def _is_alert(saved_search):
    alert_type = saved_search.content.get("alert_type")
    return bool(alert_type) and alert_type != "none"


def _is_es_use_case(saved_search):
    app = saved_search.access.get('app')
    tags = saved_search.content.get('tags') or ''
    return app == "SplunkEnterpriseSecuritySuite" or "ESCU" in tags


def _save_backup(entries, file_label, service):
    """Guarda las entradas en backups/alertas/alertas_<file_label>_<host>_backup.json."""
    name = f"alertas_{file_label}_{host_tag(service)}" if file_label else f"alertas_{host_tag(service)}"
    file_name = os.path.join(backup_dir(BACKUP_FOLDER), f"{name}_backup.json")
    write_backup(file_name, entries)
    return file_name


def _backup_cu_alerts(service, name_filter=None, file_label=None):
    """Backup de las alertas 'CU-*', opcionalmente filtradas por texto en el nombre."""
    alert_backup = [
        {"name": s.name, "content": s.content}
        for s in service.saved_searches
        if _is_alert(s) and s.name.startswith("CU-") and (not name_filter or name_filter in s.name)
    ]
    file_name = _save_backup(alert_backup, file_label, service)
    print(f"Backup completado: {len(alert_backup)} alertas guardadas en {file_name}")


def backup_alerts(service):
    """Backup de todas las alertas 'CU-*'."""
    _backup_cu_alerts(service)


def backup_alerts_especific(service, especific):
    """Backup de las alertas 'CU-*' de una tecnología concreta."""
    _backup_cu_alerts(service, especific, especific)


def backup_alerts_custom(service, custom):
    """Backup de las alertas 'CU-*' cuyo nombre contiene 'custom'."""
    _backup_cu_alerts(service, custom, custom)


def backup_alerts_by_name(service, names, file_label="seleccion"):
    """Backup de las saved searches con esos nombres exactos (las de Enterprise Security incluyen 'app')."""
    wanted = set(names)
    entries = []
    for s in service.saved_searches:
        if s.name in wanted:
            entry = {"name": s.name, "content": s.content}
            if _is_es_use_case(s):
                entry["app"] = s.access.get('app')
            entries.append(entry)

    missing = wanted - {e["name"] for e in entries}
    if missing:
        print(f"[ADVERTENCIA] No se encontraron: {', '.join(sorted(missing))}")
    file_name = _save_backup(entries, file_label, service)
    print(f"Backup completado: {len(entries)} alertas guardadas en {file_name}")


def backup_es_use_cases(service, prefixes=None):
    """
    Realiza un backup de los casos de uso (saved searches) de Splunk Enterprise Security.

    Args:
        service: Conexión al servicio Splunk.
        prefixes (str or list of str, optional): Prefijo o lista de prefijos para filtrar los nombres de los casos de uso.
    """
    if prefixes and isinstance(prefixes, str):
        prefixes = [prefixes]

    es_use_cases = [
        {"name": s.name, "app": s.access.get('app'), "content": s.content}
        for s in service.saved_searches
        if _is_es_use_case(s) and (not prefixes or any(s.name.startswith(p) for p in prefixes))
    ]
    file_name = _save_backup(es_use_cases, "es_use_cases", service)
    print(f"Backup completado: {len(es_use_cases)} casos de uso de ES guardados en {file_name}")


def backup_es_use_cases_custom(service, custom):
    """Backup de los casos de uso de ES cuyo nombre contiene 'custom'."""
    es_use_cases = [
        {"name": s.name, "app": s.access.get('app'), "content": s.content}
        for s in service.saved_searches
        if _is_es_use_case(s) and custom in s.name
    ]
    file_name = _save_backup(es_use_cases, f"es_use_cases_{custom}", service)
    print(f"Backup completado: {len(es_use_cases)} casos de uso ES guardados en {file_name}")
