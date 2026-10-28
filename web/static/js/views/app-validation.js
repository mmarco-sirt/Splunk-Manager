// Validar app: versiones del paquete de la app subidas para validarlas antes de desplegarlas. Revisa el paquete (y, contra
// un cliente, sus macros, lookups y el SPL con el parser de Splunk), lo compara con otra versión o con un cliente y ejecuta
// las búsquedas que cambian como búsquedas ad hoc (nunca la saved search ni sus acciones). Exporta el informe.
import { get, post, del, upload, enc } from '../api.js';
import { state, on, startJob, navigate } from '../core.js';
import {
  h, append, clear, button, badge, pageHeader, segmented, DataTable, openDrawer, openModal, confirmDialog, infoBox,
  errorBox, emptyState, section, kv, codeBlock, loadingRows, fmtNum, fmtBytes, fmtDate, fmtRelative, fmtDuration, withBusy, toast,
} from '../ui.js';
import { fieldDiffs } from './compare.js';

const LEVELS = { error: ['Error', 'bad', 'xCircle'], warning: ['Aviso', 'warn', 'alert'], info: ['Info', 'outline', 'info'] };
const CATEGORIES = {
  encoding: 'Codificación', syntax: 'Sintaxis .conf', app: 'App', naming: 'Nombres', fields: 'Campos obligatorios',
  test: 'Versión TEST', dependencies: 'Dependencias', lookups: 'Lookups', views: 'Dashboards', spl: 'SPL',
};
const CHANGES = {
  added: ['Nuevo', 'good', 'plus'], removed: ['Eliminado', 'bad', 'trash'], modified: ['Modificado', 'warn', 'edit'],
  affected: ['Usa macros que cambian', 'accent', 'braces'], present: ['En los dos', 'outline'],
};
const RUNS = {
  ok: ['OK', 'good', 'check'], warning: ['Con avisos', 'warn', 'alert'], error: ['Error', 'bad', 'xCircle'],
  blocked: ['No ejecutada', 'outline', 'lock'], timeout: ['Tiempo agotado', 'warn', 'clock'], cancelled: ['Cancelada', 'outline', 'stop'],
};
const TABS = { validation: 'Validación', changes: 'Cambios', run: 'Ejecución' };
const PRESETS = [['-15m', '15 min'], ['-1h', '1 h'], ['-24h', '24 h'], ['-7d', '7 días']];

// Lo elegido en cada pestaña (cliente, base de comparación, cambios, búsquedas) se recuerda mientras no se cambie de versión
const memory = { id: null };

function remember(id) {
  if (memory.id !== id) Object.assign(memory, { id, client: null, baseKind: null, basePackage: null, diff: null, runNames: null });
}

const statusBadge = (map, key) => {
  const [text, variant, iconName] = map[key] || [key, 'outline'];
  return badge(text, variant, iconName);
};
const clientSelect = (selected) => h('select', { class: 'select', style: { maxWidth: '280px' } },
  state.clients.map((c) => h('option', { value: c.name, selected: c.name === selected }, c.name)));
const resultLabel = (r) => `${r.client} · ${fmtDate(r.created)}${r.status === 'cancelled' ? ' (cancelada)' : ''}`;

/** Búsquedas que conviene ejecutar según los últimos cambios: nuevas, con el SPL cambiado o que usan macros que cambian. */
function changedSearches() {
  return (memory.diff?.items || []).filter((i) => i.type === 'savedsearches' && ['added', 'modified', 'affected'].includes(i.status)
    && (i.spl_changed || i.affected_by?.length)).map((i) => i.name);
}

function baseLabel(diff) {
  const b = diff?.base;
  if (!b) return '';
  if (b.kind === 'package') return `la versión ${b.version} (${b.filename})`;
  return `${b.client} (${b.installed ? `instalada ${b.installed.version || 'sin versión'}` : 'no instalada'})`;
}

