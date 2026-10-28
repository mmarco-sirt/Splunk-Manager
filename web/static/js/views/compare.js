// Comparar clientes: un cliente de referencia frente a otros, con diferencias campo a campo y copia de objetos.
import { post } from '../api.js';
import { state, on, startJob } from '../core.js';
import {
  h, append, clear, icon, button, badge, pageHeader, segmented, DataTable, openDrawer, openModal, infoBox, errorBox,
  emptyState, section, loadingRows, fmtNum, withBusy, toast,
} from '../ui.js';

const PREFS_KEY = 'sm.compare';
const FOLDERS = { alerts: 'alertas', reports: 'reportes', macros: 'macros', dashboards: 'dashboards', roles: 'roles' };
const STATUS = {
  same: { label: 'Igual', variant: 'good', icon: 'check', order: 4 },
  different: { label: 'Distinto', variant: 'warn', icon: 'compare', order: 1 },
  missing: { label: 'Falta', variant: 'bad', icon: 'xCircle', order: 0 },
  extra: { label: 'Solo aquí', variant: 'accent', icon: 'plus', order: 2 },
  absent: { label: '—', variant: '', order: 5 },
  error: { label: 'Error', variant: 'bad', icon: 'alert', order: 3 },
};
const NOT_SAME = ['different', 'missing', 'extra'];

function readPrefs() {
  try { return JSON.parse(localStorage.getItem(PREFS_KEY)) || {}; } catch { return {}; }
}

function savePrefs(prefs) {
  try { localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)); } catch { /* sin almacenamiento */ }
}

function statusBadge(status, { compact = false } = {}) {
  const s = STATUS[status] || STATUS.absent;
  if (status === 'absent') return h('span', { class: 'muted' }, '—');
  return badge(compact && status === 'same' ? '=' : s.label, s.variant, compact && status === 'same' ? null : s.icon);
}

/** Fases de un caso de uso en un cliente al emparejar TEST y producción (solo producción no lleva etiqueta). */
function phaseBadge(phases) {
  if (!phases?.length || (phases.length === 1 && phases[0] === 'prod')) return null;
  return phases.length === 2 ? badge('Prod + TEST', 'outline') : badge('Solo TEST', 'warn');
}

/** Nombre real del elemento en un cliente (al emparejar, la fila usa el nombre sin «TEST - »). */
const realName = (item, client) => item.keys?.[client] ?? item.name;

function summaryText(type, s) {
  if (!s) return '';
  switch (type) {
    case 'alerts':
    case 'reports':
      return [s.app, s.scheduled ? s.cron : 'no programada', s.disabled ? 'deshabilitada' : null].filter(Boolean).join(' · ');
    case 'macros': return s.definition;
    case 'dashboards': return [s.label, s.app].filter(Boolean).join(' · ');
    case 'roles': return s.imported_roles ? `hereda de ${s.imported_roles}` : '';
    case 'users': return [s.realname, s.roles].filter(Boolean).join(' · ');
    case 'indexes': return `retención ${fmtNum(s.retention_days)} días`;
    case 'apps': return [s.label, s.version && `v${s.version}`].filter(Boolean).join(' · ');
    default: return '';
  }
}

function diffLines(lines) {
  return h('pre', { class: 'diff-lines' }, lines.map((line) => h('span', {
    class: line.startsWith('+') ? 'add' : line.startsWith('-') ? 'del' : line.startsWith('@@') ? 'hunk' : '',
  }, line || ' ')));
}

/** Diferencias de campos: 'before' es la referencia y 'after' el otro cliente. */
export function fieldDiffs(fields, reference, target) {
  return h('div', { class: 'diff-fields' },
    h('div', { class: 'diff-legend' }, h('span', { class: 'diff-old' }, reference), h('span', { class: 'arrow' }, '→'), h('span', { class: 'diff-new' }, target)),
    fields.map((f) => h('div', {},
      h('div', { class: 'diff-field-name' }, f.field),
      f.diff
        ? diffLines(f.diff)
        : h('div', { class: 'diff-values' },
          h('div', { class: 'diff-old' }, f.before === null ? '(no existe)' : f.before || '(vacío)'),
          h('span', { class: 'arrow' }, '→'),
          h('div', { class: 'diff-new' }, f.after === null ? '(no existe)' : f.after || '(vacío)')))));
}

/** Mensaje de error sin el nombre del cliente delante (ya se muestra al lado). */
function errorText(client, message) {
  return String(message || '').startsWith(`${client}: `) ? message.slice(client.length + 2) : message;
}

