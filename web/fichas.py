"""
Fichas de cliente («cheat sheet»): una hoja por cliente con lo que NO cambia a menudo y sirve para saber de un vistazo
qué tiene cada uno: plataforma, fuentes de datos por índice, casos de uso por tecnología, apps y add-ons, integraciones
(tokens HEC, puertos de salida, IP permitidas) y roles.

Se evita a propósito lo que varía cada día (tamaños, volúmenes, estado de salud): la ficha es una instantánea pensada para
publicarse (p. ej. en SharePoint) sin tener que mantenerla. Se genera en HTML (autocontenido, con buscador) y en Markdown,
una carpeta por cliente: <salida>/<CLIENTE>/Ficha_<CLIENTE>.html y .md, más un índice con todos los clientes.
"""
import ast
import os
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from html import escape

from core import acs
from core.conventions import is_cu_alert, is_test_alert, sirt_app, strip_test_prefix
from core.clients import _read_source
from core.index_coverage import DATA_DAYS, matches
from core.paths import REPORTS_ROOT
from web import services
from web.explorer import list_apps, list_roles, list_saved_searches, list_users
from web.index_coverage import analyze_client
from web.operations import OperationError

WORKERS = 4
SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
DEFAULT_OUTPUT = os.path.join(REPORTS_ROOT, "fichas")
MAX_SOURCETYPES = 15          # fuentes que se listan por índice (el resto se resume con «… y N más»)
# CU-<TÁCTICA>-<TECNOLOGÍA>-<ID>-<MITRE> <Nombre>: p. ej. CU-CA-AD-1052-T1555.004 (Credential Access, Active Directory)
_TECH = re.compile(r"^CU-([A-Za-z0-9]+)-([A-Za-z0-9]+)-", re.IGNORECASE)

# Apps que vienen con Splunk Cloud/Enterprise: no aportan información sobre el cliente
BUILTIN_APPS = {
    "search", "launcher", "learned", "legacy", "introspection_generator_addon", "sample_app", "splunkforwarder",
    "alert_logevent", "alert_webhook", "appsbrowser", "user-prefs", "journald_input", "python_upgrade_readiness_app",
    "splunk-dashboard-studio", "splunk_httpinput", "splunk_internal_metrics", "splunk_metrics_workspace",
    "splunk_monitoring_console", "splunk_rapid_diag", "splunk_secure_gateway", "splunk_archiver", "splunk_assist",
    "splunk_essentials_9_0", "splunk_instance_monitoring", "splunk_ingest_actions", "splunk_gdi", "splunk_cloud_gateway",
    "splunk_datasets_addon", "splunk_app_addon-builder_dev", "dynamic-data-self-storage-app", "data_manager",
    "cloud_administration", "cloud_monitoring_console", "splunkclouduf", "splunk_instance_monitoring_internal",
    "splunk_product_guidance", "splunk_ai_assistant", "SplunkEnterpriseSecuritySuite_internal",
    "dmc", "dmx", "audit_trail", "guided_experience", "scsaudit", "search_artifacts_helper", "tos", "prometheus",
    "splunk-data-management", "splunk-ingest-monitoring", "splunk-unified-dataset-management", "splunk-visual-exporter",
    "splunk-ai-canvas", "splunk_instrumentation", "SplunkDeploymentServerConfig", "000-self-service", "075-cloudworks",
}
BUILTIN_PREFIXES = ("zzz_", "splunk_essentials", "splunk_cloud", "splunk_instance", "splunk_assist", "splunk_agent",
                    "splunk_mcp", "splunk_ai", "sc_admin", "100-", "Splunk_SA_", "Splunk_ML_")


# Nombres que delatan un token o rol de pruebas
_TEST_NAME = re.compile(r"(^|[\W_])(test|tests|prueba|pruebas|demo|tmp|temp)($|[\W_])", re.IGNORECASE)
MAX_ATTENTION = 5
MAX_HOWTO = 3                 # índices y sourcetypes que se muestran por tecnología


