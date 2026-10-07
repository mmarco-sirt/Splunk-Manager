// Gestión de clientes (config.py): alta, edición, borrado y conexión.
import { post, put, del, enc } from '../api.js';
import { state, on, loadClients, connectClient, disconnectClient, selectClient, emit } from '../core.js';
import {
  h, clear, button, badge, pageHeader, DataTable, openModal, confirmDialog, toast, statusLabel, fmtRelative, withBusy, infoBox,
} from '../ui.js';

function field(label, input, hint) {
  return h('div', { class: 'field' }, h('label', {}, label, input), hint && h('span', { class: 'field-hint' }, hint));
}

function clientForm(client) {
  const isEdit = Boolean(client);
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
  for (const el of Object.values(inputs)) {
    el.style.marginTop = '6px';
  }

  const el = h('form', { autocomplete: 'off', onsubmit: (e) => e.preventDefault() },
    h('div', { class: 'field-row' },
      field('Nombre', inputs.name),
      field('Host', inputs.host, 'Sin https:// ni puerto; se usa el 8089 de la API REST.')),
    infoBox('Se usa usuario y contraseña si están informados; si no, el token. Las credenciales se guardan en config.py y nunca se envían al navegador.'),
    h('div', { class: 'field-row' },
      field('Usuario', inputs.username),
      field('Contraseña', inputs.password)),
    isEdit && client.has_password ? h('label', { class: 'check', style: { margin: '-6px 0 14px' } }, clearPassword, 'Quitar la contraseña guardada') : null,
    field('Token de autenticación', inputs.splunkToken),
    isEdit && client.has_token ? h('label', { class: 'check', style: { margin: '-6px 0 14px' } }, clearToken, 'Quitar el token guardado') : null,
    h('label', { class: 'check' }, testAfter, 'Conectar al guardar para comprobar las credenciales'));

  return {
    el,
    value() {
      const data = Object.fromEntries(Object.entries(inputs).map(([k, input]) => [k, input.value.trim()]));
      if (!data.name || !data.host) throw new Error('El nombre y el host son obligatorios.');
      data.host = data.host.replace(/^https?:\/\//, '').replace(/[:/].*$/, '');
      data.clear_password = clearPassword.checked;
      data.clear_token = clearToken.checked;
      return data;
    },
    testAfter: () => testAfter.checked,
  };
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
      columns: [
        { key: 'name', label: 'Cliente', render: (c) => h('div', {}, h('div', { class: 'cell-main' }, c.name), state.client === c.name ? h('div', { class: 'cell-sub' }, 'Activo') : null) },
        { key: 'host', label: 'Host', render: (c) => h('span', { class: 'mono' }, c.host) },
        {
          key: 'auth', label: 'Autenticación',
          render: (c) => (c.auth === 'user' ? badge(c.username, '', 'user') : c.auth === 'token' ? badge('Token', 'accent', 'key') : badge('Sin credenciales', 'bad', 'alert')),
        },
        {
          key: 'connected', label: 'Estado', sort: (c) => (c.connected ? 0 : 1),
          render: (c) => (state.connecting === c.name
            ? h('span', { class: 'status' }, h('span', { class: 'spinner' }), 'Conectando…')
            : c.connected
              ? h('div', {}, statusLabel(`Conectado${c.info?.version ? ` · ${c.info.version}` : ''}`, 'good', 'checkCircle'), h('div', { class: 'cell-sub' }, fmtRelative(c.connected_at)))
              : statusLabel('Sin conexión', 'muted')),
        },
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
        subtitle: 'Plataformas Splunk configuradas en config.py. Los cambios también los ven el CLI y la aplicación de escritorio.',
        actions: [button('Nuevo cliente', { variant: 'primary', iconName: 'plus', onClick: () => openClientModal(null) })],
      }),
      table.el);

    if (params.new) openClientModal(null);
    const offs = [on('clients', () => table.setRows(state.clients)), on('connection', () => table.setRows(state.clients))];
    return () => offs.forEach((off) => off());
  },
};
