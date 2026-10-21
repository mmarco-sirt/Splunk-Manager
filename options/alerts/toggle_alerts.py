"""
Habilitar o deshabilitar alertas (saved searches): una o varias, por nombre exacto o con comodines (CU-AD-*).
Antes de cambiar nada se guarda su configuración en el backup «antes_de_editar», igual que al editarlas en la web.
"""
import fnmatch
import os

from options.alerts.backup_alerts import backup_before_edit


def _flag(value):
    return str(value).strip().lower() in ("1", "true", "yes")


def parse_patterns(text):
    """'CU-AD-001, CU-FW-*' (o una lista) -> ['CU-AD-001', 'CU-FW-*']"""
    items = text.split(",") if isinstance(text, str) else (text or [])
    return list(dict.fromkeys(p.strip() for p in items if p and p.strip()))


def matches(name, pattern):
    """Nombre exacto o, si el patrón tiene * o ?, con comodines (los corchetes del nombre se toman literalmente)."""
    if "*" not in pattern and "?" not in pattern:
        return name == pattern
    return fnmatch.fnmatchcase(name, pattern.replace("[", "[[]"))


def find_saved_searches(service, patterns):
    """Saved searches que coinciden con algún patrón. Devuelve (encontradas, patrones sin ninguna coincidencia)."""
    found, matched = [], set()
    for s in service.saved_searches:
        hits = [p for p in patterns if matches(s.name, p)]
        if hits:
            found.append(s)
            matched.update(hits)
    return found, [p for p in patterns if p not in matched]


def set_alerts_enabled(service, patterns, enabled, dry_run=False):
    """
    Habilita (enabled=True) o deshabilita las saved searches que coinciden con 'patterns'. Con dry_run solo informa de lo
    que cambiaría. Devuelve {"changed": [...], "unchanged": [...], "failed": [...], "missing": [patrones]}.
    """
    patterns = parse_patterns(patterns)
    state_text = "habilitada" if enabled else "deshabilitada"
    found, missing = find_saved_searches(service, patterns)
    result = {"changed": [], "unchanged": [], "failed": [], "missing": missing}

    for pattern in missing:
        print(f"· Ninguna alerta coincide con «{pattern}».")
    pending = [s for s in found if _flag(s.content.get("disabled")) == enabled]
    result["unchanged"] = [s.name for s in found if _flag(s.content.get("disabled")) != enabled]
    if result["unchanged"]:
        print(f"· Ya estaban {state_text}s ({len(result['unchanged'])}): {', '.join(result['unchanged'])}")
    if not pending:
        return result

    if dry_run:
        print(f"[SIMULACIÓN] Quedarían {state_text}s ({len(pending)}): {', '.join(s.name for s in pending)}")
        result["changed"] = [s.name for s in pending]
        return result

    paths = backup_before_edit(service, pending)
    print(f"Configuración anterior guardada en {', '.join(os.path.basename(p) for p in paths)}")
    for s in pending:
        try:
            if enabled:
                s.enable()
            else:
                s.disable()
            print(f"✅ '{s.name}' {state_text}.")
            result["changed"].append(s.name)
        except Exception as e:
            print(f"❌ No se pudo dejar '{s.name}' {state_text}: {e}")
            result["failed"].append(s.name)
    return result


def toggle_alerts_cli(service):
    """Menú de consola: habilitar o deshabilitar una o varias alertas, con confirmación."""
    choice = input("1) Deshabilitar  2) Habilitar: ").strip()
    enabled = {"1": False, "2": True}.get(choice)
    if enabled is None:
        print("Opción no válida.")
        return
    patterns = parse_patterns(input("Nombres separados por coma (admite comodines, ej. CU-AD-*): "))
    if not patterns:
        print("Operación cancelada.")
        return
    preview = set_alerts_enabled(service, patterns, enabled, dry_run=True)
    if not preview["changed"]:
        print("No hay nada que cambiar.")
        return
    verb = "habilitar" if enabled else "deshabilitar"
    if input(f"¿{verb.capitalize()} {len(preview['changed'])} alertas? (s/N): ").strip().lower() == "s":
        set_alerts_enabled(service, patterns, enabled)
    else:
        print("Operación cancelada.")