def excluded_indexes():
    """Patrones de índices que la ficha no muestra ni cuenta: config.py -> fichas = {"indices_excluidos": [...]}."""
    try:
        for node in ast.parse(_read_source()).body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "fichas" for t in node.targets):
                value = ast.literal_eval(node.value)
                return [str(p) for p in (value or {}).get("indices_excluidos") or []]
    except (OSError, ValueError, SyntaxError):
        pass
    return []


def _technology(name):
    """(tecnología, táctica) de un CU; ('SIN-TEC', '') si el nombre no sigue la convención."""
    match = _TECH.match(strip_test_prefix(name))
    return (match.group(2).upper(), match.group(1).upper()) if match else ("SIN-TEC", "")


def _is_builtin_app(app):
    name = app["name"]
    return name in BUILTIN_APPS or name.startswith(BUILTIN_PREFIXES)


def _platform(name, client, service):
    info = services.platform_of(client, services.server_info(name))
    server = services.server_info(name) or {}
    return {"kind": info["kind"], "stack": info.get("stack"), "acs": info["acs"], "version": server.get("version"),
            "server": server.get("serverName"), "experience": info.get("experience")}


def _use_cases(service, client):
    rows = list_saved_searches(service)
    cus = [r for r in rows if r["is_cu"]]
    by_tech = {}
    for r in cus:
        name, tactic = _technology(r["name"])
        tech = by_tech.setdefault(name, {"prod": [], "test": [], "disabled": [], "tactics": set()})
        if tactic:
            tech["tactics"].add(tactic)
        if r["is_test"]:
            tech["test"].append(r["name"])
        elif r["disabled"]:
            tech["disabled"].append(r["name"])
        else:
            tech["prod"].append(r["name"])
    for tech in by_tech.values():
        tech["tactics"] = sorted(tech["tactics"])
        for key in ("prod", "test", "disabled"):
            tech[key].sort(key=str.lower)
    others = [r for r in rows if r["is_alert"] and not r["is_cu"] and not r["is_es"]]
    cyences = [r for r in others if str(r["app"]).lower().startswith("cyences")]
    es = [r for r in rows if r["is_es"]]
    return {
        "technologies": dict(sorted(by_tech.items())),
        "prod": sum(len(t["prod"]) for t in by_tech.values()),
        "test": sum(len(t["test"]) for t in by_tech.values()),
        "disabled": sum(len(t["disabled"]) for t in by_tech.values()),
        "cyences": len(cyences), "other_alerts": len(others) - len(cyences), "es": len(es),
    }


def _sources(service, client_name, excluded):
    result = analyze_client(service, client_name)
    rows = []
    for r in result["indexes"]:
        if matches(r["name"], excluded):
            continue
        rows.append({"index": r["name"], "sourcetypes": [s["name"] for s in r["sourcetypes"]],
                     "cus": r["active_count"], "status": r["status"]})
    rows.sort(key=lambda r: r["index"].lower())
    return {"days": result["period_days"], "indexes": rows,
            "without_data": sorted(n for n in result["without_data"] if not matches(n, excluded)),
            "metrics": sorted(result["metrics"]), "howto": _howto(result, rows, excluded)}


