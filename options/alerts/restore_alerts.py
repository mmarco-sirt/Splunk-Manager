import json
import os
import re
from splunklib.binding import HTTPError

from core.backup_files import choose_backup_file

BACKUP_FOLDER = "alertas"

# Campos que Splunk devuelve al leer una saved search pero que no acepta al crear/actualizar.
# Se descartan de antemano para no gastar reintentos en ellos.
READ_ONLY_FIELDS = {"next_scheduled_time", "qualifiedSearch", "triggered_alert_count", "embed.enabled"}

UNSUPPORTED_ARG_PATTERN = re.compile(r'Argument\s+"([^"]+)"\s+is not supported')


def restore_alerts(service, file_path=None):
    """Restaura alertas desde un archivo JSON (si no se indica, se pide por consola)."""
    file_path = file_path or choose_backup_file(BACKUP_FOLDER, "alertas_", "backup de alertas")
    if not file_path:
        return

    restore_alerts_from_file(service, file_path)


def restore_alerts_from_file(service, backup_file, sharing="global"):
    """
    Restaura alertas desde un archivo JSON concreto, usando la lógica robusta de reintentos.
    Si una alerta trae el campo 'app' (backups de Enterprise Security) se crea en esa app.
    """
    existing_alert_names = {s.name for s in service.saved_searches}
    failed_alerts = []
    unnamed_counter = 1

    try:
        with open(backup_file, "r", encoding="utf-8") as file:
            alerts = json.load(file)

        for alert in alerts:
            if "name" in alert and alert["name"]:
                alert_name = alert["name"]
            else:
                alert_name = f"SIN_NOMBRE_{unnamed_counter}"
                unnamed_counter += 1
                print(f"[ADVERTENCIA] No se encontró el campo 'name' en una alerta. "
                      f"Se creará con el nombre '{alert_name}'. Revisa el JSON.")

            alert_content = alert.get("content", {})

            if "search" not in alert_content or alert_content["search"] is None:
                print(f"No se puede crear/actualizar la alerta '{alert_name}': falta 'search' o es None.")
                failed_alerts.append(alert_name)
                continue

            filtered_content = {
                k: v for k, v in alert_content.items() if v is not None
            }

            try:
                if alert_name in existing_alert_names:
                    print(f"Actualizando alerta: {alert_name}")
                    create_or_update_alert(service, alert_name, filtered_content, is_update=True, sharing=sharing)
                    print(f"Alerta '{alert_name}' actualizada correctamente.\n")
                else:
                    print(f"Creando alerta: {alert_name}")
                    create_or_update_alert(service, alert_name, filtered_content, is_update=False,
                                           app=alert.get("app"), sharing=sharing)
                    print(f"Alerta '{alert_name}' creada correctamente.\n")
                    existing_alert_names.add(alert_name)
            except Exception as e:
                print(f"Error al procesar la alerta '{alert_name}': {e}\n")
                failed_alerts.append(alert_name)

    except Exception as e:
        print(f"Error al restaurar alertas: {e}")

    if failed_alerts:
        print("\n[ERROR] Las siguientes alertas no se pudieron restaurar:")
        for name in failed_alerts:
            print(f" - {name}")
    print(f"Restore completado desde {os.path.basename(backup_file)}")


def create_or_update_alert(service, alert_name, attributes, is_update=False, app=None, sharing="global", max_retries=None):
    """
    Intenta crear o actualizar una alerta con los 'attributes' proporcionados.
    Si Splunk rechaza algún campo, lo elimina y reintenta (por defecto, tantas veces como campos haya).
    Si se indica 'app', la alerta nueva se crea en esa app. Al terminar aplica el 'sharing' indicado.
    """
    attributes = {k: v for k, v in attributes.items() if k not in READ_ONLY_FIELDS}
    if max_retries is None:
        max_retries = len(attributes) + 1

    for _ in range(max_retries):
        try:
            if is_update:
                saved_search = service.saved_searches[alert_name]
                saved_search.update(**attributes)
            else:
                params = dict(attributes)
                search = params.pop("search")
                if app:
                    params.update(owner="nobody", app=app, sharing="app")
                saved_search = service.saved_searches.create(alert_name, search, **params)
            break
        except HTTPError as he:
            match = UNSUPPORTED_ARG_PATTERN.search(str(he))
            if not match:
                print(" - No se pudo identificar el campo conflictivo. Abortando.")
                raise
            bad_field = match.group(1)
            if bad_field not in attributes or bad_field == "search":
                print(f" - El campo '{bad_field}' no se puede eliminar de los atributos. Abortando.")
                raise
            del attributes[bad_field]
    else:
        raise Exception(f"No se pudo crear/actualizar la alerta '{alert_name}' tras {max_retries} reintentos.")

    apply_sharing(saved_search, sharing)


def apply_sharing(saved_search, sharing):
    """Aplica el nivel de compartición (user/app/global) manteniendo el propietario actual."""
    if not sharing or saved_search.access.get("sharing") == sharing:
        return
    owner = saved_search.access.get("owner") or "nobody"
    try:
        saved_search.acl_update(sharing=sharing, owner=owner)
    except Exception as e:
        print(f" - [ADVERTENCIA] No se pudo aplicar sharing='{sharing}' a '{saved_search.name}': {e}")
