// Salud de todos los clientes: conexión, alertas, planificador, ingesta y fuentes que han dejado de enviar datos.
import { get, post } from '../api.js';
import { state, selectClient, navigate } from '../core.js';
import { openReportModal } from './reports.js';
import { platformBadges } from './platform.js';
import {
  h, append, clear, icon, button, badge, pageHeader, DataTable, openDrawer, kv, section, errorBox, infoBox, emptyState,
  statusLabel, fmtNum, fmtBytes, fmtRelative, fmtDate, withBusy,
} from '../ui.js';

const THRESHOLD_KEY = 'sm.silentHours';
const IGNORE_KEY = 'sm.silentIgnored';
const THRESHOLDS = [4, 12, 24, 48];
const RANK = { pending: -1, ok: 0, info: 0, warning: 1, serious: 2, critical: 3 };
const LEVEL_UI = {
  ok: { text: 'Correcto', variant: 'good', icon: 'checkCircle' },
  info: { text: 'Info', variant: 'muted', icon: 'info' },
  warning: { text: 'Aviso', variant: 'warn', icon: 'alert' },
  serious: { text: 'Problema', variant: 'serious', icon: 'alert' },
  critical: { text: 'Crítico', variant: 'bad', icon: 'xCircle' },
  pending: { text: 'Sin analizar', variant: 'muted', icon: 'clock' },
};
const CHECK_LABELS = { alerts: 'las alertas', scheduler: 'el planificador', ingest: 'la ingesta', sources: 'las fuentes de datos', platform: 'la plataforma (ACS)' };
const CHECK_ERRORS = { stack: 'el estado del stack', windows: 'las ventanas de mantenimiento', freezes: 'los change freezes' };
const WINDOW_NOTICE_DAYS = 7;

/** Avisos de la comprobación de plataforma: misma lógica que platform_problems() de web/health.py. */
export function platformProblems(p, now = Date.now() / 1000) {
  const problems = [];
  const add = (level, text) => problems.push({ level, text });
  const t = state.meta?.token_warn_days || { warning: 30, serious: 7 };
  if (p.token_expiry) {
    const days = (p.token_expiry - now) / 86400;
    const when = fmtDate(p.token_expiry);
    if (days < 0) add('critical', `El token de acceso caducó el ${when}: hay que renovarlo`);
    else if (days < t.serious) add('serious', `El token de acceso caduca el ${when} (en ${Math.max(Math.floor(days), 0)} días)`);
    else if (days < t.warning) add('warning', `El token de acceso caduca el ${when} (en ${Math.floor(days)} días)`);
  }
  if (p.restart_required) add('warning', 'El stack tiene un reinicio pendiente');
  if (p.stack_status && String(p.stack_status).toLowerCase() !== 'ready') add('serious', `ACS indica que el stack está en estado «${p.stack_status}»`);
  if (p.pending_addons) add('info', p.pending_addons === 1 ? '1 add-on con actualización pendiente' : `${p.pending_addons} add-ons con actualización pendiente`);
  const w = p.next_window;
  if (w?.start && w.start - now < WINDOW_NOTICE_DAYS * 86400) add('info', `Ventana de mantenimiento de Splunk el ${fmtDate(w.start)} (${w.type || 'mantenimiento'}${w.zero_downtime ? ', sin corte' : ''})`);
  for (const f of p.active_freezes || []) add('info', `Change freeze vigente hasta el ${f.end}${f.reason || f.applies_to ? `: ${f.reason || f.applies_to}` : ''}`);
  for (const [key, err] of Object.entries(p.errors || {})) if (key !== 'addons') add('info', `ACS no devolvió ${CHECK_ERRORS[key] || key}: ${String(err).slice(0, 120)}`);
  return problems;
}

const pct = (n) => `${Math.round(n * 100)} %`;

function readThreshold() {
  try { return Number(localStorage.getItem(THRESHOLD_KEY)) || 24; } catch { return 24; }
}

function readIgnored() {
  try { return new Set(JSON.parse(localStorage.getItem(IGNORE_KEY)) || []); } catch { return new Set(); }
}

function saveIgnored(set) {
  try { localStorage.setItem(IGNORE_KEY, JSON.stringify([...set])); } catch { /* sin almacenamiento */ }
}

const sourceKey = (client, s) => `${client}|${s.index}|${s.sourcetype}`;

