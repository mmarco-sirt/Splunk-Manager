// Add-ons y apps de Splunkbase: actualizaciones pendientes del cliente, actualización vía ACS (tarea en segundo plano),
// visión de todos los clientes y cuenta de Splunkbase guardada en config.py.
import { get, put, del } from '../api.js';
import { state, on, startJob, selectClient } from '../core.js';
import {
  h, clear, icon, button, badge, pageHeader, DataTable, openModal, confirmDialog, infoBox, errorBox, loadingRows, toast,
  withBusy, fmtRelative,
} from '../ui.js';

const ACCOUNT_SOURCES = { 'config.py': 'guardada en config.py', entorno: 'de las variables de entorno' };

let overviewCache = null;   // último resultado de «todos los clientes»; se conserva al cambiar de sección o de cliente

const manageable = (apps) => apps.filter((a) => a.acs !== false);
const changeText = (a) => `${a.name} ${a.version || '?'} → ${a.new_version}`;
const timeNow = () => new Date().toLocaleTimeString('es', { hour: '2-digit', minute: '2-digit' });

const field = (label, input, hint) => h('div', { class: 'field' }, h('label', {}, label), input,
  hint ? h('span', { class: 'field-hint' }, hint) : null);

function versionCell(version) {
  return version ? h('code', {}, version) : h('span', { class: 'muted' }, '—');
}

function notesCell(app) {
  const items = [];
  if (app.major) items.push(badge('Versión mayor', 'warn', 'alert'));
  if (app.disabled) items.push(badge('Deshabilitada', 'outline'));
  if (app.acs === false) items.push(badge('No gestionable por ACS', 'bad'));
  if (app.acs === null) items.push(badge('ACS sin comprobar', 'outline'));
  return items.length ? h('div', { class: 'badges' }, items) : h('span', { class: 'muted' }, '—');
}

// --- Cuenta de Splunkbase ---------------------------------------------------------------------

function openAccountModal(status, onSaved) {
  const username = h('input', { class: 'input', autocomplete: 'username', value: status?.username || '', placeholder: 'usuario@empresa.com' });
  const password = h('input', { class: 'input', type: 'password', autocomplete: 'current-password' });
  const test = h('input', { type: 'checkbox', checked: true });
  openModal({
    title: status?.saved ? 'Cambiar la cuenta de Splunkbase' : 'Guardar la cuenta de Splunkbase',
    subtitle: 'Usuario y contraseña de splunk.com. ACS la usa para descargar las apps de Splunkbase.',
    body: [
      field('Usuario', username),
      field('Contraseña', password, 'Se guarda cifrada con DPAPI de Windows en config.py, como las credenciales de los clientes.'),
      h('label', { class: 'check' }, test, 'Comprobar la cuenta en Splunkbase antes de guardarla'),
    ],
    actions: [
      { label: 'Cancelar' },
      {
        label: 'Guardar', variant: 'primary', icon: 'save',
        onClick: async () => {
          const data = await put('/api/splunkbase-account', { username: username.value.trim(), password: password.value, test: test.checked });
          toast('Cuenta de Splunkbase guardada.', 'success');
          onSaved(data);
        },
      },
    ],
  });
}

function accountCard(status, reload) {
  let text;
  if (!status) text = h('span', { class: 'muted' }, 'Cargando…');
  else if (status.error) text = h('span', { class: 'status status-bad' }, icon('xCircle'), status.error);
  else if (status.configured) text = [badge(status.username, 'good', 'user'), h('span', { class: 'muted' }, ` · ${ACCOUNT_SOURCES[status.source] || status.source}`)];
  else text = [badge('Sin cuenta', 'warn', 'alert'), h('span', { class: 'muted' }, ' · hace falta para actualizar: con ella ACS descarga la app desde Splunkbase')];

  const actions = status && !status.error ? [
    button(status.saved ? 'Cambiar…' : 'Guardar cuenta…', { iconName: 'key', size: 'sm', variant: status.configured ? '' : 'primary', onClick: () => openAccountModal(status, reload) }),
    status.saved ? button('Quitar', {
      iconName: 'trash', size: 'sm', variant: 'ghost',
      onClick: async () => {
        const ok = await confirmDialog({
          title: '¿Quitar la cuenta de Splunkbase?', message: 'Se borra de config.py. Para volver a actualizar add-ons habrá que guardarla de nuevo.',
          confirmLabel: 'Quitar', danger: true,
        });
        if (!ok) return;
        await del('/api/splunkbase-account');
        toast('Cuenta de Splunkbase quitada.', 'info');
        reload();
      },
    }) : null,
  ] : [];

  return h('div', { class: 'card' },
    h('div', { class: 'card-header' },
      h('div', {}, h('h2', { class: 'card-title' }, 'Cuenta de Splunkbase'), h('p', { class: 'card-subtitle' }, text)),
      h('div', { class: 'page-actions' }, actions)),
    status?.problem ? h('div', { class: 'card-body' }, infoBox(status.problem, 'warn-box')) : null);
}

