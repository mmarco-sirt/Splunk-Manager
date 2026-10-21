// Vistas de listado de objetos de Splunk: macros, dashboards, usuarios, roles, índices y apps.
import { state, loadResource, cachedAt, navigate } from '../core.js';
import {
  h, clear, button, badge, pageHeader, DataTable, openDrawer, kv, section, codeBlock, errorBox, loadingRows,
  fmtNum, fmtMB, fmtRelative, fmtDate, withBusy, confirmDialog, toast,
} from '../ui.js';
import { openRestoreModal, runBackup } from '../ops.js';
import { mergeRows } from '../alert-editor.js';
import { openMacroEditor, setMacrosEnabled } from '../macro-editor.js';

/**
 * Fábrica de vistas de listado con caché por cliente, botón de actualizar y tabla.
 * 'table' puede ser una función (ctx) => opciones, donde ctx.refresh() vuelve a pintar la tabla tras cambiar filas.
 */
function listView({ id, title, iconName, resource, subtitle, table: tableOpts, actions = () => [], prepare = (rows) => rows }) {
  return {
    id, title, icon: iconName, needsClient: true,
    render(root, params) {
      const body = h('div', {}, loadingRows(8));
      const stamp = h('span', { class: 'muted', style: { fontSize: '12.5px' } });
      let table = null;
      const opts = typeof tableOpts === 'function' ? tableOpts({ refresh: () => table?.render() }) : tableOpts;

      const load = async (force = false) => {
        try {
          const rows = await prepare(await loadResource(resource, { force }), { force });
          stamp.textContent = `Datos ${fmtRelative(cachedAt(resource) / 1000)}`;
          if (!table) {
            table = new DataTable({ emptyIcon: iconName, emptyTitle: `No hay ${title.toLowerCase()}`, initialQuery: params.q || '', ...opts, rows });
            clear(body).append(table.el);
          } else {
            table.setRows(rows);
          }
        } catch (e) {
          clear(body).append(errorBox(e.message));
        }
      };

      const refreshBtn = button('Actualizar', { iconName: 'refresh' });
      refreshBtn.addEventListener('click', () => withBusy(refreshBtn, () => load(true)));
      clear(root).append(pageHeader({ title, subtitle: subtitle(), actions: [stamp, refreshBtn, ...actions()] }), body);
      load();
    },
  };
}

const list = (values, max = 4) => {
  if (!values?.length) return h('span', { class: 'muted' }, '—');
  const shown = values.slice(0, max).map((v) => badge(v));
  if (values.length > max) shown.push(badge(`+${values.length - max}`, 'outline'));
  return h('div', { class: 'badges' }, shown);
};

const backupButton = (label, kind) => button(label, { iconName: 'download', variant: 'primary', onClick: () => runBackup(kind) });
const restoreButton = (folder) => button('Restaurar…', { iconName: 'restore', onClick: () => openRestoreModal({ folder }) });

// --- Macros ------------------------------------------------------------------

const macroState = (m) => (m.disabled ? badge('Deshabilitada', 'outline') : badge('Habilitada', 'good', 'check'));

/** Habilita o deshabilita (con backup previo); al deshabilitar avisa de que las búsquedas que la usan fallarán. */
async function toggleMacros(rows, enabled, ctx) {
  if (!enabled) {
    const ok = await confirmDialog({
      title: rows.length === 1 ? `¿Deshabilitar «${rows[0].name}»?` : `¿Deshabilitar ${fmtNum(rows.length)} macros?`,
      message: `Mientras estén deshabilitadas, las búsquedas y alertas de ${state.client} que las usen fallarán. Antes se guarda su configuración en Backups.`,
      confirmLabel: 'Deshabilitar', danger: true,
    });
    if (!ok) return false;
  }
  try {
    mergeRows(rows, await setMacrosEnabled(rows, enabled));
  } catch (e) {
    toast(e.message, 'error');
  }
  ctx.refresh();
  return true;
}

