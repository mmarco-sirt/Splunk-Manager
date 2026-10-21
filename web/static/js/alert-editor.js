// Edición de alertas y reportes (saved searches): formulario por pestañas, editor avanzado y cambios en bloque.
import { get, post } from './api.js';
import { state } from './core.js';
import { h, append, clear, icon, button, badge, openModal, toast, infoBox, loadingRows, errorBox, fmtNum } from './ui.js';

export const SEVERITY_OPTIONS = [['1', 'Debug'], ['2', 'Info'], ['3', 'Baja'], ['4', 'Media'], ['5', 'Alta'], ['6', 'Crítica']];

const CRON_PRESETS = [
  ['*/5 * * * *', 'Cada 5 minutos'], ['*/15 * * * *', 'Cada 15 minutos'], ['*/30 * * * *', 'Cada 30 minutos'],
  ['0 * * * *', 'Cada hora'], ['0 */4 * * *', 'Cada 4 horas'], ['0 8 * * *', 'Cada día a las 08:00'],
  ['0 8 * * 1', 'Cada lunes a las 08:00'],
];
const EARLIEST = ['-5m', '-15m', '-30m', '-1h', '-4h', '-24h', '-7d', '-1d@d', '@d', 'rt-5m'];
const LATEST = ['now', '@m', '-1m', '@h', '@d', 'rt'];
const DAYS = ['domingo', 'lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo'];
const KNOWN_ACTIONS = ['email', 'webhook', 'notable', 'risk', 'lookup', 'script', 'logevent', 'slack', 'summary_index'];

/** Definición de los campos con formulario propio (los demás se editan en «Avanzado»). */
export const FIELDS = {
  description: { label: 'Descripción', type: 'textarea', rows: 2 },
  search: { label: 'Búsqueda SPL', type: 'code', rows: 9 },
  disabled: { label: 'Deshabilitada', type: 'bool' },
  'action.correlationsearch.label': { label: 'Etiqueta de ES', type: 'text' },
  is_scheduled: { label: 'Programada', type: 'bool' },
  cron_schedule: { label: 'Programación (cron)', type: 'cron' },
  'dispatch.earliest_time': { label: 'Ventana: desde', type: 'text', list: EARLIEST, placeholder: '-15m', mono: true },
  'dispatch.latest_time': { label: 'Ventana: hasta', type: 'text', list: LATEST, placeholder: 'now', mono: true },
  schedule_window: { label: 'Margen de ejecución', type: 'select', options: [['0', 'Sin margen'], ['auto', 'Automático'], ['5', '5 minutos'], ['15', '15 minutos'], ['30', '30 minutos'], ['60', '1 hora']], hint: 'Permite al planificador retrasar la ejecución para repartir la carga.' },
  schedule_priority: { label: 'Prioridad', type: 'select', options: [['default', 'Normal'], ['higher', 'Alta'], ['highest', 'Máxima']] },
  alert_type: { label: 'Disparar cuando', type: 'select', options: [['always', 'Siempre (por ejecución)'], ['number of events', 'Número de eventos'], ['number of results', 'Número de resultados'], ['number of hosts', 'Número de hosts'], ['number of sources', 'Número de fuentes'], ['custom', 'Condición personalizada'], ['none', 'Nunca (es un reporte)']] },
  alert_comparator: { label: 'Comparación', type: 'select', options: [['greater than', 'es mayor que'], ['less than', 'es menor que'], ['equal to', 'es igual a'], ['not equal to', 'es distinto de'], ['drops by', 'baja en'], ['rises by', 'sube en']] },
  alert_threshold: { label: 'Umbral', type: 'text', placeholder: '0', mono: true },
  alert_condition: { label: 'Condición personalizada (SPL)', type: 'code', rows: 2, placeholder: 'search count > 10' },
  'alert.severity': { label: 'Severidad', type: 'select', options: SEVERITY_OPTIONS },
  'alert.digest_mode': { label: 'Disparar', type: 'select', options: [['1', 'Una vez por ejecución'], ['0', 'Una vez por cada resultado']] },
  'alert.suppress': { label: 'Suprimir repeticiones (throttle)', type: 'bool' },
  'alert.suppress.period': { label: 'Suprimir durante', type: 'text', placeholder: '1h', mono: true, hint: 'Ej.: 30m, 1h, 1d' },
  'alert.suppress.fields': { label: 'Suprimir por campos', type: 'text', placeholder: 'user, src', hint: 'Vacío = suprimir cualquier disparo.' },
  'alert.expires': { label: 'Caducidad de la alerta disparada', type: 'text', placeholder: '24h', mono: true },
  'alert.track': { label: 'Añadir a «Alertas disparadas»', type: 'bool' },
  actions: { label: 'Acciones', type: 'text', mono: true, hint: 'Separadas por comas, p. ej. email, webhook.' },
  'action.email.to': { label: 'Email: destinatarios', type: 'text', placeholder: 'soc@empresa.com, …' },
  'action.email.cc': { label: 'Email: CC', type: 'text' },
  'action.email.subject': { label: 'Email: asunto', type: 'text', placeholder: 'Splunk Alert: $name$' },
  'action.email.message.alert': { label: 'Email: mensaje', type: 'textarea', rows: 3 },
  'action.webhook.param.url': { label: 'Webhook: URL', type: 'text', mono: true },
};