// --- Actualizar ------------------------------------------------------------------------------------

async function updateApps(apps, client) {
  const list = manageable(apps);
  if (!list.length) {
    toast('Ninguna de las apps seleccionadas se puede actualizar desde ACS.', 'warn');
    return;
  }
  const majors = list.filter((a) => a.major);
  const ok = await confirmDialog({
    title: list.length === 1 ? `¿Actualizar ${list[0].name} en ${client}?` : `¿Actualizar ${list.length} apps en ${client}?`,
    confirmLabel: 'Actualizar',
    message: h('div', {},
      h('ul', { style: { margin: '0 0 12px 18px', padding: 0 } }, list.map((a) => h('li', {}, changeText(a)))),
      majors.length ? infoBox(`${majors.length === 1 ? 'Una de ellas cambia' : `${majors.length} de ellas cambian`} de versión mayor: conviene revisar antes sus notas de la versión en Splunkbase.`, 'warn-box') : null,
      h('p', { style: { margin: '12px 0 0' } }, 'Se actualizan de una en una a través de ACS, esperando a que termine cada una, igual que el botón «Update» de Splunk Web. Antes se guarda la lista de versiones instaladas en backups/addons/.')),
  });
  if (!ok) return;
  await startJob('/api/addons/update', { client, names: list.map((a) => a.name) });
}

/** Actualiza todas las pendientes (gestionables por ACS) en varios clientes, con opción de excluir versiones mayores. */
async function updateMany(rows) {
  const ready = rows.filter((r) => !r.error && r.acs?.available && manageable(r.pending).length);
  if (!ready.length) {
    toast('Ninguno de los clientes seleccionados tiene actualizaciones gestionables por ACS.', 'warn');
    return;
  }
  const majors = ready.reduce((n, r) => n + manageable(r.pending).filter((a) => a.major).length, 0);
  const excludeMajor = h('input', { type: 'checkbox', checked: majors > 0 });
  let go = false;
  const modal = openModal({
    title: `¿Actualizar add-ons en ${ready.length === 1 ? ready[0].client : `${ready.length} clientes`}?`,
    size: 'lg',
    body: [
      h('ul', { class: 'list-plain', style: { marginBottom: '12px' } }, ready.map((r) => h('li', {}, h('strong', {}, r.client), ': ', manageable(r.pending).map(changeText).join(', ')))),
      majors ? h('label', { class: 'check', style: { marginBottom: '10px' } }, excludeMajor, `Dejar fuera las ${majors} que cambian de versión mayor (conviene revisar antes sus notas de la versión)`) : null,
      h('p', { class: 'muted', style: { margin: 0 } }, 'Se actualizan cliente a cliente y app a app a través de ACS, esperando a que termine cada una. En cada cliente se guarda antes la lista de versiones instaladas en backups/addons/.'),
    ],
    actions: [{ label: 'Cancelar' }, { label: 'Actualizar', variant: 'primary', icon: 'download', onClick: () => { go = true; } }],
  });
  modal.onClose = async () => {
    if (!go) return;
    await startJob('/api/addons/update-many', { clients: ready.map((r) => r.client), names: ['todos'], exclude_major: excludeMajor.checked });
  };
}

// --- Todos los clientes -----------------------------------------------------------------------------

