// Búsqueda SPL en el cliente activo o en varios clientes a la vez (resultados unidos con la columna «cliente»).
import { get, post, enc } from '../api.js';
import { state, on } from '../core.js';
import {
  h, clear, icon, button, DataTable, errorBox, infoBox, fmtNum, fmtDuration, toast, emptyState,
  segmented, statusLabel,
} from '../ui.js';

const HISTORY_KEY = 'sm.searchHistory';
const MODE_KEY = 'sm.searchMode';
const MULTI_KEY = 'sm.multiSearchClients';
const TIME_RANGES = [
  ['-15m', 'now', 'Últimos 15 minutos'],
  ['-60m', 'now', 'Última hora'],
  ['-4h', 'now', 'Últimas 4 horas'],
  ['-24h', 'now', 'Últimas 24 horas'],
  ['-7d@d', 'now', 'Últimos 7 días'],
  ['-30d@d', 'now', 'Últimos 30 días'],
  ['@d', 'now', 'Hoy'],
  ['', '', 'Todo el tiempo'],
  ['custom', '', 'Personalizado…'],
];
const STATES = {
  QUEUED: 'En cola', PARSING: 'Analizando', RUNNING: 'Ejecutando', FINALIZING: 'Finalizando',
  DONE: 'Completada', FAILED: 'Fallida', PAUSED: 'En pausa',
};
const CLIENT_COLUMN = 'cliente';

function readJson(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; }
}

function writeJson(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* sin almacenamiento */ }
}

function saveHistory(entry) {
  const items = readJson(HISTORY_KEY, []).filter((e) => e.query !== entry.query);
  writeJson(HISTORY_KEY, [entry, ...items].slice(0, 20));
}

function cellValue(value) {
  return Array.isArray(value) ? value.join('\n') : value ?? '';
}

function visibleFields(fields) {
  if (fields.includes('_raw')) {
    return ['_time', 'host', 'source', 'sourcetype', '_raw'].filter((f) => f === '_raw' || f === '_time' || fields.includes(f));
  }
  return fields.filter((f) => !f.startsWith('_') || f === '_time');
}

function union(lists) {
  const seen = [];
  for (const list of lists) for (const item of list) if (!seen.includes(item)) seen.push(item);
  return seen;
}