def _howto(result, rows, excluded):
    """Por tecnología, dónde buscar: índices y sourcetypes que consultan sus CU (los de alcance amplio no cuentan)."""
    data = {r["index"].lower(): r["sourcetypes"] for r in rows}
    per_tech = {}
    for a in result["alerts"]:
        if a["mode"] in ("all", "default") or not a["indexes"]:
            continue
        tech = per_tech.setdefault(_technology(a["name"])[0], {"active": [], "other": []})
        tech["active" if a["kind"] == "active" else "other"].append(a)
    howto = {}
    for name, groups in per_tech.items():
        alerts = groups["active"] or groups["other"]
        indexes, sourcetypes = Counter(), {}
        for a in alerts:
            for index in a["indexes"]:
                if matches(index, excluded):
                    continue
                indexes[index] += 1
                sourcetypes.setdefault(index, Counter()).update(a["sourcetype_filters"])
        entries = []
        ranked = sorted(indexes.items(), key=lambda kv: (kv[0].lower() not in data, -kv[1]))   # primero los que tienen datos
        for index, _ in ranked[:MAX_HOWTO]:
            present = data.get(index.lower(), [])
            filters = [st for st, _ in sourcetypes[index].most_common() if st != "*"]
            # los sourcetypes que piden los CU y existen en el índice; si ninguno, los que de verdad tiene el índice
            sts = ([st for st in present if matches(st, filters)] or present)[:MAX_HOWTO]
            if len(sts) == 1:
                clause = f" sourcetype={sts[0]}"
            else:
                clause = " (" + " OR ".join(f"sourcetype={st}" for st in sts) + ")" if sts else ""
            entries.append({"index": index, "sourcetypes": sts, "spl": f"index={index}{clause} earliest=-24h | head 20",
                            "only_disabled": not groups["active"], "no_data": index.lower() not in data})
        if entries:
            howto[name] = entries
    return dict(sorted(howto.items()))


def _apps(service, client):
    sirt = sirt_app(client)
    apps = [a for a in list_apps(service) if not a["disabled"]]
    sirt_row = next((a for a in apps if a["name"] == sirt), None)
    es = any(a["name"] == "SplunkEnterpriseSecuritySuite" for a in apps)
    others = sorted(({"name": a["name"], "label": a["label"], "version": a["version"]}
                     for a in apps if a["name"] != sirt and not _is_builtin_app(a)), key=lambda a: a["label"].lower())
    return {"sirt": {"name": sirt, "version": sirt_row["version"]} if sirt_row else None, "es": es, "apps": others}


def _integrations(name, client, service):
    from web.access import inventory
    from web.hec import annotate, index_names
    data = {"hec": [], "outbound": [], "allowlists": [], "errors": {}}
    if not acs.unavailable_reason(client) is None:
        data["errors"]["acs"] = acs.unavailable_reason(client)
        return data
    try:
        hec = acs.list_hec(client)
        try:
            annotate(hec, index_names(service))
        except Exception:
            pass
        data["hec"] = [{"name": h["name"], "index": h["default_index"], "sourcetype": h["default_sourcetype"],
                        "allowed": h["allowed_indexes"], "disabled": h["disabled"]} for h in hec]
    except Exception as e:
        data["errors"]["hec"] = str(e)
    try:
        inv = inventory(name)
        data["outbound"] = [{"port": p.get("port"), "subnets": p.get("subnets") or []} for p in inv["outbound"]
                            if p.get("port")]
        per_feature = {}
        for a in inv["allowlists"]:
            if a["error"]:
                continue
            per_feature.setdefault(a["label"], []).extend(a["subnets"])
        data["allowlists"] = [{"feature": f, "subnets": sorted(set(s))} for f, s in per_feature.items() if s]
    except Exception as e:
        data["errors"]["access"] = str(e)
    return data


def _access(service):
    users, roles = list_users(service), list_roles(service)
    return {"users": len(users), "roles": sorted(r["name"] for r in roles)}


def collect(name):
    """Datos de la ficha de un cliente. Lo que falle va a 'errors' sin parar el resto; si no hay conexión, a 'error'."""
    client = services.find_client(name)
    data = {"name": name, "host": client.get("host", ""), "error": None, "errors": {}}
    try:
        service = services.get_service(name)
    except Exception as e:
        data["error"] = str(e)
        return data
    excluded = excluded_indexes()
    data["excluded"] = excluded
    steps = {
        "platform": lambda: _platform(name, client, service),
        "use_cases": lambda: _use_cases(service, client),
        "sources": lambda: _sources(service, name, excluded),
        "apps": lambda: _apps(service, client),
        "integrations": lambda: _integrations(name, client, service),
        "access": lambda: _access(service),
    }
    for key, step in steps.items():
        try:
            data[key] = step()
        except Exception as e:
            data["errors"][key] = f"{type(e).__name__}: {e}"
    data["attention"] = _attention(data)
    return data


