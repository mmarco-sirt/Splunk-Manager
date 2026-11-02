"""
Actualizar apps y add-ons de Splunkbase en un cliente de Splunk Cloud (Victoria Experience) a través de ACS.

«Pendientes» son las apps para las que el propio Splunk ya ha detectado una versión nueva en Splunkbase (lo mismo que
el aviso «Update» de Apps → Manage Apps). Se actualizan de una en una y se espera a que ACS termine cada una. Antes de
tocar nada se guarda en backups/addons/ la lista de apps con su versión instalada, por si hay que volver atrás (ACS
permite instalar una versión concreta).
"""
import fnmatch
import getpass
import json
import os
import time

from core import acs
from core.backup_files import write_backup
from core.paths import backup_dir, host_tag
from core.splunkbase_account import get_account, save_account

ALL = ("todos", "all", "*")
FATAL_STATUSES = ("HTTP 401", "HTTP 403")   # con el token mal, todas las apps fallarían igual


def parse_names(text):
    """'Splunk_TA_windows, TA-*' (o una lista) -> ['Splunk_TA_windows', 'TA-*']."""
    items = text.split(",") if isinstance(text, str) else (text or [])
    return list(dict.fromkeys(p.strip() for p in items if p and p.strip()))


def _matches(app, pattern):
    if pattern.lower() in ALL:
        return True
    for candidate in (app["name"], app["label"]):
        if candidate.lower() == pattern.lower():
            return True
        if ("*" in pattern or "?" in pattern) and fnmatch.fnmatchcase(candidate.lower(), pattern.lower()):
            return True
    return False


def select_pending(pending, names):
    """Pendientes que coinciden con 'names' (nombre o etiqueta, con comodines; 'todos'). Devuelve (elegidas, sin coincidencia)."""
    names = parse_names(names)
    chosen, matched = [], set()
    for app in pending:
        hits = [n for n in names if _matches(app, n)]
        if hits:
            chosen.append(app)
            matched.update(hits)
    return chosen, [n for n in names if n not in matched]


def pending_with_acs(service, client):
    """
    Actualizaciones pendientes del cliente con el campo 'acs': True si ACS la gestiona como app de Splunkbase, False si
    no (instalada como app privada o por soporte: no se puede actualizar por esta vía) y None si no se pudo consultar
    ACS. Devuelve (pendientes, avisos).
    """
    pending = acs.pending_updates(service)
    warnings, managed = [], None
    reason = acs.unavailable_reason(client)
    if reason:
        warnings.append(f"⚠️ {client.get('name')}: {reason}; se muestran las pendientes pero no se pueden actualizar "
                        "desde aquí.")
    else:
        try:
            managed = {a.get("name"): a for a in acs.list_apps(client)}
        except acs.AcsError as e:
            warnings.append(f"⚠️ No se pudo consultar ACS: {e}")
    for app in pending:
        info = (managed or {}).get(app["name"]) if managed is not None else None
        app["acs"] = None if managed is None else bool(info and info.get("splunkbaseID"))
        if info and info.get("splunkbaseID") and not app["splunkbase_id"]:
            app["splunkbase_id"] = str(info["splunkbaseID"])
    return pending, warnings


def list_pending(service, client, log=print):
    """Muestra las actualizaciones pendientes del cliente (ver pending_with_acs) y las devuelve."""
    pending, warnings = pending_with_acs(service, client)
    for warning in warnings:
        log(warning)
    if not pending:
        log(f"{client.get('name')}: no hay apps ni add-ons con actualización pendiente.")
        return pending
    log(f"\nActualizaciones pendientes en {client.get('name')} ({len(pending)}):")
    width = max(len(a["name"]) for a in pending)
    for i, app in enumerate(pending, 1):
        notes = []
        if app["major"]:
            notes.append("⚠️ cambio de versión mayor: revise las notas de la versión")
        if app["disabled"]:
            notes.append("deshabilitada")
        if app["acs"] is False:
            notes.append("no gestionable desde ACS (app privada: subir el paquete nuevo o pedirlo a soporte)")
        line = f"  {i:>2}) {app['name']:<{width}}  {app['version'] or '?':>8} → {app['new_version']:<8}"
        log(line + (f"  [{', '.join(notes)}]" if notes else ""))
    return pending


