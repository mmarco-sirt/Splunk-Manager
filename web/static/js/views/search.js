// Búsqueda SPL: lanza un job en el cliente activo, muestra el progreso y los resultados.
import { get, post, enc } from '../api.js';
import { state } from '../core.js';
import {
  h, clear, icon, button, pageHeader, DataTable, errorBox, infoBox, fmtNum, fmtDuration, toast, emptyState,
} from '../ui.js';

const HISTORY_KEY = 'sm.searchHistory';
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

function readHistory() {
  try { return JSON.parse(localStorage.getItem(HISTORY_KEY)) || []; } catch { return []; }
}

function saveHistory(entry) {
  try {
    const items = readHistory().filter((e) => e.query !== entry.query);
    localStorage.setItem(HISTORY_KEY, JSON.stringify([entry, ...items].slice(0, 20)));
  } catch { /* sin almacenamiento */ }
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

export default {
  id: 'search',
  title: 'Búsqueda',
  icon: 'search',
  needsClient: true,
  render(root, params) {
    let timer = null;
    let currentSid = null;
    let alive = true;

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

    editor.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); run(); }
    });

    function setRunning(running) {
      runBtn.disabled = running;
      finalizeBtn.hidden = !running;
      cancelBtn.hidden = !running;
      progressBar.hidden = !running;
    }

    async function run() {
      const query = editor.value.trim();
      if (!query) { editor.focus(); return; }
      const [earliest, latest] = TIME_RANGES[rangeSelect.value][0] === 'custom'
        ? [earliestInput.value.trim(), latestInput.value.trim()]
        : TIME_RANGES[rangeSelect.value];
      clearTimeout(timer);
      clear(messages);
      clear(results);
      try {
        setRunning(true);
        const { sid } = await post('/api/search', { client: state.client, query, earliest, latest });
        saveHistory({ query, range: rangeSelect.value, earliest, latest });
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
        if (!alive) return;
        if (status.query && !editor.value.trim()) editor.value = status.query.replace(/^search\s+/, '');
        renderStatus(status);
        if (!status.done) {
          timer = setTimeout(poll, 900);
          return;
        }
        setRunning(false);
        if (status.failed) {
          clear(messages).append(errorBox(status.messages.join(' · ') || 'La búsqueda falló.'));
        } else {
          renderResults(status);
        }
      } catch (e) {
        setRunning(false);
        clear(messages).append(errorBox(e.message));
      }
    }

    async function control(actionName) {
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
      const fields = visibleFields(s.fields);
      const rows = s.results.map((r, i) => ({ ...r, __i: i }));
      const table = new DataTable({
        rows,
        rowKey: (r) => r.__i,
        compact: true,
        pageSize: 100,
        searchText: (r) => fields.map((f) => cellValue(r[f])).join(' '),
        searchPlaceholder: 'Filtrar resultados…',
        toolbarExtra: [
          button('CSV', { size: 'sm', iconName: 'download', onClick: () => download(`busqueda_${state.client}.csv`, `﻿${toCsv(s.fields, s.results)}`, 'text/csv;charset=utf-8') }),
          button('JSON', { size: 'sm', iconName: 'download', onClick: () => download(`busqueda_${state.client}.json`, JSON.stringify(s.results, null, 2), 'application/json') }),
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
            : h('div', { class: 'cell-wrap', style: { maxWidth: '520px' } }, cellValue(r[f]))),
        })),
      });
      if (s.result_count > s.results.length) {
        results.append(infoBox(`Se muestran ${fmtNum(s.results.length)} de ${fmtNum(s.result_count)} resultados. Aumente el máximo para ver más.`));
      }
      results.append(table.el);
    }

    function historyCard() {
      const items = readHistory();
      if (!items.length) return null;
      return h('section', { class: 'card', style: { marginTop: '16px' } },
        h('div', { class: 'card-header' }, h('h2', { class: 'card-title' }, 'Búsquedas recientes')),
        h('div', { class: 'card-body', style: { padding: '8px' } }, items.slice(0, 10).map((e) => h('div', {
          class: 'history-item', role: 'button', tabindex: '0',
          onclick: () => { editor.value = e.query; if (e.range) rangeSelect.value = e.range; rangeSelect.dispatchEvent(new Event('change')); earliestInput.value = e.earliest || ''; latestInput.value = e.latest || ''; editor.focus(); },
        }, icon('clock'), h('code', { title: e.query }, e.query)))));
    }

    clear(root).append(
      pageHeader({ title: 'Búsqueda', subtitle: `Ejecute búsquedas SPL en ${state.client}. Ctrl+Enter para lanzar.` }),
      h('section', { class: 'card' },
        h('div', { class: 'search-box' }, editor,
          h('div', { class: 'search-controls' }, rangeSelect, customBox, maxSelect, h('div', { class: 'toolbar-spacer' }), cancelBtn, finalizeBtn, runBtn)),
        progressBar, statusLine),
      messages,
      results);

    if (params.sid) follow(params.sid);
    else {
      const hist = historyCard();
      if (hist) results.append(hist);
      editor.focus();
    }

    return () => { alive = false; clearTimeout(timer); };
  },
};
