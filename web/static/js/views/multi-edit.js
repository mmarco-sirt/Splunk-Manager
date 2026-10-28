// Edición en varios clientes: cambiar una o varias alertas (cron, SPL, estado...) en todos los clientes que las tienen,
// con vista previa de cada cliente y backup antes de aplicar.
import { post } from '../api.js';
import { state, on, startJob, loadResource, isConnected } from '../core.js';
import {
  h, append, clear, icon, button, badge, pageHeader, segmented, infoBox, errorBox, emptyState, loadingRows, confirmDialog,
  toast, fmtNum, withBusy,
} from '../ui.js';
import { FIELDS, fieldInput } from '../alert-editor.js';
import { fieldDiffs } from './compare.js';

const PREFS_KEY = 'sm.multiEdit';
const EDIT_FIELDS = ['search', 'cron_schedule', 'disabled', 'is_scheduled', 'dispatch.earliest_time', 'dispatch.latest_time',
  'schedule_window', 'alert.severity', 'alert.suppress', 'alert.suppress.period', 'alert.suppress.fields', 'alert_type',
  'alert_comparator', 'alert_threshold', 'alert_condition', 'actions', 'action.email.to', 'description'];
const TEXT_TYPES = ['text', 'textarea', 'code', 'cron'];
// Valor actual de cada campo en las filas del listado (para rellenar «Establecer» con el del cliente de referencia)
const ROW_VALUES = {
  search: (r) => r.search, cron_schedule: (r) => r.cron, description: (r) => r.description, 'alert.severity': (r) => r.severity,
  'dispatch.earliest_time': (r) => r.earliest, 'dispatch.latest_time': (r) => r.latest, actions: (r) => r.actions,
  alert_type: (r) => r.alert_type, disabled: (r) => (r.disabled ? '1' : '0'), is_scheduled: (r) => (r.scheduled ? '1' : '0'),
};
const PRESETS = [
  ['Buscar y reemplazar en el SPL', 'search', { field: 'search', mode: 'replace' }],
  ['Cambiar la programación', 'clock', { field: 'cron_schedule', mode: 'set' }],
  ['Deshabilitar', 'power', { field: 'disabled', mode: 'set', value: '1' }],
  ['Habilitar', 'power', { field: 'disabled', mode: 'set', value: '0' }],
];
const STATUS = {
  change: { label: 'Cambia', variant: 'accent', icon: 'edit', order: 0 },
  invalid: { label: 'No válido', variant: 'bad', icon: 'alert', order: 1 },
  error: { label: 'Error de conexión', variant: 'bad', icon: 'xCircle', order: 2 },
  not_found: { label: 'Texto no encontrado', variant: 'warn', icon: 'search', order: 3 },
  same: { label: 'Ya tiene esos valores', variant: 'good', icon: 'check', order: 4 },
  missing: { label: 'No existe', variant: 'outline', order: 5 },
};

function readPrefs() {
  try { return JSON.parse(localStorage.getItem(PREFS_KEY)) || {}; } catch { return {}; }
}

function savePrefs(prefs) {
  try { localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)); } catch { /* sin almacenamiento */ }
}

function parseNames(value) {
  if (!value) return [];
  try {
    const list = JSON.parse(value);
    if (Array.isArray(list)) return list.map(String);
  } catch { /* no es JSON: un nombre por línea */ }
  return String(value).split('\n');
}

const fieldLabel = (key) => FIELDS[key]?.label || key;
const isCustom = (key) => !EDIT_FIELDS.includes(key);