function overviewCard() {
  const body = h('div');
  const stamp = h('span', { class: 'muted', style: { fontSize: '12.5px' } });
  const runBtn = button('Comprobar todos los clientes', { iconName: 'refresh' });

  function render() {
    clear(body);
    if (!overviewCache) {
      body.append(h('p', { class: 'muted', style: { margin: 0 } }, 'Pulse «Comprobar todos los clientes» para ver qué add-ons tienen actualización pendiente en cada cliente. Conecta con todos, así que puede tardar alrededor de un minuto.'));
      return;
    }
    stamp.textContent = `Datos ${fmtRelative(overviewCache.at / 1000)}`;
    const table = new DataTable({
      rows: overviewCache.rows, rowKey: (r) => r.client, compact: true, selectable: true,
      searchText: (r) => `${r.client} ${r.host} ${r.pending.map((a) => a.name).join(' ')}`,
      searchPlaceholder: 'Buscar cliente o app…',
      initialSort: { key: 'count', dir: 'desc' },
      selectionActions: [{ label: 'Actualizar pendientes en los seleccionados', icon: 'download', variant: 'primary', onClick: (sel) => updateMany(sel) }],
      filters: [{
        id: 'state',
        options: [
          { value: 'all', label: 'Todos' },
          { value: 'pending', label: 'Con pendientes', test: (r) => r.pending.length > 0 },
          { value: 'ok', label: 'Al día', test: (r) => !r.error && !r.pending.length },
          { value: 'error', label: 'Con error', test: (r) => Boolean(r.error) },
        ],
      }],
      columns: [
        { key: 'client', label: 'Cliente', render: (r) => h('div', {}, h('div', { class: 'cell-main' }, r.client), h('div', { class: 'cell-sub' }, r.host || '')) },
        {
          key: 'count', label: 'Pendientes', sort: (r) => (r.error ? -1 : r.pending.length),
          render: (r) => (r.error ? badge('Error', 'bad', 'xCircle') : r.pending.length ? badge(String(r.pending.length), 'warn') : badge('Al día', 'good', 'check')),
        },
        {
          key: 'apps', label: 'Apps', sortable: false,
          render: (r) => {
            if (r.error) return h('span', { class: 'muted' }, r.error);
            const warnings = (r.warnings || []).map((w) => w.replace(/^⚠️\s*/, ''));
            const apps = r.pending.length ? h('div', { class: 'badges' }, r.pending.map((a) => badge(changeText(a), a.acs === false ? 'outline' : a.major ? 'warn' : ''))) : null;
            if (!apps && !warnings.length) return h('span', { class: 'muted' }, '—');
            return h('div', {}, apps, warnings.map((w) => h('div', { class: 'cell-sub', style: { marginTop: apps ? '4px' : 0 } }, w)));
          },
        },
        {
          key: 'acs', label: 'ACS', sort: (r) => (r.acs?.available ? (r.warnings?.length ? 1 : 2) : 0),
          render: (r) => {
            if (!r.acs) return h('span', { class: 'muted' }, '—');
            if (!r.acs.available) return h('span', { class: 'muted', title: r.acs.reason || '' }, 'No disponible');
            const warnings = (r.warnings || []).map((w) => w.replace(/^⚠️\s*/, ''));
            return warnings.length ? badge('Con avisos', 'warn', 'alert') : badge('Disponible', 'good');
          },
        },
      ],
      onRowClick: (r) => { if (r.client !== state.client) selectClient(r.client); window.scrollTo(0, 0); },
      emptyTitle: 'No hay clientes configurados',
    });
    body.append(table.el);
  }

  runBtn.addEventListener('click', () => withBusy(runBtn, async () => {
    clear(body).append(h('p', { class: 'muted' }, 'Consultando todos los clientes…'), loadingRows(4));
    try {
      overviewCache = { at: Date.now(), rows: await get('/api/addons/overview') };
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
        h('p', { class: 'card-subtitle' }, 'Qué add-ons tienen actualización pendiente en cada cliente. Pulse una fila para pasar a ese cliente.')),
      h('div', { class: 'page-actions' }, stamp, runBtn)),
    h('div', { class: 'card-body' }, body));
}

// --- Vista -------------------------------------------------------------------------------------------

export default {
  id: 'addons',
  title: 'Add-ons',
  icon: 'package',
  needsClient: true,
  render(root) {
    const client = state.client;
    let data = null;
    let account = null;
    let table = null;

    const stamp = h('span', { class: 'muted', style: { fontSize: '12.5px' } });
    const accountSlot = h('div');
    const noticeSlot = h('div');
    const tableSlot = h('div', { style: { marginTop: '16px' } }, loadingRows(5));
    const updateAllBtn = button('Actualizar todas', { iconName: 'download', variant: 'primary', disabled: true });
    const refreshBtn = button('Comprobar', { iconName: 'refresh' });

    const renderNotices = () => {
      clear(noticeSlot);
      if (data && !data.acs.available) {
        noticeSlot.append(infoBox(`ACS no está disponible para ${client}: ${data.acs.reason}. Estas actualizaciones hay que hacerlas desde Splunk Web.`, 'warn-box'));
      } else if (account && !account.configured && data?.pending.length) {
        noticeSlot.append(infoBox('Para actualizar hace falta la cuenta de Splunkbase: guárdela en la tarjeta de arriba.', 'warn-box'));
      }
      for (const warning of data?.warnings || []) noticeSlot.append(infoBox(warning.replace(/^⚠️\s*/, ''), 'warn-box'));
      if (noticeSlot.childElementCount) noticeSlot.style.marginTop = '16px';
      updateAllBtn.disabled = !(data?.acs.available && account?.configured && manageable(data.pending).length);
    };

    const loadAccount = async () => {
      try {
        account = await get('/api/splunkbase-account');
      } catch (e) {
        account = { configured: false, saved: false, error: e.message };
      }
      clear(accountSlot).append(accountCard(account, loadAccount));
      renderNotices();
    };

    const load = async () => {
      try {
        data = await get('/api/addons/pending', { client });
        stamp.textContent = `Comprobado a las ${timeNow()}`;
        if (!table) {
          table = new DataTable({
            rows: data.pending, selectable: true, rowKey: (a) => a.name,
            searchText: (a) => `${a.name} ${a.label}`, searchPlaceholder: 'Buscar app…',
            initialSort: { key: 'name', dir: 'asc' },
            emptyIcon: 'package', emptyTitle: 'No hay actualizaciones pendientes',
            emptyText: `Splunk no ha detectado ninguna versión nueva en Splunkbase para las apps de ${client}.`,
            columns: [
              { key: 'name', label: 'App', render: (a) => h('div', {}, h('div', { class: 'cell-main' }, a.label || a.name), h('div', { class: 'cell-sub' }, a.name)) },
              { key: 'version', label: 'Instalada', render: (a) => versionCell(a.version) },
              { key: 'new_version', label: 'Nueva', render: (a) => versionCell(a.new_version) },
              { key: 'notes', label: 'Notas', sortable: false, render: notesCell },
              {
                key: 'homepage', label: '', sortable: false, className: 'col-actions',
                render: (a) => (a.homepage ? h('a', { href: a.homepage, target: '_blank', rel: 'noopener', title: 'Ver en Splunkbase', 'aria-label': `Ver ${a.name} en Splunkbase` }, icon('external')) : null),
              },
            ],
            selectionActions: [{ label: 'Actualizar seleccionadas', icon: 'download', variant: 'primary', onClick: (sel) => updateApps(sel, client) }],
          });
          clear(tableSlot).append(table.el);
        } else {
          table.setRows(data.pending);
        }
      } catch (e) {
        clear(tableSlot).append(errorBox(e.message));
      }
      renderNotices();
    };

    refreshBtn.addEventListener('click', () => withBusy(refreshBtn, load));
    updateAllBtn.addEventListener('click', () => withBusy(updateAllBtn, () => updateApps(data?.pending || [], client)));

    clear(root).append(
      pageHeader({
        title: 'Add-ons y apps de Splunkbase',
        subtitle: `Apps de ${client} con una versión nueva en Splunkbase (las mismas del aviso «Update» de Apps → Manage Apps) y su actualización vía ACS, el servicio de administración de Splunk Cloud.`,
        actions: [stamp, refreshBtn, updateAllBtn],
      }),
      accountSlot, noticeSlot, tableSlot, overviewCard());

    loadAccount();
    load();
    // Al terminar una actualización de este cliente se vuelve a comprobar qué queda pendiente
    return on('job-finished', (job) => {
      if (job.kind === 'addons') {
        if (job.clients.includes(client)) load();
        overviewCache = null;
      }
    });
  },
};