function editMacro(m, ctx, { reopen = false } = {}) {
  openMacroEditor(m, {
    onSaved: (updated) => {
      Object.assign(m, updated);
      ctx.refresh();
      if (reopen) macroDetail(m, ctx);
    },
  });
}

// Alertas, reportes y macros que la llaman (`nombre` o `nombre(...)`), con los listados de la caché
async function macroUsage(m) {
  const base = m.name.replace(/\(\d+\)$/, '');
  const uses = (text) => String(text || '').includes(`\`${base}\``) || String(text || '').includes(`\`${base}(`);
  const [searches, macros] = await Promise.all([loadResource('saved-searches'), loadResource('macros')]);
  return {
    searches: searches.filter((s) => uses(s.search)),
    macros: macros.filter((other) => other.name !== m.name && uses(other.definition)),
  };
}

function macroDetail(m, ctx) {
  const editBtn = button('Editar', { iconName: 'edit', size: 'sm', variant: 'primary', onClick: () => editMacro(m, ctx, { reopen: true }) });
  const toggleBtn = button(m.disabled ? 'Habilitar' : 'Deshabilitar', { iconName: 'power', size: 'sm' });
  toggleBtn.addEventListener('click', () => withBusy(toggleBtn, async () => {
    if (await toggleMacros([m], m.disabled, ctx)) drawer.close();
  }));
  const usage = h('div', {}, loadingRows(2));
  const drawer = openDrawer({
    title: m.args ? `${m.name}(${m.args})` : m.name,
    subtitle: [m.app, m.owner, m.sharing].filter(Boolean).join(' · '),
    actions: [editBtn, toggleBtn],
    body: [
      h('div', { class: 'badges', style: { marginBottom: '16px' } }, macroState(m), m.iseval ? badge('Expresión eval', 'accent') : null),
      section('Definición', codeBlock(m.definition)),
      section('Detalles', kv([['Argumentos', m.args || null], ['Descripción', m.description || null], ['App', m.app], ['Propietario', m.owner], ['Compartición', m.sharing]])),
      section('Dónde se usa', usage),
    ],
  });
  macroUsage(m).then(({ searches, macros }) => {
    const link = (label, onClick) => h('li', {}, h('a', { href: '#', onclick: (e) => { e.preventDefault(); drawer.close(); onClick(); } }, label));
    const total = searches.length + macros.length;
    clear(usage).append(total
      ? h('ul', { class: 'list-plain' },
        searches.slice(0, 50).map((s) => link(`${s.name}${s.disabled ? ' (deshabilitada)' : ''}`, () => navigate(s.is_alert || s.is_es ? 'alerts' : 'reports', { q: s.name }))),
        macros.slice(0, 50).map((other) => link(`Macro ${other.name}`, () => navigate('macros', { q: other.name }))),
        total > 100 ? h('li', { class: 'muted' }, 'y más…') : null)
      : h('p', { class: 'muted', style: { margin: 0 } }, 'Ninguna alerta, reporte ni macro de este cliente la usa (los dashboards no se revisan).'));
  }).catch((e) => clear(usage).append(errorBox(e.message)));
}