export default {
  id: 'multi-edit',
  title: 'Edición en varios clientes',
  icon: 'layers',
  render(root, params) {
    const prefs = readPrefs();
    const known = state.clients.map((c) => c.name);
    const names = [...new Set(parseNames(params.names).map((n) => n.trim()).filter(Boolean))];
    const searchIn = new Set((prefs.clients || known).filter((n) => known.includes(n)));
    const targets = new Set();       // clientes en los que se aplica (de los que tienen la alerta)
    const lines = [];                // [{ field, mode, value, find, replace }]
    let lookup = null;
    let preview = null;
    let alive = true;

    const namesSlot = h('div');
    const clientsSlot = h('div');
    const lookupSlot = h('div');
    const linesSlot = h('div');
    const previewSlot = h('div');
    const lookupBtn = button('Buscar en los clientes', { variant: 'primary', iconName: 'search' });
    const previewBtn = button('Generar vista previa', { variant: 'primary', iconName: 'eye' });

    // Cualquier cambio en alertas, clientes o cambios invalida la vista previa
    function stale() {
      if (!preview) return;
      preview = null;
      renderPreview();
    }

    // --- 1. Alertas ----------------------------------------------------------------

    const nameInput = h('input', { class: 'input', placeholder: 'Nombre exacto, p. ej. CU-AD-001', list: 'medit-names', spellcheck: 'false' });
    const suggestions = h('datalist', { id: 'medit-names' });
    if (state.client && isConnected()) {
      loadResource('saved-searches').then((rows) => {
        append(clear(suggestions), [...new Set(rows.filter((r) => r.is_alert || r.is_es).map((r) => r.name))].sort().map((n) => h('option', { value: n })));
      }).catch(() => {});
    }
    const addNames = () => {
      const added = nameInput.value.split(/[\n,]/).map((n) => n.trim()).filter((n) => n && !names.includes(n));
      if (!added.length) return;
      names.push(...added);
      nameInput.value = '';
      lookup = null;
      stale();
      renderNames();
      renderLookup();
    };
    nameInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); addNames(); } });

    function renderNames() {
      append(clear(namesSlot), [
        h('div', { class: 'medit-add' }, nameInput, suggestions, button('Añadir', { iconName: 'plus', onClick: addNames })),
        h('span', { class: 'field-hint' }, 'Una o varias alertas (o reportes). Se buscan por nombre exacto en cada cliente; las sugerencias son las del cliente activo.'),
        names.length ? h('div', { class: 'client-chips', style: { marginTop: '10px' } }, names.map((name) => h('span', { class: 'client-chip selected' }, name,
          button('', {
            size: 'sm', variant: 'ghost', iconName: 'x', title: `Quitar ${name}`,
            onClick: () => { names.splice(names.indexOf(name), 1); lookup = null; stale(); renderNames(); renderLookup(); },
          })))) : null,
      ]);
      lookupBtn.disabled = !names.length || !searchIn.size;
    }

    // --- 2. Clientes ----------------------------------------------------------------

    function renderClients() {
      const chips = h('div', { class: 'client-chips' }, known.map((name) => {
        const cb = h('input', { type: 'checkbox', checked: searchIn.has(name) });
        cb.addEventListener('change', () => {
          if (cb.checked) searchIn.add(name); else searchIn.delete(name);
          savePrefs({ clients: [...searchIn] });
          renderClients();
        });
        return h('label', { class: `client-chip ${searchIn.has(name) ? 'selected' : ''}` }, cb, name);
      }));
      const setAll = (all) => { searchIn.clear(); if (all) known.forEach((n) => searchIn.add(n)); savePrefs({ clients: [...searchIn] }); renderClients(); };
      append(clear(clientsSlot), [
        h('div', { class: 'msearch-head' }, h('strong', {}, 'Buscar en'), h('span', { class: 'muted', style: { fontSize: '12.5px' } }, `${searchIn.size} de ${known.length} clientes`),
          button('Todos', { size: 'sm', onClick: () => setAll(true) }), button('Ninguno', { size: 'sm', variant: 'ghost', onClick: () => setAll(false) })),
        chips,
        h('div', { style: { marginTop: '12px' } }, lookupBtn),
      ]);
      lookupBtn.disabled = !names.length || !searchIn.size;
    }

    lookupBtn.addEventListener('click', () => withBusy(lookupBtn, async () => {
      const clients = known.filter((n) => searchIn.has(n));
      clear(lookupSlot).append(h('p', { class: 'status', style: { margin: '12px 0' } }, h('span', { class: 'spinner' }), `Buscando en ${clients.length} clientes…`), loadingRows(3));
      try {
        lookup = await post('/api/multi-edit/lookup', { names, clients });
      } catch (e) {
        if (alive) clear(lookupSlot).append(errorBox(e.message));
        return;
      }
      if (!alive) return;
      targets.clear();
      for (const c of lookup.clients) if (c.items.some((i) => i.rows.length)) targets.add(c.client);
      stale();
      renderLookup();
      renderLines();
    }));

    function referenceRow(name = names[0]) {
      const order = lookup ? [...lookup.clients].sort((a, b) => (b.client === state.client) - (a.client === state.client)) : [];
      for (const c of order) {
        const row = c.items.find((i) => i.name === name)?.rows[0];
        if (row) return { client: c.client, row };
      }
      return null;
    }

    function renderLookup() {
      clear(lookupSlot);
      if (!lookup) return;
      const withAlert = lookup.clients.filter((c) => c.items.some((i) => i.rows.length));
      const cell = (item) => {
        if (!item.rows.length) return h('span', { class: 'muted' }, 'No existe');
        return h('div', {}, item.rows.map((r) => h('div', { class: 'medit-found' },
          r.disabled ? badge('Deshabilitada', 'outline') : badge('Habilitada', 'good', 'check'),
          r.scheduled ? h('code', {}, r.cron) : h('span', { class: 'muted' }, 'no programada'),
          item.rows.length > 1 || r.sharing === 'user'
            ? h('span', { class: 'cell-sub' }, [r.app, r.sharing === 'user' ? `privada de ${r.owner}` : null].filter(Boolean).join(' · ')) : null)));
      };
      const table = h('table', { class: 'data compact' },
        h('thead', {}, h('tr', {}, h('th', { class: 'col-check' }), h('th', {}, 'Cliente'), lookup.names.map((n) => h('th', {}, n)))),
        h('tbody', {}, lookup.clients.map((c) => {
          const has = c.items.some((i) => i.rows.length);
          const cb = h('input', { type: 'checkbox', checked: targets.has(c.client), disabled: !has, 'aria-label': `Aplicar en ${c.client}` });
          cb.addEventListener('change', () => { if (cb.checked) targets.add(c.client); else targets.delete(c.client); stale(); renderLookupCounter(); });
          return h('tr', {},
            h('td', { class: 'col-check' }, cb),
            h('td', {}, h('span', { class: 'cell-main' }, c.client)),
            c.error ? h('td', { colspan: lookup.names.length, class: 'status-bad' }, c.error) : c.items.map((i) => h('td', {}, cell(i))));
        })));
      const counter = h('span', { class: 'muted', style: { fontSize: '12.5px' } });
      const renderLookupCounter = () => { counter.textContent = `Se aplicará en ${targets.size} de los ${withAlert.length} clientes que la tienen.`; };
      renderLookupCounter();
      append(lookupSlot, [
        h('div', { class: 'table-card', style: { marginTop: '14px' } }, h('div', { class: 'table-scroll' }, table)),
        h('p', { style: { margin: '8px 0 0' } }, counter),
        !withAlert.length ? infoBox('Ningún cliente tiene esas alertas. Revise el nombre (se busca el nombre exacto).', 'warn-box') : null,
        pairsBox(),
      ]);
    }

    // La versión TEST y la de producción de un caso de uso son alertas distintas: se ofrecen para añadirlas, sin marcar
    function pairsBox() {
      const pairs = Object.entries(lookup.pairs || {}).flatMap(([of, list]) => list
        .filter((p) => !names.includes(p.name)).map((p) => ({ of, name: p.name, clients: p.clients })));
      if (!pairs.length) return null;
      return h('div', { class: 'medit-pairs' },
        h('strong', {}, 'Otra versión del mismo caso de uso'),
        h('span', { class: 'field-hint' }, 'La versión TEST y la de producción son alertas distintas. Marque las que quiera cambiar también: se añaden y se vuelve a buscar.'),
        pairs.map((p) => {
          const cb = h('input', { type: 'checkbox' });
          cb.addEventListener('change', () => {
            if (!cb.checked || names.includes(p.name)) return;
            names.push(p.name);
            lookup = null;
            stale();
            renderNames();
            lookupBtn.click();
          });
          return h('label', { class: 'check' }, cb,
            h('span', {}, `«${p.name}»`, h('span', { class: 'muted' }, ` · de «${p.of}», en ${p.clients.join(', ')}`)));
        }));
    }

    // --- 3. Cambios ------------------------------------------------------------------

    // Línea nueva; en «Establecer» empieza con el valor del cliente de referencia (o la primera opción)
    function makeLine(init = {}) {
      const field = init.field ?? 'cron_schedule';
      const spec = FIELDS[field];
      const line = { field, mode: init.mode || 'set', value: init.value, find: '', replace: '' };
      if (line.value === undefined) {
        const ref = referenceRow();
        line.value = ref && ROW_VALUES[field] ? ROW_VALUES[field](ref.row) : (spec?.type === 'bool' ? '1' : '');
      }
      if (spec?.type === 'select' && !spec.options.some(([v]) => v === line.value)) line.value = spec.options[0][0];
      return line;
    }

    function addLine(init) {
      const line = makeLine(init);
      const existing = lines.findIndex((l) => l.field === line.field);
      if (existing >= 0) lines.splice(existing, 1, line); else lines.push(line);
      stale();
      renderLines();
    }

    function renderLines() {
      const ref = referenceRow();
      const presets = h('div', { class: 'medit-presets' }, PRESETS.map(([label, iconName, init]) => button(label, { size: 'sm', iconName, onClick: () => addLine({ ...init }) })));
      const list = h('div', {}, lines.map((line, i) => {
        const spec = FIELDS[line.field] || { type: 'text' };
        const custom = isCustom(line.field);
        const canReplace = custom || TEXT_TYPES.includes(spec.type);
        if (!canReplace) line.mode = 'set';
        const fieldSelect = h('select', { class: 'select', 'aria-label': 'Campo' },
          EDIT_FIELDS.map((k) => h('option', { value: k, selected: k === line.field }, fieldLabel(k))),
          h('option', { value: '__custom', selected: custom }, 'Otro campo…'));
        fieldSelect.addEventListener('change', () => {
          const field = fieldSelect.value === '__custom' ? '' : fieldSelect.value;
          if (field && lines.some((l, j) => j !== i && l.field === field)) {
            toast(`«${fieldLabel(field)}» ya está en la lista de cambios.`, 'warn');
          } else {
            lines[i] = makeLine({ field });
            stale();
          }
          renderLines();
        });
        const customKey = custom ? h('input', { class: 'input mono', placeholder: 'nombre.del.campo', value: line.field, spellcheck: 'false' }) : null;
        customKey?.addEventListener('input', () => { line.field = customKey.value.trim(); stale(); });
        const modeSeg = canReplace ? segmented([{ value: 'set', label: 'Establecer' }, { value: 'replace', label: 'Reemplazar texto' }], line.mode,
          (v) => { line.mode = v; stale(); renderLines(); }) : null;

        let valueEl;
        if (line.mode === 'replace') {
          const tag = line.field === 'search' ? 'textarea' : 'input';
          const findEl = h(tag, { class: `${tag === 'textarea' ? 'textarea' : 'input'} mono`, rows: 2, spellcheck: 'false', placeholder: 'Texto actual, p. ej. count > 5' });
          const replaceEl = h(tag, { class: `${tag === 'textarea' ? 'textarea' : 'input'} mono`, rows: 2, spellcheck: 'false', placeholder: 'Texto nuevo, p. ej. count > 10' });
          findEl.value = line.find;
          replaceEl.value = line.replace;
          findEl.addEventListener('input', () => { line.find = findEl.value; stale(); });
          replaceEl.addEventListener('input', () => { line.replace = replaceEl.value; stale(); });
          valueEl = h('div', {},
            h('div', { class: 'field-row' },
              h('div', { class: 'field' }, h('label', {}, 'Buscar'), findEl),
              h('div', { class: 'field' }, h('label', {}, 'Reemplazar por'), replaceEl)),
            h('span', { class: 'field-hint' }, 'Se reemplazan todas las apariciones (distingue mayúsculas) en el valor de cada cliente, así que cada uno conserva el resto de su SPL.'));
        } else if (custom) {
          const input = h('input', { class: 'input mono', value: line.value || '', spellcheck: 'false' });
          input.addEventListener('input', () => { line.value = input.value; stale(); });
          valueEl = input;
        } else {
          const input = fieldInput(line.field, line.value, (v) => { line.value = v; stale(); });
          valueEl = spec.type === 'bool'
            ? h('label', { class: 'check' }, input, line.field === 'disabled' ? 'Deshabilitada (desmarcado = habilitada)' : 'Activado')
            : input;
          if (ref && ROW_VALUES[line.field] && !['disabled', 'is_scheduled'].includes(line.field)) {
            valueEl = h('div', {}, valueEl, h('span', { class: 'field-hint' }, `Valor inicial tomado de ${ref.client}. Se pondrá el mismo en todos los clientes.`));
          }
        }
        return h('div', { class: 'medit-line' },
          h('div', { class: 'medit-line-head' }, fieldSelect, customKey, modeSeg, h('div', { class: 'toolbar-spacer' }),
            button('', { size: 'sm', variant: 'ghost', iconName: 'trash', title: 'Quitar este cambio', onClick: () => { lines.splice(i, 1); stale(); renderLines(); } })),
          valueEl);
      }));
      append(clear(linesSlot), [
        presets,
        lines.length ? list : h('p', { class: 'muted', style: { margin: '10px 0' } }, 'Elija un cambio habitual o añada un campo.'),
        h('div', { style: { margin: '6px 0 14px' } }, button('Añadir campo', { size: 'sm', variant: 'ghost', iconName: 'plus', onClick: () => addLine({ field: ['cron_schedule', ...EDIT_FIELDS].find((k) => !lines.some((l) => l.field === k)) ?? '' }) })),
        previewBtn,
      ]);
    }

    function opsPayload() {
      if (!lines.length) throw new Error('Añada al menos un cambio.');
      return lines.map((l) => {
        if (!l.field) throw new Error('Indique el nombre del campo.');
        return l.mode === 'replace' ? { field: l.field, mode: 'replace', find: l.find, replace: l.replace } : { field: l.field, mode: 'set', value: l.value ?? '' };
      });
    }

    // --- 4. Vista previa y aplicar --------------------------------------------------------

    async function runPreview() {
      if (!names.length) throw new Error('Añada al menos una alerta.');
      if (!lookup) throw new Error('Busque primero en qué clientes está la alerta.');
      const clients = known.filter((n) => targets.has(n));
      if (!clients.length) throw new Error('Marque al menos un cliente que tenga la alerta.');
      const ops = opsPayload();
      clear(previewSlot).append(h('p', { class: 'status', style: { margin: '12px 0' } }, h('span', { class: 'spinner' }), `Calculando los cambios en ${clients.length} clientes…`), loadingRows(4));
      try {
        preview = { ...(await post('/api/multi-edit/preview', { names, clients, ops })), request: { names: [...names], ops } };
      } catch (e) {
        preview = null;
        if (alive) clear(previewSlot).append(errorBox(e.message));
        return;
      }
      if (alive) renderPreview();
    }

    previewBtn.addEventListener('click', () => withBusy(previewBtn, () => runPreview().catch((e) => clear(previewSlot).append(errorBox(e.message)))));

    function renderPreview() {
      clear(previewSlot);
      if (!preview) {
        if (lookup && lines.length) previewSlot.append(h('p', { class: 'muted', style: { marginTop: '14px' } }, 'Genere la vista previa para ver qué cambiará en cada cliente antes de aplicarlo.'));
        return;
      }
      const changes = preview.items.filter((i) => i.status === 'change');
      const changeClients = new Set(changes.map((i) => i.client));
      const summary = h('div', { class: 'diff-summary', style: { marginTop: '14px' } },
        Object.entries(STATUS).filter(([k]) => preview.counts[k]).map(([k, s]) => badge(`${s.label}: ${fmtNum(preview.counts[k])}`, s.variant, s.icon)));

      const sorted = [...preview.items].sort((a, b) => STATUS[a.status].order - STATUS[b.status].order || a.client.localeCompare(b.client, 'es'));
      const blocks = sorted.map((item) => {
        const s = STATUS[item.status];
        let content = null;
        if (item.status === 'change') {
          content = [
            item.not_found?.length ? infoBox(`No se encontró el texto a reemplazar en: ${item.not_found.map(fieldLabel).join(', ')} (ese campo no cambia).`, 'warn-box') : null,
            fieldDiffs(item.fields, 'Ahora', 'Después'),
          ];
        } else if (item.status === 'invalid' || item.status === 'error') content = h('p', { class: 'status-bad', style: { margin: 0 } }, item.error);
        else if (item.status === 'not_found') content = h('p', { class: 'muted', style: { margin: 0 } }, 'El texto a reemplazar no aparece: no se modifica.');
        const where = [item.app, item.sharing === 'user' ? `privada de ${item.owner}` : null].filter(Boolean).join(', ');
        const head = h('div', { class: 'compare-block-head' }, h('strong', {}, item.client), item.name ? h('span', {}, `· ${item.name}`) : null,
          where ? h('span', { class: 'muted' }, `(${where})`) : null, h('div', { style: { flex: 1 } }), badge(s.label, s.variant, s.icon));
        if (item.status !== 'change') return h('div', { class: 'compare-block medit-quiet' }, head, content);
        return h('details', { class: 'compare-block', open: changes.length <= 6 }, h('summary', {}, head), content);
      });

      const applyBtn = button(changes.length ? `Aplicar ${fmtNum(changes.length)} ${changes.length === 1 ? 'cambio' : 'cambios'} en ${changeClients.size} ${changeClients.size === 1 ? 'cliente' : 'clientes'}` : 'No hay nada que aplicar',
        { variant: 'primary', iconName: 'save', disabled: !changes.length });
      applyBtn.addEventListener('click', () => withBusy(applyBtn, async () => {
        const ok = await confirmDialog({
          title: `¿Aplicar los cambios en ${changeClients.size} ${changeClients.size === 1 ? 'cliente' : 'clientes'}?`,
          message: `Se modificarán ${changes.length} saved searches en: ${[...changeClients].join(', ')}. Antes se guarda la configuración actual de cada cliente en Backups («antes_de_editar»).`,
          confirmLabel: 'Aplicar',
        });
        if (!ok) return;
        try {
          await startJob('/api/multi-edit/apply', {
            names: preview.request.names, ops: preview.request.ops,
            targets: changes.map((i) => ({ key: i.key, fingerprint: i.fingerprint })),
          }, { folder: 'alertas' });
        } catch (e) {
          toast(e.message, 'error');
        }
      }));

      append(previewSlot, [
        summary,
        blocks,
        h('div', { class: 'medit-apply' }, applyBtn,
          h('span', { class: 'muted', style: { fontSize: '12.5px' } }, 'Solo se tocan los elementos marcados como «Cambia» y solo si nadie los ha modificado desde esta vista previa.')),
      ]);
    }

    // Al terminar de aplicar, se repite la vista previa para comprobar que ya no queda nada pendiente
    const off = on('job-finished', (job) => {
      if (job.kind !== 'edit' || !alive || !lookup || !lines.length) return;
      toast('Cambios aplicados: comprobando el resultado…', 'info');
      runPreview().catch(() => {});
    });

    clear(root).append(
      pageHeader({
        title: 'Edición en varios clientes',
        subtitle: 'Cambie una alerta (o varias) en todos los clientes que la tienen: programación, SPL, estado, severidad… Primero verá qué cambia en cada cliente y, antes de aplicarlo, se guarda la configuración anterior en Backups.',
      }),
      known.length
        ? h('div', { class: 'card' }, h('div', { class: 'card-body steps', style: { paddingTop: '22px' } },
          h('div', { class: 'step' }, h('h2', { class: 'step-title' }, 'Alertas'), namesSlot),
          h('div', { class: 'step' }, h('h2', { class: 'step-title' }, 'Clientes'), clientsSlot, lookupSlot),
          h('div', { class: 'step' }, h('h2', { class: 'step-title' }, 'Cambios'), linesSlot),
          h('div', { class: 'step', style: { marginBottom: 0 } }, h('h2', { class: 'step-title' }, 'Vista previa y aplicar'), previewSlot)))
        : h('div', { class: 'card' }, emptyState({ iconName: 'server', title: 'No hay clientes configurados', text: 'Añada clientes en la sección Clientes.' })));

    renderNames();
    renderClients();
    renderLines();
    renderPreview();
    if (names.length && searchIn.size) lookupBtn.click();
    return () => { alive = false; off(); };
  },
};