function clientChips(clients, selected, onChange) {
  const el = h('div', { class: 'client-chips' });
  const render = () => {
    clear(el);
    for (const name of clients) {
      const cb = h('input', { type: 'checkbox', checked: selected.has(name) });
      cb.addEventListener('change', () => {
        if (cb.checked) selected.add(name); else selected.delete(name);
        render();
        onChange?.();
      });
      el.append(h('label', { class: `client-chip ${selected.has(name) ? 'selected' : ''}` }, cb, name));
    }
  };
  render();
  el.refresh = render;
  return el;
}

/** Diálogo para copiar objetos de 'source' a uno o varios clientes. */
function openCopyModal({ type, typeLabel, source, names, targets, preselected }) {
  const selected = new Set(preselected.length ? preselected : targets);
  const chips = clientChips(targets, selected);
  const folder = FOLDERS[type];
  const sharing = h('select', { class: 'select' },
    h('option', { value: 'global' }, 'Global (todas las apps)'), h('option', { value: 'app' }, 'App'), h('option', { value: 'user' }, 'Privado del usuario'));
  openModal({
    title: `Copiar ${names.length === 1 ? `«${names[0]}»` : `${fmtNum(names.length)} ${typeLabel.toLowerCase()}`} de ${source}`,
    subtitle: 'Se crean en los clientes de destino y los que ya existan con el mismo nombre se sobrescriben.',
    size: 'lg',
    body: [
      h('div', { class: 'field' }, h('span', { class: 'field-label' }, 'Clientes de destino'), chips),
      folder === 'alertas' || folder === 'reportes' ? h('div', { class: 'field' }, h('label', {}, 'Compartición tras copiar'), sharing) : null,
      names.length > 1 ? h('details', { class: 'bulk-details' }, h('summary', {}, `Ver los ${fmtNum(names.length)} elementos`),
        h('ul', { class: 'list-plain bulk-targets' }, names.map((n) => h('li', { class: 'truncate' }, n)))) : null,
      infoBox(['Antes de sobrescribir nada se guarda lo que había en cada destino en ', h('code', {}, `backups/${folder}/`),
        ' (archivo «antes_de_copiar»), para poder deshacerlo desde Backups.'], 'warn-box'),
    ],
    actions: [
      { label: 'Cancelar' },
      {
        label: 'Copiar', variant: 'primary', icon: 'copy',
        onClick: async () => {
          const destination = targets.filter((t) => selected.has(t));
          if (!destination.length) throw new Error('Seleccione al menos un cliente de destino.');
          await startJob('/api/compare/copy', { type, source, targets: destination, names, options: { sharing: sharing.value } }, { folder });
        },
      },
    ],
  });
}