export const macrosView = listView({
  id: 'macros', title: 'Macros', iconName: 'braces', resource: 'macros',
  subtitle: () => `Macros de búsqueda de ${state.client}. Pulse una fila para ver su definición y dónde se usa, o edítela con el lápiz.`,
  actions: () => [restoreButton('macros'), backupButton('Backup de macros', 'macros')],
  table: (ctx) => ({
    rowKey: (m) => `${m.app}/${m.owner}/${m.name}`,
    searchText: (m) => `${m.name} ${m.definition} ${m.app}`,
    searchPlaceholder: 'Buscar por nombre o definición…',
    initialSort: { key: 'name', dir: 'asc' },
    selectable: true,
    onRowClick: (m) => macroDetail(m, ctx),
    selectionActions: [
      { label: 'Habilitar', icon: 'power', onClick: (sel) => toggleMacros(sel, true, ctx) },
      { label: 'Deshabilitar', icon: 'power', onClick: (sel) => toggleMacros(sel, false, ctx) },
    ],
    filters: [
      {
        id: 'sharing',
        options: [
          { value: 'all', label: 'Todas' },
          { value: 'global', label: 'Globales', test: (m) => m.sharing === 'global' },
          { value: 'app', label: 'De app', test: (m) => m.sharing === 'app' },
          { value: 'user', label: 'Privadas', test: (m) => m.sharing === 'user' },
        ],
      },
      {
        id: 'state',
        options: [
          { value: 'all', label: 'Todas' },
          { value: 'on', label: 'Habilitadas', test: (m) => !m.disabled },
          { value: 'off', label: 'Deshabilitadas', test: (m) => m.disabled },
        ],
      },
    ],
    columns: [
      { key: 'name', label: 'Nombre', render: (m) => h('div', {}, h('div', { class: 'cell-main' }, m.name), m.args ? h('div', { class: 'cell-sub' }, `(${m.args})`) : null) },
      { key: 'definition', label: 'Definición', render: (m) => h('div', { class: 'cell-code', title: m.definition }, m.definition) },
      { key: 'app', label: 'App' },
      { key: 'sharing', label: 'Compartición', render: (m) => badge(m.sharing || '—') },
      { key: 'disabled', label: 'Estado', render: macroState, sort: (m) => (m.disabled ? 1 : 0) },
      {
        key: 'actions', label: '', className: 'col-actions', sortable: false,
        render: (m) => {
          const toggle = button('', { size: 'sm', variant: 'ghost', iconName: 'power', title: m.disabled ? 'Habilitar' : 'Deshabilitar' });
          toggle.addEventListener('click', () => withBusy(toggle, () => toggleMacros([m], m.disabled, ctx)));
          return h('div', { class: 'row-actions' },
            button('', { size: 'sm', variant: 'ghost', iconName: 'edit', title: 'Editar', onClick: () => editMacro(m, ctx) }),
            toggle,
            button('', { size: 'sm', variant: 'ghost', iconName: 'eye', title: 'Ver detalle', onClick: () => macroDetail(m, ctx) }));
        },
      },
    ],
  }),
});

// --- Dashboards --------------------------------------------------------------

export const dashboardsView = listView({
  id: 'dashboards', title: 'Dashboards', iconName: 'dashboard', resource: 'dashboards',
  subtitle: () => `Dashboards de todas las apps de ${state.client}. Seleccione los que quiera respaldar.`,
  actions: () => [restoreButton('dashboards')],
  table: {
    rowKey: (d) => `${d.app}/${d.owner}/${d.name}`,
    searchText: (d) => `${d.name} ${d.label} ${d.app}`,
    searchPlaceholder: 'Buscar por nombre, título o app…',
    selectable: true,
    initialSort: { key: 'label', dir: 'asc' },
    selectionActions: [{
      label: 'Backup de la selección', icon: 'download',
      onClick: (sel) => runBackup('dashboards', { names: [...new Set(sel.map((d) => d.name))] }),
    }],
    filters: [{
      id: 'sharing',
      options: [
        { value: 'all', label: 'Todos' },
        { value: 'shared', label: 'Compartidos', test: (d) => d.sharing !== 'user' },
        { value: 'private', label: 'Privados', test: (d) => d.sharing === 'user' },
      ],
    }],
    columns: [
      { key: 'label', label: 'Dashboard', render: (d) => h('div', {}, h('div', { class: 'cell-main' }, d.label), h('div', { class: 'cell-sub' }, d.name)) },
      { key: 'app', label: 'App' },
      { key: 'owner', label: 'Propietario' },
      { key: 'sharing', label: 'Compartición', render: (d) => badge(d.sharing || '—') },
      { key: 'version', label: 'Tipo', render: (d) => (d.version === '2' ? badge('Dashboard Studio', 'accent') : badge(d.version ? `XML v${d.version}` : 'XML')) },
      { key: 'updated', label: 'Actualizado', defaultDir: 'desc', render: (d) => h('span', { title: fmtDate(d.updated) }, fmtRelative(d.updated)) },
      {
        key: 'actions', label: '', className: 'col-actions', sortable: false,
        render: (d) => button('', { size: 'sm', variant: 'ghost', iconName: 'download', title: 'Backup', onClick: () => runBackup('dashboards', { names: [d.name] }) }),
      },
    ],
  },
});