/** Diagnóstico de un cliente: nivel global y lista de problemas, según el umbral de fuentes silenciosas. */
export function evaluate(result, thresholdHours, ignored) {
  const problems = [];
  const add = (level, text) => problems.push({ level, text });
  if (!result || !result.checks) {
    if (result?.status === 'error') {
      add('critical', `Sin conexión: ${result.error}`);
      return { level: 'critical', problems, silent: [] };
    }
    return { level: 'pending', problems, silent: [] };
  }
  if (result.status === 'error') add('critical', `Sin conexión: ${result.error}`);

  const checks = result.checks;
  for (const [key, check] of Object.entries(checks)) {
    if (!check.ok) add('warning', `No se pudo comprobar ${CHECK_LABELS[key] || key}: ${check.error}`);
  }

  const scheduler = checks.scheduler?.ok ? checks.scheduler.data : null;
  if (scheduler?.errors) add('serious', `${fmtNum(scheduler.errors)} ejecuciones del planificador con error en 24 h`);
  if (scheduler?.skipped) add('warning', `${fmtNum(scheduler.skipped)} ejecuciones del planificador saltadas en 24 h`);

  let silent = [];
  if (checks.sources?.ok) {
    const limit = Date.now() / 1000 - thresholdHours * 3600;
    silent = checks.sources.data.filter((s) => s.last < limit && !ignored.has(sourceKey(result.client, s)));
    if (silent.length) add('serious', `${silent.length} ${silent.length === 1 ? 'fuente sin datos' : 'fuentes sin datos'} en las últimas ${thresholdHours} h`);
  }

  const ingest = checks.ingest?.ok ? checks.ingest.data : null;
  if (ingest && ingest.avg7d_bytes > 0) {
    if (ingest.last24h_bytes === 0) add('critical', 'No ha entrado ningún dato en las últimas 24 h');
    else if (ingest.change < -0.5) add('serious', `La ingesta ha caído un ${pct(-ingest.change)} respecto a la media de 7 días`);
    else if (ingest.change > 1) add('warning', `La ingesta ha subido un ${pct(ingest.change)} respecto a la media de 7 días`);
  }

  const alerts = checks.alerts?.ok ? checks.alerts.data : null;
  if (alerts?.cu_disabled) add('info', alerts.cu_disabled === 1 ? '1 caso de uso CU- deshabilitado' : `${alerts.cu_disabled} casos de uso CU- deshabilitados`);

  const platform = checks.platform?.ok ? checks.platform.data : null;
  if (platform) for (const p of platformProblems(platform)) add(p.level, p.text);

  const level = problems.reduce((current, p) => (RANK[p.level] > RANK[current] ? p.level : current), 'ok');
  problems.sort((a, b) => RANK[b.level] - RANK[a.level]);
  return { level, problems, silent };
}

function levelLabel(level, running) {
  const ui = running && level === 'pending' ? { text: 'Analizando…', variant: 'muted' } : LEVEL_UI[level];
  const el = statusLabel(ui.text, ui.variant, running ? null : ui.icon);
  if (running) el.prepend(h('span', { class: 'spinner' }));
  return el;
}

function deltaLabel(change) {
  if (change === null || change === undefined) return null;
  const tone = change < -0.5 ? 'delta-serious' : change > 1 ? 'delta-warn' : '';
  return h('span', { class: `delta ${tone}`, title: 'Frente a la media diaria de los 7 días anteriores' },
    `${change >= 0 ? '▲' : '▼'} ${pct(Math.abs(change))}`);
}

function problemList(problems) {
  if (!problems.length) return h('p', { class: 'status status-good', style: { margin: 0 } }, icon('checkCircle'), 'Todo correcto');
  return h('ul', { class: 'problem-list' }, problems.map((p) => {
    const ui = LEVEL_UI[p.level];
    return h('li', { class: `status-${ui.variant}` }, icon(ui.icon), h('span', { style: { color: 'var(--text)' } }, p.text));
  }));
}