def _attention(card):
    """Hasta MAX_ATTENTION líneas, solo estructurales: índices sin CU, CU deshabilitados y tokens o roles de prueba."""
    lines = []
    src, uc, integ, acc = (card.get(k) for k in ("sources", "use_cases", "integrations", "access"))
    if src:
        names = [r["index"] for r in src["indexes"] if r["cus"] == 0]
        if names:
            lines.append(f"Índices con datos y 0 CU activos ({len(names)}): {', '.join(names)}")
    if uc:
        disabled = [f"{t} ({len(v['disabled'])})" for t, v in uc["technologies"].items() if v["disabled"]]
        if disabled:
            lines.append(f"Tecnologías con CU deshabilitados: {', '.join(disabled)}")
    tokens = [h["name"] for h in (integ or {}).get("hec", []) if _TEST_NAME.search(h["name"])]
    if tokens:
        lines.append(f"Tokens HEC de prueba: {', '.join(tokens)}")
    roles = [r for r in (acc or {}).get("roles", []) if _TEST_NAME.search(r)]
    if roles:
        lines.append(f"Roles de prueba: {', '.join(roles)}")
    return lines[:MAX_ATTENTION]


# --- Markdown ---------------------------------------------------------------------------------

def _cut(items, limit):
    items = list(items)
    return items[:limit], max(0, len(items) - limit)


def _more(extra):
    return f" … y {extra} más" if extra else ""


