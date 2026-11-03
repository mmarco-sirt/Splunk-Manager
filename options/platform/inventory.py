"""
Plataforma de Splunk Cloud desde la consola (ACS): estado del stack y mantenimiento, inventario de tokens HEC, listas de
IP y puertos de salida, tokens de autenticación e índices, exportación de apps y reinicio. Lo usan el menú interactivo
(opción 22) y el modo no interactivo (--estado, --hec, --acceso, --tokens, --indices, --exportar-apps, --reiniciar).

Los inventarios se guardan en backups/<tipo>/ (con historial), así que un inventario programado deja rastro de cómo
estaba cada stack cada semana.
"""
import json
import os
import time

from core import acs, audit
from core.backup_files import write_backup
from core.paths import backup_dir
from web import access, hec, platform
from web.explorer import list_indexes

INDEX_FOLDER, INDEX_PREFIX = "indices", "indices_"
TOKEN_FOLDER, TOKEN_PREFIX = "tokens", "tokens_"


def _when(epoch):
    return time.strftime("%d/%m/%Y %H:%M", time.localtime(epoch)) if epoch else "—"


def _stack(client):
    return acs.stack_name(client.get("host")) or client.get("name")


def _needs_acs(client, log):
    reason = acs.unavailable_reason(client)
    if reason:
        log(f"⚠️ {client.get('name')}: {reason}; esta operación necesita ACS.")
        return False
    return True


# --- Estado -----------------------------------------------------------------------------------------

def show_status(service, client, log=print):
    """Estado del stack, token, ventanas de mantenimiento y change freezes del cliente."""
    name = client.get("name")
    own = platform.client_token(client)
    log(f"Token guardado: {'caduca el ' + _when(own['expiry']) if own['expiry'] else ('sin caducidad' if own['present'] else 'ninguno')}"
        + (f" (usuario {own['user']})" if own["user"] else ""))
    if own["level"] in ("expired", "serious", "warning"):
        log(f"{'❌' if own['level'] == 'expired' else '⚠️'} El token {'ha caducado' if own['level'] == 'expired' else 'caduca pronto'}: "
            f"renuévelo desde Plataforma → Tokens (web) o con la opción 22 del menú.")
    if not _needs_acs(client, log):
        return None
    data = platform.status(name, force=True)
    stack = data.get("stack")
    if stack:
        log(f"Stack {_stack(client)}: {stack['experience'] or '?'} · versión {stack['version'] or '?'} · estado {stack['status'] or '?'}"
            + (" · ⚠️ REINICIO PENDIENTE" if stack["restart_required"] else ""))
    for key, error in data.get("errors", {}).items():
        log(f"⚠️ ACS no devolvió {key}: {error}")
    windows = data.get("windows") or []
    if windows:
        log(f"Ventanas de mantenimiento ({len(windows)}):")
        for w in windows:
            ops = "; ".join(o["description"] for o in w["operations"] if o["description"])
            log(f"  - {_when(w['start'])} → {_when(w['end'])}: {w['type']} ({w['status']}{', sin corte' if w['zero_downtime'] else ''})"
                + (f" · {ops}" if ops else ""))
    elif stack:
        log("Sin ventanas de mantenimiento programadas en los próximos 30 días.")
    for f in data.get("active_freezes") or []:
        log(f"Change freeze vigente ({f['by']}) hasta {f['end']}: {f['reason'] or f['applies_to']}")
    return data


# --- Inventarios --------------------------------------------------------------------------------------

def inventory_hec(service, client, log=print):
    """Tokens HEC del cliente (con avisos) y backup en backups/hec/."""
    if not _needs_acs(client, log):
        return []
    data = hec.list_tokens(client.get("name"), with_values=True)
    rows = data["tokens"]
    if data.get("warning"):
        log(f"⚠️ {data['warning']}")
    if not rows:
        log("No hay tokens HEC.")
    else:
        width = max(len(r["name"]) for r in rows)
        log(f"Tokens HEC ({len(rows)}):")
        for r in rows:
            notes = list(r.get("warnings") or [])
            log(f"  {r['name']:<{width}}  índice {r['default_index'] or '?':<16} {'deshabilitado' if r['disabled'] else 'habilitado':<13}"
                + (f"  ⚠️ {'; '.join(notes)}" if notes else ""))
    path = hec._backup_rows(client.get("name"), client, rows)
    log(f"Guardado en {os.path.basename(path)}")
    return rows


def inventory_access(service, client, log=print):
    """Listas de IP permitidas y puertos de salida del cliente, y backup en backups/acceso/."""
    if not _needs_acs(client, log):
        return None
    data = access.inventory(client.get("name"))
    for a in data["allowlists"]:
        if a["error"]:
            continue
        log(f"  {a['label']} (IPv{a['version']}): {', '.join(a['subnets']) if a['subnets'] else '(vacía)'}")
    unavailable = [f"{a['feature']} IPv{a['version']}" for a in data["allowlists"] if a["error"]]
    if unavailable:
        log(f"  No disponibles en este stack: {', '.join(unavailable)}")
    for p in data["outbound"]:
        log(f"  Puerto de salida {p['port']} (IPv{p['version']}): {', '.join(p['subnets'])}")
    if not data["outbound"]:
        log("  Sin puertos de salida abiertos.")
    access.backup(service, client, log=log)
    return data


