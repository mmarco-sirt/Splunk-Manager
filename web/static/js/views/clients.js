// Gestión de clientes (config.py): alta, edición, borrado, prueba de credenciales y conexión.
import { post, put, del, enc } from '../api.js';
import { state, on, loadClients, connectClient, disconnectClient, selectClient, emit } from '../core.js';
import {
  h, clear, icon, button, badge, pageHeader, DataTable, openModal, confirmDialog, toast, statusLabel, fmtRelative, withBusy,
  infoBox, segmented,
} from '../ui.js';

const METHOD_LABELS = { password: 'Usuario y contraseña', token: 'Token' };
const FIELD_LABELS = { password: 'la contraseña', splunkToken: 'el token' };

function field(label, input, hint) {
  return h('div', { class: 'field' }, h('label', {}, label, input), hint && h('span', { class: 'field-hint' }, hint));
}

function clientForm(client) {
  const isEdit = Boolean(client);
  const problems = client?.credential_problems || [];
  const inputs = {
    name: h('input', { class: 'input', value: client?.name || '', required: true, autocomplete: 'off' }),
    host: h('input', { class: 'input', value: client?.host || '', placeholder: 'cliente.splunkcloud.com', autocomplete: 'off' }),
    username: h('input', { class: 'input', value: client?.username || '', autocomplete: 'off' }),
    password: h('input', { class: 'input', type: 'password', autocomplete: 'new-password', placeholder: client?.has_password ? '•••••••• (sin cambios)' : '' }),
    splunkToken: h('input', { class: 'input', type: 'password', autocomplete: 'off', placeholder: client?.has_token ? '•••••••• (sin cambios)' : '' }),
  };
  const clearPassword = h('input', { type: 'checkbox' });
  const clearToken = h('input', { type: 'checkbox' });
  const testAfter = h('input', { type: 'checkbox', checked: true });
  for (const el of Object.values(inputs)) el.style.marginTop = '6px';

  let auth = client && client.auth !== 'none' ? client.auth : 'password';
  const authSlot = h('div');

  // Qué métodos tendrá el cliente al guardar (lo escrito ahora o lo que ya tenía guardado)
  const willHave = () => ({
    password: Boolean(inputs.username.value.trim()) && !clearPassword.checked && Boolean(inputs.password.value || client?.has_password),
    token: !clearToken.checked && Boolean(inputs.splunkToken.value || client?.has_token),
  });

  const renderAuth = () => {
    const has = willHave();
    clear(authSlot);
    if (has.password && has.token) {
      authSlot.append(h('div', { class: 'field' },
        h('span', { class: 'field-label' }, 'Método preferido'),
        h('div', {}, segmented([{ value: 'password', label: METHOD_LABELS.password }, { value: 'token', label: METHOD_LABELS.token }], auth, (v) => { auth = v; })),
        h('span', { class: 'field-hint' }, 'Se usa primero el preferido; si Splunk rechaza esas credenciales, se prueba automáticamente el otro.')));
    } else {
      const only = has.password ? METHOD_LABELS.password : has.token ? METHOD_LABELS.token : null;
      authSlot.append(h('p', { class: 'field-hint', style: { margin: '0 0 14px' } },
        only ? `Se conectará con ${only.toLowerCase()}. Puede guardar también ${has.password ? 'un token' : 'usuario y contraseña'} como alternativa.`
          : 'Indique usuario y contraseña, un token o ambos.'));
    }
  };
  for (const el of [inputs.username, inputs.password, inputs.splunkToken]) el.addEventListener('input', renderAuth);
  for (const el of [clearPassword, clearToken]) el.addEventListener('change', renderAuth);

  const value = () => {
    const data = Object.fromEntries(Object.entries(inputs).map(([k, input]) => [k, input.value.trim()]));
    if (!data.name || !data.host) throw new Error('El nombre y el host son obligatorios.');
    data.host = data.host.replace(/^https?:\/\//, '').replace(/[:/].*$/, '');
    data.clear_password = clearPassword.checked;
    data.clear_token = clearToken.checked;
    data.auth = auth;
    return data;
  };

  // Prueba de un método con lo escrito en el formulario (sin guardar)
  const tester = (method) => {
    const result = h('div', { class: 'test-result', 'aria-live': 'polite' });
    const btn = button('Probar', { size: 'sm', iconName: 'plug', title: `Probar la conexión con ${METHOD_LABELS[method].toLowerCase()}` });
    btn.addEventListener('click', () => withBusy(btn, async () => {
      clear(result).append(h('span', { class: 'status' }, h('span', { class: 'spinner' }), 'Probando…'));
      try {
        const data = value();
        const res = await post('/api/clients/test', { ...data, method, original: client?.name || '' });
        clear(result).append(res.ok
          ? statusLabel(`${res.message}${res.version ? ` · Splunk ${res.version}` : ''}`, 'good', 'checkCircle')
          : h('span', { class: 'status status-bad' }, icon('xCircle'), h('span', { class: 'test-error' }, res.message)));
      } catch (e) {
        clear(result).append(h('span', { class: 'status status-bad' }, icon('xCircle'), e.message));
      }
    }));
    return { btn, result };
  };
  const testPassword = tester('password');
  const testToken = tester('token');

  const credBlock = (title, iconName, body, test) => h('div', { class: 'cred-block' },
    h('div', { class: 'cred-head' }, icon(iconName), h('strong', {}, title), h('div', { style: { flex: 1 } }), test.btn),
    body, test.result);

  const el = h('form', { autocomplete: 'off', onsubmit: (e) => e.preventDefault() },
    h('div', { class: 'field-row' },
      field('Nombre', inputs.name),
      field('Host', inputs.host, 'Sin https:// ni puerto; se usa el 8089 de la API REST.')),
    problems.length ? infoBox(`No se puede descifrar ${problems.map((p) => FIELD_LABELS[p]).join(' ni ')} de este cliente: se guardó con otro usuario de Windows o en otro equipo. Vuelva a introducir${problems.length > 1 ? 'los' : 'la'}.`, 'warn-box') : null,
    credBlock('Usuario y contraseña', 'user', [
      h('div', { class: 'field-row' }, field('Usuario', inputs.username), field('Contraseña', inputs.password)),
      isEdit && client.has_password ? h('label', { class: 'check', style: { margin: '-6px 0 8px' } }, clearPassword, 'Quitar la contraseña guardada') : null,
    ], testPassword),
    credBlock('Token de autenticación', 'key', [
      field('Token', inputs.splunkToken, 'Splunk Web → Configuración → Tokens. Se usa como «Authorization: Bearer».'),
      isEdit && client.has_token ? h('label', { class: 'check', style: { margin: '-6px 0 8px' } }, clearToken, 'Quitar el token guardado') : null,
    ], testToken),
    authSlot,
    infoBox('Las contraseñas y tokens se guardan cifrados en config.py con DPAPI de Windows (solo su usuario de Windows puede descifrarlos) y nunca se envían al navegador.'),
    h('label', { class: 'check' }, testAfter, 'Conectar al guardar para comprobar las credenciales'));
  renderAuth();

  return { el, value, testAfter: () => testAfter.checked };
}

export function openClientModal(client) {
  const form = clientForm(client);
  openModal({
    title: client ? `Editar ${client.name}` : 'Nuevo cliente',
    subtitle: client ? 'Deje la contraseña y el token vacíos para mantener los actuales.' : 'Se añadirá a config.py.',
    size: 'lg',
    body: form.el,
    actions: [
      { label: 'Cancelar' },
      {
        label: 'Guardar', variant: 'primary', icon: 'save',
        onClick: async () => {
          const data = form.value();
          const saved = client ? await put(`/api/clients/${enc(client.name)}`, data) : await post('/api/clients', data);
          const wasActive = client && state.client === client.name;
          await loadClients();
          toast(`Cliente ${saved.name} guardado`, 'success');
          if (wasActive || !state.client) await selectClient(saved.name);
          else if (form.testAfter()) await connectClient(saved.name);
          emit('client');
        },
      },
    ],
  });
}

function authCell(c) {
  if (c.auth === 'none') return badge('Sin credenciales', 'bad', 'alert');
  const both = c.methods.length > 1;
  const badges = c.methods.map((m) => (m === 'password' ? badge(c.username, '', 'user') : badge('Token', 'accent', 'key')));
  return h('div', {},
    h('div', { class: 'badges' }, badges),
    both ? h('div', { class: 'cell-sub' }, `Preferido: ${METHOD_LABELS[c.auth].toLowerCase()}`) : null,
    c.credential_problems?.length ? h('div', { class: 'cell-sub status-bad' }, `No se puede descifrar ${c.credential_problems.map((p) => FIELD_LABELS[p]).join(' ni ')}`) : null);
}

function stateCell(c) {
  if (state.connecting === c.name) return h('span', { class: 'status' }, h('span', { class: 'spinner' }), 'Conectando…');
  if (!c.connected) return statusLabel('Sin conexión', 'muted');
  const fallback = c.connected_with && c.connected_with !== c.auth;
  return h('div', {},
    statusLabel(`Conectado${c.info?.version ? ` · ${c.info.version}` : ''}`, 'good', 'checkCircle'),
    h('div', { class: `cell-sub ${fallback ? 'status-warn' : ''}`, title: fallback ? 'El método preferido falló; revise esas credenciales.' : null },
      `${c.connected_with ? `con ${c.connected_with === 'token' ? 'token' : c.username || 'usuario'}${fallback ? ' (falló el preferido)' : ''} · ` : ''}${fmtRelative(c.connected_at)}`));
}

export default {
  id: 'clients',
  title: 'Clientes',
  icon: 'server',
  render(root, params) {
    const table = new DataTable({
      rows: state.clients,
      searchText: (c) => `${c.name} ${c.host} ${c.username}`,
      searchPlaceholder: 'Buscar cliente…',
      initialSort: { key: 'name', dir: 'asc' },
      emptyIcon: 'server',
      emptyTitle: 'No hay clientes',
      emptyText: 'Añada el primero con el botón «Nuevo cliente».',
      onRowClick: (c) => openClientModal(c),
      filters: [{
        id: 'auth',
        options: [
          { value: 'all', label: 'Todos' },
          { value: 'both', label: 'Con contraseña y token', test: (c) => c.methods.length > 1 },
          { value: 'one', label: 'Un solo método', test: (c) => c.methods.length === 1 },
          { value: 'problems', label: 'Con problemas', test: (c) => c.auth === 'none' || c.credential_problems?.length > 0 },
        ],
      }],
      columns: [
        { key: 'name', label: 'Cliente', render: (c) => h('div', {}, h('div', { class: 'cell-main' }, c.name), state.client === c.name ? h('div', { class: 'cell-sub' }, 'Activo') : null) },
        { key: 'host', label: 'Host', render: (c) => h('span', { class: 'mono' }, c.host) },
        { key: 'auth', label: 'Autenticación', sort: (c) => c.methods.length, render: authCell },
        { key: 'connected', label: 'Estado', sort: (c) => (c.connected ? 0 : 1), render: stateCell },
        {
          key: 'actions', label: '', className: 'col-actions', sortable: false,
          render: (c) => {
            const connectBtn = button(c.connected ? 'Reconectar' : 'Conectar', { size: 'sm', iconName: c.connected ? 'refresh' : 'plug' });
            connectBtn.addEventListener('click', () => withBusy(connectBtn, () => connectClient(c.name)));
            return h('div', { class: 'row-actions' },
              connectBtn,
              c.connected ? button('', { size: 'sm', variant: 'ghost', iconName: 'power', title: 'Desconectar', onClick: () => disconnectClient(c.name) }) : null,
              button('', { size: 'sm', variant: 'ghost', iconName: 'edit', title: 'Editar', onClick: () => openClientModal(c) }),
              button('', {
                size: 'sm', variant: 'ghost', iconName: 'trash', title: 'Eliminar',
                onClick: async () => {
                  const ok = await confirmDialog({
                    title: `¿Eliminar ${c.name}?`,
                    message: 'Se quitará de config.py junto con sus credenciales. Los backups existentes no se borran.',
                    confirmLabel: 'Eliminar', danger: true,
                  });
                  if (!ok) return;
                  try {
                    await del(`/api/clients/${enc(c.name)}`);
                    await loadClients();
                    toast(`Cliente ${c.name} eliminado`, 'success');
                  } catch (e) {
                    toast(e.message, 'error');
                  }
                },
              }));
          },
        },
      ],
    });

    clear(root).append(
      pageHeader({
        title: 'Clientes',
        subtitle: 'Plataformas Splunk configuradas en config.py, con usuario/contraseña, token o ambos (cifrados). Los cambios también los ve el CLI.',
        actions: [button('Nuevo cliente', { variant: 'primary', iconName: 'plus', onClick: () => openClientModal(null) })],
      }),
      table.el);

    if (params.new) openClientModal(null);
    const offs = [on('clients', () => table.setRows(state.clients)), on('connection', () => table.setRows(state.clients))];
    return () => offs.forEach((off) => off());
  },
};
