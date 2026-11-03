// Tokens HEC (HTTP Event Collector) vía ACS: inventario del cliente con avisos, alta, edición y borrado (con simulación),
// valor del token bajo demanda, backup y vista de todos los clientes.
import { get, post, put } from '../api.js';
import { state, on, startJob, selectClient } from '../core.js';
import {
  h, clear, icon, button, badge, pageHeader, DataTable, openModal, openDrawer, confirmDialog, infoBox, errorBox, loadingRows,
  toast, withBusy, kv, section, codeBlock, fmtRelative, copyText,
} from '../ui.js';
import { openRestoreModal } from '../ops.js';
import { acsNotice, simulationBox } from './platform.js';

let overviewCache = null;

const field = (label, input, hint) => h('div', { class: 'field' }, h('label', {}, label, input), hint ? h('span', { class: 'field-hint' }, hint) : null);

function stateBadges(t) {
  const items = [t.disabled ? badge('Deshabilitado', 'outline') : badge('Habilitado', 'good', 'check')];
  if (t.use_ack) items.push(badge('Ack', 'accent'));
  return h('div', { class: 'badges' }, items);
}

function warningsCell(t) {
  if (!t.warnings?.length) return h('span', { class: 'muted' }, '—');
  return h('div', { class: 'badges' }, t.warnings.map((w) => badge(w, 'warn', 'alert')));
}

/** Formulario de alta o edición. Devuelve { el, value() } con los campos editables (y nombre/token al crear). */
function tokenForm(token, indexes) {
  const isEdit = Boolean(token);
  const inputs = {
    name: h('input', { class: 'input mono', value: token?.name || '', disabled: isEdit, placeholder: 'p. ej. hec_fortinet', autocomplete: 'off', spellcheck: 'false' }),
    token: h('input', { class: 'input mono', placeholder: 'Vacío: ACS genera uno nuevo', autocomplete: 'off', spellcheck: 'false' }),
    default_index: h('input', { class: 'input mono', value: token?.default_index || '', list: 'hec-indexes', autocomplete: 'off', spellcheck: 'false' }),
    allowed_indexes: h('input', { class: 'input mono', value: (token?.allowed_indexes || []).join(', '), placeholder: 'Vacío: cualquiera', autocomplete: 'off', spellcheck: 'false' }),
    default_source: h('input', { class: 'input mono', value: token?.default_source || '', autocomplete: 'off', spellcheck: 'false' }),
    default_sourcetype: h('input', { class: 'input mono', value: token?.default_sourcetype || '', autocomplete: 'off', spellcheck: 'false' }),
    default_host: h('input', { class: 'input mono', value: token?.default_host || '', placeholder: 'Vacío: lo pone ACS', autocomplete: 'off', spellcheck: 'false' }),
    meta: h('input', { class: 'input mono', value: token?.meta || '', placeholder: 'clave::valor clave2::valor', autocomplete: 'off', spellcheck: 'false' }),
  };
  const disabled = h('input', { type: 'checkbox', checked: Boolean(token?.disabled) });
  const useAck = h('input', { type: 'checkbox', checked: Boolean(token?.use_ack) });
  for (const el of Object.values(inputs)) el.style.marginTop = '6px';
  const datalist = h('datalist', { id: 'hec-indexes' }, (indexes || []).map((i) => h('option', { value: i })));

  const el = h('form', { autocomplete: 'off', onsubmit: (e) => e.preventDefault() },
    datalist,
    h('div', { class: 'field-row' },
      field('Nombre', inputs.name, isEdit ? 'El nombre no se puede cambiar.' : 'Sin espacios ni barras.'),
      field('Índice por defecto', inputs.default_index, 'Debe existir en el cliente: si no, los datos se pierden.')),
    isEdit ? null : field('Valor del token (opcional)', inputs.token, 'Para recrear un token con el mismo valor que ya usan los emisores.'),
    field('Índices permitidos', inputs.allowed_indexes, 'Separados por comas. Si se indica alguno, el índice por defecto debe estar entre ellos.'),
    h('div', { class: 'field-row' }, field('Source por defecto', inputs.default_source), field('Sourcetype por defecto', inputs.default_sourcetype)),
    h('div', { class: 'field-row' }, field('Host por defecto', inputs.default_host), field('Metadatos (_meta)', inputs.meta)),
    h('div', { class: 'badges', style: { gap: '18px', marginBottom: '12px' } },
      h('label', { class: 'check' }, disabled, 'Deshabilitado'),
      h('label', { class: 'check' }, useAck, 'Indexer acknowledgement (solo Kinesis Firehose)')));

  return {
    el,
    value() {
      const data = {
        default_index: inputs.default_index.value.trim(),
        allowed_indexes: inputs.allowed_indexes.value.split(/[\s,]+/).map((v) => v.trim()).filter(Boolean),
        default_source: inputs.default_source.value.trim(),
        default_sourcetype: inputs.default_sourcetype.value.trim(),
        default_host: inputs.default_host.value.trim(),
        meta: inputs.meta.value.trim(),
        disabled: disabled.checked,
        use_ack: useAck.checked,
      };
      if (!isEdit) {
        data.name = inputs.name.value.trim();
        if (!data.name) throw new Error('Indique el nombre del token.');
        if (inputs.token.value.trim()) data.token = inputs.token.value.trim();
      }
      if (!data.default_index) throw new Error('Indique el índice por defecto.');
      return data;
    },
    changes() {
      // Solo lo que cambia respecto al token actual (PATCH)
      const data = this.value();
      const changed = {};
      for (const [k, v] of Object.entries(data)) {
        const before = token[k];
        const same = Array.isArray(v) ? JSON.stringify(v) === JSON.stringify(before || []) : v === (before ?? '');
        if (!same) changed[k] = v;
      }
      return changed;
    },
  };
}