/** Campos que se ofrecen en la edición en bloque. */
const BULK_FIELDS = ['disabled', 'is_scheduled', 'cron_schedule', 'dispatch.earliest_time', 'dispatch.latest_time', 'schedule_window',
  'alert.severity', 'alert.suppress', 'alert.suppress.period', 'alert.suppress.fields', 'alert.digest_mode', 'alert.expires',
  'alert_type', 'alert_comparator', 'alert_threshold', 'actions', 'action.email.to', 'action.email.cc', 'description'];

const truthy = (v) => ['1', 'true', 'yes', 'on'].includes(String(v ?? '').trim().toLowerCase());

/** Texto legible de una expresión cron habitual ('' si no se reconoce). */
export function describeCron(expr) {
  const parts = String(expr || '').trim().split(/\s+/);
  if (!expr || !expr.trim()) return '';
  if (parts.length !== 5) return 'Debe tener 5 campos: minuto hora día-del-mes mes día-de-la-semana.';
  const [min, hour, dom, mon, dow] = parts;
  const hm = (hh, mm) => `${hh.padStart(2, '0')}:${mm.padStart(2, '0')}`;
  const allDays = dom === '*' && mon === '*';
  let m;
  if (allDays && dow === '*' && hour === '*' && (m = min.match(/^\*\/(\d+)$/))) return `Cada ${m[1]} minutos`;
  if (allDays && dow === '*' && hour === '*' && min === '*') return 'Cada minuto';
  if (allDays && dow === '*' && hour === '*' && /^\d+$/.test(min)) return `Cada hora, en el minuto ${min}`;
  if (allDays && dow === '*' && /^\d+$/.test(min) && (m = hour.match(/^\*\/(\d+)$/))) return `Cada ${m[1]} horas, en el minuto ${min}`;
  if (allDays && /^\d+$/.test(min) && /^\d+$/.test(hour)) {
    if (dow === '*') return `Cada día a las ${hm(hour, min)}`;
    if (/^\d$/.test(dow)) return `Cada ${DAYS[Number(dow)]} a las ${hm(hour, min)}`;
    if (dow === '1-5') return `De lunes a viernes a las ${hm(hour, min)}`;
  }
  if (/^\d+$/.test(min) && /^\d+$/.test(hour) && /^\d+$/.test(dom) && mon === '*' && dow === '*') return `El día ${dom} de cada mes a las ${hm(hour, min)}`;
  return '';
}

