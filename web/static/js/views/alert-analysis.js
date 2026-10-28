// Análisis de alertas: las ruidosas (más disparos), las muertas (no disparan, no se ejecutan o el planificador las salta) y su tendencia.
import { get } from '../api.js';
import { state, navigate, invalidate } from '../core.js';
import {
  h, append, clear, icon, button, badge, pageHeader, segmented, DataTable, openDrawer, confirmDialog, toast, kv, section,
  codeBlock, errorBox, infoBox, fmtNum, fmtRelative, fmtDuration, withBusy,
} from '../ui.js';
import { columnChart, sparkline } from '../charts.js';
import { openEditor, openBulkEditor, setEnabled, mergeRows, describeCron } from '../alert-editor.js';
import { editInClients, testBadge } from './savedsearches.js';
import { alertKindFilter } from '../filters.js';

const PREFS_KEY = 'sm.alertAnalysis';
const THRESHOLDS = [2, 5, 10, 24, 50];
const cache = new Map();

const DEAD = {
  never_fired: ['Nunca dispara', 'warn'],
  no_actions: ['Sin acciones', 'warn'],
  never_ran: ['No se ejecuta', 'bad'],
  always_skipped: ['Siempre saltada', 'bad'],
  always_failed: ['Siempre con error', 'bad'],
  no_schedule: ['No programada', 'outline'],
};
const DEAD_HELP = {
  never_fired: 'Se ejecuta con normalidad pero no ha disparado ni una vez en el periodo.',
  no_actions: 'Se ejecuta pero no tiene acciones: aunque se cumpla la condición, no avisa a nadie.',
  never_ran: 'Su cron indica que debería haberse ejecutado y no hay ninguna ejecución en el log del planificador.',
  always_skipped: 'El planificador la ha saltado todas las veces (normalmente por exceso de búsquedas concurrentes).',
  always_failed: 'Todas sus ejecuciones han terminado con error.',
  no_schedule: 'Está habilitada pero no programada: nunca se ejecuta sola.',
};
const SCHEDULER_DEAD = ['never_ran', 'always_skipped', 'always_failed'];
const STATE_UI = {
  active: ['Activa', 'good'], disabled: ['Deshabilitada', 'outline'], unscheduled: ['No programada', 'outline'], realtime: ['Tiempo real', 'accent'],
};

function readPrefs() {
  try { return JSON.parse(localStorage.getItem(PREFS_KEY)) || {}; } catch { return {}; }
}

function savePrefs(prefs) {
  try { localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)); } catch { /* sin almacenamiento */ }
}

function load(client, days, force = false) {
  const key = `${client}|${days}`;
  if (!cache.has(key) || force) {
    const entry = { at: Date.now(), promise: get('/api/alert-analysis', { client, days }) };
    entry.promise.catch(() => { if (cache.get(key) === entry) cache.delete(key); });
    cache.set(key, entry);
  }
  return cache.get(key);
}

const pct = (n) => `${Math.round(n * 100)} %`;
const dec = (n) => n.toLocaleString('es-ES', { maximumFractionDigits: n < 100 ? 1 : 0 });
const dayLabel = (iso, options) => new Date(`${iso}T12:00:00`).toLocaleDateString('es-ES', options);

// Mismo criterio que noisy_reason() de web/alert_analysis.py, con el umbral elegido en la página
function noisyReason(a, rules, threshold) {
  const s = a.stats;
  if (a.stats.state !== 'active') return null;
  if (s.fired_per_day >= threshold) return 'per_day';
  if (s.runs >= rules.always_min_runs && (s.fire_rate || 0) >= rules.always_rate) return 'always';
  return null;
}

function noisyText(a) {
  if (a.noisy === 'per_day') return `${dec(a.stats.fired_per_day)} disparos al día de media`;
  if (a.noisy === 'always') return `Dispara en el ${pct(a.stats.fire_rate)} de sus ejecuciones`;
  return '';
}