export default {
  id: 'compare',
  title: 'Comparar clientes',
  icon: 'compare',
  render(root) {
    const types = state.meta?.compare_types || {};
    const prefs = readPrefs();
    const names = state.clients.map((c) => c.name);
    let type = types[prefs.type] ? prefs.type : 'alerts';
    let reference = names.includes(prefs.reference) ? prefs.reference : (state.client || names[0]);
    const targets = new Set((prefs.targets || []).filter((n) => names.includes(n) && n !== reference));
    let fields = prefs.fields === 'all' ? 'all' : 'main';
    let pairTest = Boolean(prefs.pair);
    let result = null;
    let alive = true;

    const typeSlot = h('div');
    const fieldsSlot = h('div');
    const refSelect = h('select', { class: 'select', style: { maxWidth: '320px' }, 'aria-label': 'Cliente de referencia' },
      names.map((n) => h('option', { value: n, selected: n === reference }, n)));
    const chipsSlot = h('div');
    const counter = h('span', { class: 'muted', style: { fontSize: '12.5px' } });
    const resultSlot = h('div');
    const runBtn = button('Comparar', { variant: 'primary', iconName: 'compare' });

    const persist = () => savePrefs({ type, reference, targets: [...targets], fields, pair: pairTest });

    function renderType() {
      clear(typeSlot).append(segmented(Object.entries(types).map(([k, v]) => ({ value: k, label: v.label })), type, (v) => {
        type = v;
        persist();
        renderFields();
      }));
    }

    function renderFields() {
      clear(fieldsSlot);
      if (types[type]?.has_main) {
        fieldsSlot.append(h('div', { class: 'field', style: { marginBottom: 0 } }, h('span', { class: 'field-label' }, 'Campos a comparar'),
          h('div', {}, segmented([{ value: 'main', label: 'Principales' }, { value: 'all', label: 'Todos' }], fields, (v) => { fields = v; persist(); })),
          h('span', { class: 'field-hint' }, 'Principales: SPL, programación, estado, disparo, severidad, supresión y acciones. Todos incluye también los parámetros de visualización.')));
      }
      if (types[type]?.pair) {
        fieldsSlot.append(h('div', { class: 'field', style: { marginBottom: 0, marginTop: '14px' } }, h('span', { class: 'field-label' }, 'Versiones TEST'),
          h('div', {}, segmented([{ value: 'apart', label: 'Por separado' }, { value: 'pair', label: 'Emparejar TEST y producción' }],
            pairTest ? 'pair' : 'apart', (v) => { pairTest = v === 'pair'; persist(); })),
          h('span', { class: 'field-hint' }, 'Emparejar: «TEST - CU-X» y «CU-X» van en la misma fila, para ver en qué fase está cada cliente. En cada cliente se compara la misma fase que en la referencia (o la otra si no la tiene). Al copiar se usa siempre el nombre real.')));
      }
    }

    function renderChips() {
      targets.delete(reference);
      const others = names.filter((n) => n !== reference);
      const chips = clientChips(others, targets, () => { persist(); updateCounter(); });
      clear(chipsSlot).append(
        h('div', { class: 'msearch-head' }, h('strong', {}, 'Comparar con'), counter,
          button('Todos', { size: 'sm', onClick: () => { others.forEach((n) => targets.add(n)); persist(); chips.refresh(); updateCounter(); } }),
          button('Ninguno', { size: 'sm', variant: 'ghost', onClick: () => { targets.clear(); persist(); chips.refresh(); updateCounter(); } })),
        chips);
      updateCounter();
    }

    function updateCounter() {
      counter.textContent = `${targets.size} de ${names.length - 1} clientes`;
      runBtn.disabled = !targets.size;
    }

    refSelect.addEventListener('change', () => {
      reference = refSelect.value;
      persist();
      renderChips();
    });

    async function run(params = { type, reference, targets: names.filter((n) => targets.has(n)), fields, pair_test: pairTest }) {
      if (!params.targets.length) return;
      clear(resultSlot).append(h('div', { class: 'card' }, h('div', { class: 'card-body' },
        h('p', { class: 'status', style: { marginBottom: '12px' } }, h('span', { class: 'spinner' }),
          `Leyendo ${types[params.type]?.label.toLowerCase()} de ${params.targets.length + 1} clientes…`),
        loadingRows(6))));
      try {
        result = await post('/api/compare', params);
        if (alive) renderResult();
      } catch (e) {
        if (alive) clear(resultSlot).append(errorBox(e.message));
      }
    }
    runBtn.addEventListener('click', () => withBusy(runBtn, () => run()));

    function renderResult() {
      const r = result;
      const typeLabel = r.label;
      const errorEntries = Object.entries(r.errors || {});

      const summary = h('div', { class: 'table-card', style: { marginBottom: '16px' } }, h('div', { class: 'table-scroll' },
        h('table', { class: 'data compact' },
          h('thead', {}, h('tr', {}, h('th', {}, `Frente a ${r.reference}`), h('th', { class: 'num' }, 'Iguales'), h('th', { class: 'num' }, 'Distintos'),
            h('th', { class: 'num' }, `Faltan (están en ${r.reference})`), h('th', { class: 'num' }, `No están en ${r.reference}`))),
          h('tbody', {}, r.targets.map((t) => {
            const c = r.counts[t];
            return h('tr', {},
              h('td', {}, h('span', { class: 'cell-main' }, t), r.errors[t] ? h('div', { class: 'cell-sub status-bad' }, errorText(t, r.errors[t])) : null),
              ...(r.errors[t] ? [h('td', { colspan: 4, class: 'muted' }, 'No se pudo leer')]
                : [h('td', { class: 'num' }, fmtNum(c.same)),
                  h('td', { class: 'num' }, c.different ? badge(fmtNum(c.different), 'warn') : '0'),
                  h('td', { class: 'num' }, c.missing ? badge(fmtNum(c.missing), 'bad') : '0'),
                  h('td', { class: 'num' }, c.extra ? badge(fmtNum(c.extra), 'accent') : '0')]));
          })))));

      const statusColumns = r.targets.map((t) => ({
        key: `t:${t}`, label: t, sort: (item) => STATUS[item.status[t]]?.order ?? 9,
        render: (item) => {
          const status = statusBadge(item.status[t], { compact: r.targets.length > 4 });
          const phase = r.pair_test ? phaseBadge(item.phases?.[t]) : null;
          return phase ? h('div', { class: 'badges' }, status, phase) : status;
        },
      }));

      const filters = [{
        id: 'diff',
        label: 'Mostrar',
        value: 'diff',
        options: [
          { value: 'diff', label: 'Con diferencias', test: (i) => Object.values(i.status).some((s) => NOT_SAME.includes(s)) },
          { value: 'missing', label: `Faltan en algún cliente`, test: (i) => Object.values(i.status).includes('missing') },
          { value: 'different', label: 'Distintos', test: (i) => Object.values(i.status).includes('different') },
          { value: 'extra', label: `No están en ${r.reference}`, test: (i) => !i.in_reference },
          { value: 'same', label: 'Iguales en todos', test: (i) => Object.values(i.status).every((s) => s === 'same' || s === 'error') },
          { value: 'all', label: 'Todos' },
        ],
      }];
      if (types[r.type]?.pair) {
        // Tipo: sin emparejar, la versión TEST es una fila aparte; al emparejar, la fase va en su propio filtro
        filters.push({
          id: 'kind',
          label: 'Tipo',
          options: [
            { value: 'all', label: 'Todas' },
            r.pair_test
              ? { value: 'cu', label: 'CU-', test: (i) => i.summary?.is_cu }
              : { value: 'cu', label: 'CU-', title: 'Casos de uso en producción (CU-…)', test: (i) => i.summary?.is_cu && !i.summary?.is_test },
            r.pair_test ? null : { value: 'test', label: 'TEST', title: 'Casos de uso en pruebas (TEST - CU-…)', test: (i) => i.summary?.is_test },
            r.type === 'alerts' ? { value: 'es', label: 'ES', test: (i) => i.summary?.is_es } : null,
          ].filter(Boolean),
        });
      }
      if (r.pair_test) {
        filters.push({
          id: 'phase',
          label: 'Fase',
          options: [
            { value: 'all', label: 'Todas' },
            { value: 'pending', label: 'Solo en TEST en algún cliente', test: (i) => Object.values(i.phases || {}).some((p) => !p.includes('prod')) },
            { value: 'prod', label: 'En producción donde existe', test: (i) => Object.values(i.phases || {}).every((p) => p.includes('prod')) },
          ],
        });
      }

      const table = new DataTable({
        rows: r.items,
        rowKey: (i) => i.name,
        selectable: r.can_copy,
        searchText: (i) => `${i.name} ${summaryText(r.type, i.summary)}`,
        searchPlaceholder: `Buscar en ${typeLabel.toLowerCase()}…`,
        initialSort: { key: 'name', dir: 'asc' },
        filters,
        emptyIcon: 'compare',
        emptyTitle: 'No hay elementos',
        onRowClick: (item) => openItem(item),
        selectionActions: r.can_copy ? [{
          label: `Copiar de ${r.reference}…`, icon: 'copy', variant: 'primary',
          onClick: (sel) => {
            const inRef = sel.filter((i) => i.in_reference);
            if (!inRef.length) { toast(`Ninguno de los seleccionados existe en ${r.reference}.`, 'warn'); return; }
            if (inRef.length < sel.length) toast(`${sel.length - inRef.length} seleccionados no existen en ${r.reference} y no se copiarán.`, 'warn');
            const needed = r.targets.filter((t) => !r.errors[t] && inRef.some((i) => ['missing', 'different'].includes(i.status[t])));
            openCopyModal({ type: r.type, typeLabel, source: r.reference, names: inRef.map((i) => realName(i, r.reference)),
              targets: r.targets.filter((t) => !r.errors[t]), preselected: needed });
          },
        }] : [],
        columns: [
          {
            key: 'name', label: 'Nombre',
            render: (i) => {
              const tag = r.pair_test ? phaseBadge(i.phases?.[r.reference]) : (i.summary?.is_test ? badge('TEST', 'warn') : null);
              tag?.classList.add('name-badge');
              return h('div', { style: { minWidth: '220px', maxWidth: '460px' } },
                h('div', { class: 'cell-main' }, i.name, tag, !i.in_reference ? h('span', { class: 'muted' }, ` · no está en ${r.reference}`) : null),
                h('div', { class: 'cell-sub truncate' }, summaryText(r.type, i.summary)));
            },
          },
          ...statusColumns,
        ],
      });

      append(clear(resultSlot), [
        h('h2', { class: 'section-title' }, `${typeLabel}: ${r.reference} frente a ${r.targets.length === 1 ? r.targets[0] : `${r.targets.length} clientes`}`),
        errorEntries.length ? infoBox(`No se pudieron leer: ${errorEntries.map(([n, e]) => `${n} (${errorText(n, e)})`).join(' · ')}`, 'warn-box') : null,
        summary,
        r.can_copy ? h('p', { class: 'muted', style: { fontSize: '12.5px', margin: '0 0 10px' } },
          `Seleccione filas para copiarlas desde ${r.reference}, o pulse una fila para ver las diferencias campo a campo.`) : null,
        table.el,
      ]);
    }

    function openItem(item) {
      const r = result;
      const copyBtn = (source, dest) => button(`Copiar de ${source} a ${dest}`, {
        size: 'sm', iconName: 'copy',
        onClick: () => openCopyModal({ type: r.type, typeLabel: r.label, source, names: [realName(item, source)], targets: [dest], preselected: [dest] }),
      });
      const blocks = r.targets.map((t) => {
        const status = item.status[t];
        // Al emparejar, puede que se comparen la versión TEST de un cliente y la de producción del otro
        const refName = item.keys?.[r.reference];
        const targetName = item.keys?.[t];
        const crossed = refName && targetName && refName !== targetName
          ? infoBox(`Se compara «${refName}» de ${r.reference} con «${targetName}» de ${t}.`) : null;
        let content;
        if (status === 'different') content = fieldDiffs(item.fields[t], r.reference, t);
        else if (status === 'missing') content = h('p', { class: 'muted' }, `Existe en ${r.reference} pero no en ${t}.`);
        else if (status === 'extra') content = h('p', { class: 'muted' }, `Existe en ${t} pero no en ${r.reference}.`);
        else if (status === 'same') content = h('p', { class: 'muted' }, 'Misma configuración (se ignoran los campos que Splunk cambia solo).');
        else if (status === 'error') content = h('p', { class: 'status-bad' }, errorText(t, r.errors[t]));
        else content = h('p', { class: 'muted' }, `No existe ni en ${r.reference} ni en ${t}.`);
        const actions = [];
        if (r.can_copy && ['different', 'missing'].includes(status)) actions.push(copyBtn(r.reference, t));
        if (r.can_copy && status === 'extra') actions.push(copyBtn(t, r.reference));
        return h('div', { class: 'compare-block' },
          h('div', { class: 'compare-block-head' }, h('strong', {}, t), statusBadge(status), r.pair_test ? phaseBadge(item.phases?.[t]) : null,
            h('div', { style: { flex: 1 } }), actions),
          crossed, content);
      });
      openDrawer({
        title: item.name,
        subtitle: `${r.label} · referencia ${r.reference}${item.summary ? ` · ${summaryText(r.type, item.summary)}` : ''}`,
        width: 900,
        body: [
          r.targets.length > 1 ? section('Resumen', h('div', { class: 'badges' }, r.targets.map((t) => h('span', { class: 'compare-pill' }, t, statusBadge(item.status[t], { compact: true }))))) : null,
          ...blocks,
        ],
      });
    }

    clear(root).append(
      pageHeader({
        title: 'Comparar clientes',
        subtitle: 'Compare la configuración de un cliente de referencia con otros: qué falta, qué sobra y qué es distinto. Puede copiar objetos de un cliente a otro.',
      }),
      names.length < 2
        ? h('div', { class: 'card' }, emptyState({ iconName: 'server', title: 'Hacen falta al menos dos clientes', text: 'Añada clientes en la sección Clientes.' }))
        : h('div', { class: 'card', style: { marginBottom: '20px' } }, h('div', { class: 'card-body compare-form' },
          h('div', { class: 'field' }, h('span', { class: 'field-label' }, 'Qué comparar'), typeSlot),
          h('div', { class: 'field' }, h('label', {}, 'Cliente de referencia'), refSelect,
            h('span', { class: 'field-hint' }, 'Normalmente el cliente con la configuración «buena» o más completa.')),
          h('div', { class: 'field' }, chipsSlot),
          fieldsSlot,
          h('div', { class: 'compare-run' }, runBtn,
            h('span', { class: 'muted', style: { fontSize: '12.5px' } }, 'Se lee la configuración en vivo de cada cliente; con muchos clientes puede tardar un poco.')))),
      resultSlot);

    renderType();
    renderFields();
    renderChips();

    // Tras una copia entre clientes que afecte a la comparación mostrada, se vuelve a comparar
    const off = on('job-finished', (job) => {
      if (job.kind !== 'copy' || !result || !alive) return;
      const involved = new Set([result.reference, ...result.targets]);
      if (job.clients.some((c) => involved.has(c))) {
        toast('Copia terminada: actualizando la comparación…', 'info');
        run({ type: result.type, reference: result.reference, targets: result.targets, fields: result.fields, pair_test: result.pair_test });
      }
    });
    return () => { alive = false; off(); };
  },
};