// --- Usuarios ----------------------------------------------------------------

function userDetail(u) {
  openDrawer({
    title: u.realname || u.name,
    subtitle: u.name,
    body: [
      section('Datos', kv([
        ['Usuario', u.name], ['Nombre', u.realname || null], ['Email', u.email || null], ['Tipo', u.type || null],
        ['App por defecto', u.default_app || null], ['Último acceso', u.last_login ? fmtDate(Number(u.last_login)) : null],
        ['Bloqueado', u.locked ? badge('Sí', 'bad') : 'No'],
      ])),
      section('Roles', list(u.roles, 50)),
    ],
  });
}

export const usersView = listView({
  id: 'users', title: 'Usuarios', iconName: 'users', resource: 'users',
  subtitle: () => `Usuarios de ${state.client} con sus roles.`,
  actions: () => [restoreButton('users'), backupButton('Backup de usuarios', 'users')],
  table: {
    searchText: (u) => `${u.name} ${u.realname} ${u.email} ${u.roles.join(' ')}`,
    searchPlaceholder: 'Buscar por usuario, nombre, email o rol…',
    initialSort: { key: 'name', dir: 'asc' },
    onRowClick: userDetail,
    filters: [{
      id: 'type',
      options: [
        { value: 'all', label: 'Todos' },
        { value: 'admin', label: 'Administradores', test: (u) => u.roles.some((r) => /admin/i.test(r)) },
        { value: 'sso', label: 'SAML/LDAP', test: (u) => /saml|ldap/i.test(u.type) },
        { value: 'local', label: 'Locales', test: (u) => !/saml|ldap/i.test(u.type) },
      ],
    }],
    columns: [
      { key: 'name', label: 'Usuario', render: (u) => h('div', {}, h('div', { class: 'cell-main' }, u.name), u.realname ? h('div', { class: 'cell-sub' }, u.realname) : null) },
      { key: 'email', label: 'Email', render: (u) => u.email || h('span', { class: 'muted' }, '—') },
      { key: 'roles', label: 'Roles', render: (u) => list(u.roles), sort: (u) => u.roles.join(',') },
      { key: 'type', label: 'Tipo', render: (u) => (u.type ? badge(u.type) : h('span', { class: 'muted' }, '—')) },
      { key: 'last_login', label: 'Último acceso', defaultDir: 'desc', sort: (u) => Number(u.last_login) || 0, render: (u) => (u.last_login ? h('span', { title: fmtDate(Number(u.last_login)) }, fmtRelative(Number(u.last_login))) : h('span', { class: 'muted' }, '—')) },
    ],
  },
});

// --- Roles -------------------------------------------------------------------

function roleDetail(r) {
  openDrawer({
    title: r.name,
    subtitle: r.users !== undefined ? `${fmtNum(r.users)} usuarios con este rol` : '',
    body: [
      section('Herencia', list(r.imported_roles, 50)),
      section('Índices', kv([['Permitidos', list(r.indexes_allowed, 50)], ['Por defecto', list(r.indexes_default, 50)]])),
      section('Límites', kv([['Búsquedas simultáneas', r.jobs_quota || null], ['Disco (MB)', r.disk_quota || null], ['Ventana de tiempo (s)', r.time_window || null], ['App por defecto', r.default_app || null]])),
      section(`Capacidades propias (${r.capabilities.length})`, list(r.capabilities, 500)),
      r.imported_capabilities.length ? section(`Capacidades heredadas (${r.imported_capabilities.length})`, list(r.imported_capabilities, 500)) : null,
    ],
  });
}