def render_markdown(card, generated):
    p, uc, src, apps, integ, acc = (card.get(k) for k in ("platform", "use_cases", "sources", "apps", "integrations", "access"))
    out = [f"# {card['name']}", "", f"_Instantánea del {generated:%d/%m/%Y}. Datos de fuentes: últimos {DATA_DAYS} días._", ""]
    if card["error"]:
        return "\n".join(out + [f"> No se pudo conectar: {card['error']}", ""])
    out += ["## Atención", ""] + ([f"- {x}" for x in card.get("attention", [])] or ["_Sin puntos de atención._"]) + [""]
    out += ["## General", ""]
    if p:
        kind = {"cloud": "Splunk Cloud", "enterprise": "Splunk Enterprise"}.get(p["kind"], "Desconocido")
        out += [f"- **Plataforma:** {kind}" + (f" ({p['experience']})" if p.get("experience") else ""),
                f"- **Versión:** {p['version'] or '—'}", f"- **Host:** {card['host']}"]
    if apps:
        sirt = apps["sirt"]
        out.append(f"- **App SIRT:** {sirt['name']} {sirt['version']}" if sirt else "- **App SIRT:** no instalada")
        out.append(f"- **Enterprise Security:** {'sí' if apps['es'] else 'no'}")
    if uc:
        out.append(f"- **Casos de uso:** {uc['prod']} en producción, {uc['test']} en TEST, {uc['disabled']} deshabilitados"
                   f" · {len(uc['technologies'])} tecnologías")
    if src:
        out.append(f"- **Índices con datos:** {len(src['indexes'])}")
    out.append("")
    out += ["## Fuentes de datos por índice", ""]
    if src:
        out += ["| Índice | Fuentes (sourcetypes) | CU activos |", "|---|---|---|"]
        for r in src["indexes"]:
            shown, extra = _cut(r["sourcetypes"], MAX_SOURCETYPES)
            out.append(f"| {r['index']} | {', '.join(shown)}{_more(extra)} | {r['cus']} |")
        if src["without_data"]:
            out += ["", f"Índices sin datos recientes: {', '.join(src['without_data'])}"]
    else:
        out.append(f"_No disponible: {card['errors'].get('sources', '')}_")
    out += ["", "## Casos de uso por tecnología", ""]
    if uc:
        out += ["| Tecnología | Producción | TEST | Deshabilitados | Tácticas |", "|---|---|---|---|---|"]
        for tech, t in uc["technologies"].items():
            out.append(f"| {tech} | {len(t['prod'])} | {len(t['test'])} | {len(t['disabled'])} | {', '.join(t['tactics'])} |")
        out += ["", f"Alertas no CU — Cyences: {uc['cyences']} · Otras: {uc['other_alerts']} · Correlation searches de ES: {uc['es']}"]
        for tech, t in uc["technologies"].items():
            if t["prod"]:
                out += ["", f"**{tech}:** " + "; ".join(t["prod"])]
    else:
        out.append(f"_No disponible: {card['errors'].get('use_cases', '')}_")
    out += ["", "## Cómo buscar", ""]
    if src and src["howto"]:
        for tech, entries in src["howto"].items():
            out.append(f"**{tech}**" + (" _(solo hay CU deshabilitados)_" if entries[0]["only_disabled"] else ""))
            out += [f"- índice `{e['index']}`" + (" _(sin datos recientes)_" if e["no_data"] else "") + (f", sourcetype `{', '.join(e['sourcetypes'])}`" if e["sourcetypes"] else "")
                    + f" → `{e['spl']}`" for e in entries]
            out.append("")
    else:
        out.append("_Sin datos._")
    out += ["", "## Apps y add-ons", ""]
    if apps:
        out += [f"- {a['label']} ({a['name']}) {a['version']}" for a in apps["apps"]] or ["_Ninguna_"]
    else:
        out.append(f"_No disponible: {card['errors'].get('apps', '')}_")
    out += ["", "## Integraciones", ""]
    if integ:
        if integ["errors"].get("acs"):
            out.append(f"_Sin ACS: {integ['errors']['acs']}_")
        out += ["**Tokens HEC**", ""]
        out += [f"- {h['name']} → índice {h['index'] or '—'}, sourcetype {h['sourcetype'] or '—'}"
                + (" (deshabilitado)" if h["disabled"] else "") for h in integ["hec"]] or ["_Ninguno_"]
        out += ["", "**Puertos de salida**", ""]
        out += [f"- {o['port']}: {', '.join(o['subnets'])}" for o in integ["outbound"]] or ["_Ninguno_"]
        out += ["", "**IP permitidas**", ""]
        out += [f"- {a['feature']}: {len(a['subnets'])} subredes" for a in integ["allowlists"]] or ["_Ninguna_"]
    out += ["", "## Accesos", ""]
    if acc:
        out += [f"- Usuarios: {acc['users']}", f"- Roles: {', '.join(acc['roles'])}"]
    if card["errors"]:
        out += ["", "## Avisos", ""] + [f"- {k}: {v}" for k, v in card["errors"].items()]
    out += ["", "---", f"_Índices omitidos de esta ficha (config.py → fichas): {', '.join(card.get('excluded') or []) or 'ninguno'}._"]
    return "\n".join(out) + "\n"


# --- HTML -------------------------------------------------------------------------------------

CSS = """
:root{--bg:#fff;--fg:#1c2330;--muted:#5b6678;--card:#f4f6fa;--line:#dde2ea;--accent:#1f6feb;--good:#1a7f37;--warn:#9a6700}
@media (prefers-color-scheme:dark){:root{--bg:#0f141b;--fg:#e6ebf2;--muted:#9aa6b8;--card:#171e29;--line:#2a3443;--accent:#58a6ff;--good:#3fb950;--warn:#d29922}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 Segoe UI,system-ui,sans-serif}
main{max-width:1100px;margin:0 auto;padding:16px}h1{margin:0}h2{margin:28px 0 8px;font-size:17px;border-bottom:1px solid var(--line);padding-bottom:4px}
.sub{color:var(--muted);font-size:12.5px}.tiles{display:flex;flex-wrap:wrap;gap:10px;margin:14px 0}
.tile{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 14px;min-width:120px}
.tile b{display:block;font-size:22px}.tile span{color:var(--muted);font-size:12px}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:12px}.chip{display:inline-block;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:0 8px;margin:1px 2px 1px 0;font-size:12px}
.off{color:var(--warn)}input[type=search]{width:100%;padding:8px 10px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--fg);margin:10px 0}
details{margin:4px 0}summary{cursor:pointer}.warn{color:var(--warn)}.attention{border-left:4px solid var(--warn);background:var(--card);padding:6px 14px;margin:14px 0;border-radius:4px}.attention ul{margin:4px 0 2px;padding-left:18px}.attention p{margin:4px 0}code{background:var(--card);border:1px solid var(--line);border-radius:4px;padding:0 4px}a{color:var(--accent)}
@media print{input{display:none}details{display:block}}
"""
JS = """
const q=document.getElementById('q');
q.addEventListener('input',()=>{const t=q.value.toLowerCase();
document.querySelectorAll('tbody tr,li').forEach(r=>{r.style.display=!t||r.textContent.toLowerCase().includes(t)?'':'none'});
document.querySelectorAll('details').forEach(d=>{if(t)d.open=true});});
"""


