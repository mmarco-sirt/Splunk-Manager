// Ingesta y licencia: volumen diario indexado (license_usage.log) por índice y sourcetype, con su tendencia.
import { get } from '../api.js';
import { state } from '../core.js';
import {
  h, clear, icon, button, pageHeader, segmented, DataTable, errorBox, fmtBytes, fmtNum, withBusy, fmtRelative,
} from '../ui.js';
import { columnChart, sparkline } from '../charts.js';

const PERIODS = [7, 30, 90];
const DAYS_KEY = 'sm.ingestDays';
const cache = new Map();

function load(client, days, force = false) {
  const key = `${client}|${days}`;
  if (!cache.has(key) || force) {
    const entry = { at: Date.now(), promise: get('/api/ingest', { client, days }) };
    entry.promise.catch(() => { if (cache.get(key) === entry) cache.delete(key); });
    cache.set(key, entry);
  }
  return cache.get(key);
}

const dayLabel = (iso, options) => new Date(`${iso}T12:00:00`).toLocaleDateString('es-ES', options);
const fmtPct = (n) => `${(n * 100).toLocaleString('es-ES', { maximumFractionDigits: n < 0.01 ? 2 : 1 })} %`;

function delta(value, reference) {
  if (!reference) return null;
  const change = (value - reference) / reference;
  return h('span', { class: 'delta' }, `${change >= 0 ? '▲' : '▼'} ${fmtPct(Math.abs(change))} vs media`);
}

function tile(label, iconName, value, meta) {
  return h('div', { class: 'tile' }, h('div', { class: 'tile-label' }, icon(iconName), label), h('div', { class: 'tile-value' }, value), h('div', { class: 'tile-meta' }, meta || ' '));
}

function usageTable(rows, total, { kind }) {
  return new DataTable({
    rows,
    compact: true,
    searchText: (r) => `${r.name} ${(r.indexes || []).join(' ')}`,
    searchPlaceholder: kind === 'index' ? 'Buscar índice…' : 'Buscar sourcetype o índice…',
    initialSort: { key: 'avg_day', dir: 'desc' },
    emptyTitle: 'Sin datos de ingesta',
    columns: [
      {
        key: 'name', label: kind === 'index' ? 'Índice' : 'Sourcetype',
        render: (r) => h('div', {}, h('div', { class: 'cell-main' }, r.name), r.indexes ? h('div', { class: 'cell-sub' }, r.indexes.join(', ')) : null),
      },
      { key: 'avg_day', label: 'Media / día', className: 'num', defaultDir: 'desc', render: (r) => fmtBytes(r.avg_day) },
      { key: 'last_day', label: 'Último día', className: 'num', defaultDir: 'desc', render: (r) => fmtBytes(r.last_day) },
      { key: 'total', label: 'Total periodo', className: 'num', defaultDir: 'desc', render: (r) => fmtBytes(r.total) },
      {
        key: 'share', label: 'Peso', defaultDir: 'desc', sort: (r) => r.total,
        render: (r) => {
          const share = total ? r.total / total : 0;
          return h('div', { style: { minWidth: '120px' }, title: `${fmtPct(share)} del total del periodo` },
            h('div', { style: { fontSize: '12px', marginBottom: '3px' } }, fmtPct(share)),
            h('div', { class: 'meter' }, h('span', { style: { width: `${Math.max(share * 100, 0.5)}%` } })));
        },
      },
      {
        key: 'trend', label: 'Tendencia', sortable: false,
        render: (r) => sparkline(r.series.slice(0, -1), { label: `Tendencia de ${r.name}` }),
      },
    ],
  });
}