/** Control de un campo según su definición. onChange(valor como texto). Devuelve el elemento. */
export function fieldInput(key, value, onChange) {
  const spec = FIELDS[key] || { type: 'text' };
  const id = `f-${key.replace(/[^a-z0-9]/gi, '-')}-${Math.random().toString(36).slice(2, 7)}`;
  if (spec.type === 'bool') {
    const cb = h('input', { type: 'checkbox', id, checked: truthy(value) });
    cb.addEventListener('change', () => onChange(cb.checked ? '1' : '0'));
    return cb;
  }
  if (spec.type === 'select') {
    const known = spec.options.some(([v]) => v === value);
    const select = h('select', { class: 'select', id },
      !known ? h('option', { value: value ?? '' }, value ? `${value} (actual)` : '— sin definir —') : null,
      spec.options.map(([v, label]) => h('option', { value: v, selected: v === value }, label)));
    select.addEventListener('change', () => onChange(select.value));
    return select;
  }
  if (spec.type === 'textarea' || spec.type === 'code') {
    const ta = h('textarea', { class: `textarea ${spec.type === 'code' ? 'mono code-input' : ''}`, id, rows: spec.rows || 3, placeholder: spec.placeholder || '', spellcheck: 'false' });
    ta.value = value ?? '';
    ta.addEventListener('input', () => onChange(ta.value));
    return ta;
  }
  if (spec.type === 'cron') {
    const input = h('input', { class: 'input mono', id, value: value ?? '', placeholder: '*/5 * * * *', spellcheck: 'false' });
    const hint = h('span', { class: 'field-hint' });
    const presets = h('select', { class: 'select', 'aria-label': 'Programaciones habituales', style: { maxWidth: '220px' } },
      h('option', { value: '' }, 'Habituales…'), CRON_PRESETS.map(([v, label]) => h('option', { value: v }, label)));
    const update = () => {
      const text = describeCron(input.value);
      hint.textContent = text || 'Formato cron: minuto hora día-del-mes mes día-de-la-semana';
      hint.classList.toggle('status-bad', text.startsWith('Debe'));
    };
    input.addEventListener('input', () => { update(); onChange(input.value); });
    presets.addEventListener('change', () => {
      if (!presets.value) return;
      input.value = presets.value;
      presets.value = '';
      update();
      onChange(input.value);
    });
    update();
    return h('div', { class: 'cron-field' }, h('div', { class: 'cron-row' }, input, presets), hint);
  }
  const listId = spec.list ? `${id}-list` : null;
  const input = h('input', { class: `input ${spec.mono ? 'mono' : ''}`, id, value: value ?? '', placeholder: spec.placeholder || '', list: listId, spellcheck: 'false' });
  input.addEventListener('input', () => onChange(input.value));
  return spec.list ? h('div', {}, input, h('datalist', { id: listId }, spec.list.map((v) => h('option', { value: v })))) : input;
}

function actionList(value) {
  return String(value || '').split(',').map((a) => a.trim()).filter(Boolean);
}

// --- Editor de una saved search -------------------------------------------------------

/**
 * Abre el editor. onSaved(row) recibe la fila actualizada (mismo formato que el listado).
 */