function download(filename, content, type) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const a = h('a', { href: url, download: filename });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function toCsv(fields, rows) {
  const esc = (v) => {
    const s = String(cellValue(v));
    return /[",\n;]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [fields.map(esc).join(','), ...rows.map((r) => fields.map((f) => esc(r[f])).join(','))].join('\r\n');
}

/** Tabla de resultados con filtro, ordenación y exportación. */
function resultsTable(rows, fields, exportFields, filename, extraToolbar = []) {
  const table = new DataTable({
    rows,
    rowKey: (r) => r.__i,
    compact: true,
    pageSize: 100,
    searchText: (r) => fields.map((f) => cellValue(r[f])).join(' '),
    searchPlaceholder: 'Filtrar resultados…',
    toolbarExtra: [
      ...extraToolbar,
      button('CSV', { size: 'sm', iconName: 'download', onClick: () => download(`${filename}.csv`, `﻿${toCsv(exportFields, table.visibleRows)}`, 'text/csv;charset=utf-8') }),
      button('JSON', { size: 'sm', iconName: 'download', onClick: () => download(`${filename}.json`, JSON.stringify(table.visibleRows.map(({ __i, ...r }) => r), null, 2), 'application/json') }),
    ],
    columns: fields.map((f) => ({
      key: f,
      label: f,
      sort: (r) => {
        const v = cellValue(r[f]);
        const n = Number(v);
        return v !== '' && !Number.isNaN(n) ? n : v;
      },
      render: (r) => (f === '_raw'
        ? h('div', { class: 'raw-event' }, cellValue(r[f]))
        : f === CLIENT_COLUMN
          ? h('span', { class: 'cell-main' }, r[f])
          : h('div', { class: 'cell-wrap', style: { maxWidth: '520px' } }, cellValue(r[f]))),
    })),
  });
  return table;
}

export default {
  id: 'search',
  title: 'Búsqueda',
  icon: 'search',
  render(root, params) {
    let timer = null;
    let alive = true;
    let currentSid = null;        // búsqueda en el cliente activo
    let runs = null;              // búsqueda en varios clientes: [{ client, sid, state, status, error }]
    let mode = params.sid ? 'single' : (readJson(MODE_KEY, 'single') === 'multi' ? 'multi' : 'single');
    const known = new Set(state.clients.map((c) => c.name));
    const selected = new Set(readJson(MULTI_KEY, state.client ? [state.client] : []).filter((c) => known.has(c)));

    const editor = h('textarea', {
      class: 'spl-editor', spellcheck: 'false', 'aria-label': 'Búsqueda SPL',
      placeholder: 'index=_internal sourcetype=splunkd log_level=ERROR | stats count by component',
    });
    editor.value = params.q || '';

    const rangeSelect = h('select', { class: 'select', 'aria-label': 'Rango de tiempo' },
      TIME_RANGES.map(([e, , label], i) => h('option', { value: String(i), selected: e === '-24h' }, label)));
    const earliestInput = h('input', { class: 'input', placeholder: 'earliest, ej. -2d@d', style: { width: '160px' } });
    const latestInput = h('input', { class: 'input', placeholder: 'latest, ej. now', style: { width: '140px' } });
    const customBox = h('div', { style: { display: 'none', gap: '8px' } }, earliestInput, latestInput);
    rangeSelect.addEventListener('change', () => {
      customBox.style.display = TIME_RANGES[rangeSelect.value][0] === 'custom' ? 'flex' : 'none';
    });
    const maxSelect = h('select', { class: 'select', 'aria-label': 'Máximo de resultados' },
      [100, 1000, 5000, 10000, 50000].map((n) => h('option', { value: String(n), selected: n === 1000 }, `${fmtNum(n)} resultados`)));

    const runBtn = button('Buscar', { variant: 'primary', iconName: 'play', onClick: () => run() });
    const finalizeBtn = button('Finalizar', { iconName: 'flag', onClick: () => control('finalize') });
    const cancelBtn = button('Cancelar', { iconName: 'stop', variant: 'ghost', onClick: () => control('cancel') });
    finalizeBtn.hidden = true;
    cancelBtn.hidden = true;

    const progress = h('span', { style: { width: '0%' } });
    const progressBar = h('div', { class: 'progress', hidden: true }, progress);
    const statusLine = h('div', { class: 'search-status', hidden: true });
    const messages = h('div');
    const results = h('div', { style: { marginTop: '16px' } });
    const subtitle = h('p', { class: 'page-subtitle' });
    const perClient = h('span', { class: 'muted', style: { fontSize: '12.5px' } }, 'por cliente');

    // --- Selector de modo y de clientes ---

    const modeSeg = h('div');
    const clientsBox = h('div', { style: { marginTop: '12px' } });
    const chips = h('div', { class: 'client-chips' });
    const selectedCount = h('span', { class: 'muted', style: { fontSize: '12.5px' } });

    function renderChips() {
      clear(chips);
      for (const c of state.clients) {
        const cb = h('input', { type: 'checkbox', checked: selected.has(c.name) });
        cb.addEventListener('change', () => {
          if (cb.checked) selected.add(c.name); else selected.delete(c.name);
          writeJson(MULTI_KEY, [...selected]);
          renderChips();
        });
        chips.append(h('label', { class: `client-chip ${selected.has(c.name) ? 'selected' : ''}`, title: c.host }, cb, c.name));
      }
      selectedCount.textContent = `${selected.size} de ${state.clients.length} clientes`;
    }

    function setAll(all) {
      selected.clear();
      if (all) state.clients.forEach((c) => selected.add(c.name));
      writeJson(MULTI_KEY, [...selected]);
      renderChips();
    }

    clientsBox.append(
      h('div', { class: 'msearch-head' }, h('strong', {}, 'Clientes'), selectedCount,
        button('Todos', { size: 'sm', onClick: () => setAll(true) }),
        button('Ninguno', { size: 'sm', variant: 'ghost', onClick: () => setAll(false) })),
      chips);

    function renderMode() {
      clear(modeSeg).append(segmented([
        { value: 'single', label: state.client ? `Cliente activo (${state.client})` : 'Cliente activo' },
        { value: 'multi', label: 'Varios clientes' },
      ], mode, (value) => { mode = value; writeJson(MODE_KEY, mode); renderMode(); }));
      clientsBox.hidden = mode !== 'multi';
      perClient.hidden = mode !== 'multi';
      subtitle.textContent = mode === 'multi'
        ? 'La misma búsqueda se lanza en paralelo en los clientes elegidos y los resultados se unen con una columna «cliente». Ctrl+Enter para lanzar.'
        : `Ejecute búsquedas SPL en ${state.client || 'el cliente activo'}. Ctrl+Enter para lanzar.`;
      renderChips();
    }

    editor.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); run(); }
    });

    function setRunning(running, { multi = false } = {}) {
      runBtn.disabled = running;
      finalizeBtn.hidden = !running || multi;
      cancelBtn.hidden = !running;
      progressBar.hidden = !running;
    }

    function timeRange() {
      return TIME_RANGES[rangeSelect.value][0] === 'custom'
        ? [earliestInput.value.trim(), latestInput.value.trim()]
        : TIME_RANGES[rangeSelect.value];
    }

    function run() {
      const query = editor.value.trim();
      if (!query) { editor.focus(); return; }
      clearTimeout(timer);
      currentSid = null;
      runs = null;
      clear(messages);
      clear(results);
      const [earliest, latest] = timeRange();
      saveHistory({ query, range: rangeSelect.value, earliest, latest });
      if (mode === 'multi') runMulti(query, earliest, latest);
      else runSingle(query, earliest, latest);
    }

    // --- Un cliente ---

    async function runSingle(query, earliest, latest) {
      if (!state.client) {
        messages.append(errorBox('Seleccione un cliente en la barra superior o use «Varios clientes».'));
        return;
      }
      try {
        setRunning(true);
        const { sid } = await post('/api/search', { client: state.client, query, earliest, latest });
        follow(sid);
      } catch (e) {
        setRunning(false);
        messages.append(errorBox(e.message));
      }
    }

    function follow(sid) {
      currentSid = sid;
      setRunning(true);
      poll();
    }

    async function poll() {
      if (!alive || !currentSid) return;
      try {
        const status = await get(`/api/search/${enc(currentSid)}`, { client: state.client, max: maxSelect.value });
        if (!alive || !currentSid) return;
        if (status.query && !editor.value.trim()) editor.value = status.query.replace(/^search\s+/, '');
        renderStatus(status);
        if (!status.done) {
          timer = setTimeout(poll, 900);
          return;
        }
        setRunning(false);
        if (status.failed) clear(messages).append(errorBox(status.messages.join(' · ') || 'La búsqueda falló.'));
        else renderResults(status);
      } catch (e) {
        setRunning(false);
        clear(messages).append(errorBox(e.message));
      }
    }

    function renderStatus(s) {
      statusLine.hidden = false;
      progress.style.width = `${Math.round(s.progress * 100)}%`;
      clear(statusLine).append(
        h('span', { class: 'status' }, s.done ? icon(s.failed ? 'xCircle' : 'checkCircle') : h('span', { class: 'spinner' }), STATES[s.state] || s.state),
        h('span', {}, `${fmtNum(s.result_count)} resultados`),
        h('span', {}, `${fmtNum(s.event_count)} eventos`),
        h('span', {}, `${fmtNum(s.scan_count)} escaneados`),
        h('span', {}, fmtDuration(s.duration)),
        h('span', { class: 'muted mono', title: 'SID del job' }, s.sid));
      clear(messages);
      const notes = s.messages.filter(Boolean);
      if (notes.length && !s.failed) messages.append(infoBox(notes.join(' · ')));
    }

    function renderResults(s) {
      clear(results);
      if (!s.results.length) {
        results.append(h('div', { class: 'card' }, emptyState({ iconName: 'search', title: 'Sin resultados', text: 'La búsqueda terminó sin devolver resultados en ese rango de tiempo.' })));
        return;
      }
      if (s.result_count > s.results.length) {
        results.append(infoBox(`Se muestran ${fmtNum(s.results.length)} de ${fmtNum(s.result_count)} resultados. Aumente el máximo para ver más.`));
      }
      const rows = s.results.map((r, i) => ({ ...r, __i: i }));
      results.append(resultsTable(rows, visibleFields(s.fields), s.fields, `busqueda_${state.client}`).el);
    }

    // --- Varios clientes ---

    async function runMulti(query, earliest, latest) {
      const targets = state.clients.map((c) => c.name).filter((name) => selected.has(name));
      if (!targets.length) {
        messages.append(errorBox('Seleccione al menos un cliente.'));
        return;
      }
      const current = targets.map((client) => ({ client, sid: null, state: 'starting', status: null, error: null }));
      runs = current;
      setRunning(true, { multi: true });
      renderMulti();
      await Promise.all(current.map(async (r) => {
        try {
          const { sid } = await post('/api/search', { client: r.client, query, earliest, latest });
          r.sid = sid;
          r.state = 'running';
        } catch (e) {
          r.state = 'error';
          r.error = e.message;
        }
        if (runs === current) renderMulti();
      }));
      if (runs === current) pollMulti(current);
    }

    async function pollMulti(current) {
      if (!alive || runs !== current) return;
      await Promise.all(current.filter((r) => r.state === 'running').map(async (r) => {
        try {
          const s = await get(`/api/search/${enc(r.sid)}`, { client: r.client, max: maxSelect.value });
          r.status = s;
          if (s.done) {
            r.state = s.failed ? 'error' : 'done';
            if (s.failed) r.error = s.messages.join(' · ') || 'La búsqueda falló.';
          }
        } catch (e) {
          r.state = 'error';
          r.error = e.message;
        }
      }));
      if (!alive || runs !== current) return;
      renderMulti();
      if (current.some((r) => r.state === 'running')) timer = setTimeout(() => pollMulti(current), 1200);
      else setRunning(false);
    }

    function runLabel(r) {
      if (r.state === 'starting') return h('span', { class: 'status' }, h('span', { class: 'spinner' }), 'Conectando…');
      if (r.state === 'running') return h('span', { class: 'status' }, h('span', { class: 'spinner' }), `${STATES[r.status?.state] || 'Ejecutando'} ${r.status ? `${Math.round(r.status.progress * 100)} %` : ''}`);
      if (r.state === 'cancelled') return statusLabel('Cancelada', 'muted', 'stop');
      if (r.state === 'error') return statusLabel('Error', 'bad', 'xCircle');
      return statusLabel('Completada', 'good', 'checkCircle');
    }

    function renderMulti() {
      const total = runs.length;
      const finished = runs.filter((r) => !['starting', 'running'].includes(r.state)).length;
      const resultsCount = runs.reduce((sum, r) => sum + (r.status?.results?.length || 0), 0);
      const withHits = runs.filter((r) => r.status?.results?.length).length;
      const avgProgress = runs.reduce((sum, r) => sum + (r.state === 'running' ? (r.status?.progress || 0) : r.state === 'starting' ? 0 : 1), 0) / total;

      statusLine.hidden = false;
      progress.style.width = `${Math.round(avgProgress * 100)}%`;
      clear(statusLine).append(
        h('span', { class: 'status' }, finished === total ? icon('checkCircle') : h('span', { class: 'spinner' }), `${finished} de ${total} clientes terminados`),
        h('span', {}, `${fmtNum(resultsCount)} resultados`),
        h('span', {}, `${withHits} ${withHits === 1 ? 'cliente' : 'clientes'} con resultados`));

      // Progreso por cliente (los que tienen resultados primero al terminar)
      const maxHits = Math.max(1, ...runs.map((r) => r.status?.results?.length || 0));
      const ordered = finished === total ? [...runs].sort((a, b) => (b.status?.results?.length || 0) - (a.status?.results?.length || 0)) : runs;
      const progressCard = h('section', { class: 'card' },
        h('div', { class: 'card-header' }, h('h2', { class: 'card-title' }, 'Resultado por cliente')),
        h('div', { class: 'table-scroll' }, h('table', { class: 'data compact' },
          h('thead', {}, h('tr', {}, h('th', {}, 'Cliente'), h('th', {}, 'Estado'), h('th', { class: 'num' }, 'Resultados'), h('th', { class: 'num' }, 'Duración'), h('th', {}, 'Detalle'))),
          h('tbody', {}, ordered.map((r) => {
            const hits = r.status?.results?.length || 0;
            const truncated = r.status && r.status.result_count > hits;
            return h('tr', {},
              h('td', { class: 'cell-main' }, r.client),
              h('td', {}, runLabel(r)),
              h('td', { class: 'num' }, r.status?.done ? h('div', { style: { minWidth: '110px' } },
                `${fmtNum(hits)}${truncated ? ` de ${fmtNum(r.status.result_count)}` : ''}`,
                h('div', { class: 'meter', style: { marginTop: '4px' } }, h('span', { style: { width: `${(hits / maxHits) * 100}%` } }))) : '—'),
              h('td', { class: 'num' }, r.status ? fmtDuration(r.status.duration) : '—'),
              h('td', { style: { maxWidth: '420px', fontSize: '12.5px' } }, r.error ? h('span', { class: 'status-bad' }, r.error) : (r.status?.messages || []).filter(Boolean).join(' · ')));
          })))));

      clear(results).append(progressCard);
      if (finished === total) renderMultiResults();
    }

    function renderMultiResults() {
      const done = runs.filter((r) => r.status?.results?.length);
      if (!done.length) {
        results.append(h('div', { class: 'card', style: { marginTop: '16px' } }, emptyState({ iconName: 'search', title: 'Sin resultados', text: 'Ningún cliente devolvió resultados en ese rango de tiempo.' })));
        return;
      }
      const rows = done.flatMap((r) => r.status.results.map((row, i) => ({ [CLIENT_COLUMN]: r.client, ...row, __i: `${r.client}#${i}` })));
      const fields = [CLIENT_COLUMN, ...union(done.map((r) => visibleFields(r.status.fields)))];
      const exportFields = [CLIENT_COLUMN, ...union(done.map((r) => r.status.fields))];

      const clientFilter = h('select', { class: 'select input-sm', style: { width: 'auto' }, 'aria-label': 'Filtrar por cliente' },
        h('option', { value: '' }, 'Todos los clientes'),
        done.map((r) => h('option', { value: r.client }, `${r.client} (${fmtNum(r.status.results.length)})`)));
      const table = resultsTable(rows, fields, exportFields, 'busqueda_multicliente', [clientFilter]);
      clientFilter.addEventListener('change', () => table.setRows(clientFilter.value ? rows.filter((r) => r[CLIENT_COLUMN] === clientFilter.value) : rows));

      const truncated = runs.filter((r) => r.status && r.status.result_count > (r.status.results?.length || 0));
      results.append(h('div', { style: { marginTop: '16px' } },
        truncated.length ? infoBox(`Resultados recortados al máximo por cliente en: ${truncated.map((r) => r.client).join(', ')}. Aumente el máximo para ver más.`) : null,
        table.el));
    }

    async function control(actionName) {
      if (runs) {
        const current = runs;
        clearTimeout(timer);
        await Promise.all(current.filter((r) => r.state === 'running').map(async (r) => {
          try { await post(`/api/search/${enc(r.sid)}/control`, { client: r.client, action: 'cancel' }); } catch { /* ya terminada */ }
          r.state = 'cancelled';
        }));
        setRunning(false);
        renderMulti();
        toast('Búsquedas canceladas', 'info');
        return;
      }
      if (!currentSid) return;
      try {
        await post(`/api/search/${enc(currentSid)}/control`, { client: state.client, action: actionName });
        if (actionName === 'cancel') {
          clearTimeout(timer);
          setRunning(false);
          currentSid = null;
          toast('Búsqueda cancelada', 'info');
        }
      } catch (e) {
        toast(e.message, 'error');
      }
    }

    function historyCard() {
      const items = readJson(HISTORY_KEY, []);
      if (!items.length) return null;
      return h('section', { class: 'card' },
        h('div', { class: 'card-header' }, h('h2', { class: 'card-title' }, 'Búsquedas recientes')),
        h('div', { class: 'card-body', style: { padding: '8px' } }, items.slice(0, 10).map((e) => h('div', {
          class: 'history-item', role: 'button', tabindex: '0',
          onclick: () => {
            editor.value = e.query;
            if (e.range) rangeSelect.value = e.range;
            rangeSelect.dispatchEvent(new Event('change'));
            earliestInput.value = e.earliest || '';
            latestInput.value = e.latest || '';
            editor.focus();
          },
        }, icon('clock'), h('code', { title: e.query }, e.query)))));
    }

    clear(root).append(
      h('div', { class: 'page-header' }, h('div', {}, h('h1', { class: 'page-title' }, 'Búsqueda'), subtitle)),
      h('section', { class: 'card' },
        h('div', { class: 'search-box' },
          h('div', { style: { marginBottom: '10px' } }, modeSeg),
          editor,
          h('div', { class: 'search-controls' }, rangeSelect, customBox, maxSelect, perClient,
            h('div', { class: 'toolbar-spacer' }), cancelBtn, finalizeBtn, runBtn),
          clientsBox),
        progressBar, statusLine),
      messages,
      results);
    renderMode();

    if (params.sid) follow(params.sid);
    else {
      const hist = historyCard();
      if (hist) results.append(hist);
      editor.focus();
    }

    const off = on('client', renderMode);
    return () => { alive = false; clearTimeout(timer); off(); };
  },
};