export default {
  id: 'ingest',
  title: 'Ingesta y licencia',
  icon: 'barChart',
  needsClient: true,
  render(root) {
    let days = PERIODS.includes(Number(localStorage.getItem(DAYS_KEY))) ? Number(localStorage.getItem(DAYS_KEY)) : 30;
    let tab = 'index';
    let alive = true;
    const client = state.client;
    const body = h('div');
    const stamp = h('span', { class: 'muted', style: { fontSize: '12.5px' } });

    function loading() {
      clear(body).append(h('div', { class: 'card' }, h('div', { class: 'empty' },
        h('span', { class: 'spinner', style: { width: '28px', height: '28px', borderWidth: '3px' } }),
        h('h3', {}, `Calculando la ingesta de los últimos ${days} días…`),
        h('p', {}, 'Se suma license_usage.log por día, índice y sourcetype. Con 30 días suele tardar entre 10 y 20 segundos.'))));
    }

    async function show(force = false) {
      const entry = load(client, days, force);
      loading();
      try {
        const data = await entry.promise;
        if (!alive) return;
        stamp.textContent = `Datos ${fmtRelative(entry.at / 1000)}`;
        render(data);
      } catch (e) {
        if (alive) clear(body).append(errorBox(e.message));
      }
    }

    function render(data) {
      const lastComplete = data.totals.length > 1 ? data.totals[data.totals.length - 2] : 0;
      const tiles = h('div', { class: 'tiles' },
        tile('Media diaria', 'barChart', fmtBytes(data.avg_day), `últimos ${days - 1} días completos`),
        tile('Último día completo', 'clock', fmtBytes(lastComplete),
          [dayLabel(data.days[data.days.length - 2] || data.days[0], { weekday: 'long', day: 'numeric', month: 'short' }), ' · ', delta(lastComplete, data.avg_day)]),
        tile('Pico del periodo', 'zap', data.peak ? fmtBytes(data.peak.bytes) : '—', data.peak ? dayLabel(data.peak.day, { weekday: 'long', day: 'numeric', month: 'long' }) : null),
        tile('Total del periodo', 'database', fmtBytes(data.total), `${fmtNum(days)} días, hoy incluido`));

      const points = data.days.map((day, i) => ({ label: day, value: data.totals[i], partial: i === data.days.length - 1 }));
      const chart = h('section', { class: 'card' },
        h('div', { class: 'card-header' }, h('div', {},
          h('h2', { class: 'card-title' }, 'Ingesta diaria'),
          h('p', { class: 'card-subtitle' }, `Volumen que consume licencia cada día, últimos ${days} días`))),
        h('div', { class: 'card-body' },
          columnChart(points, {
            average: data.avg_day,
            tooltipLabel: (p) => dayLabel(p.label, { weekday: 'long', day: 'numeric', month: 'long' }),
            xLabel: (p) => dayLabel(p.label, { day: 'numeric', month: 'short' }),
          }),
          h('div', { class: 'chart-legend' },
            h('span', {}, h('i'), 'Día completo'),
            h('span', {}, h('i', { class: 'partial' }), 'Hoy (en curso)'),
            h('span', {}, h('i', { class: 'avg' }), 'Media diaria'))));

      const tableSlot = h('div');
      const tabs = h('div', { class: 'tabs', role: 'tablist', style: { margin: '0', padding: '0 14px' } });
      const renderTabs = () => {
        clear(tabs);
        for (const [key, label, count] of [['index', 'Por índice', data.indexes.length], ['sourcetype', 'Por sourcetype', data.sourcetypes.length]]) {
          tabs.append(h('button', {
            class: `tab ${tab === key ? 'active' : ''}`, type: 'button', role: 'tab', 'aria-selected': String(tab === key),
            onclick: () => { tab = key; renderTabs(); renderTable(); },
          }, label, h('span', { class: 'badge' }, fmtNum(count))));
        }
      };
      const renderTable = () => {
        const rows = tab === 'index' ? data.indexes : data.sourcetypes;
        clear(tableSlot).append(usageTable(rows, data.total, { kind: tab }).el);
      };
      renderTabs();
      renderTable();

      clear(body).append(tiles, chart, h('section', { class: 'card', style: { marginTop: '16px' } },
        h('div', { class: 'card-header' }, h('div', {},
          h('h2', { class: 'card-title' }, 'Reparto del volumen'),
          h('p', { class: 'card-subtitle' }, 'La media y la tendencia no incluyen el día en curso'))),
        tabs, tableSlot));
    }

    const periodSeg = segmented(PERIODS.map((d) => ({ value: d, label: `${d} días` })), days, (value) => {
      days = value;
      try { localStorage.setItem(DAYS_KEY, String(days)); } catch { /* sin almacenamiento */ }
      show();
    });
    const refreshBtn = button('Actualizar', { iconName: 'refresh' });
    refreshBtn.addEventListener('click', () => withBusy(refreshBtn, () => show(true)));

    clear(root).append(
      pageHeader({
        title: 'Ingesta y licencia',
        subtitle: `Volumen indexado por ${client} según license_usage.log, que es lo que consume licencia. El tamaño en disco de cada índice está en Índices.`,
        actions: [stamp, periodSeg, refreshBtn],
      }),
      body);
    show();
    return () => { alive = false; };
  },
};