export async function openEditor(row, { onSaved } = {}) {
  const modal = openModal({
    title: `Editar ${row.name}`,
    subtitle: `${state.client} · ${[row.app, row.owner].filter(Boolean).join(' / ')}`,
    size: 'xl',
    body: loadingRows(10),
    actions: [{ label: 'Cancelar' }],
  });

  let detail;
  try {
    detail = await get('/api/splunk/saved-searches/detail', { client: state.client, name: row.name, app: row.app, owner: row.owner });
  } catch (e) {
    clear(modal.body).append(errorBox(e.message));
    return;
  }

  const original = detail.content;
  const values = { ...original };
  const isAlert = (values.alert_type && values.alert_type !== 'none') || row.is_es;
  let tab = 'general';
  const tabs = h('div', { class: 'tabs', role: 'tablist' });
  const panel = h('div', { class: 'editor-panel' });
  const counter = h('span', { class: 'muted editor-count' });

  const changedKeys = () => Object.keys(values).filter((k) => (values[k] ?? '') !== (original[k] ?? ''));
  const refreshCounter = () => {
    const n = changedKeys().length;
    counter.textContent = n ? `${n} ${n === 1 ? 'cambio sin guardar' : 'cambios sin guardar'}` : 'Sin cambios';
    counter.classList.toggle('has-changes', n > 0);
  };

  const set = (key, value, fieldEl) => {
    values[key] = value;
    fieldEl?.classList.toggle('is-modified', (value ?? '') !== (original[key] ?? ''));
    refreshCounter();
  };

  // Campo con etiqueta; 'invert' muestra un booleano al revés (p. ej. Habilitada = !disabled)
  const formField = (key, { invert = false, label } = {}) => {
    const spec = FIELDS[key] || { label: key };
    const wrap = h('div', { class: `field ${spec.type === 'bool' ? 'field-check' : ''} ${(values[key] ?? '') !== (original[key] ?? '') ? 'is-modified' : ''}` });
    const current = invert ? (truthy(values[key]) ? '0' : '1') : values[key];
    const input = fieldInput(key, current, (v) => {
      set(key, invert ? (v === '1' ? '0' : '1') : v, wrap);
      if (key === 'alert_type' || key === 'is_scheduled' || key === 'alert.suppress' || key === 'actions') renderPanel();
    });
    const title = label || spec.label;
    if (spec.type === 'bool') {
      wrap.append(h('label', { class: 'check' }, input, title, invert ? null : h('code', { class: 'field-key' }, key)));
    } else {
      wrap.append(h('label', { for: input.id || input.querySelector?.('input,select,textarea')?.id }, title, h('code', { class: 'field-key' }, key)), input);
      if (spec.hint && spec.type !== 'cron') wrap.append(h('span', { class: 'field-hint' }, spec.hint));
    }
    return wrap;
  };

  const row2 = (...fields) => h('div', { class: 'field-row' }, fields);

  const PANELS = {
    general: () => [
      formField('disabled', { invert: true, label: 'Habilitada' }),
      row.is_es || 'action.correlationsearch.label' in values ? formField('action.correlationsearch.label') : null,
      formField('description'),
      formField('search'),
    ],
    schedule: () => [
      formField('is_scheduled'),
      truthy(values.is_scheduled) ? [
        formField('cron_schedule'),
        row2(formField('dispatch.earliest_time'), formField('dispatch.latest_time')),
        row2(formField('schedule_window'), formField('schedule_priority')),
      ] : infoBox('No está programada: solo se ejecuta a mano o desde un dashboard.'),
    ],
    trigger: () => {
      const type = values.alert_type || 'none';
      return [
        formField('alert_type'),
        ['number of events', 'number of results', 'number of hosts', 'number of sources'].includes(type)
          ? row2(formField('alert_comparator'), formField('alert_threshold')) : null,
        type === 'custom' ? formField('alert_condition') : null,
        type !== 'none' ? [
          row2(formField('alert.severity'), formField('alert.digest_mode')),
          formField('alert.suppress'),
          truthy(values['alert.suppress']) ? row2(formField('alert.suppress.period'), formField('alert.suppress.fields')) : null,
          row2(formField('alert.expires'), formField('alert.track')),
        ] : infoBox('Con «Nunca» la búsqueda se comporta como un reporte y no dispara alertas.'),
      ];
    },
    actions: () => {
      const current = actionList(values.actions);
      const available = [...new Set([...KNOWN_ACTIONS.filter((a) => ['email', 'webhook'].includes(a) || `action.${a}` in original),
        ...Object.keys(original).map((k) => k.match(/^action\.([^.]+)$/)?.[1]).filter(Boolean), ...current])];
      const toggles = h('div', { class: 'action-toggles' }, available.map((name) => {
        const cb = h('input', { type: 'checkbox', checked: current.includes(name) });
        cb.addEventListener('change', () => {
          const list = actionList(values.actions).filter((a) => a !== name);
          if (cb.checked) list.push(name);
          set('actions', list.join(','));
          renderPanel();
        });
        return h('label', { class: `client-chip ${current.includes(name) ? 'selected' : ''}` }, cb, name);
      }));
      return [
        h('div', { class: 'field' }, h('span', { class: 'field-label' }, 'Acciones al dispararse'), toggles,
          h('span', { class: 'field-hint' }, 'Al activar o quitar una acción se actualiza también su campo action.<nombre>. El resto de parámetros de cada acción están en «Avanzado».')),
        formField('actions'),
        current.includes('email') ? [
          row2(formField('action.email.to'), formField('action.email.cc')),
          formField('action.email.subject'),
          formField('action.email.message.alert'),
        ] : null,
        current.includes('webhook') ? formField('action.webhook.param.url') : null,
      ];
    },
    advanced: () => advancedPanel(),
  };

  let advancedFilter = '';
  function advancedPanel() {
    const filter = h('input', { class: 'input input-sm', type: 'search', placeholder: 'Filtrar campos o valores…', value: advancedFilter });
    const onlyChanged = h('input', { type: 'checkbox' });
    const list = h('div', { class: 'adv-list' });
    const renderList = () => {
      clear(list);
      const q = advancedFilter.toLowerCase();
      const keys = Object.keys(values).sort().filter((k) => (!q || k.toLowerCase().includes(q) || String(values[k]).toLowerCase().includes(q))
        && (!onlyChanged.checked || (values[k] ?? '') !== (original[k] ?? '')));
      for (const key of keys.slice(0, 400)) {
        const modified = (values[key] ?? '') !== (original[key] ?? '');
        const rowEl = h('div', { class: `adv-row ${modified ? 'is-modified' : ''}` });
        const long = String(values[key] ?? '').length > 90 || String(values[key] ?? '').includes('\n');
        const input = long
          ? h('textarea', { class: 'textarea mono', rows: 3, spellcheck: 'false' })
          : h('input', { class: 'input input-sm mono', spellcheck: 'false' });
        input.value = values[key] ?? '';
        input.addEventListener('input', () => set(key, input.value, rowEl));
        const undo = button('', { size: 'sm', variant: 'ghost', iconName: 'restore', title: 'Deshacer este cambio' });
        undo.addEventListener('click', () => {
          if (key in original) values[key] = original[key]; else delete values[key];
          refreshCounter();
          renderList();
        });
        append(rowEl, [h('code', { class: 'adv-key', title: key }, key, !(key in original) ? badge('nuevo', 'accent') : null), input, modified ? undo : h('span')]);
        list.append(rowEl);
      }
      if (keys.length > 400) list.append(h('p', { class: 'muted' }, `Se muestran 400 de ${fmtNum(keys.length)}. Afine el filtro.`));
      if (!keys.length) list.append(h('p', { class: 'muted' }, 'Ningún campo coincide.'));
    };
    filter.addEventListener('input', () => { advancedFilter = filter.value; renderList(); });
    onlyChanged.addEventListener('change', renderList);

    const newKey = h('input', { class: 'input input-sm mono', placeholder: 'action.webhook.param.url', spellcheck: 'false' });
    const newValue = h('input', { class: 'input input-sm mono', placeholder: 'valor', spellcheck: 'false' });
    const addBtn = button('Añadir', { size: 'sm', iconName: 'plus' });
    addBtn.addEventListener('click', () => {
      const key = newKey.value.trim();
      if (!/^[A-Za-z0-9_][A-Za-z0-9_.:-]*$/.test(key)) { toast('Nombre de campo no válido.', 'error'); return; }
      if (key.startsWith('eai:') || detail.readonly.includes(key)) { toast('Ese campo es de solo lectura.', 'error'); return; }
      set(key, newValue.value);
      newKey.value = '';
      newValue.value = '';
      advancedFilter = key;
      renderPanel();
    });
    renderList();
    return [
      h('p', { class: 'muted', style: { margin: '0 0 10px', fontSize: '12.5px' } },
        'Todos los parámetros de la saved search tal como los guarda Splunk. Los campos internos (eai:*) y los de solo lectura no aparecen.'),
      h('div', { class: 'toolbar', style: { padding: 0, border: 0, marginBottom: '10px' } },
        h('label', { class: 'search-input' }, icon('search'), filter),
        h('label', { class: 'check' }, onlyChanged, 'Solo modificados')),
      list,
      h('div', { class: 'adv-add' }, h('strong', {}, 'Añadir parámetro'), newKey, newValue, addBtn),
    ];
  }

  const TABS = [
    ['general', 'General', 'file'],
    ['schedule', 'Programación', 'clock'],
    isAlert || row.is_alert ? ['trigger', 'Disparo', 'bell'] : ['trigger', 'Convertir en alerta', 'bell'],
    ['actions', 'Acciones', 'zap'],
    ['advanced', 'Avanzado', 'sliders'],
  ];

  function renderTabs() {
    clear(tabs);
    for (const [key, label, iconName] of TABS) {
      tabs.append(h('button', {
        class: `tab ${tab === key ? 'active' : ''}`, type: 'button', role: 'tab', 'aria-selected': String(tab === key),
        onclick: () => { tab = key; renderTabs(); renderPanel(); },
      }, icon(iconName), label));
    }
  }

  function renderPanel() {
    append(clear(panel), PANELS[tab]());
  }

  const showEditor = () => {
    append(clear(modal.body), [tabs, panel]);
    renderTabs();
    renderPanel();
    refreshCounter();
    modal.setActions([
      { label: 'Cancelar' },
      { label: 'Revisar cambios', variant: 'primary', icon: 'arrowRight', onClick: () => { showReview(); return false; } },
    ]);
    modal.el.querySelector('.modal-footer').prepend(counter);
  };

  const showReview = () => {
    const keys = changedKeys().sort();
    if (!keys.length) throw new Error('No ha cambiado nada.');
    const short = (v) => (v === undefined || v === null || v === '' ? h('span', { class: 'muted' }, '(vacío)') : String(v));
    append(clear(modal.body), [
      infoBox(['Se aplicarán estos cambios en ', h('strong', {}, state.client), '. Antes se guarda la configuración actual en ',
        h('code', {}, `backups/${isAlert ? 'alertas' : 'reportes'}/…antes_de_editar…`), ' para poder deshacerlo desde Backups.']),
      h('div', { class: 'review-list' }, keys.map((k) => h('div', { class: 'review-item' },
        h('div', { class: 'review-name' }, FIELDS[k]?.label ? h('strong', {}, FIELDS[k].label) : null, h('code', { class: 'field-key' }, k)),
        h('div', { class: 'diff-values' },
          h('div', { class: 'diff-old' }, k in original ? short(original[k]) : h('span', { class: 'muted' }, '(no existía)')),
          h('span', { class: 'arrow' }, '→'),
          h('div', { class: 'diff-new' }, short(values[k])))))),
    ]);
    modal.setActions([
      { label: 'Volver', icon: 'edit', onClick: () => { showEditor(); return false; } },
      {
        label: `Guardar ${keys.length} ${keys.length === 1 ? 'cambio' : 'cambios'} en Splunk`, variant: 'primary', icon: 'save',
        onClick: async () => {
          const changes = Object.fromEntries(keys.map((k) => [k, values[k] ?? '']));
          const result = await post('/api/splunk/saved-searches/update', {
            client: state.client, name: row.name, app: row.app, owner: row.owner, changes,
          });
          toast(`${result.message}${result.backup?.length ? ' Configuración anterior guardada en Backups.' : ''}`, 'success');
          onSaved?.(result.row);
        },
      },
    ]);
  };

  showEditor();
}