function issueTexts(s) {
  const attempts = s.runs + s.skipped + s.errors;
  return s.issues.map((issue) => ({
    skips: `Saltada el ${pct(s.skipped / attempts)} de las veces`,
    errors: `${fmtNum(s.errors)} ejecuciones con error`,
    missing_runs: `Se ejecuta menos de lo previsto (${fmtNum(s.attempts_complete)} de ~${fmtNum(s.expected)})`,
  }[issue]));
}

const isDead = (a) => Boolean(a.stats.dead) && ['active', 'unscheduled'].includes(a.stats.state);
const hasSchedulerIssue = (a) => a.stats.state === 'active' && (a.stats.issues.length > 0 || SCHEDULER_DEAD.includes(a.stats.dead));
const stateOf = (a) => (a.disabled ? 'disabled' : !a.scheduled ? 'unscheduled'
  : (String(a.earliest).startsWith('rt') || String(a.latest).startsWith('rt')) ? 'realtime' : 'active');

function trendCell(a) {
  const t = a.stats.trend;
  let text;
  if (!t || t.direction === 'none') text = h('span', { class: 'muted' }, '—');
  else if (t.direction === 'new') text = badge('Empieza', 'accent');
  else if (t.direction === 'flat') text = h('span', { class: 'delta' }, 'estable');
  else text = h('span', { class: `delta ${t.direction === 'up' ? 'delta-warn' : ''}` }, `${t.change > 0 ? '▲' : '▼'} ${pct(Math.abs(t.change))}`);
  return h('div', { class: 'trend-cell' }, sparkline(a.stats.series.slice(0, -1), { label: `Disparos por día de ${a.name}` }), text);
}

function nameCell(a, sub) {
  return h('div', { style: { minWidth: '220px', maxWidth: '440px' } },
    h('div', { class: 'cell-main' }, a.name, testBadge(a, { inline: true })),
    h('div', { class: 'cell-sub truncate' }, sub || [a.app, a.es_label].filter(Boolean).join(' · ')));
}

function scheduleText(a) {
  if (!a.scheduled) return h('span', { class: 'muted' }, 'No programada');
  const text = describeCron(a.cron);
  return h('div', {}, h('code', {}, a.cron || '—'), text && !text.startsWith('Debe') ? h('div', { class: 'cell-sub' }, text) : null);
}

function diagnosisBadges(a) {
  const s = a.stats;
  const badges = [];
  if (a.noisy) badges.push(badge('Ruidosa', 'serious', 'zap'));
  if (isDead(a)) badges.push(badge(DEAD[s.dead][0], DEAD[s.dead][1]));
  if (s.state === 'active' && s.issues.includes('skips')) badges.push(badge('Se salta', 'warn'));
  if (s.state === 'active' && s.issues.includes('errors')) badges.push(badge('Errores', 'bad'));
  if (s.state === 'active' && s.issues.includes('missing_runs')) badges.push(badge('Menos ejecuciones', 'warn'));
  return badges.length ? h('div', { class: 'badges' }, badges) : h('span', { class: 'muted' }, '—');
}

function tile(label, iconName, value, meta) {
  return h('div', { class: 'tile' }, h('div', { class: 'tile-label' }, icon(iconName), label), h('div', { class: 'tile-value' }, value), h('div', { class: 'tile-meta' }, meta || ' '));
}

function schedulerSearch(a, days) {
  const quote = (v) => String(v).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
  return `index=_internal sourcetype=scheduler savedsearch_name="${quote(a.name)}" app="${quote(a.app)}" earliest=-${days - 1}d@d\n`
    + '| table _time status result_count alert_actions suppressed run_time reason\n| sort - _time';
}