function openTokenModal({ client, token, indexes, onDone }) {
  const form = tokenForm(token, indexes);
  const simulation = h('div');
  const isEdit = Boolean(token);
  const send = async (simulate) => {
    const body = isEdit ? { client, changes: form.changes(), simulate } : { client, ...form.value(), simulate };
    if (isEdit && !Object.keys(body.changes).length) throw new Error('No hay ningún cambio.');
    const result = isEdit ? await put(`/api/hec/${encodeURIComponent(token.name)}`, body) : await post('/api/hec', body);
    if (simulate) {
      clear(simulation).append(simulationBox(result));
      return false;
    }
    toast(isEdit ? `Token ${token.name} actualizado` : `Token ${result.name} creado`, 'success');
    onDone(result);
    return true;
  };
  openModal({
    title: isEdit ? `Editar el token HEC ${token.name}` : 'Nuevo token HEC',
    subtitle: `${client} · ACS aplica el cambio en unos segundos; se espera a que termine.`,
    size: 'lg',
    body: [form.el, simulation],
    actions: [
      { label: 'Cancelar' },
      { label: 'Simular', icon: 'eye', onClick: () => send(true) },
      { label: isEdit ? 'Guardar' : 'Crear', variant: 'primary', icon: 'save', onClick: () => send(false) },
    ],
  });
}

async function deleteToken(client, token, onDone) {
  const ok = await confirmDialog({
    title: `¿Borrar el token HEC ${token.name}?`,
    message: 'Los emisores que lo usen dejarán de poder enviar datos. Antes se guarda su configuración (con el valor del token) en backups/hec/, así que se puede recrear igual.',
    confirmLabel: 'Borrar', danger: true,
  });
  if (!ok) return;
  try {
    await post(`/api/hec/${encodeURIComponent(token.name)}/delete`, { client });
    toast(`Token ${token.name} borrado`, 'success');
    onDone();
  } catch (e) {
    toast(e.message, 'error');
  }
}

