// Edición de macros: formulario con revisión de cambios y habilitar/deshabilitar una o varias.
import { get, post } from './api.js';
import { state } from './core.js';
import { h, append, clear, openModal, toast, infoBox, loadingRows, errorBox, fmtNum } from './ui.js';

const LABELS = {
  disabled: 'Habilitada', definition: 'Definición', args: 'Argumentos', iseval: 'Es una expresión eval',
  description: 'Descripción', validation: 'Validación de argumentos', errormsg: 'Mensaje si la validación falla',
};
const BOOLEAN = ['disabled', 'iseval'];

/**
 * Abre el editor de una macro. onSaved(row) recibe la fila actualizada (mismo formato que el listado).
 */
export async function openMacroEditor(row, { onSaved } = {}) {
  const modal = openModal({
    title: `Editar ${row.name}`,
    subtitle: `${state.client} · ${[row.app, row.owner].filter(Boolean).join(' / ')}`,
    size: 'lg',
    body: loadingRows(8),
    actions: [{ label: 'Cancelar' }],
  });

  let detail;
  try {
    detail = await get('/api/splunk/macros/detail', { client: state.client, name: row.name, app: row.app, owner: row.owner });
  } catch (e) {
    clear(modal.body).append(errorBox(e.message));
    return;
  }

  const original = detail.content;
  const values = { ...original };
  const expected = detail.expected_args;
  const counter = h('span', { class: 'muted editor-count' });
  const changedKeys = () => Object.keys(values).filter((k) => values[k] !== original[k]);
  const refreshCounter = () => {
    const n = changedKeys().length;
    counter.textContent = n ? `${n} ${n === 1 ? 'cambio sin guardar' : 'cambios sin guardar'}` : 'Sin cambios';
    counter.classList.toggle('has-changes', n > 0);
  };

  // Campo con etiqueta que se marca como modificado; 'disabled' se muestra al revés (Habilitada)
  const formField = (key, { type = 'text', rows = 2, hint, placeholder } = {}) => {
    const wrap = h('div', { class: `field ${type === 'bool' ? 'field-check' : ''}` });
    const mark = () => wrap.classList.toggle('is-modified', values[key] !== original[key]);
    let input;
    if (type === 'bool') {
      const invert = key === 'disabled';
      input = h('input', { type: 'checkbox', checked: invert ? values[key] !== '1' : values[key] === '1' });
      input.addEventListener('change', () => { values[key] = (input.checked !== invert) ? '1' : '0'; mark(); refreshCounter(); });
      wrap.append(h('label', { class: 'check' }, input, LABELS[key], invert ? null : h('code', { class: 'field-key' }, key)));
    } else {
      input = type === 'textarea' || type === 'code'
        ? h('textarea', { class: `textarea ${type === 'code' ? 'mono code-input' : ''}`, rows, spellcheck: 'false', placeholder: placeholder || '' })
        : h('input', { class: 'input mono', spellcheck: 'false', placeholder: placeholder || '' });
      input.value = values[key] ?? '';
      input.addEventListener('input', () => { values[key] = input.value; mark(); refreshCounter(); });
      wrap.append(h('label', {}, LABELS[key], h('code', { class: 'field-key' }, key)), input);
    }
    if (hint) wrap.append(h('span', { class: 'field-hint' }, hint));
    mark();
    return wrap;
  };

  const showArgs = expected > 0 || original.args;
  const argsHint = expected
    ? `Separados por comas. Esta macro lleva ${expected} ${expected === 1 ? 'argumento' : 'argumentos'} (el número forma parte del nombre); en la definición se usan como $nombre$.`
    : 'El nombre de esta macro no termina en (N), así que no debería llevar argumentos.';

  const showEditor = () => {
    append(clear(modal.body), [
      formField('disabled', { type: 'bool' }),
      formField('definition', { type: 'code', rows: 8, hint: 'Texto que sustituye a la macro en la búsqueda (o expresión eval si se marca abajo).' }),
      showArgs ? formField('args', { placeholder: 'arg1,arg2', hint: argsHint }) : null,
      formField('iseval', { type: 'bool', hint: 'La definición es una expresión eval cuyo resultado es el texto de la búsqueda.' }),
      formField('description', { type: 'textarea', rows: 2 }),
      showArgs || original.validation || original.errormsg ? h('div', { class: 'field-row' },
        formField('validation', { placeholder: 'isint($count$)', hint: 'Expresión eval que deben cumplir los argumentos (opcional).' }),
        formField('errormsg', { placeholder: 'count debe ser un número' })) : null,
    ]);
    refreshCounter();
    modal.setActions([
      { label: 'Cancelar' },
      { label: 'Revisar cambios', variant: 'primary', icon: 'arrowRight', onClick: () => { showReview(); return false; } },
    ]);
    modal.el.querySelector('.modal-footer').prepend(counter);
  };

  const shown = (key, value) => {
    if (BOOLEAN.includes(key)) return key === 'disabled' ? (value === '1' ? 'No' : 'Sí') : (value === '1' ? 'Sí' : 'No');
    return value ? String(value) : h('span', { class: 'muted' }, '(vacío)');
  };

  const showReview = () => {
    const keys = changedKeys();
    if (!keys.length) throw new Error('No ha cambiado nada.');
    append(clear(modal.body), [
      infoBox(['Se aplicarán estos cambios en ', h('strong', {}, state.client), '. Antes se guarda la configuración actual en ',
        h('code', {}, 'backups/macros/…antes_de_editar…'), ' para poder deshacerlo desde Backups.',
        values.disabled === '1' && original.disabled !== '1' ? ' Mientras esté deshabilitada, las búsquedas y alertas que la usen fallarán.' : '']),
      h('div', { class: 'review-list' }, keys.map((k) => h('div', { class: 'review-item' },
        h('div', { class: 'review-name' }, h('strong', {}, LABELS[k]), h('code', { class: 'field-key' }, k)),
        h('div', { class: 'diff-values' },
          h('div', { class: 'diff-old' }, shown(k, original[k])),
          h('span', { class: 'arrow' }, '→'),
          h('div', { class: 'diff-new' }, shown(k, values[k])))))),
    ]);
    modal.setActions([
      { label: 'Volver', icon: 'edit', onClick: () => { showEditor(); return false; } },
      {
        label: `Guardar ${keys.length} ${keys.length === 1 ? 'cambio' : 'cambios'} en Splunk`, variant: 'primary', icon: 'save',
        onClick: async () => {
          const changes = Object.fromEntries(keys.map((k) => [k, values[k] ?? '']));
          const result = await post('/api/splunk/macros/update', { client: state.client, name: row.name, app: row.app, owner: row.owner, changes });
          toast(`${result.message}${result.backup?.length ? ' Configuración anterior guardada en Backups.' : ''}`, 'success');
          onSaved?.(result.row);
        },
      },
    ]);
  };

  showEditor();
}

/**
 * Habilita o deshabilita una o varias macros del cliente activo en una sola petición. Antes se guarda su
 * configuración en Backups («antes_de_editar»). Devuelve las filas actualizadas.
 */
export async function setMacrosEnabled(rows, enabled) {
  const result = await post('/api/splunk/macros/bulk-update', {
    client: state.client, items: rows.map((r) => ({ name: r.name, app: r.app, owner: r.owner })), changes: { disabled: enabled ? '0' : '1' },
  });
  const done = enabled ? 'habilitada' : 'deshabilitada';
  if (rows.length === 1 && result.updated) toast(`«${rows[0].name}» ${done}`, 'success');
  else if (result.updated) toast(`${fmtNum(result.updated)} macros ${done}s · configuración anterior guardada en Backups`, 'success');
  if (result.unchanged && !result.updated) toast(rows.length === 1 ? `Ya estaba ${done}` : `Ya estaban ${done}s`, 'info');
  if (result.failed) {
    const failed = result.results.filter((r) => !r.ok);
    toast(`No se pudieron cambiar ${failed.length}: ${failed.slice(0, 3).map((r) => `${r.name}: ${r.error}`).join(' · ')}`, 'error');
  }
  return result.results.filter((r) => r.ok).map((r) => r.row);
}