def inventory_tokens(service, client, log=print):
    """Tokens de autenticación del stack (sin su valor) y backup en backups/tokens/."""
    name = client.get("name")
    own = platform.client_token(client)
    log(f"Token guardado de {name}: {'caduca el ' + _when(own['expiry']) if own['expiry'] else ('sin caducidad' if own['present'] else 'ninguno')}")
    if not _needs_acs(client, log):
        return []
    rows = platform.tokens(name)["tokens"]
    if not rows:
        log("ACS no devuelve tokens en este stack.")
    for t in rows:
        flag = {"expired": "❌ caducado", "serious": "⚠️ caduca en menos de 7 días", "warning": "⚠️ caduca en menos de 30 días"}.get(t["level"], "")
        log(f"  {t['user']:<24} {t['audience']:<24} {t['status']:<9} caduca {_when(t['expires_on'])}  último uso {_when(t['last_used'])}"
            + (f"  {flag}" if flag else "") + ("  ← este cliente" if t.get("is_own") else ""))
    path = os.path.join(backup_dir(TOKEN_FOLDER), f"{TOKEN_PREFIX}{_stack(client)}_backup.json")
    write_backup(path, rows)
    log(f"Guardado en {os.path.basename(path)}")
    return rows


def inventory_indexes(service, client, log=print):
    """Índices del cliente con retención y archivado, y backup en backups/indices/."""
    rows = list_indexes(service)
    log(f"Índices ({len(rows)}):")
    for r in rows:
        extra = f" + {int(r['archive_days'])} d archivo" if r.get("archive_days") else ""
        log(f"  {r['name']:<32} {r['datatype']:<7} retención {r['retention_days'] or '?'} d{extra}"
            + ("  (deshabilitado)" if r.get("disabled") else ""))
    path = os.path.join(backup_dir(INDEX_FOLDER), f"{INDEX_PREFIX}{_stack(client)}_backup.json")
    write_backup(path, rows)
    log(f"Guardado en {os.path.basename(path)}")
    return rows


# --- Exportar apps y reiniciar --------------------------------------------------------------------------

def export_apps(service, client, names, options=None, dry_run=False, log=print):
    """Exporta las apps indicadas ('todos' = todas las de ACS) a backups/apps/<stack>/."""
    if not _needs_acs(client, log):
        return None
    try:
        apps = acs.list_apps(client)
    except acs.AcsError as e:
        log(f"❌ No se pudieron listar las apps: {e}")
        return None
    versions = {a.get("name"): a.get("version") for a in apps}
    wanted = [str(n).strip() for n in names if str(n).strip()]
    if any(n.lower() in ("todos", "all", "*") for n in wanted):
        chosen = sorted(versions)
    else:
        chosen = [n for n in wanted if n in versions]
        for n in [n for n in wanted if n not in versions]:
            log(f"· {n} no está entre las apps que ACS gestiona en {client.get('name')}.")
    if not chosen:
        log("No hay nada que exportar.")
        return None
    if dry_run:
        log(f"[SIMULACIÓN] Se exportarían ({len(chosen)}): {', '.join(chosen)}")
        return {"exported": [], "failed": [], "simulated": chosen}
    return platform.export_apps(client, chosen, options or {}, log=log, versions=versions)


def restart(service, client, dry_run=False, log=print):
    if not _needs_acs(client, log):
        return
    title, run = platform.build_restart(client.get("name"), dry_run=dry_run)
    log(f"▶ {title}")
    run()


# --- Menú interactivo ----------------------------------------------------------------------------------

def platform_menu_cli(service, client):
    """Submenú de plataforma (opción 22 del menú principal)."""
    while True:
        print(f"\n--- Plataforma de {client.get('name')} (ACS) ---")
        print("1) Estado del stack, mantenimiento y token")
        print("2) Tokens HEC (inventario y backup)")
        print("3) Listas de IP permitidas y puertos de salida (inventario y backup)")
        print("4) Tokens de autenticación (inventario) y crear uno nuevo")
        print("5) Índices (inventario y backup)")
        print("6) Exportar apps (.tgz vía ACS)")
        print("7) Reiniciar el stack")
        print("0) Volver")
        choice = input("Seleccione una opción: ").strip()
        try:
            if choice == "1":
                show_status(service, client)
            elif choice == "2":
                inventory_hec(service, client)
            elif choice == "3":
                inventory_access(service, client)
            elif choice == "4":
                inventory_tokens(service, client)
                _create_token_cli(client)
            elif choice == "5":
                inventory_indexes(service, client)
            elif choice == "6":
                names = input("Apps a exportar separadas por coma ('todos' para todas): ").strip()
                if names:
                    export_apps(service, client, names.split(","))
            elif choice == "7":
                if input(f"¿Reiniciar el stack de {client.get('name')}? Se interrumpen las búsquedas en curso. (s/N): ").strip().lower() == "s":
                    restart(service, client)
            elif choice == "0":
                return
            else:
                print("Opción no válida.")
        except Exception as e:
            print(f"❌ {e}")


def _create_token_cli(client):
    import getpass
    if input("¿Crear un token nuevo para este stack? (s/N): ").strip().lower() != "s":
        return
    username = input(f"Usuario del stack [{client.get('username') or ''}]: ").strip() or client.get("username") or ""
    password = client.get("password") if username == client.get("username") and client.get("password") else ""
    if not password:
        password = getpass.getpass("Contraseña del stack: ")
    expires = input("Caducidad (+90d, +365d o AAAA-MM-DD) [+90d]: ").strip() or "+90d"
    audience = input("Audiencia [Splunk Manager]: ").strip() or "Splunk Manager"
    save = input("¿Guardarlo como token de este cliente en config.py? (S/n): ").strip().lower() in ("", "s")
    result = platform.create_token(client.get("name"), {"username": username, "password": password, "user": username,
                                                        "audience": audience, "expires_on": expires, "save": save})
    if result.get("saved"):
        print(f"✅ Token creado (caduca {_when(result['expires_on'])}) y guardado cifrado en config.py.")
    else:
        print(f"✅ Token creado (caduca {_when(result['expires_on'])}). Cópielo ahora, ACS no lo vuelve a mostrar:\n{result['token']}")