function tokenDetail(client, token, indexes, reload) {
  const valueSlot = h('div', { class: 'muted' }, 'Pulse «Ver valor» para pedirlo a ACS (queda en el registro de auditoría).');
  const showBtn = button('Ver valor', { size: 'sm', iconName: 'eye' });
  showBtn.addEventListener('click', () => withBusy(showBtn, async () => {
    try {
      const data = await get('/api/hec/value', { client, name: token.name });
      clear(valueSlot).append(codeBlock(data.token));
    } catch (e) {
      clear(valueSlot).append(errorBox(e.message));
    }
  }));
  const drawer = openDrawer({
    title: token.name,
    subtitle: `Token HEC de ${client}`,
    width: 640,
    actions: [
      button('Editar', { size: 'sm', variant: 'primary', iconName: 'edit', onClick: () => { drawer.close(); openTokenModal({ client, token, indexes, onDone: reload }); } }),
      button(token.disabled ? 'Habilitar' : 'Deshabilitar', { size: 'sm', iconName: 'power', onClick: async () => {
        try {
          await put(`/api/hec/${encodeURIComponent(token.name)}`, { client, changes: { disabled: !token.disabled } });
          toast(`Token ${token.name} ${token.disabled ? 'habilitado' : 'deshabilitado'}`, 'success');
          drawer.close();
          reload();
        } catch (e) { toast(e.message, 'error'); }
      } }),
      button('Borrar', { size: 'sm', variant: 'danger', iconName: 'trash', onClick: () => deleteToken(client, token, () => { drawer.close(); reload(); }) }),
    ],
    body: [
      h('div', { style: { marginBottom: '14px' } }, stateBadges(token)),
      token.warnings?.length ? infoBox(h('ul', { class: 'list-plain' }, token.warnings.map((w) => h('li', {}, w))), 'warn-box') : null,
      section('Configuración', kv([
        ['Índice por defecto', token.default_index ? h('code', {}, token.default_index) : null],
        ['Índices permitidos', token.allowed_indexes?.length ? h('div', { class: 'badges' }, token.allowed_indexes.map((i) => badge(i))) : 'Cualquiera'],
        ['Source', token.default_source || null], ['Sourcetype', token.default_sourcetype || null],
        ['Host', token.default_host || null], ['Metadatos', token.meta || null],
      ])),
      section('Valor del token', h('div', { style: { marginBottom: '8px' } }, showBtn), valueSlot),
    ],
  });
}

// --- Todos los clientes -----------------------------------------------------------------------------