def backup_versions(service, log=print):
    """Guarda en backups/addons/ todas las apps del cliente con su versión (con historial). Devuelve la ruta."""
    response = service.get("apps/local", output_mode="json", count=0)
    entries = json.loads(response.body.read().decode("utf-8")).get("entry", [])
    data = []
    for entry in entries:
        content = entry.get("content") or {}
        data.append({"name": entry.get("name"), "label": content.get("label"), "version": content.get("version") or "",
                     "disabled": str(content.get("disabled")).lower() in ("1", "true")})
    # Nombre del stack completo (es.cliente), no solo el primer trozo del host, para que los clientes «es.*» no choquen
    tag = acs.stack_name(getattr(service, "host", "")) or host_tag(service)
    path = os.path.join(backup_dir("addons"), f"addons_{tag}_backup.json")
    write_backup(path, data)
    log(f"Versiones actuales guardadas en {os.path.basename(path)}")
    return path


def update_addons(service, client, names, account=None, dry_run=False, log=print, sleep=time.sleep, stop=None,
                  progress=None):
    """
    Actualiza las apps pendientes que coinciden con 'names' ('todos' o nombres/comodines) a la versión que Splunk ha
    detectado. 'account' es {"username", "password"} de Splunkbase (si no, se usa la guardada o la del entorno). Con
    dry_run solo muestra lo que se actualizaría. 'stop()' se consulta antes de cada app (cancelación) y
    'progress(hechas, total, app)' informa del avance. Devuelve {"updated", "failed", "skipped", "missing", "cancelled"}.
    """
    result = {"updated": [], "failed": [], "skipped": [], "missing": [], "cancelled": []}
    pending = list_pending(service, client, log)
    chosen, result["missing"] = select_pending(pending, names)
    for name in result["missing"]:
        log(f"· Ninguna actualización pendiente coincide con «{name}».")
    for app in [a for a in chosen if a["acs"] is False]:
        log(f"· {app['name']} no se puede actualizar desde ACS (no consta como app de Splunkbase): suba el paquete "
            "nuevo como app privada o pídalo a soporte de Splunk.")
        result["skipped"].append(app["name"])
    chosen = [a for a in chosen if a["acs"] is not False]
    if not chosen:
        if not result["skipped"] and not result["missing"]:
            log("No hay nada que actualizar.")
        return result

    summary = ", ".join(f"{a['name']} {a['version'] or '?'}→{a['new_version']}" for a in chosen)
    if dry_run:
        log(f"[SIMULACIÓN] Se actualizarían ({len(chosen)}): {summary}")
        result["updated"] = [a["name"] for a in chosen]
        return result

    reason = acs.unavailable_reason(client)
    if reason:
        log(f"❌ {client.get('name')}: {reason}.")
        result["failed"] = [a["name"] for a in chosen]
        return result
    account = account or get_account()
    if not account:
        log("❌ No hay cuenta de Splunkbase: guárdela en config.py (menú de add-ons o sección Add-ons de la web) o indique "
            "SPLUNKBASE_USERNAME y SPLUNKBASE_PASSWORD.")
        result["failed"] = [a["name"] for a in chosen]
        return result
    try:
        session = acs.splunkbase_login(account["username"], account["password"])
    except acs.AcsError as e:
        log(f"❌ {e}")
        result["failed"] = [a["name"] for a in chosen]
        return result
    log(f"Sesión de Splunkbase iniciada como {account['username']}.")
    try:
        backup_versions(service, log)
    except Exception as e:
        log(f"⚠️ No se pudo guardar la lista de versiones actuales: {e}")

    total = len(chosen)
    log(f"Actualizando {total} {'app' if total == 1 else 'apps'} en {client.get('name')} (de una en una)...")
    for position, app in enumerate(chosen):
        name, target = app["name"], app["new_version"]
        if stop and stop():
            result["cancelled"] = [a["name"] for a in chosen[position:]]
            log(f"⏹ Cancelado: no se actualizan {', '.join(result['cancelled'])}.")
            break
        if progress:
            progress(position, total, name)
        started = time.time()
        log(f"▶ {name}: {app['version'] or '?'} → {target}")
        try:
            license_url = ""
            if app["splunkbase_id"]:
                try:
                    license_url = acs.splunkbase_app(app["splunkbase_id"])["license_url"]
                except acs.AcsError as e:
                    log(f"   ⚠️ {e} Se intenta sin URL de licencia.")
            response = acs.update_app(client, name, target, session, license_url, log=log)
            status, version = str(response.get("status") or "").lower(), str(response.get("version") or "")
            if not (status == "installed" and version == target):
                log(f"   ACS ha aceptado la petición (estado {status or '?'}); esperando a que termine...")
                response = acs.wait_for_update(client, name, target, log=log, sleep=sleep)
            log(f"✅ {name} actualizada a {response.get('version') or target} ({time.time() - started:.0f} s).")
            result["updated"].append(name)
        except acs.AcsError as e:
            log(f"❌ {name}: {e}")
            result["failed"].append(name)
            if any(code in str(e) for code in FATAL_STATUSES):
                rest = [a["name"] for a in chosen if a["name"] not in result["updated"] + result["failed"]]
                if rest:
                    log(f"❌ Se cancela el resto ({', '.join(rest)}): el token no sirve para ACS.")
                    result["failed"] += rest
                break
        except Exception as e:
            log(f"❌ {name}: error inesperado: {e}")
            result["failed"].append(name)
    if progress:
        progress(len(result["updated"]) + len(result["failed"]), total, "")
    log(f"Resultado: {len(result['updated'])} actualizadas, {len(result['failed'])} con error, "
        f"{len(result['skipped'])} omitidas" + (f", {len(result['cancelled'])} canceladas." if result["cancelled"] else "."))
    return result