export default {
  id: 'health',
  title: 'Salud de clientes',
  icon: 'heartPulse',
  render(root) {
    let snapshot = { results: {}, running: false };
    let threshold = readThreshold();
    let ignored = readIgnored();
    let timer = null;
    let alive = true;
    let openClient = null;
    let drawer = null;
    let detailKey = null;

    const tiles = h('div', { class: 'tiles tiles-5' });
    const errorSlot = h('div');
    const stamp = h('span', { class: 'muted', style: { fontSize: '12.5px' } });

    const rows = () => state.clients.map((c) => {
      const result = snapshot.results[c.name] || null;
      return { ...c, result, ...evaluate(result, threshold, ignored), running: result?.status === 'running' };
    });

    const runClients = async (clients) => {
      snapshot = await post('/api/health/run', clients ? { clients } : {});
      refresh();
      schedulePoll();
    };

    const table = new DataTable({
      rows: [],
      searchText: (r) => `${r.name} ${r.host}`,
      searchPlaceholder: 'Buscar cliente…',
      initialSort: { key: 'level', dir: 'desc' },
      emptyIcon: 'server',
      emptyTitle: 'No hay clientes configurados',
      onRowClick: (r) => showDetail(r.name),
      filters: [{
        id: 'level',
        options: [
          { value: 'all', label: 'Todos' },
          { value: 'problems', label: 'Con incidencias', test: (r) => RANK[r.level] >= 1 },
          { value: 'ok', label: 'Correctos', test: (r) => r.level === 'ok' },
          { value: 'pending', label: 'Sin analizar', test: (r) => r.level === 'pending' },
        ],
      }],
      columns: [
        { key: 'name', label: 'Cliente', render: (r) => h('div', {}, h('div', { class: 'cell-main' }, r.name), h('div', { class: 'cell-sub' }, r.host)) },
        { key: 'level', label: 'Estado', defaultDir: 'desc', sort: (r) => RANK[r.level], render: (r) => levelLabel(r.level, r.running) },
        {
          key: 'problems', label: 'Diagnóstico', sortable: false,
          render: (r) => (r.problems.length
            ? h('div', { style: { maxWidth: '300px' } }, h('div', { class: 'truncate', title: r.problems.map((p) => p.text).join('\n') }, r.problems[0].text),
              r.problems.length > 1 ? h('div', { class: 'cell-sub' }, `y ${r.problems.length - 1} más`) : null)
            : h('span', { class: 'muted' }, r.level === 'pending' ? '—' : 'Sin incidencias')),
        },
        { key: 'version', label: 'Versión', sort: (r) => r.result?.info?.version || '', render: (r) => r.result?.info?.version || h('span', { class: 'muted' }, '—') },
        {
          key: 'platform', label: 'Plataforma', sort: (r) => (r.result?.checks?.platform?.data?.token_expiry || 9e12),
          render: (r) => {
            const p = r.result?.checks?.platform?.data;
            if (!p) return h('span', { class: 'muted' }, '—');
            const t = state.meta?.token_warn_days || { warning: 30, serious: 7 };
            const days = p.token_expiry ? (p.token_expiry - Date.now() / 1000) / 86400 : null;
            const token = days === null ? null : days < 0 ? badge('Token caducado', 'bad', 'alert') : days < t.serious ? badge(`Token: ${Math.max(Math.floor(days), 0)} d`, 'serious', 'alert') : days < t.warning ? badge(`Token: ${Math.floor(days)} d`, 'warn', 'key') : null;
            return h('div', {}, platformBadges(p), token ? h('div', { class: 'badges', style: { marginTop: '3px' } }, token, p.pending_addons ? badge(`${fmtNum(p.pending_addons)} add-ons`, 'outline') : null) : (p.pending_addons ? h('div', { class: 'cell-sub' }, `${fmtNum(p.pending_addons)} add-ons pendientes`) : null));
          },
        },
        {
          key: 'alerts', label: 'Alertas', className: 'num', sort: (r) => r.result?.checks?.alerts?.data?.total ?? -1,
          render: (r) => {
            const a = r.result?.checks?.alerts?.data;
            if (!a) return h('span', { class: 'muted' }, '—');
            return h('div', {}, `${fmtNum(a.total - a.disabled)} / ${fmtNum(a.total)}`, a.disabled ? h('div', { class: 'cell-sub' }, `${fmtNum(a.disabled)} deshab.`) : null);
          },
        },
        {
          key: 'scheduler', label: 'Planificador 24 h', sort: (r) => (r.result?.checks?.scheduler?.data?.errors ?? -1) * 1e6 + (r.result?.checks?.scheduler?.data?.skipped ?? 0),
          defaultDir: 'desc',
          render: (r) => {
            const s = r.result?.checks?.scheduler?.data;
            if (!s) return h('span', { class: 'muted' }, '—');
            return h('div', { class: 'badges' }, badge(`${fmtNum(s.success)} ok`, 'good'),
              s.skipped ? badge(`${fmtNum(s.skipped)} saltadas`, 'warn') : null,
              s.errors ? badge(`${fmtNum(s.errors)} con error`, 'serious') : null);
          },
        },
        {
          key: 'ingest', label: 'Ingesta 24 h', className: 'num', defaultDir: 'desc', sort: (r) => r.result?.checks?.ingest?.data?.last24h_bytes ?? -1,
          render: (r) => {
            const i = r.result?.checks?.ingest?.data;
            if (!i) return h('span', { class: 'muted' }, '—');
            return h('div', {}, fmtBytes(i.last24h_bytes), h('div', {}, deltaLabel(i.change)));
          },
        },
        {
          key: 'silent', label: 'Sin datos', className: 'num', defaultDir: 'desc', sort: (r) => r.silent.length,
          render: (r) => (r.result?.checks?.sources?.ok
            ? (r.silent.length ? badge(fmtNum(r.silent.length), 'serious') : h('span', { class: 'muted' }, '0'))
            : h('span', { class: 'muted' }, '—')),
        },
        { key: 'checked', label: 'Analizado', defaultDir: 'desc', sort: (r) => r.result?.finished || 0, render: (r) => (r.result?.finished ? h('span', { title: fmtDate(r.result.finished) }, fmtRelative(r.result.finished)) : h('span', { class: 'muted' }, '—')) },
        {
          key: 'actions', label: '', className: 'col-actions', sortable: false,
          render: (r) => {
            const again = button('', { size: 'sm', variant: 'ghost', iconName: 'refresh', title: `Analizar ${r.name}`, disabled: r.running });
            again.addEventListener('click', () => withBusy(again, () => runClients([r.name])));
            return h('div', { class: 'row-actions' }, again);
          },
        },
      ],
    });

    function renderTiles(all) {
      const count = (level) => all.filter((r) => r.level === level).length;
      const analysed = all.filter((r) => r.level !== 'pending');
      const ingest = analysed.reduce((sum, r) => sum + (r.result?.checks?.ingest?.data?.last24h_bytes || 0), 0);
      const tile = (label, value, iconName, variant, meta) => h('div', { class: 'tile' },
        h('div', { class: 'tile-label' }, h('span', { class: `status status-${variant}` }, icon(iconName)), label),
        h('div', { class: 'tile-value' }, value), h('div', { class: 'tile-meta' }, meta || ' '));
      clear(tiles).append(
        tile('Correctos', fmtNum(count('ok')), 'checkCircle', 'good', `de ${fmtNum(all.length)} clientes`),
        tile('Con avisos', fmtNum(count('warning')), 'alert', 'warn'),
        tile('Con problemas', fmtNum(count('serious')), 'alert', 'serious'),
        tile('Críticos o sin conexión', fmtNum(count('critical')), 'xCircle', 'bad'),
        tile('Ingesta total 24 h', analysed.length ? fmtBytes(ingest) : '—', 'barChart', 'muted', analysed.length ? `${fmtNum(analysed.length)} clientes analizados` : null));
    }

    function refresh() {
      const all = rows();
      table.setRows(all);
      renderTiles(all);
      const finished = Object.values(snapshot.results).map((r) => r.finished || 0);
      stamp.textContent = snapshot.running ? 'Analizando…' : finished.length ? `Última comprobación ${fmtRelative(Math.max(...finished))}` : '';
      if (openClient && drawer) renderDetail(openClient);
    }

    // Clave del contenido del panel: solo se repinta si cambia el resultado del cliente
    const resultKey = (result) => `${result?.status}|${result?.finished}|${threshold}|${ignored.size}`;

    function schedulePoll() {
      clearTimeout(timer);
      if (!alive || !snapshot.running) return;
      timer = setTimeout(async () => {
        try {
          snapshot = await get('/api/health');
          refresh();
        } catch (e) {
          clear(errorSlot).append(errorBox(e.message));
        }
        schedulePoll();
      }, 1500);
    }

    function toggleIgnored(client, source) {
      const key = sourceKey(client, source);
      if (ignored.has(key)) ignored.delete(key); else ignored.add(key);
      saveIgnored(ignored);
      detailKey = null;
      refresh();
    }

    function showDetail(name) {
      openClient = name;
      detailKey = null;
      drawer = openDrawer({ title: name, body: [], width: 820 });
      drawer.onClose = () => { openClient = null; drawer = null; };
      renderDetail(name);
    }

    function renderDetail(name) {
      const row = rows().find((r) => r.name === name);
      if (!row || !drawer) return;
      const result = row.result;
      if (detailKey === resultKey(result)) return;
      detailKey = resultKey(result);
      const checks = result?.checks || {};
      drawer.setTitle(name, [row.host, result?.info?.version && `Splunk ${result.info.version}`, result?.finished && `comprobado ${fmtRelative(result.finished)}`].filter(Boolean).join(' · '));

      const again = button(row.running ? 'Analizando…' : 'Volver a analizar', { size: 'sm', variant: 'primary', iconName: 'refresh', disabled: row.running });
      again.addEventListener('click', () => withBusy(again, () => runClients([name])));
      drawer.setActions([
        again,
        button('Abrir cliente', { size: 'sm', iconName: 'external', onClick: () => { drawer.close(); selectClient(name); navigate('overview'); } }),
      ]);

      if (!result) {
        drawer.setBody(emptyState({ iconName: 'heartPulse', title: 'Sin analizar', text: 'Pulse «Volver a analizar» para comprobar este cliente.' }));
        return;
      }

      const body = [section('Diagnóstico', problemList(row.problems))];
      const a = checks.alerts?.data;
      if (a) {
        body.push(section('Alertas', kv([
          ['Activas', `${fmtNum(a.total - a.disabled)} de ${fmtNum(a.total)}`],
          ['Casos de uso CU-', `${fmtNum(a.cu_total)}${a.cu_disabled ? ` (${fmtNum(a.cu_disabled)} deshabilitados)` : ''}`],
          ['Enterprise Security', fmtNum(a.es)],
        ])));
      }
      const s = checks.scheduler?.data;
      if (s) {
        body.push(section('Planificador (últimas 24 h)', kv([
          ['Correctas', fmtNum(s.success)], ['Saltadas', fmtNum(s.skipped)], ['Con error', fmtNum(s.errors)],
        ]), s.problems.length ? new DataTable({
          rows: s.problems, compact: true, rowKey: (p) => `${p.name}|${p.status}|${p.reason}`, searchText: (p) => `${p.name} ${p.reason}`,
          searchPlaceholder: 'Filtrar búsquedas…', initialSort: { key: 'count', dir: 'desc' },
          columns: [
            { key: 'name', label: 'Búsqueda', render: (p) => h('div', {}, h('div', { class: 'cell-main' }, p.name || '—'), h('div', { class: 'cell-sub' }, p.app)) },
            { key: 'status', label: 'Estado', render: (p) => badge(p.status, p.status === 'skipped' ? 'warn' : 'serious') },
            { key: 'reason', label: 'Motivo', render: (p) => h('div', { class: 'cell-wrap', style: { maxWidth: '320px', fontSize: '12.5px' } }, p.reason || '—') },
            { key: 'count', label: 'Veces', className: 'num', defaultDir: 'desc', render: (p) => fmtNum(p.count) },
          ],
        }).el : null));
      }
      const p = checks.platform?.data;
      if (p) {
        body.push(section('Plataforma', kv([
          ['Tipo', platformBadges(p)],
          ['Stack', p.stack_version ? `${p.experience || '?'} · versión ${p.stack_version} · estado ${p.stack_status || '?'}` : (p.acs ? null : p.reason)],
          ['Token de acceso', p.token_expiry ? `caduca el ${fmtDate(p.token_expiry)}` : (p.acs ? 'sin caducidad' : null)],
          ['Add-ons pendientes', p.pending_addons === null ? null : fmtNum(p.pending_addons)],
          ['Próxima ventana', p.next_window ? `${fmtDate(p.next_window.start)} · ${p.next_window.type}${p.next_window.zero_downtime ? ' (sin corte)' : ''}` : (p.acs ? 'ninguna en 30 días' : null)],
          ['Change freeze', p.active_freezes?.length ? p.active_freezes.map((f) => `hasta ${f.end}: ${f.reason || f.applies_to}`).join(' · ') : (p.acs ? 'ninguno' : null)],
        ]), Object.keys(p.errors || {}).length ? h('p', { class: 'muted', style: { fontSize: '12.5px' } }, Object.entries(p.errors).map(([k, v]) => `${k}: ${v}`).join(' · ')) : null));
      }
      const i = checks.ingest?.data;
      if (i) {
        body.push(section('Ingesta (license_usage.log)', kv([
          ['Últimas 24 h', h('span', {}, fmtBytes(i.last24h_bytes), ' ', deltaLabel(i.change))],
          ['Media 7 días anteriores', `${fmtBytes(i.avg7d_bytes)} / día`],
        ])));
      }
      if (checks.sources?.ok) {
        const limit = Date.now() / 1000 - threshold * 3600;
        const sources = checks.sources.data.map((src) => ({ ...src, key: sourceKey(name, src), stale: src.last < limit }));
        body.push(section(`Fuentes con datos en los últimos 7 días (${sources.length})`,
          h('p', { class: 'muted', style: { margin: '0 0 8px', fontSize: '12.5px' } },
            `Se marcan las que no envían datos desde hace más de ${threshold} h. «Ignorar» las excluye del diagnóstico (p. ej. fuentes que solo envían una vez al día).`),
          new DataTable({
            rows: sources, compact: true, rowKey: (src) => src.key, searchText: (src) => `${src.index} ${src.sourcetype}`,
            searchPlaceholder: 'Filtrar fuentes…', initialSort: { key: 'last', dir: 'asc' },
            filters: [{
              id: 'stale', value: sources.some((src) => src.stale) ? 'stale' : 'all',
              options: [
                { value: 'stale', label: 'Sin datos', test: (src) => src.stale },
                { value: 'all', label: 'Todas' },
              ],
            }],
            columns: [
              { key: 'index', label: 'Índice', render: (src) => h('span', { class: 'cell-main' }, src.index) },
              { key: 'sourcetype', label: 'Sourcetype' },
              {
                key: 'last', label: 'Último evento',
                render: (src) => h('div', { title: fmtDate(src.last) }, fmtRelative(src.last),
                  src.stale ? h('div', {}, ignored.has(src.key) ? badge('Ignorada', 'outline', 'eyeOff') : badge('Sin datos', 'serious')) : null),
              },
              { key: 'count', label: 'Eventos 7 d', className: 'num', defaultDir: 'desc', render: (src) => fmtNum(src.count) },
              {
                key: 'actions', label: '', className: 'col-actions', sortable: false,
                render: (src) => (src.stale || ignored.has(src.key)
                  ? button(ignored.has(src.key) ? 'Vigilar' : 'Ignorar', { size: 'sm', variant: 'ghost', iconName: ignored.has(src.key) ? 'eye' : 'eyeOff', onClick: () => toggleIgnored(name, src) })
                  : null),
              },
            ],
          }).el));
      }
      const timings = Object.entries(checks).map(([k, c]) => `${k} ${c.seconds ?? '?'} s`).join(' · ');
      if (timings) body.push(h('p', { class: 'muted', style: { fontSize: '12px' } }, `Tiempos: ${timings}`));
      drawer.setBody(body);
    }

    const thresholdSelect = h('select', { class: 'select', style: { width: 'auto' }, 'aria-label': 'Umbral de fuente sin datos' },
      THRESHOLDS.map((t) => h('option', { value: String(t), selected: t === threshold }, `Sin datos > ${t} h`)));
    thresholdSelect.addEventListener('change', () => {
      threshold = Number(thresholdSelect.value);
      try { localStorage.setItem(THRESHOLD_KEY, String(threshold)); } catch { /* sin almacenamiento */ }
      refresh();
    });

    const runAll = button('Analizar todos', { variant: 'primary', iconName: 'heartPulse' });
    runAll.addEventListener('click', () => withBusy(runAll, () => runClients(null)));

    append(clear(root), [
      pageHeader({
        title: 'Salud de clientes',
        subtitle: 'Estado de todos los clientes de un vistazo: conexión, alertas, planificador e ingesta de las últimas 24 h, fuentes que han dejado de enviar datos y, vía ACS, token, reinicio pendiente, add-ons y mantenimiento.',
        actions: [stamp, thresholdSelect,
          button('Exportar informe…', { iconName: 'fileText', onClick: () => openReportModal({ clients: state.clients.map((c) => c.name), sections: ['health'] }) }),
          runAll],
      }),
      errorSlot,
      state.clients.length > 6 ? infoBox(`«Analizar todos» se conecta a los ${state.clients.length} clientes y lanza 3 búsquedas cortas en cada uno (de 6 en 6). Tarda alrededor de un minuto.`) : null,
      tiles,
      table.el]);

    get('/api/health').then((data) => {
      snapshot = data;
      refresh();
      schedulePoll();
    }).catch((e) => clear(errorSlot).append(errorBox(e.message)));
    refresh();

    return () => { alive = false; clearTimeout(timer); };
  },
};