function overviewCard() {
  const body = h('div');
  const stamp = h('span', { class: 'muted', style: { fontSize: '12.5px' } });
  const runBtn = button('Comprobar todos los clientes', { iconName: 'refresh' });

  function render() {
    clear(body);
    if (!overviewCache) {
      body.append(h('p', { class: 'muted', style: { margin: 0 } }, 'Pulse «Comprobar todos los clientes» para ver los tokens HEC de cada cliente y sus avisos.'));
      return;
    }
    stamp.textContent = `Datos ${fmtRelative(overviewCache.at / 1000)}`;
    const rows = overviewCache.rows.flatMap((r) => (r.error || !r.tokens.length
      ? [{ client: r.client, host: r.host, error: r.error, name: '', token: null }]
      : r.tokens.map((t) => ({ client: r.client, host: r.host, name: t.name, token: t }))));
    const table = new DataTable({
      rows, rowKey: (r) => `${r.client}/${r.name}`, compact: true,
      searchText: (r) => `${r.client} ${r.name} ${r.token?.default_index || ''} ${(r.token?.warnings || []).join(' ')}`,
      searchPlaceholder: 'Buscar cliente, token o índice…',
      initialSort: { key: 'client', dir: 'asc' },
      filters: [{
        id: 'state',
        options: [
          { value: 'all', label: 'Todos' },
          { value: 'warn', label: 'Con avisos', test: (r) => r.token?.warnings?.length > 0 },
          { value: 'off', label: 'Deshabilitados', test: (r) => r.token?.disabled },
          { value: 'error', label: 'Clientes con error', test: (r) => Boolean(r.error) },
        ],
      }],
      columns: [
        { key: 'client', label: 'Cliente', render: (r) => h('div', {}, h('div', { class: 'cell-main' }, r.client), h('div', { class: 'cell-sub' }, r.host || '')) },
        { key: 'name', label: 'Token', render: (r) => (r.error ? h('span', { class: 'status status-bad' }, icon('xCircle'), r.error) : r.token ? h('span', { class: 'mono' }, r.name) : h('span', { class: 'muted' }, 'Sin tokens HEC')) },
        { key: 'index', label: 'Índice', sort: (r) => r.token?.default_index || '', render: (r) => (r.token?.default_index ? h('code', {}, r.token.default_index) : h('span', { class: 'muted' }, '—')) },
        { key: 'state', label: 'Estado', sortable: false, render: (r) => (r.token ? stateBadges(r.token) : '') },
        { key: 'warnings', label: 'Avisos', sortable: false, render: (r) => (r.token ? warningsCell(r.token) : '') },
      ],
      onRowClick: (r) => { if (r.client !== state.client) selectClient(r.client); window.scrollTo(0, 0); },
      emptyTitle: 'No hay clientes configurados',
    });
    body.append(table.el);
  }

  runBtn.addEventListener('click', () => withBusy(runBtn, async () => {
    clear(body).append(h('p', { class: 'muted' }, 'Consultando todos los clientes…'), loadingRows(4));
    try {
      overviewCache = { at: Date.now(), rows: await get('/api/hec/overview') };
    } catch (e) {
      clear(body).append(errorBox(e.message));
      return;
    }
    render();
  }));

  render();
  return h('div', { class: 'card', style: { marginTop: '16px' } },
    h('div', { class: 'card-header' },
      h('div', {}, h('h2', { class: 'card-title' }, 'Todos los clientes'),
        h('p', { class: 'card-subtitle' }, 'Tokens HEC de cada cliente con sus avisos. Pulse una fila para pasar a ese cliente.')),
      h('div', { class: 'page-actions' }, stamp, runBtn)),
    h('div', { class: 'card-body' }, body));
}

// --- Vista ----------------------------------------------------------------------------------------------