# --- Menú de consola -------------------------------------------------------------------------------

def _ask_account():
    """Cuenta de Splunkbase: la guardada/entorno o se pide por consola (con opción de guardarla cifrada)."""
    account = get_account()
    if account:
        print(f"Cuenta de Splunkbase: {account['username']} ({account['source']}).")
        if input("¿Usar esta cuenta? (S/n): ").strip().lower() in ("", "s"):
            return account
    username = input("Usuario de Splunkbase (splunk.com): ").strip()
    password = getpass.getpass("Contraseña de Splunkbase: ")
    if not username or not password:
        return None
    if input("¿Guardar la cuenta cifrada en config.py para las próximas veces? (s/N): ").strip().lower() == "s":
        try:
            save_account(username, password)
            print("✅ Cuenta de Splunkbase guardada en config.py (contraseña cifrada con DPAPI).")
        except Exception as e:
            print(f"⚠️ No se pudo guardar la cuenta: {e}")
    return {"username": username, "password": password}


def update_addons_cli(service, client):
    """Menú de consola: muestra las actualizaciones pendientes del cliente y actualiza las que se elijan."""
    pending = list_pending(service, client)
    if not pending:
        return
    choice = input("\nNúmeros a actualizar separados por coma, 'todos' o vacío para cancelar: ").strip()
    if not choice:
        print("Operación cancelada.")
        return
    if choice.lower() in ALL:
        names = ["todos"]
    else:
        try:
            indexes = [int(x) - 1 for x in choice.split(",") if x.strip()]
            names = [pending[i]["name"] for i in indexes if 0 <= i < len(pending)]
        except ValueError:
            names = parse_names(choice)
    chosen, _ = select_pending(pending, names)
    chosen = [a for a in chosen if a["acs"] is not False]
    if not chosen:
        print("No hay nada que actualizar con esa selección.")
        return
    if any(a["major"] for a in chosen):
        print("⚠️ Alguna actualización cambia la versión mayor: conviene revisar antes sus notas de la versión.")
    account = _ask_account()
    if not account:
        print("Operación cancelada: hace falta la cuenta de Splunkbase.")
        return
    plural = "app" if len(chosen) == 1 else "apps"
    if input(f"¿Actualizar {len(chosen)} {plural} en {client.get('name')}? (s/N): ").strip().lower() != "s":
        print("Operación cancelada.")
        return
    update_addons(service, client, [a["name"] for a in chosen], account=account)