export default {
  id: 'alert-analysis',
  title: 'Análisis de alertas',
  icon: 'trending',
  needsClient: true,
  render(root) {
    const prefs = readPrefs();
    const periods = state.meta?.analysis_days || [7, 14, 30];
    let days = periods.includes(prefs.days) ? prefs.days : 7;
    let threshold = THRESHOLDS.includes(prefs.threshold) ? prefs.threshold : 10;
    let tab = ['noisy', 'dead', 'scheduler', 'all'].includes(prefs.tab) ? prefs.tab : 'noisy';
    let data = null;
    let alive = true;
    const client = state.client;
    const body = h('div');
    const stamp = h('span', { class: 'muted', style: { fontSize: '12.5px' } });
    const persist = () => savePrefs({ days, threshold, tab });

    function loading() {
      clear(body).append(h('div', { class: 'card' }, h('div', { class: 'empty' },
        h('span', { class: 'spinner', style: { width: '28px', height: '28px', borderWidth: '3px' } }),
        h('h3', {}, `Analizando ${days} días del planificador de ${client}…`),
        h('p', {}, 'Se resume index=_internal sourcetype=scheduler por alerta y día. Con 30 días y muchas alertas puede tardar un minuto.'))));
    }

    async function show(force = false) {
      const entry = load(client, days, force);
      loading();
      try {
        const result = await entry.promise;
        if (!alive) return;
        data = result;
        stamp.textContent = `Datos ${fmtRelative(entry.at / 1000)}`;
        render();
      } catch (e) {
        if (alive) clear(body).append(errorBox(e.message));
      }
    }

    // --- Acciones sobre las alertas -------------------------------------------------

    function afterChange(updated, rows) {
      mergeRows(rows, updated);
      for (const a of rows) a.stats.state = stateOf(a);
      invalidate(client, ['saved-searches']);
      render();
    }

    async function toggle(rows, enabled) {
      if (!enabled) {
        const ok = await confirmDialog({
          title: rows.length === 1 ? `¿Deshabilitar «${rows[0].name}»?` : `¿Deshabilitar ${fmtNum(rows.length)} alertas?`,
          message: `Dejarán de ejecutarse en ${client} hasta que se vuelvan a habilitar. Antes se guarda su configuración en Backups.`,
          confirmLabel: 'Deshabilitar', danger: true,
        });
        if (!ok) return;
      }
      try {
        afterChange(await setEnabled(rows, enabled), rows);
      } catch (e) {
        toast(e.message, 'error');
      }
    }

    const selectionActions = [
      { label: 'Deshabilitar', icon: 'power', variant: 'primary', onClick: (sel) => toggle(sel, false) },
      { label: 'Habilitar', icon: 'power', onClick: (sel) => toggle(sel, true) },
      { label: 'Modificar…', icon: 'sliders', onClick: (sel) => openBulkEditor(sel, { onSaved: (updated) => afterChange(updated, sel) }) },
      { label: 'En varios clientes…', icon: 'layers', onClick: (sel) => editInClients(sel) },
    ];

    function openDetail(a) {
      const s = a.stats;
      const points = data.days.map((d, i) => ({ label: d, value: s.series[i], partial: i === data.days.length - 1 }));
      const problems = data.days.map((d, i) => ({ label: d, value: s.series_skipped[i] + s.series_errors[i], partial: i === data.days.length - 1 }));
      const chartOpts = {
        counts: true, format: fmtNum, height: 150,
        tooltipLabel: (p) => dayLabel(p.label, { weekday: 'long', day: 'numeric', month: 'long' }),
        xLabel: (p) => dayLabel(p.label, { day: 'numeric', month: 'short' }),
      };
      const editBtn = button('Editar', { iconName: 'edit', size: 'sm', variant: 'primary' });
      editBtn.addEventListener('click', () => openEditor(a, { onSaved: (row) => { afterChange([row], [a]); drawer.close(); } }));
      const toggleBtn = button(a.disabled ? 'Habilitar' : 'Deshabilitar', { iconName: 'power', size: 'sm' });
      toggleBtn.addEventListener('click', () => withBusy(toggleBtn, async () => { await toggle([a], a.disabled); drawer.close(); }));
      const notes = [];
      if (a.noisy) notes.push(infoBox([h('strong', {}, 'Ruidosa: '), noisyText(a),
        a.suppress ? `. Tiene supresión (${a.suppress_period || 'sin periodo'}).` : '. No tiene supresión (throttle): puede ser lo primero que conviene ajustar.'], 'warn-box'));
      if (isDead(a)) notes.push(infoBox([h('strong', {}, `${DEAD[s.dead][0]}: `), DEAD_HELP[s.dead]], 'warn-box'));
      for (const text of s.state === 'active' ? issueTexts(s) : []) notes.push(infoBox(text, 'warn-box'));

      const drawer = openDrawer({
        title: a.name,
        subtitle: [a.app, a.owner, `últimos ${data.period_days} días`].filter(Boolean).join(' · '),
        width: 760,
        actions: [
          editBtn, toggleBtn,
          button('Ejecuciones en Búsqueda', { iconName: 'search', size: 'sm', onClick: () => { drawer.close(); navigate('search', { q: schedulerSearch(a, data.period_days) }); } }),
          button('En varios clientes…', { iconName: 'layers', size: 'sm', onClick: () => { drawer.close(); editInClients([a]); } }),
        ],
        body: [
          h('div', { class: 'badges', style: { marginBottom: '14px' } }, badge(...STATE_UI[s.state]), testBadge(a), a.is_es ? badge('Enterprise Security', 'accent') : null),
          notes,
          section('Ejecuciones', kv([
            ['Ejecuciones', `${fmtNum(s.runs)}${s.expected !== null && s.expected !== undefined ? ` (su cron indica ~${fmtNum(s.expected)} en los días completos)` : ''}`],
            ['Disparos', `${fmtNum(s.fired)} · ${dec(s.fired_per_day)} al día${s.fire_rate !== null ? ` · en el ${pct(s.fire_rate)} de las ejecuciones` : ''}`],
            ['Resultados al disparar', fmtNum(s.results)],
            ['Suprimidos (throttle)', fmtNum(s.suppressed)],
            ['Saltadas', fmtNum(s.skipped)],
            ['Con error', fmtNum(s.errors)],
            ['Duración media', s.avg_runtime !== null ? fmtDuration(s.avg_runtime) : null],
          ])),
          section('Disparos por día', columnChart(points, chartOpts)),
          s.skipped || s.errors ? section('Saltadas y con error por día', columnChart(problems, chartOpts)) : null,
          s.skip_reasons.length ? section('Motivos de los saltos', h('ul', { class: 'list-plain' }, s.skip_reasons.map((r) => h('li', {}, r)))) : null,
          section('Configuración', kv([
            ['Programación', scheduleText(a)],
            ['Ventana', a.earliest || a.latest ? `${a.earliest || '—'} → ${a.latest || 'now'}` : null],
            ['Acciones', a.actions || null],
            ['Supresión', a.suppress ? `Sí${a.suppress_period ? ` · ${a.suppress_period}` : ''}` : 'No'],
          ])),
          section('Búsqueda SPL', codeBlock(a.search)),
        ],
      });
    }

    // --- Tablas ------------------------------------------------------------------------

    const num = (key, label, fn, extra = {}) => ({ key, label, className: 'num', defaultDir: 'desc', sort: fn, render: (a) => fmtNum(fn(a)), ...extra });

    const TABS = {
      noisy: {
        label: 'Ruidosas',
        rows: () => data.alerts.filter((a) => a.noisy),
        sort: { key: 'fired', dir: 'desc' },
        empty: ['Ninguna alerta ruidosa', `Ninguna alerta activa dispara ${threshold} o más veces al día ni en casi todas sus ejecuciones.`],
        columns: [
          { key: 'name', label: 'Alerta', render: (a) => nameCell(a, noisyText(a)) },
          num('fired', 'Disparos', (a) => a.stats.fired),
          { key: 'per_day', label: 'Al día', className: 'num', defaultDir: 'desc', sort: (a) => a.stats.fired_per_day, render: (a) => dec(a.stats.fired_per_day) },
          num('results', 'Resultados', (a) => a.stats.results),
          { key: 'rate', label: '% ejecuciones', className: 'num', defaultDir: 'desc', sort: (a) => a.stats.fire_rate || 0, render: (a) => (a.stats.fire_rate !== null ? pct(a.stats.fire_rate) : '—') },
          { key: 'trend', label: 'Tendencia', defaultDir: 'desc', sort: (a) => a.stats.trend?.change ?? (a.stats.trend?.direction === 'new' ? 99 : -99), render: trendCell },
          { key: 'suppress', label: 'Supresión', sort: (a) => (a.suppress ? 1 : 0), render: (a) => (a.suppress ? badge(a.suppress_period || 'Sí', 'good') : badge('Sin supresión', 'warn')) },
        ],
      },
      dead: {
        label: 'Muertas',
        rows: () => data.alerts.filter(isDead),
        sort: { key: 'reason', dir: 'asc' },
        empty: ['Ninguna alerta muerta', 'Todas las alertas activas se ejecutan y han disparado alguna vez en el periodo.'],
        columns: [
          { key: 'name', label: 'Alerta', render: (a) => nameCell(a) },
          { key: 'reason', label: 'Motivo', sort: (a) => Object.keys(DEAD).indexOf(a.stats.dead), render: (a) => badge(...DEAD[a.stats.dead]) },
          { key: 'cron', label: 'Programación', sort: (a) => a.cron, render: scheduleText },
          num('runs', 'Ejecuciones', (a) => a.stats.runs),
          { key: 'expected', label: 'Esperadas', className: 'num', defaultDir: 'desc', sort: (a) => a.stats.expected ?? -1, render: (a) => (a.stats.expected ?? null) === null ? '—' : fmtNum(a.stats.expected) },
          num('skipped', 'Saltadas', (a) => a.stats.skipped),
          num('errors', 'Errores', (a) => a.stats.errors),
        ],
      },
      scheduler: {
        label: 'Planificador',
        rows: () => data.alerts.filter(hasSchedulerIssue),
        sort: { key: 'skipped', dir: 'desc' },
        empty: ['Sin problemas de planificador', 'Ninguna alerta activa se salta a menudo, falla o se ejecuta menos de lo que indica su cron.'],
        columns: [
          { key: 'name', label: 'Alerta', render: (a) => nameCell(a, [SCHEDULER_DEAD.includes(a.stats.dead) ? DEAD[a.stats.dead][0] : null, ...issueTexts(a.stats)].filter(Boolean).join(' · ')) },
          num('runs', 'Ejecutadas', (a) => a.stats.runs),
          {
            key: 'skipped', label: 'Saltadas', className: 'num', defaultDir: 'desc', sort: (a) => a.stats.skipped,
            render: (a) => {
              const attempts = a.stats.runs + a.stats.skipped + a.stats.errors;
              return h('div', {}, fmtNum(a.stats.skipped), attempts ? h('div', { class: 'cell-sub' }, pct(a.stats.skipped / attempts)) : null);
            },
          },
          num('errors', 'Errores', (a) => a.stats.errors),
          { key: 'expected', label: 'Esperadas', className: 'num', defaultDir: 'desc', sort: (a) => a.stats.expected ?? -1, render: (a) => (a.stats.expected ?? null) === null ? '—' : fmtNum(a.stats.expected) },
          { key: 'reason', label: 'Motivo del salto', sortable: false, render: (a) => h('div', { class: 'cell-wrap', style: { maxWidth: '360px', fontSize: '12.5px' } }, a.stats.skip_reasons[0] || '—') },
          { key: 'runtime', label: 'Duración media', className: 'num', defaultDir: 'desc', sort: (a) => a.stats.avg_runtime || 0, render: (a) => (a.stats.avg_runtime !== null ? fmtDuration(a.stats.avg_runtime) : '—') },
        ],
      },
      all: {
        label: 'Todas',
        rows: () => data.alerts,
        sort: { key: 'fired', dir: 'desc' },
        empty: ['No hay alertas', ''],
        filters: [{
          id: 'state',
          label: 'Estado',
          options: [
            { value: 'all', label: 'Todas' },
            { value: 'active', label: 'Activas', test: (a) => a.stats.state === 'active' },
            { value: 'disabled', label: 'Deshabilitadas', test: (a) => a.stats.state === 'disabled' },
          ],
        }],
        columns: [
          { key: 'name', label: 'Alerta', render: (a) => nameCell(a) },
          { key: 'state', label: 'Estado', sort: (a) => a.stats.state, render: (a) => badge(...STATE_UI[a.stats.state]) },
          num('runs', 'Ejecuciones', (a) => a.stats.runs),
          num('fired', 'Disparos', (a) => a.stats.fired),
          { key: 'per_day', label: 'Al día', className: 'num', defaultDir: 'desc', sort: (a) => a.stats.fired_per_day, render: (a) => dec(a.stats.fired_per_day) },
          { key: 'trend', label: 'Tendencia', defaultDir: 'desc', sort: (a) => a.stats.trend?.change ?? (a.stats.trend?.direction === 'new' ? 99 : -99), render: trendCell },
          num('skipped', 'Saltadas', (a) => a.stats.skipped),
          { key: 'diagnosis', label: 'Diagnóstico', sortable: false, render: diagnosisBadges },
        ],
      },
    };

    function render() {
      for (const a of data.alerts) a.noisy = noisyReason(a, data.rules, threshold);
      const active = data.alerts.filter((a) => a.stats.state === 'active');
      const counts = Object.fromEntries(Object.entries(TABS).map(([k, t]) => [k, t.rows().length]));
      const totals = data.totals;
      const complete = totals.fired.slice(0, -1);
      const avg = complete.length ? complete.reduce((x, y) => x + y, 0) / complete.length : 0;
      const sum = (list) => list.reduce((x, y) => x + y, 0);

      const tiles = h('div', { class: 'tiles tiles-5' },
        tile('Alertas activas', 'bell', fmtNum(active.length), `de ${fmtNum(data.alerts.length)} · ${fmtNum(data.alerts.filter((a) => a.stats.state === 'disabled').length)} deshabilitadas`),
        tile('Disparos', 'zap', fmtNum(sum(totals.fired)), `${dec(avg)} al día de media`),
        tile('Ruidosas', 'trending', fmtNum(counts.noisy), `≥ ${threshold} al día o casi siempre`),
        tile('Muertas', 'xCircle', fmtNum(counts.dead), 'no disparan o no se ejecutan'),
        tile('Planificador', 'clock', fmtNum(counts.scheduler), `${fmtNum(sum(totals.skipped))} saltadas · ${fmtNum(sum(totals.errors))} con error`));

      const points = data.days.map((d, i) => ({ label: d, value: totals.fired[i], partial: i === data.days.length - 1 }));
      const chart = h('section', { class: 'card' },
        h('div', { class: 'card-header' }, h('div', {},
          h('h2', { class: 'card-title' }, 'Disparos por día'),
          h('p', { class: 'card-subtitle' }, `Ejecuciones de alertas que lanzaron acciones en ${client}, últimos ${data.period_days} días`))),
        h('div', { class: 'card-body' },
          columnChart(points, {
            counts: true, format: fmtNum, average: avg,
            tooltipLabel: (p) => dayLabel(p.label, { weekday: 'long', day: 'numeric', month: 'long' }),
            xLabel: (p) => dayLabel(p.label, { day: 'numeric', month: 'short' }),
          }),
          h('div', { class: 'chart-legend' },
            h('span', {}, h('i'), 'Día completo'), h('span', {}, h('i', { class: 'partial' }), 'Hoy (en curso)'), h('span', {}, h('i', { class: 'avg' }), 'Media diaria'))));

      const tabs = h('div', { class: 'tabs', role: 'tablist', style: { margin: '0', padding: '0 14px' } });
      const tableSlot = h('div');
      const renderTabs = () => {
        clear(tabs);
        for (const [key, t] of Object.entries(TABS)) {
          tabs.append(h('button', {
            class: `tab ${tab === key ? 'active' : ''}`, type: 'button', role: 'tab', 'aria-selected': String(tab === key),
            onclick: () => { tab = key; persist(); renderTabs(); renderTable(); },
          }, t.label, h('span', { class: 'badge' }, fmtNum(counts[key]))));
        }
      };
      const renderTable = () => {
        const t = TABS[tab];
        const table = new DataTable({
          rows: t.rows(),
          rowKey: (a) => `${a.app}/${a.owner}/${a.sharing}/${a.name}`,
          selectable: true,
          compact: true,
          searchText: (a) => `${a.name} ${a.app} ${a.es_label}`,
          searchPlaceholder: 'Buscar alerta…',
          filters: [alertKindFilter, ...(t.filters || [])],
          initialSort: t.sort,
          emptyIcon: 'checkCircle',
          emptyTitle: t.empty[0],
          emptyText: t.empty[1],
          onRowClick: openDetail,
          selectionActions,
          columns: t.columns,
        });
        clear(tableSlot).append(table.el);
      };
      renderTabs();
      renderTable();

      append(clear(body), [
        tiles,
        chart,
        h('section', { class: 'card', style: { marginTop: '16px' } },
          h('div', { class: 'card-header' }, h('div', {},
            h('h2', { class: 'card-title' }, 'Alertas'),
            h('p', { class: 'card-subtitle' }, 'Pulse una fila para ver su evolución, o seleccione varias para deshabilitarlas o modificarlas.'))),
          tabs, tableSlot),
        h('details', { class: 'bulk-details', style: { marginTop: '16px' } },
          h('summary', {}, '¿Cómo se calcula?'),
          h('ul', { class: 'list-plain help-list' },
            h('li', {}, 'Fuente: index=_internal sourcetype=scheduler (lo que suele guardarse 30 días). Un disparo es una ejecución que lanzó acciones (email, notable, webhook…); las suprimidas por el throttle se cuentan aparte.'),
            h('li', {}, `Ruidosa: dispara de media ${threshold} o más veces al día, o en el ${pct(data.rules.always_rate)} o más de sus ejecuciones (con al menos ${data.rules.always_min_runs}).`),
            h('li', {}, 'Muerta: habilitada y programada pero no ha disparado nunca, no tiene acciones, no se ha ejecutado (y su cron decía que sí), el planificador la salta siempre o falla siempre; o habilitada sin programar.'),
            h('li', {}, 'Planificador: se salta el 20 % de las veces o más, tiene ejecuciones con error o se ejecuta menos de la mitad de lo que indica su cron.'),
            h('li', {}, 'Medias y tendencia: solo días completos. La tendencia compara la media diaria de la segunda mitad del periodo con la de la primera.'),
            h('li', {}, 'Las alertas en tiempo real y las deshabilitadas no se clasifican.'))),
      ]);
    }

    const periodSeg = segmented(periods.map((d) => ({ value: d, label: `${d} días` })), days, (value) => {
      days = value;
      persist();
      show();
    });
    const thresholdSelect = h('select', { class: 'select', 'aria-label': 'Umbral de alerta ruidosa', title: 'Disparos al día a partir de los que una alerta se considera ruidosa', style: { width: 'auto' } },
      THRESHOLDS.map((t) => h('option', { value: String(t), selected: t === threshold }, `Ruidosa: ≥ ${t}/día`)));
    thresholdSelect.addEventListener('change', () => {
      threshold = Number(thresholdSelect.value);
      persist();
      if (data) render();
    });
    const refreshBtn = button('Actualizar', { iconName: 'refresh' });
    refreshBtn.addEventListener('click', () => withBusy(refreshBtn, () => show(true)));

    clear(root).append(
      pageHeader({
        title: 'Análisis de alertas',
        subtitle: `Qué alertas de ${client} disparan demasiado, cuáles no sirven (no disparan o no se ejecutan) y cómo evolucionan, según el log del planificador.`,
        actions: [stamp, periodSeg, thresholdSelect, refreshBtn],
      }),
      body);
    show();
    return () => { alive = false; };
  },
};