export const rolesView = listView({
  id: 'roles', title: 'Roles', iconName: 'shield', resource: 'roles',
  subtitle: () => `Roles de ${state.client}: herencia, índices y capacidades.`,
  actions: () => [restoreButton('roles'), backupButton('Backup de roles', 'roles')],
  // Se añade el nº de usuarios por rol si se pueden cargar los usuarios
  prepare: async (roles, { force }) => {
    try {
      const users = await loadResource('users', { force });
      return roles.map((r) => ({ ...r, users: users.filter((u) => u.roles.includes(r.name)).length }));
    } catch {
      return roles;
    }
  },
  table: {
    searchText: (r) => `${r.name} ${r.imported_roles.join(' ')} ${r.indexes_allowed.join(' ')}`,
    searchPlaceholder: 'Buscar por rol, herencia o índice…',
    initialSort: { key: 'name', dir: 'asc' },
    onRowClick: roleDetail,
    columns: [
      { key: 'name', label: 'Rol', render: (r) => h('span', { class: 'cell-main' }, r.name) },
      { key: 'imported_roles', label: 'Hereda de', render: (r) => list(r.imported_roles, 3), sort: (r) => r.imported_roles.join(',') },
      { key: 'indexes_allowed', label: 'Índices permitidos', render: (r) => list(r.indexes_allowed, 3), sort: (r) => r.indexes_allowed.length },
      { key: 'capabilities', label: 'Capacidades', className: 'num', defaultDir: 'desc', sort: (r) => r.capabilities.length, render: (r) => fmtNum(r.capabilities.length) },
      { key: 'users', label: 'Usuarios', className: 'num', defaultDir: 'desc', render: (r) => (r.users === undefined ? '—' : fmtNum(r.users)) },
    ],
  },
});

// --- Índices -----------------------------------------------------------------

const fmtPct = (n) => (n > 0 && n < 0.01 ? '< 0,01 %' : `${n.toLocaleString('es-ES', { maximumFractionDigits: n < 1 ? 2 : 1 })} %`);

function sizeCell(r) {
  if (r.size_mb === null) {
    return h('span', { class: 'muted', title: 'Splunk Cloud no expone el tamaño de los índices internos' }, '—');
  }
  const kind = r.size_kind === 'raw' ? ' sin comprimir' : '';
  // Con límite de tamaño: ocupación respecto al límite. Sin límite (Splunk Cloud): peso sobre el total del cliente
  const limited = r.max_size_mb > 0;
  const pct = limited ? Math.min(100, (r.size_mb / r.max_size_mb) * 100) : r.share * 100;
  const title = limited
    ? `${fmtMB(r.size_mb)}${kind} de ${fmtMB(r.max_size_mb)} (${fmtPct(pct)})`
    : `${fmtMB(r.size_mb)}${kind} · ${fmtPct(pct)} del volumen total de los índices`;
  return h('div', { title, style: { minWidth: '150px' } },
    h('div', { class: 'num', style: { textAlign: 'left' } }, fmtMB(r.size_mb),
      h('span', { class: 'muted' }, limited ? ` / ${fmtMB(r.max_size_mb)}` : ` · ${fmtPct(pct)}`)),
    h('div', { class: `meter ${limited && pct > 90 ? 'bad' : limited && pct > 75 ? 'warn' : ''}`, style: { marginTop: '4px' } },
      h('span', { style: { width: `${Math.max(pct, 0.5)}%` } })));
}

function retentionCell(r) {
  if (!r.retention_days) return h('span', { class: 'muted' }, '—');
  return h('div', {}, `${fmtNum(r.retention_days)} días`,
    r.archive_days ? h('div', { class: 'cell-sub', title: 'Archivado activo (DDAA)' }, `+ ${fmtNum(r.archive_days)} días de archivo`) : null);
}