// --- Habilitar / deshabilitar ------------------------------------------------------------

/** Copia en 'rows' los datos actualizados (mismo nombre, app y propietario). */
export function mergeRows(rows, updated) {
  for (const u of updated) {
    const row = rows.find((r) => r.name === u.name && r.app === u.app && r.owner === u.owner);
    if (row) Object.assign(row, u);
  }
}

/**
 * Habilita o deshabilita una o varias saved searches del cliente activo en una sola petición. Antes se guarda su
 * configuración en Backups («antes_de_editar»). Devuelve las filas actualizadas.
 */
export async function setEnabled(rows, enabled) {
  const result = await post('/api/splunk/saved-searches/bulk-update', {
    client: state.client, items: rows.map((r) => ({ name: r.name, app: r.app, owner: r.owner })), changes: { disabled: enabled ? '0' : '1' },
  });
  const done = enabled ? 'habilitada' : 'deshabilitada';
  if (rows.length === 1 && result.updated) toast(`«${rows[0].name}» ${done}`, 'success');
  else if (result.updated) toast(`${fmtNum(result.updated)} ${done}s · configuración anterior guardada en Backups`, 'success');
  if (result.unchanged && !result.updated) toast(rows.length === 1 ? `Ya estaba ${done}` : `Ya estaban ${done}s`, 'info');
  if (result.failed) {
    const failed = result.results.filter((r) => !r.ok);
    toast(`No se pudieron cambiar ${failed.length}: ${failed.slice(0, 3).map((r) => `${r.name}: ${r.error}`).join(' · ')}`, 'error');
  }
  return result.results.filter((r) => r.ok).map((r) => r.row);
}