export default {
  id: 'hec',
  title: 'Tokens HEC',
  icon: 'inbox',
  needsClient: true,
  render(root) {
    const client = state.client;
    let data = null;
    let table = null;
    const stamp = h('span', { class: 'muted', style: { fontSize: '12.5px' } });
    const noticeSlot = h('div');
    const tableSlot = h('div', {}, loadingRows(5));
    const newBtn = button('Nuevo token', { iconName: 'plus', variant: 'primary', disabled: true });
    const refreshBtn = button('Actualizar', { iconName: 'refresh' });
    const backupBtn = button('Backup', { iconName: 'download', onClick: () => startJob('/api/hec/backup', { clients: [client] }) });
    const restoreBtn = button('Restaurar…', { iconName: 'restore', onClick: () => openRestoreModal({ folder: 'hec' }) });

    const load = async () => {
      clear(noticeSlot);
      const platform = state.clients.find((c) => c.name === client)?.platform;
      if (platform && !platform.acs) {
        clear(tableSlot).append(acsNotice(client, platform));
        newBtn.disabled = true;
        return;
      }
      try {
        data = await get('/api/hec', { client });
        stamp.textContent = `Datos ${fmtRelative(Date.now() / 1000)}`;
        if (data.warning) noticeSlot.append(infoBox(data.warning, 'warn-box'));
        newBtn.disabled = false;
        if (!table) {
          table = new DataTable({
            rows: data.tokens, rowKey: (t) => t.name, selectable: true,
            searchText: (t) => `${t.name} ${t.default_index} ${t.allowed_indexes.join(' ')} ${t.default_sourcetype}`,
            searchPlaceholder: 'Buscar token o índice…',
            initialSort: { key: 'name', dir: 'asc' },
            emptyIcon: 'inbox', emptyTitle: 'No hay tokens HEC', emptyText: `${client} no tiene ningún token HEC definido.`,
            filters: [{
              id: 'state',
              options: [
                { value: 'all', label: 'Todos' },
                { value: 'warn', label: 'Con avisos', test: (t) => t.warnings?.length > 0 },
                { value: 'on', label: 'Habilitados', test: (t) => !t.disabled },
                { value: 'off', label: 'Deshabilitados', test: (t) => t.disabled },
              ],
            }],
            onRowClick: (t) => tokenDetail(client, t, data.indexes, load),
            selectionActions: [
              { label: 'Deshabilitar', icon: 'power', onClick: (sel) => toggleMany(sel, true) },
              { label: 'Habilitar', icon: 'power', onClick: (sel) => toggleMany(sel, false) },
            ],
            columns: [
              { key: 'name', label: 'Token', render: (t) => h('span', { class: 'cell-main mono' }, t.name) },
              { key: 'default_index', label: 'Índice por defecto', render: (t) => (t.default_index ? h('code', {}, t.default_index) : h('span', { class: 'muted' }, '—')) },
              { key: 'allowed', label: 'Índices permitidos', sort: (t) => t.allowed_indexes.length, render: (t) => (t.allowed_indexes.length ? h('div', { class: 'badges' }, t.allowed_indexes.slice(0, 4).map((i) => badge(i)), t.allowed_indexes.length > 4 ? badge(`+${t.allowed_indexes.length - 4}`, 'outline') : null) : h('span', { class: 'muted' }, 'Cualquiera')) },
              { key: 'sourcetype', label: 'Sourcetype', sort: (t) => t.default_sourcetype, render: (t) => t.default_sourcetype || h('span', { class: 'muted' }, '—') },
              { key: 'state', label: 'Estado', sort: (t) => (t.disabled ? 1 : 0), render: stateBadges },
              { key: 'warnings', label: 'Avisos', sort: (t) => t.warnings?.length || 0, defaultDir: 'desc', render: warningsCell },
              {
                key: 'actions', label: '', className: 'col-actions', sortable: false,
                render: (t) => h('div', { class: 'row-actions' },
                  button('', { size: 'sm', variant: 'ghost', iconName: 'edit', title: 'Editar', onClick: () => openTokenModal({ client, token: t, indexes: data.indexes, onDone: load }) }),
                  button('', { size: 'sm', variant: 'ghost', iconName: 'trash', title: 'Borrar', onClick: () => deleteToken(client, t, load) })),
              },
            ],
          });
          clear(tableSlot).append(table.el);
        } else {
          table.setRows(data.tokens);
        }
      } catch (e) {
        clear(tableSlot).append(errorBox(e.message));
      }
    };

    async function toggleMany(tokens, disable) {
      const failed = [];
      for (const t of tokens) {
        if (t.disabled === disable) continue;
        try { await put(`/api/hec/${encodeURIComponent(t.name)}`, { client, changes: { disabled: disable } }); } catch (e) { failed.push(`${t.name}: ${e.message}`); }
      }
      if (failed.length) toast(failed.join(' · '), 'error'); else toast(`Tokens ${disable ? 'deshabilitados' : 'habilitados'}`, 'success');
      table?.clearSelection();
      load();
    }

    newBtn.addEventListener('click', () => openTokenModal({ client, token: null, indexes: data?.indexes || [], onDone: load }));
    refreshBtn.addEventListener('click', () => withBusy(refreshBtn, load));

    clear(root).append(
      pageHeader({
        title: 'Tokens HEC',
        subtitle: `Tokens del HTTP Event Collector de ${client} gestionados con ACS: índice por defecto, índices permitidos y estado, con aviso cuando apuntan a índices que no existen.`,
        actions: [stamp, refreshBtn, restoreBtn, backupBtn, newBtn],
      }),
      noticeSlot, tableSlot, overviewCard());
    load();
    return on('job-finished', (job) => { if (job.kind === 'restore' && job.folder === 'hec' && job.clients.includes(client)) load(); });
  },
};

export { copyText };