export const indexesView = listView({
  id: 'indexes', title: 'Índices', iconName: 'database', resource: 'indexes',
  subtitle: () => `Índices de ${state.client}: eventos, volumen, retención y archivado. En Splunk Cloud el tamaño es el de los datos sin comprimir y los índices internos no lo exponen.`,
  prepare: (rows) => {
    const total = rows.reduce((sum, r) => sum + (r.size_mb || 0), 0);
    return rows.map((r) => ({ ...r, share: total && r.size_mb ? r.size_mb / total : 0 }));
  },
  table: {
    searchText: (r) => r.name,
    searchPlaceholder: 'Buscar índice…',
    initialSort: { key: 'size_mb', dir: 'desc' },
    filters: [{
      id: 'kind',
      value: 'own',
      options: [
        { value: 'own', label: 'Propios', test: (r) => !r.name.startsWith('_') },
        { value: 'internal', label: 'Internos', test: (r) => r.name.startsWith('_') },
        { value: 'all', label: 'Todos' },
      ],
    }],
    columns: [
      { key: 'name', label: 'Índice', render: (r) => h('div', {}, h('span', { class: 'cell-main' }, r.name), r.disabled ? h('div', {}, badge('Deshabilitado', 'outline')) : null) },
      { key: 'datatype', label: 'Tipo', render: (r) => badge(r.datatype === 'metric' ? 'Métricas' : 'Eventos', r.datatype === 'metric' ? 'accent' : '') },
      { key: 'events', label: 'Eventos', className: 'num', defaultDir: 'desc', render: (r) => fmtNum(r.events) },
      { key: 'size_mb', label: 'Tamaño', defaultDir: 'desc', sort: (r) => r.size_mb ?? -1, render: sizeCell },
      { key: 'retention_days', label: 'Retención', defaultDir: 'desc', render: retentionCell },
      { key: 'max_time', label: 'Último evento', defaultDir: 'desc', sort: (r) => r.max_time || 0, render: (r) => (r.max_time ? h('span', { title: fmtDate(r.max_time) }, fmtRelative(r.max_time)) : h('span', { class: 'muted' }, '—')) },
    ],
  },
});

// --- Apps --------------------------------------------------------------------

export const appsView = listView({
  id: 'apps', title: 'Apps', iconName: 'apps', resource: 'apps',
  subtitle: () => `Apps y add-ons instalados en ${state.client}.`,
  table: {
    searchText: (a) => `${a.name} ${a.label} ${a.author}`,
    searchPlaceholder: 'Buscar app…',
    initialSort: { key: 'label', dir: 'asc' },
    filters: [{
      id: 'state',
      options: [
        { value: 'all', label: 'Todas' },
        { value: 'on', label: 'Habilitadas', test: (a) => !a.disabled },
        { value: 'off', label: 'Deshabilitadas', test: (a) => a.disabled },
        { value: 'visible', label: 'Visibles', test: (a) => a.visible },
      ],
    }],
    columns: [
      { key: 'label', label: 'App', render: (a) => h('div', {}, h('div', { class: 'cell-main' }, a.label), h('div', { class: 'cell-sub' }, a.name)) },
      { key: 'version', label: 'Versión', render: (a) => (a.version ? h('code', {}, a.version) : h('span', { class: 'muted' }, '—')) },
      { key: 'author', label: 'Autor', render: (a) => a.author || h('span', { class: 'muted' }, '—') },
      { key: 'visible', label: 'Visible', render: (a) => (a.visible ? 'Sí' : h('span', { class: 'muted' }, 'No')) },
      { key: 'disabled', label: 'Estado', render: (a) => (a.disabled ? badge('Deshabilitada', 'outline') : badge('Habilitada', 'good', 'check')) },
    ],
  },
});