def _e(value):
    return escape(str(value if value is not None else ""))


def _chips(items):
    return "".join(f'<span class="chip">{_e(i)}</span>' for i in items)


def _table(headers, rows):
    head = "".join(f"<th>{_e(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _tile(value, label):
    return f'<div class="tile"><b>{_e(value)}</b><span>{_e(label)}</span></div>'


def render_html(card, generated):
    p, uc, src, apps, integ, acc = (card.get(k) for k in ("platform", "use_cases", "sources", "apps", "integrations", "access"))
    parts = [f"<h1>{_e(card['name'])}</h1>",
             f'<div class="sub">{_e(card["host"])} · instantánea del {generated:%d/%m/%Y} · fuentes de los últimos {DATA_DAYS} días</div>']
    if card["error"]:
        parts.append(f'<p class="warn">No se pudo conectar con el cliente: {_e(card["error"])}</p>')
        return _page(card["name"], parts, generated)
    tiles = []
    if p:
        kind = {"cloud": "Splunk Cloud", "enterprise": "Splunk Enterprise"}.get(p["kind"], "?")
        tiles += [_tile(kind + (f" {p['experience']}" if p.get("experience") else ""), "plataforma"), _tile(p["version"] or "—", "versión")]
    if src:
        tiles.append(_tile(len(src["indexes"]), "índices con datos"))
    if uc:
        tiles += [_tile(uc["prod"], "CU en producción"), _tile(uc["test"], "CU en TEST"), _tile(len(uc["technologies"]), "tecnologías")]
    if apps:
        sirt = apps["sirt"]
        tiles += [_tile(sirt["version"] if sirt else "no", "app SIRT"), _tile("sí" if apps["es"] else "no", "Enterprise Security")]
    if acc:
        tiles.append(_tile(acc["users"], "usuarios"))
    attention = "".join(f"<li>{_e(x)}</li>" for x in card.get("attention", []))
    box = f"<ul>{attention}</ul>" if attention else "<p>Sin puntos de atención.</p>"
    parts += [f'<div class="attention"><b>Atención</b>{box}</div>',
              f'<div class="tiles">{"".join(tiles)}</div>',
              '<input type="search" id="q" placeholder="Buscar en la ficha (índice, sourcetype, CU, app…)">']

    parts.append("<h2>Fuentes de datos por índice</h2>")
    if src:
        rows = []
        for r in src["indexes"]:
            shown, extra = _cut(r["sourcetypes"], MAX_SOURCETYPES)
            rows.append([f"<b>{_e(r['index'])}</b>", _chips(shown) + (f'<span class="sub">{_more(extra)}</span>' if extra else ""), r["cus"]])
        parts.append(_table(["Índice", "Fuentes (sourcetypes)", "CU activos"], rows))
        if src["without_data"]:
            parts.append(f'<p class="sub">Sin datos recientes: {_e(", ".join(src["without_data"]))}</p>')
    else:
        parts.append(f'<p class="warn">No disponible: {_e(card["errors"].get("sources"))}</p>')

    parts.append("<h2>Casos de uso por tecnología</h2>")
    if uc:
        rows = []
        for tech, t in uc["technologies"].items():
            listing = "".join(f"<li>{_e(n)}</li>" for n in t["prod"])
            listing += "".join(f'<li class="off">{_e(n)} (TEST)</li>' for n in t["test"])
            listing += "".join(f'<li class="off">{_e(n)} (deshabilitado)</li>' for n in t["disabled"])
            rows.append([f"<b>{_e(tech)}</b>", len(t["prod"]), len(t["test"]), len(t["disabled"]), _chips(t["tactics"]),
                         f"<details><summary>ver</summary><ul>{listing}</ul></details>"])
        parts.append(_table(["Tecnología", "Producción", "TEST", "Deshab.", "Tácticas", "Casos de uso"], rows))
        parts.append(f'<p class="sub">Alertas no CU — Cyences: {uc["cyences"]} · Otras: {uc["other_alerts"]} · Correlation searches de ES: {uc["es"]}</p>')
    else:
        parts.append(f'<p class="warn">No disponible: {_e(card["errors"].get("use_cases"))}</p>')

    parts.append("<h2>Cómo buscar</h2>")
    if src and src["howto"]:
        rows = []
        for tech, entries in src["howto"].items():
            note = ' <span class="sub">(solo CU deshabilitados)</span>' if entries[0]["only_disabled"] else ""
            for i, e in enumerate(entries):
                rows.append([f"<b>{_e(tech)}</b>{note}" if i == 0 else "", _e(e["index"]) + (' <span class="sub">(sin datos recientes)</span>' if e["no_data"] else ""),
                             _chips(e["sourcetypes"]),
                             f"<code>{_e(e['spl'])}</code>"])
        parts.append(_table(["Tecnología", "Índice", "Sourcetype", "SPL de ejemplo"], rows))
    else:
        parts.append('<p class="sub">Sin datos.</p>')

    parts.append("<h2>Apps y add-ons</h2>")
    if apps:
        parts.append(_table(["App", "Id", "Versión"], [[_e(a["label"]), _e(a["name"]), _e(a["version"])] for a in apps["apps"]])
                     if apps["apps"] else '<p class="sub">Ninguna app adicional.</p>')
    else:
        parts.append(f'<p class="warn">No disponible: {_e(card["errors"].get("apps"))}</p>')

    parts.append("<h2>Integraciones</h2>")
    if integ:
        if integ["errors"].get("acs"):
            parts.append(f'<p class="sub">Sin ACS: {_e(integ["errors"]["acs"])}</p>')
        parts.append("<h3>Tokens HEC</h3>" + (_table(["Token", "Índice", "Sourcetype", "Estado"], [
            [_e(h["name"]), _e(h["index"] or "—"), _e(h["sourcetype"] or "—"),
             '<span class="off">deshabilitado</span>' if h["disabled"] else "activo"] for h in integ["hec"]]) if integ["hec"]
            else '<p class="sub">Ninguno.</p>'))
        parts.append("<h3>Puertos de salida</h3>" + (_table(["Puerto", "Destinos"], [
            [_e(o["port"]), _chips(o["subnets"])] for o in integ["outbound"]]) if integ["outbound"] else '<p class="sub">Ninguno.</p>'))
        parts.append("<h3>IP permitidas</h3>" + (_table(["Funcionalidad", "Subredes"], [
            [_e(a["feature"]), f"<details><summary>{len(a['subnets'])} subredes</summary>{_chips(a['subnets'])}</details>"]
            for a in integ["allowlists"]]) if integ["allowlists"] else '<p class="sub">Ninguna.</p>'))
        for key, text in integ["errors"].items():
            if key != "acs":
                parts.append(f'<p class="warn">{_e(key)}: {_e(text)}</p>')

    parts.append("<h2>Accesos</h2>")
    if acc:
        parts.append(f'<p>{acc["users"]} usuarios · Roles: {_chips(acc["roles"])}</p>')
    if card["errors"]:
        parts.append("<h2>Avisos</h2>" + "".join(f'<p class="warn">{_e(k)}: {_e(v)}</p>' for k, v in card["errors"].items()))
    omitted = ", ".join(card.get("excluded") or []) or "ninguno"
    parts.append('<p class="sub" style="margin-top:28px;border-top:1px solid var(--line);padding-top:8px">'
                 f"Índices omitidos de esta ficha (config.py → fichas): {_e(omitted)}.</p>")
    return _page(card["name"], parts, generated)


def _page(title, parts, generated):
    return (f'<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>Ficha {_e(title)}</title><style>{CSS}</style></head><body><main>{''.join(parts)}</main><script>{JS}</script></body></html>")


def render_index(cards, generated):
    rows = []
    for c in cards:
        folder = _folder(c["name"])
        uc, src, p = c.get("use_cases"), c.get("sources"), c.get("platform")
        rows.append([f'<a href="{_e(folder)}/Ficha_{_e(folder)}.html"><b>{_e(c["name"])}</b></a>',
                     _e(p["version"]) if p else "—", len(src["indexes"]) if src else "—",
                     uc["prod"] if uc else "—", len(uc["technologies"]) if uc else "—",
                     f'<span class="warn">{_e(c["error"])}</span>' if c["error"] else ""])
    parts = ["<h1>Fichas de clientes</h1>", f'<div class="sub">Instantánea del {generated:%d/%m/%Y}</div>',
             '<input type="search" id="q" placeholder="Buscar cliente…">',
             _table(["Cliente", "Versión", "Índices", "CU en producción", "Tecnologías", ""], rows)]
    return _page("clientes", parts, generated)


def _folder(name):
    return SAFE_NAME.sub("_", name).strip("_") or "cliente"


# --- Generación -------------------------------------------------------------------------------

def build_fichas(clients, options=None):
    """Valida la petición y devuelve (título, función) para generar las fichas en una tarea."""
    options = options or {}
    clients = [c for c in dict.fromkeys(clients or []) if c]
    if not clients:
        raise OperationError("Seleccione al menos un cliente.")
    output = os.path.abspath(str(options.get("output") or "").strip() or DEFAULT_OUTPUT)
    formats = [f for f in ("html", "md") if f in (options.get("formats") or ["html", "md"])]
    if not formats:
        raise OperationError("Seleccione al menos un formato.")

    def run():
        generated = datetime.now()
        print(f"▶ Recopilando datos de {len(clients)} {'cliente' if len(clients) == 1 else 'clientes'}…")
        cards = {}
        with ThreadPoolExecutor(max_workers=min(WORKERS, len(clients))) as pool:
            futures = {pool.submit(collect, n): n for n in clients}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    cards[name] = future.result()
                except Exception as e:
                    cards[name] = {"name": name, "host": "", "error": f"{type(e).__name__}: {e}", "errors": {}}
                card = cards[name]
                if card["error"]:
                    print(f"❌ {name}: {card['error']}")
                else:
                    print(f"  · {name}: datos recopilados" + (f" (⚠️ sin {', '.join(card['errors'])})" if card["errors"] else ""))
        ordered = [cards[n] for n in clients]
        for card in ordered:
            folder = os.path.join(output, _folder(card["name"]))
            os.makedirs(folder, exist_ok=True)
            for fmt in formats:
                text = (render_html if fmt == "html" else render_markdown)(card, generated)
                with open(os.path.join(folder, f"Ficha_{_folder(card['name'])}.{fmt}"), "w", encoding="utf-8") as f:
                    f.write(text)
        if "html" in formats:
            with open(os.path.join(output, "index.html"), "w", encoding="utf-8") as f:
                f.write(render_index(ordered, generated))
        print(f"✅ Fichas generadas en {output}")

    return f"Fichas de {clients[0] if len(clients) == 1 else str(len(clients)) + ' clientes'}", run