export default {
  id: 'app-validation',
  title: 'Validar app',
  icon: 'package',
  render(root, params) {
    const id = params.id || null;
    remember(id);
    let tab = TABS[params.tab] ? params.tab : 'validation';
    let packages = [];
    let detail = null;
    let alive = true;

    const listSlot = h('div', {}, loadingRows(4));
    const detailSlot = h('div');
    const fileInput = h('input', { type: 'file', accept: '.tgz,.tar.gz,.spl,.zip', hidden: true });
    const uploadBtn = button('Subir paquete…', { variant: 'primary', iconName: 'upload', onClick: () => fileInput.click() });
    fileInput.addEventListener('change', () => withBusy(uploadBtn, async () => {
      const file = fileInput.files[0];
      fileInput.value = '';
      if (!file) return;
      try {
        const meta = await upload('/api/app-packages', file, { filename: file.name });
        toast(meta.duplicate ? `Ese paquete ya estaba subido: ${meta.app} ${meta.version}.` : `Subido ${meta.app} ${meta.version}.`,
          meta.duplicate ? 'info' : 'success');
        navigate('app-validation', { id: meta.id });
      } catch (e) {
        toast(e.message, 'error', { duration: 12000 });
      }
    }));

    // --- Versiones subidas ------------------------------------------------------------------

    async function loadList() {
      try {
        packages = await get('/api/app-packages');
      } catch (e) {
        clear(listSlot).append(errorBox(e.message));
        return;
      }
      if (!alive) return;
      if (!packages.length) {
        clear(listSlot).append(h('div', { class: 'card' }, emptyState({
          iconName: 'package', title: 'Todavía no hay versiones subidas',
          text: 'Suba el paquete de la app (.tgz, .tar.gz, .spl o .zip) que quiera validar antes de desplegarlo.',
          action: button('Subir paquete…', { variant: 'primary', iconName: 'upload', onClick: () => fileInput.click() }),
        })));
        return;
      }
      const table = new DataTable({
        rows: packages,
        rowKey: (p) => p.id,
        compact: true,
        searchText: (p) => `${p.app} ${p.label} ${p.version} ${p.filename}`,
        searchPlaceholder: 'Buscar versión…',
        initialSort: { key: 'uploaded', dir: 'desc' },
        pageSize: 10,
        onRowClick: (p) => navigate('app-validation', { id: p.id }),
        columns: [
          {
            key: 'app', label: 'App', render: (p) => h('div', {},
              h('div', { class: 'cell-main' }, p.label || p.app, p.id === id ? badge('Abierta', 'accent', 'eye') : null),
              h('div', { class: 'cell-sub' }, p.app)),
          },
          { key: 'version', label: 'Versión', render: (p) => h('div', {}, h('code', {}, p.version || '—'), p.build ? h('div', { class: 'cell-sub' }, `build ${p.build}`) : null) },
          { key: 'uploaded', label: 'Subida', defaultDir: 'desc', render: (p) => h('div', { title: fmtDate(p.uploaded) }, fmtRelative(p.uploaded), h('div', { class: 'cell-sub truncate', style: { maxWidth: '260px' } }, `${p.filename} · ${fmtBytes(p.size)}`)) },
          {
            key: 'summary', label: 'Validación del paquete', sortable: false,
            render: (p) => {
              const s = p.summary || {};
              if (!s.error && !s.warning) return badge('Sin problemas', 'good', 'check');
              return h('div', { class: 'badges' }, s.error ? badge(`${fmtNum(s.error)} errores`, 'bad') : null, s.warning ? badge(`${fmtNum(s.warning)} avisos`, 'warn') : null);
            },
          },
          {
            key: 'actions', label: '', className: 'col-actions', sortable: false,
            render: (p) => button('', {
              size: 'sm', variant: 'ghost', iconName: 'trash', title: 'Borrar esta versión',
              onClick: async () => {
                const ok = await confirmDialog({
                  title: `¿Borrar ${p.app} ${p.version}?`, danger: true, confirmLabel: 'Borrar',
                  message: 'Se borra el paquete subido y sus validaciones y ejecuciones guardadas. Los informes generados se conservan.',
                });
                if (!ok) return;
                try {
                  await del(`/api/app-packages/${enc(p.id)}`);
                  toast('Versión borrada', 'success');
                  navigate('app-validation', p.id === id ? {} : { id });
                } catch (e) {
                  toast(e.message, 'error');
                }
              },
            }),
          },
        ],
      });
      clear(listSlot).append(table.el);
    }

    // --- Versión abierta ----------------------------------------------------------------------

    async function loadDetail({ result } = {}) {
      if (!id) return;
      if (!detail) clear(detailSlot).append(h('div', { class: 'card', style: { marginTop: '18px' } }, h('div', { class: 'card-body' }, loadingRows(6))));
      try {
        detail = await get(`/api/app-packages/${enc(id)}`);
      } catch (e) {
        clear(detailSlot).append(h('div', { style: { marginTop: '18px' } }, errorBox(e.message)));
        return;
      }
      if (alive) renderDetail(result);
    }

    function tile(label, value, sub, variant = '') {
      return h('div', { class: `tile ${variant}` }, h('div', { class: 'tile-label' }, label), h('div', { class: 'tile-value' }, value), h('div', { class: 'tile-meta' }, sub || ' '));
    }

    function renderDetail(result) {
      const { meta, info } = detail;
      const c = meta.counts || {};
      const s = meta.summary || {};
      const alerts = detail.searches.filter((x) => x.alert).length;
      const tabsEl = h('div', { class: 'tabs', role: 'tablist', style: { margin: '0', padding: '0 14px' } });
      const tabSlot = h('div', { class: 'card-body' });
      const renderTabs = () => {
        clear(tabsEl);
        for (const [key, label] of Object.entries(TABS)) {
          tabsEl.append(h('button', {
            class: `tab ${tab === key ? 'active' : ''}`, type: 'button', role: 'tab', 'aria-selected': String(tab === key),
            onclick: () => { tab = key; renderTabs(); renderTab(); },
          }, label));
        }
      };
      const renderTab = (res) => {
        clear(tabSlot);
        if (tab === 'validation') renderValidation(tabSlot, res);
        else if (tab === 'changes') renderChanges(tabSlot);
        else renderRun(tabSlot, res);
      };
      const exportBtn = button('Exportar informe…', { iconName: 'fileText', onClick: () => openExport() });
      append(clear(detailSlot), h('section', { class: 'card', style: { marginTop: '18px' } },
        h('div', { class: 'card-header' },
          h('div', {},
            h('h2', { class: 'card-title' }, `${info.label || info.id} ${info.version || ''}`),
            h('p', { class: 'card-subtitle' }, [info.id, info.build ? `build ${info.build}` : null, `${meta.filename} · ${fmtBytes(meta.size)}`,
              `subido ${fmtRelative(meta.uploaded)}`, `SHA-256 ${meta.sha256.slice(0, 12)}…`].filter(Boolean).join(' · '))),
          h('div', { class: 'page-actions' }, exportBtn)),
        h('div', { class: 'card-body', style: { paddingBottom: '4px' } },
          h('div', { class: 'tiles tiles-5' },
            tile('Saved searches', fmtNum(c.saved_searches || 0), `${fmtNum(alerts)} alertas`),
            tile('Macros', fmtNum(c.macros || 0), `${fmtNum(c.transforms || 0)} transforms`),
            tile('Lookups y dashboards', `${fmtNum(c.lookups || 0)} · ${fmtNum(c.views || 0)}`, `${fmtNum(c.files || 0)} archivos en total`),
            tile('Errores', fmtNum(s.error || 0), s.error ? 'hay que corregirlos' : 'en el paquete'),
            tile('Avisos', fmtNum(s.warning || 0), 'conviene revisarlos'))),
        tabsEl, tabSlot));
      renderTabs();
      renderTab(result);
    }

    // --- Pestaña Validación -------------------------------------------------------------------

    function renderValidation(slot, result) {
      const select = clientSelect(memory.client || state.client);
      const checkBtn = button('Validar en el cliente', { variant: 'primary', iconName: 'checkCircle' });
      checkBtn.addEventListener('click', () => withBusy(checkBtn, async () => {
        memory.client = select.value;
        try {
          await startJob(`/api/app-packages/${enc(id)}/check`, { client: select.value });
        } catch (e) {
          toast(e.message, 'error');
        }
      }));
      const checks = detail.checks || [];
      const wanted = result?.kind === 'checks' ? result.id : checks[0]?.id;
      const checkPicker = checks.length ? h('select', { class: 'select', style: { maxWidth: '360px' } },
        checks.map((r) => h('option', { value: r.id, selected: r.id === wanted }, `${resultLabel(r)} · ${fmtNum(r.counts?.error || 0)} errores`)),
        h('option', { value: '' }, 'Sin la validación en un cliente')) : null;
      const tableSlot = h('div', {}, loadingRows(4));

      append(slot, [
        h('div', { class: 'val-toolbar' },
          h('div', { class: 'field', style: { margin: 0 } }, h('label', {}, 'Cliente'), select),
          checkBtn,
          checkPicker ? h('div', { class: 'field', style: { margin: 0 } }, h('label', {}, 'Validación en cliente mostrada'), checkPicker) : null),
        h('p', { class: 'field-hint', style: { margin: '6px 0 14px' } },
          'El paquete se revisa sin Splunk. «Validar en el cliente» comprueba además que existen sus macros y lookups (y que se ven desde la app) y pasa el SPL de cada búsqueda por el parser de Splunk, sin ejecutarla.'),
        tableSlot,
      ]);

      const show = async () => {
        let check = null;
        if (checkPicker?.value) {
          try {
            check = await get(`/api/app-packages/${enc(id)}/results/checks/${enc(checkPicker.value)}`);
          } catch (e) {
            toast(e.message, 'error');
          }
        }
        const rows = [...detail.issues, ...(check?.issues || [])].map((i, n) => ({ ...i, n }));
        const table = new DataTable({
          rows,
          rowKey: (i) => i.n,
          compact: true,
          searchText: (i) => `${i.object} ${i.file} ${i.message} ${CATEGORIES[i.category] || i.category}`,
          searchPlaceholder: 'Buscar en los problemas…',
          initialSort: { key: 'level', dir: 'asc' },
          emptyIcon: 'checkCircle',
          emptyTitle: 'Ningún problema',
          emptyText: 'El paquete cumple todas las comprobaciones.',
          filters: [
            {
              id: 'level', label: 'Nivel',
              options: [
                { value: 'all', label: 'Todos' },
                { value: 'error', label: 'Errores', test: (i) => i.level === 'error' },
                { value: 'warning', label: 'Avisos', test: (i) => i.level === 'warning' },
                { value: 'info', label: 'Info', test: (i) => i.level === 'info' },
              ],
            },
            {
              id: 'source', label: 'Origen',
              options: [
                { value: 'all', label: 'Todos' },
                { value: 'package', label: 'Paquete', test: (i) => i.source === 'paquete' },
                { value: 'client', label: check ? check.client : 'Cliente', test: (i) => i.source !== 'paquete' },
              ],
            },
          ],
          columns: [
            { key: 'level', label: 'Nivel', sort: (i) => ['error', 'warning', 'info'].indexOf(i.level), render: (i) => statusBadge(LEVELS, i.level) },
            { key: 'category', label: 'Categoría', sort: (i) => CATEGORIES[i.category] || i.category, render: (i) => h('div', {}, CATEGORIES[i.category] || i.category, i.source !== 'paquete' ? h('div', { class: 'cell-sub' }, i.source) : null) },
            {
              key: 'object', label: 'Dónde', sort: (i) => `${i.object}${i.file}`,
              render: (i) => h('div', { style: { maxWidth: '320px' } }, i.object ? h('div', { class: 'cell-main cell-wrap' }, i.object) : null,
                i.file ? h('div', { class: 'cell-sub' }, `${i.file}${i.line ? `:${i.line}` : ''}`) : null),
            },
            { key: 'message', label: 'Problema', sortable: false, render: (i) => h('div', { class: 'cell-wrap', style: { maxWidth: '620px' } }, i.message) },
          ],
        });
        if (alive) clear(tableSlot).append(table.el);
      };
      checkPicker?.addEventListener('change', show);
      show();
    }

    // --- Pestaña Cambios ----------------------------------------------------------------------

    function renderChanges(slot) {
      const others = packages.filter((p) => p.app === detail.meta.app && p.id !== id);
      const older = others.find((p) => p.uploaded < detail.meta.uploaded) || others[0];
      let kind = memory.baseKind || (others.length ? 'package' : 'client');
      const packageSelect = h('select', { class: 'select', style: { maxWidth: '360px' } },
        others.map((p) => h('option', { value: p.id, selected: p.id === (memory.basePackage || older?.id) }, `${p.version} · ${p.filename} · ${fmtDate(p.uploaded)}`)));
      const select = clientSelect(memory.client || state.client);
      const pickSlot = h('div');
      const resultSlot = h('div');
      const compareBtn = button('Comparar', { variant: 'primary', iconName: 'compare' });

      const renderPick = () => {
        clear(pickSlot).append(kind === 'package'
          ? (others.length ? h('div', { class: 'field', style: { margin: 0 } }, h('label', {}, 'Versión subida'), packageSelect)
            : h('p', { class: 'muted', style: { margin: 0 } }, 'No hay otras versiones subidas de esta app.'))
          : h('div', { class: 'field', style: { margin: 0 } }, h('label', {}, 'Cliente'), select));
        compareBtn.disabled = kind === 'package' && !others.length;
      };
      compareBtn.addEventListener('click', () => withBusy(compareBtn, async () => {
        const base = kind === 'package' ? { package: packageSelect.value } : { client: select.value };
        memory.baseKind = kind;
        if (kind === 'package') memory.basePackage = packageSelect.value; else memory.client = select.value;
        clear(resultSlot).append(h('p', { class: 'status', style: { margin: '14px 0' } }, h('span', { class: 'spinner' }),
          kind === 'package' ? 'Comparando las dos versiones…' : `Leyendo la app en ${select.value}…`), loadingRows(4));
        try {
          memory.diff = await post(`/api/app-packages/${enc(id)}/diff`, { base });
          memory.runNames = null;
          if (alive) renderDiff(resultSlot);
        } catch (e) {
          clear(resultSlot).append(errorBox(e.message));
        }
      }));

      append(slot, [
        h('div', { class: 'val-toolbar' },
          h('div', { class: 'field', style: { margin: 0 } }, h('label', {}, 'Comparar con'),
            segmented([{ value: 'package', label: 'Otra versión subida' }, { value: 'client', label: 'Lo instalado en un cliente' }], kind,
              (v) => { kind = v; renderPick(); })),
          pickSlot, compareBtn),
        h('p', { class: 'field-hint', style: { margin: '6px 0 14px' } },
          'Entre versiones se distingue default/ y local/ (los campos de local/ llevan [local]). Contra un cliente se comparan los campos que define el paquete con los que tiene la app en el cliente.'),
        resultSlot,
      ]);
      renderPick();
      if (memory.diff) renderDiff(resultSlot);
    }

    function renderDiff(slot) {
      const diff = memory.diff;
      const label = baseLabel(diff);
      const target = `${detail.info.label || detail.info.id} ${detail.info.version}`;
      const toRun = changedSearches();
      const runBtn = button(toRun.length ? `Ejecutar las ${fmtNum(toRun.length)} búsquedas que cambian…` : 'Ejecutar búsquedas…', {
        iconName: 'play', onClick: () => { memory.runNames = toRun; navigate('app-validation', { id, tab: 'run' }); },
      });
      const anyAffected = diff.types.some((t) => t.counts.affected);
      const summary = h('div', { class: 'table-card', style: { marginBottom: '14px' } }, h('div', { class: 'table-scroll' },
        h('table', { class: 'data compact' },
          h('thead', {}, h('tr', {}, h('th', {}, 'Tipo'), h('th', { class: 'num' }, 'Nuevos'), h('th', { class: 'num' }, 'Modificados'),
            anyAffected ? h('th', { class: 'num', title: 'Su configuración no cambia, pero usan macros que sí cambian' }, 'Usan macros que cambian') : null,
            h('th', { class: 'num' }, 'Eliminados'), h('th', { class: 'num' }, 'Iguales'))),
          h('tbody', {}, diff.types.map((t) => h('tr', {},
            h('td', {}, t.label, t.error ? h('div', { class: 'cell-sub status-bad' }, t.error) : null),
            h('td', { class: 'num' }, t.counts.added ? badge(fmtNum(t.counts.added), 'good') : '0'),
            h('td', { class: 'num' }, t.counts.modified ? badge(fmtNum(t.counts.modified), 'warn') : '0'),
            anyAffected ? h('td', { class: 'num' }, t.counts.affected ? badge(fmtNum(t.counts.affected), 'accent') : '0') : null,
            h('td', { class: 'num' }, t.counts.removed ? badge(fmtNum(t.counts.removed), 'bad') : '0'),
            h('td', { class: 'num' }, fmtNum(t.counts.same + t.counts.present))))))));
      const table = new DataTable({
        rows: diff.items,
        rowKey: (i) => `${i.type}/${i.name}`,
        compact: true,
        searchText: (i) => `${i.name} ${i.type}`,
        searchPlaceholder: 'Buscar objeto…',
        initialSort: { key: 'type', dir: 'asc' },
        emptyIcon: 'checkCircle',
        emptyTitle: 'No hay diferencias',
        onRowClick: (i) => openDrawer({
          title: i.name, subtitle: `${diff.types.find((t) => t.type === i.type)?.label || i.type} · ${CHANGES[i.status]?.[0] || i.status}`, width: 900,
          body: [
            i.affected_by?.length ? infoBox(`Usa macros que cambian: ${i.affected_by.join(', ')}.`, 'warn-box') : null,
            i.fields?.length ? fieldDiffs(i.fields, diff.base.kind === 'package' ? `versión ${diff.base.version}` : diff.base.client, target)
              : h('p', { class: 'muted' }, i.status === 'present' ? 'Está en los dos (Splunk no deja comparar el contenido de un lookup por REST).'
                : i.status === 'removed' ? `Está en ${label} pero no en esta versión.`
                  : i.status === 'affected' ? 'Su configuración no cambia, pero el resultado puede cambiar por las macros que usa.' : 'Sin detalle de campos.'),
          ],
        }),
        filters: [{
          id: 'status', label: 'Estado',
          options: [
            { value: 'all', label: 'Todos' },
            { value: 'added', label: 'Nuevos', test: (i) => i.status === 'added' },
            { value: 'modified', label: 'Modificados', test: (i) => i.status === 'modified' || i.status === 'affected' },
            { value: 'removed', label: 'Eliminados', test: (i) => i.status === 'removed' },
            { value: 'searches', label: 'Búsquedas a ejecutar', test: (i) => toRun.includes(i.name) && i.type === 'savedsearches' },
          ],
        }],
        columns: [
          { key: 'type', label: 'Tipo', sort: (i) => diff.types.findIndex((t) => t.type === i.type), render: (i) => diff.types.find((t) => t.type === i.type)?.label || i.type },
          {
            key: 'name', label: 'Objeto',
            render: (i) => h('div', { style: { maxWidth: '520px' } }, h('div', { class: 'cell-main cell-wrap' }, i.name),
              i.type === 'savedsearches' && i.status !== 'removed' ? h('div', { class: 'cell-sub' }, [i.spl_changed ? 'cambia el SPL' : null,
                i.affected_by?.length ? `usa macros que cambian: ${i.affected_by.join(', ')}` : null].filter(Boolean).join(' · ') || 'no cambia el SPL') : null),
          },
          { key: 'status', label: 'Estado', render: (i) => statusBadge(CHANGES, i.status) },
          { key: 'fields', label: 'Campos', className: 'num', sort: (i) => i.fields?.length || 0, render: (i) => fmtNum(i.fields?.length || 0) },
        ],
      });
      const installed = diff.base.kind === 'client' ? diff.base.installed : null;
      append(clear(slot), [
        h('div', { class: 'val-diff-head' },
          h('h3', { class: 'section-title', style: { margin: 0 } }, `${target} frente a ${label}`), h('div', { style: { flex: 1 } }), runBtn),
        diff.base.kind === 'client' && !installed ? infoBox(`La app no está instalada en ${diff.base.client}: todo es nuevo.`) : null,
        installed && detail.info.version === installed.version ? infoBox(`${diff.base.client} ya tiene instalada la versión ${installed.version}.`, 'warn-box') : null,
        summary,
        table.el,
      ]);
    }

    // --- Pestaña Ejecución --------------------------------------------------------------------

    function renderRun(slot, result) {
      const select = clientSelect(memory.client || state.client);
      const searches = detail.searches;
      const suggested = memory.runNames ?? changedSearches();
      const chosen = new Set(suggested.filter((n) => searches.some((s) => s.name === n)));
      const listSlot2 = h('div', { class: 'run-list' });
      const counter = h('span', { class: 'muted', style: { fontSize: '12.5px' } });
      const filter = h('input', { class: 'input input-sm', type: 'search', placeholder: 'Filtrar búsquedas…', style: { maxWidth: '260px' } });
      const changed = new Set(changedSearches());

      const renderList = () => {
        const q = filter.value.trim().toLowerCase();
        append(clear(listSlot2), searches.filter((s) => !q || s.name.toLowerCase().includes(q)).map((s) => {
          const cb = h('input', { type: 'checkbox', checked: chosen.has(s.name) });
          cb.addEventListener('change', () => { if (cb.checked) chosen.add(s.name); else chosen.delete(s.name); memory.runNames = [...chosen]; updateCounter(); });
          return h('label', { class: 'check run-item' }, cb,
            h('span', { class: 'run-name' }, s.name, s.test ? badge('TEST', 'warn') : null, changed.has(s.name) ? badge('Cambia', 'accent') : null),
            h('span', { class: 'muted run-window' }, s.earliest ? `${s.earliest} → ${s.latest || 'now'}` : 'sin ventana (se usa -24h)'));
        }));
        updateCounter();
      };
      const updateCounter = () => {
        counter.textContent = `${fmtNum(chosen.size)} de ${fmtNum(searches.length)} búsquedas`;
        runBtn.disabled = !chosen.size;
        runBtn.lastChild.textContent = chosen.size ? `Ejecutar ${fmtNum(chosen.size)} ${chosen.size === 1 ? 'búsqueda' : 'búsquedas'}` : 'Ejecutar';
      };
      const setAll = (names) => { chosen.clear(); names.forEach((n) => chosen.add(n)); memory.runNames = [...chosen]; renderList(); };
      filter.addEventListener('input', renderList);

      let range = 'window';
      const earliest = h('input', { class: 'input input-sm mono', value: '-24h', style: { width: '110px' }, 'aria-label': 'Desde' });
      const latest = h('input', { class: 'input input-sm mono', value: 'now', style: { width: '90px' }, 'aria-label': 'Hasta' });
      const fixedSlot = h('div', { class: 'run-fixed' }, 'Desde', earliest, 'hasta', latest,
        PRESETS.map(([value, text]) => button(text, { size: 'sm', variant: 'ghost', onClick: () => { earliest.value = value; latest.value = 'now'; } })));
      fixedSlot.hidden = true;
      const previous = h('input', { type: 'checkbox', checked: true });
      const expand = h('input', { type: 'checkbox', checked: true });
      const timeout = h('input', { class: 'input input-sm', type: 'number', min: 10, max: 3600, value: 300, style: { width: '90px' } });
      const basePackage = memory.diff?.base?.kind === 'package' ? memory.diff.base : null;
      const previousLabel = basePackage ? `la versión ${basePackage.version}` : 'la que tiene instalada el cliente';

      const runBtn = button('Ejecutar', { variant: 'primary', iconName: 'play' });
      runBtn.addEventListener('click', () => withBusy(runBtn, async () => {
        memory.client = select.value;
        const ok = await confirmDialog({
          title: `¿Ejecutar ${fmtNum(chosen.size)} búsquedas en ${select.value}?`,
          message: 'Se lanzan como búsquedas ad hoc (no la saved search, así que no se disparan sus acciones), una detrás de otra. Las que escriban o envíen datos no se ejecutan. Consumen recursos del cliente.',
          confirmLabel: 'Ejecutar',
        });
        if (!ok) return;
        try {
          await startJob(`/api/app-packages/${enc(id)}/run`, {
            client: select.value, names: searches.filter((s) => chosen.has(s.name)).map((s) => s.name),
            options: { range, earliest: earliest.value.trim(), latest: latest.value.trim(), previous: previous.checked, expand: expand.checked, timeout: Number(timeout.value) },
            base: basePackage ? { package: basePackage.id } : null,
          });
        } catch (e) {
          toast(e.message, 'error');
        }
      }));

      const runs = detail.runs || [];
      const wanted = result?.kind === 'runs' ? result.id : runs[0]?.id;
      const runPicker = runs.length ? h('select', { class: 'select', style: { maxWidth: '360px' } },
        runs.map((r) => h('option', { value: r.id, selected: r.id === wanted }, resultLabel(r)))) : null;
      const resultsSlot = h('div');

      append(slot, [
        h('div', { class: 'run-form' },
          h('div', { class: 'field' }, h('label', {}, 'Cliente donde se ejecutan'), select),
          h('div', { class: 'field' },
            h('div', { class: 'msearch-head' }, h('strong', {}, 'Búsquedas'), counter,
              changed.size ? button('Las que cambian', { size: 'sm', onClick: () => setAll([...changed]) }) : null,
              button('Todas', { size: 'sm', onClick: () => setAll(searches.map((s) => s.name)) }),
              button('Ninguna', { size: 'sm', variant: 'ghost', onClick: () => setAll([]) }), filter),
            changed.size ? null : h('span', { class: 'field-hint' }, 'Compare antes con otra versión o con un cliente (pestaña Cambios) para marcar solas las que cambian.'),
            listSlot2),
          h('div', { class: 'field' }, h('span', { class: 'field-label' }, 'Rango de tiempo'),
            h('div', {}, segmented([{ value: 'window', label: 'La ventana de cada alerta' }, { value: 'fixed', label: 'Un rango fijo' }], range,
              (v) => { range = v; fixedSlot.hidden = v !== 'fixed'; })),
            fixedSlot,
            h('span', { class: 'field-hint' }, 'Las alertas en tiempo real se ejecutan como búsqueda histórica con la misma ventana.')),
          h('div', { class: 'field run-options' },
            h('label', { class: 'check' }, previous, `Ejecutar también la versión anterior (${previousLabel}) para comparar el nº de resultados`),
            h('label', { class: 'check' }, expand, 'Usar las macros del paquete (si no, se usan las que tiene el cliente)'),
            h('label', { class: 'check' }, 'Tiempo máximo por búsqueda', timeout, 'segundos')),
          infoBox('Solo búsquedas ad hoc: nunca se lanza la saved search, así que no se envían emails, notables ni webhooks. No se ejecuta nada que use outputlookup, collect, sendemail, sendalert, delete u otros comandos que escriben o envían datos, no se crea ni modifica nada en Splunk y cada job se borra al terminar.', 'warn-box'),
          h('div', { style: { marginTop: '12px' } }, runBtn)),
        h('h3', { class: 'section-title', style: { marginTop: '22px' } }, 'Resultados'),
        runPicker ? h('div', { class: 'field', style: { maxWidth: '380px' } }, runPicker) : null,
        resultsSlot,
      ]);
      renderList();

      const show = async () => {
        if (!runPicker) {
          clear(resultsSlot).append(h('p', { class: 'muted' }, 'Todavía no se ha ejecutado ninguna búsqueda de esta versión.'));
          return;
        }
        clear(resultsSlot).append(loadingRows(3));
        let data;
        try {
          data = await get(`/api/app-packages/${enc(id)}/results/runs/${enc(runPicker.value)}`);
        } catch (e) {
          clear(resultsSlot).append(errorBox(e.message));
          return;
        }
        const delta = (r) => (r.previous && r.result_count !== null && r.previous.result_count !== null && r.previous.result_count !== undefined
          ? r.result_count - r.previous.result_count : null);
        const table = new DataTable({
          rows: data.results,
          rowKey: (r) => r.name,
          compact: true,
          searchText: (r) => `${r.name} ${r.message}`,
          searchPlaceholder: 'Buscar búsqueda…',
          initialSort: { key: 'status', dir: 'asc' },
          onRowClick: (r) => openRunDetail(r, data),
          filters: [{
            id: 'status', label: 'Estado',
            options: [
              { value: 'all', label: 'Todas' },
              { value: 'problems', label: 'Con problemas', test: (r) => r.status !== 'ok' },
              { value: 'changed', label: 'Cambia el nº de resultados', test: (r) => (delta(r) ?? 0) !== 0 },
            ],
          }],
          columns: [
            { key: 'name', label: 'Búsqueda', render: (r) => h('div', { style: { maxWidth: '460px' } }, h('div', { class: 'cell-main cell-wrap' }, r.name), h('div', { class: 'cell-sub' }, `${r.earliest} → ${r.latest}`)) },
            { key: 'status', label: 'Estado', sort: (r) => Object.keys(RUNS).indexOf(r.status), render: (r) => statusBadge(RUNS, r.status) },
            { key: 'result_count', label: 'Resultados', className: 'num', defaultDir: 'desc', sort: (r) => r.result_count ?? -1, render: (r) => (r.result_count === null ? '—' : fmtNum(r.result_count)) },
            { key: 'previous', label: 'Antes', className: 'num', sort: (r) => r.previous?.result_count ?? -1, render: (r) => (r.previous ? (r.previous.result_count === null ? statusBadge(RUNS, r.previous.status) : fmtNum(r.previous.result_count)) : '—') },
            {
              key: 'delta', label: 'Diferencia', className: 'num', sort: (r) => delta(r) ?? 0,
              render: (r) => { const d = delta(r); return d === null ? '—' : h('span', { class: d ? 'delta delta-warn' : 'muted' }, d > 0 ? `+${fmtNum(d)}` : fmtNum(d)); },
            },
            { key: 'duration', label: 'Duración', className: 'num', sort: (r) => r.duration ?? 0, render: (r) => (r.duration === null ? '—' : fmtDuration(r.duration)) },
            { key: 'message', label: 'Mensaje', sortable: false, render: (r) => h('div', { class: 'cell-wrap', style: { maxWidth: '380px', fontSize: '12.5px' } }, r.message || '') },
          ],
        });
        clear(resultsSlot).append(
          h('p', { class: 'muted', style: { margin: '0 0 10px', fontSize: '12.5px' } },
            `${data.client}${data.installed ? ` (instalada ${data.installed.version || 'sin versión'})` : ' (app no instalada)'} · `
            + `${data.options.range === 'fixed' ? `${data.options.fixed.earliest} → ${data.options.fixed.latest}` : 'ventana de cada alerta'}`
            + `${data.options.expand ? ' · con las macros del paquete' : ''}${data.status === 'cancelled' ? ' · cancelada' : ''}`),
          table.el);
      };
      runPicker?.addEventListener('change', show);
      show();
    }

    function openRunDetail(r, data) {
      const block = (x) => kv([
        ['Estado', statusBadge(RUNS, x.status)],
        ['Rango', `${x.earliest} → ${x.latest}`],
        ['Resultados', x.result_count === null ? null : fmtNum(x.result_count)],
        ['Eventos', x.event_count === null ? null : fmtNum(x.event_count)],
        ['Escaneados', x.scan_count === null ? null : fmtNum(x.scan_count)],
        ['Duración', x.duration === null ? null : fmtDuration(x.duration)],
        ['Mensaje', x.message || null],
        ['Mensajes de Splunk', x.messages?.length ? h('ul', { class: 'list-plain' }, x.messages.map((m) => h('li', {}, m))) : null],
      ]);
      openDrawer({
        title: r.name, subtitle: `Ejecución en ${data.client} · ${fmtDate(data.created)}`, width: 860,
        body: [
          section('Esta versión', block(r)),
          r.expanded_macros?.length ? section('Macros del paquete sustituidas', h('div', { class: 'badges' }, r.expanded_macros.map((m) => badge(m)))) : null,
          section('SPL ejecutado', codeBlock(r.query, { maxHeight: '260px' })),
          r.previous ? section(`Versión anterior (${r.previous.label})`, block(r.previous), codeBlock(r.previous.query, { maxHeight: '200px' })) : null,
        ],
      });
    }

    // --- Exportar -------------------------------------------------------------------------------

    function openExport() {
      const available = state.meta?.report_formats || { xlsx: 'Excel (.xlsx)', html: 'HTML' };
      const chosen = new Set(Object.keys(available));
      const formatChecks = Object.entries(available).map(([value, label]) => {
        const cb = h('input', { type: 'checkbox', checked: true });
        cb.addEventListener('change', () => { if (cb.checked) chosen.add(value); else chosen.delete(value); });
        return h('label', { class: 'check' }, cb, label);
      });
      const pick = (items) => h('select', { class: 'select' }, h('option', { value: '' }, 'No incluir'),
        items.map((r, i) => h('option', { value: r.id, selected: i === 0 }, resultLabel(r))));
      const checkSelect = pick(detail.checks || []);
      const runSelect = pick(detail.runs || []);
      const includeDiff = h('input', { type: 'checkbox', checked: Boolean(memory.diff) });
      const diffBase = memory.diff ? (memory.diff.base.kind === 'package' ? { package: memory.diff.base.id } : { client: memory.diff.base.client }) : null;
      openModal({
        title: 'Exportar el informe de validación',
        subtitle: `${detail.info.label || detail.info.id} ${detail.info.version} · se guarda en informes/`,
        body: [
          h('div', { class: 'field' }, h('span', { class: 'field-label' }, 'Formatos'), h('div', { class: 'badges', style: { gap: '14px' } }, formatChecks)),
          memory.diff ? h('label', { class: 'check', style: { marginBottom: '12px' } }, includeDiff, `Incluir los cambios respecto a ${baseLabel(memory.diff)}`)
            : h('p', { class: 'field-hint' }, 'Para incluir los cambios, compare antes en la pestaña Cambios.'),
          h('div', { class: 'field-row' },
            h('div', { class: 'field' }, h('label', {}, 'Validación en cliente'), checkSelect),
            h('div', { class: 'field' }, h('label', {}, 'Ejecución de búsquedas'), runSelect)),
        ],
        actions: [
          { label: 'Cancelar' },
          {
            label: 'Generar', variant: 'primary', icon: 'fileText',
            onClick: async () => {
              if (!chosen.size) throw new Error('Elija al menos un formato.');
              await startJob(`/api/app-packages/${enc(id)}/report`, {
                formats: [...chosen],
                options: { base: includeDiff.checked ? diffBase : null, check: checkSelect.value || null, run: runSelect.value || null },
              });
            },
          },
        ],
      });
    }

    clear(root).append(
      pageHeader({
        title: 'Validar app',
        subtitle: 'Suba una versión del paquete de la app para validarla antes de desplegarla: codificación, sintaxis, nombres, campos obligatorios y dependencias; compárela con otra versión o con un cliente y ejecute las búsquedas que cambian.',
        actions: [uploadBtn, fileInput],
      }),
      listSlot,
      detailSlot);
    loadList();
    loadDetail();

    // Al terminar una validación o ejecución de esta versión se muestra su resultado
    const off = on('job-finished', (job) => {
      if (!alive || job.meta?.package !== id) return;
      if (job.kind === 'app-check') tab = 'validation';
      if (job.kind === 'app-run') tab = 'run';
      if (job.kind === 'app-check' || job.kind === 'app-run') loadDetail({ result: job.meta.result });
    });
    return () => { alive = false; off(); };
  },
};