// --- Cambios en bloque ---------------------------------------------------------------

export function openBulkEditor(rows, { onSaved } = {}) {
  const lines = [];
  const linesEl = h('div', { class: 'bulk-lines' });
  const summary = h('div');

  const addLine = (key = 'cron_schedule') => {
    const line = { key, value: '' };
    lines.push(line);
    renderLines();
  };

  function renderLines() {
    clear(linesEl);
    lines.forEach((line, i) => {
      const keySelect = h('select', { class: 'select', 'aria-label': 'Campo' },
        BULK_FIELDS.map((k) => h('option', { value: k, selected: k === line.key }, FIELDS[k]?.label || k)),
        h('option', { value: '__custom', selected: !BULK_FIELDS.includes(line.key) }, 'Otro campo…'));
      const custom = !BULK_FIELDS.includes(line.key);
      const customKey = h('input', { class: 'input mono', placeholder: 'nombre.del.campo', value: custom ? line.key : '' });
      keySelect.addEventListener('change', () => {
        line.key = keySelect.value === '__custom' ? '' : keySelect.value;
        line.value = FIELDS[line.key]?.type === 'bool' ? '1' : '';
        renderLines();
      });
      customKey.addEventListener('input', () => { line.key = customKey.value.trim(); });
      if (FIELDS[line.key]?.type === 'bool' && !line.value) line.value = '1';
      if (FIELDS[line.key]?.type === 'select' && !line.value) line.value = FIELDS[line.key].options[0][0];
      const input = custom || !line.key ? h('input', { class: 'input mono', value: line.value, oninput: (e) => { line.value = e.target.value; } })
        : fieldInput(line.key, line.value, (v) => { line.value = v; });
      const valueEl = FIELDS[line.key]?.type === 'bool'
        ? h('label', { class: 'check' }, input, line.key === 'disabled' ? 'Deshabilitar (desmarcado = habilitar)' : 'Activado')
        : input;
      linesEl.append(h('div', { class: 'bulk-line' },
        h('div', { class: 'bulk-key' }, keySelect, custom || !line.key ? customKey : null),
        h('div', { class: 'bulk-value' }, valueEl),
        lines.length > 1 ? button('', { size: 'sm', variant: 'ghost', iconName: 'trash', title: 'Quitar', onClick: () => { lines.splice(i, 1); renderLines(); } }) : h('span')));
    });
  }

  addLine();
  clear(summary).append(h('ul', { class: 'list-plain bulk-targets' }, rows.slice(0, 200).map((r) => h('li', { class: 'truncate' }, r.name))),
    rows.length > 200 ? h('p', { class: 'muted' }, `y ${rows.length - 200} más`) : null);

  openModal({
    title: `Modificar ${fmtNum(rows.length)} saved searches`,
    subtitle: `Cliente: ${state.client}. Se aplica el mismo valor a todas las seleccionadas.`,
    size: 'lg',
    body: [
      h('div', { class: 'field' }, h('span', { class: 'field-label' }, 'Cambios'), linesEl,
        h('div', {}, button('Añadir otro campo', { size: 'sm', variant: 'ghost', iconName: 'plus', onClick: () => addLine('alert.severity') }))),
      h('details', { class: 'bulk-details' }, h('summary', {}, `Ver las ${fmtNum(rows.length)} seleccionadas`), summary),
      infoBox('Antes de cambiar nada se guarda la configuración actual de todas en Backups (archivo «antes_de_editar»), para poder deshacerlo.'),
    ],
    actions: [
      { label: 'Cancelar' },
      {
        label: 'Aplicar cambios', variant: 'primary', icon: 'save',
        onClick: async () => {
          const changes = {};
          for (const line of lines) {
            if (!line.key) throw new Error('Indique el nombre del campo.');
            if (line.key in changes) throw new Error(`El campo ${line.key} está repetido.`);
            changes[line.key] = line.value;
          }
          const result = await post('/api/splunk/saved-searches/bulk-update', {
            client: state.client, items: rows.map((r) => ({ name: r.name, app: r.app, owner: r.owner })), changes,
          });
          onSaved?.(result.results.filter((r) => r.ok).map((r) => r.row));
          const parts = [`${fmtNum(result.updated)} modificadas`];
          if (result.unchanged) parts.push(`${fmtNum(result.unchanged)} ya tenían ese valor`);
          toast(parts.join(' · '), 'success');
          if (result.failed) {
            const failed = result.results.filter((r) => !r.ok);
            toast(`No se pudieron modificar ${failed.length}: ${failed.slice(0, 3).map((r) => `${r.name}: ${r.error}`).join(' · ')}`, 'error');
          }
        },
      },
    ],
  });
}
