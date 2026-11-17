"""
Índices sin casos de uso (CLI): índices que reciben datos pero que no consulta ningún caso de uso «CU-…» habilitado.
La lógica está en core/index_coverage.py; la lista de índices ignorados se comparte con la web (indices_ignorados.json).
"""
from core.index_coverage import load_ignored, save_ignored
from web.index_coverage import analyze_client, report_data

MAX_LINES = 40


def _when(epoch):
    from datetime import datetime
    return datetime.fromtimestamp(epoch).strftime("%d/%m/%Y %H:%M") if epoch else "—"


def show_index_coverage(service, client):
    """Muestra los índices sin cubrir del cliente (y devuelve el análisis)."""
    name = client.get("name")
    print("Revisando los casos de uso y los índices (puede tardar un poco)...")
    try:
        result = analyze_client(service, name)
    except Exception as e:
        print(f"❌ Error al revisar la cobertura de índices: {e}")
        return None
    data = report_data(result)
    s = data["summary"]
    print(f"\n{s['with_data']} índices con datos en {data['period_days']} días · {s['active_alerts']} CU habilitados de "
          f"{s['alerts']} · {s['used']} cubiertos · {s['ignored']} ignorados · {s['excluded']} internos/sistema excluidos")
    for warning in data["warnings"]:
        print(f"⚠️ {warning}")

    unused = [r for r in data["uncovered"] if r["status"] == "unused"]
    inactive = [r for r in data["uncovered"] if r["status"] == "inactive_only"]
    print(f"\n{'⚠️' if unused else '✅'} Índices sin ningún CU ({len(unused)}):")
    for r in unused[:MAX_LINES]:
        print(f"  - {r['name']}: {r['events']:,} eventos · último {_when(r['last'])} · {r['sourcetypes_text']}".replace(",", "."))
    if len(unused) > MAX_LINES:
        print(f"  ... y {len(unused) - MAX_LINES} más (el informe Excel los incluye todos)")
    if inactive:
        print(f"\n⚠️ Índices que solo consultan CU deshabilitados o de pruebas ({len(inactive)}):")
        for r in inactive[:MAX_LINES]:
            print(f"  - {r['name']}: {'; '.join(r['users_text'][:3])}{' …' if len(r['users_text']) > 3 else ''}")
    if data["broad"]:
        print(f"\nℹ️ CU que buscan en cualquier índice (index=* o sin índice), no cuentan como cobertura ({len(data['broad'])}):")
        for a in data["broad"][:MAX_LINES]:
            print(f"  - {a['name']} [{a['mode_label']}]")
    if data["issues"]:
        print(f"\nℹ️ CU con partes que no se pudieron resolver ({len(data['issues'])}): revíselos en la web (Índices sin CU)")
    return data


def index_coverage_cli(service, client):
    """Opción del menú: muestra el análisis y permite añadir índices a la lista de ignorados del cliente."""
    data = show_index_coverage(service, client)
    if not data or not data["uncovered"]:
        return
    names = input("\nÍndices a ignorar en este cliente (separados por comas; Enter para ninguno): ").strip()
    patterns = [n.strip() for n in names.split(",") if n.strip()]
    if not patterns:
        return
    reason = input("Motivo (opcional): ").strip()
    current = load_ignored(client["name"])["client"]
    try:
        save_ignored(client["name"], current + [{"index": p, "reason": reason} for p in patterns])
    except ValueError as e:
        print(f"❌ {e}")
        return
    print(f"✅ Ignorados en {client['name']}: {', '.join(patterns)} (se pueden quitar desde la web)")
