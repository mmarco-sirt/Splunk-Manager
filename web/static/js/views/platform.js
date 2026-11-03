// Plataforma (ACS): estado del stack y reinicio, ventanas de mantenimiento y change freezes, tokens de autenticación
// (caducidad, alta con guardado en el cliente y baja) y exportación de apps como .tgz. Vista del cliente y de todos.
import { get, post, del, enc } from '../api.js';
import { state, on, startJob, selectClient, loadClients, loadResource } from '../core.js';
import {
  h, clear, icon, button, badge, pageHeader, DataTable, openModal, confirmDialog, infoBox, errorBox, loadingRows, toast,
  withBusy, kv, section, codeBlock, fmtRelative, fmtDate, fmtBytes, fmtNum, segmented,
} from '../ui.js';

const KIND_LABELS = { cloud: 'Splunk Cloud', enterprise: 'Splunk Enterprise', unknown: 'Desconocido' };
const LEVEL_UI = { expired: ['Caducado', 'bad'], serious: ['Caduca en menos de 7 días', 'serious'], warning: ['Caduca en menos de 30 días', 'warn'], ok: ['Vigente', 'good'] };

/** Aviso de que el cliente no admite ACS (Enterprise, sin token o token caducado). */
export function acsNotice(client, platform) {
  const kind = KIND_LABELS[platform?.kind] || 'Desconocido';
  const reason = platform?.reason || 'ACS no está disponible';
  return h('div', { class: 'card' }, h('div', { class: 'card-body' },
    h('div', { class: 'empty' }, icon('cloudOff'), h('h3', {}, `Sin ACS en ${client}`),
      h('p', {}, `${kind}: ${reason}. ACS (Admin Config Service) solo existe en Splunk Cloud y necesita el token JWT de un usuario con el rol sc_admin.`))));
}

/** Resultado de una simulación: la petición que se habría enviado a ACS. */
export function simulationBox(result) {
  const req = result.request || {};
  return h('div', { style: { marginTop: '12px' } },
    infoBox(result.message || 'Simulación: no se ha enviado nada a ACS.'),
    codeBlock(`${req.method || ''} ${req.url || ''}\n${req.body === undefined || req.body === null ? '' : JSON.stringify(req.body, null, 2)}`.trim()));
}

export function platformBadges(p) {
  if (!p) return h('span', { class: 'muted' }, '—');
  const items = [];
  if (p.kind === 'cloud') items.push(badge(p.experience ? `Cloud ${p.experience[0].toUpperCase()}${p.experience.slice(1)}` : 'Splunk Cloud', 'accent', 'cloud'));
  else if (p.kind === 'enterprise') items.push(badge('Enterprise', '', 'server'));
  else items.push(badge('Desconocido', 'outline'));
  if (p.kind === 'cloud') items.push(p.acs ? badge('ACS', 'good', 'check') : badge('Sin ACS', 'warn', 'alert'));
  if (p.restart_required) items.push(badge('Reinicio pendiente', 'warn', 'power'));
  return h('div', { class: 'badges', title: p.reason || '' }, items);
}

export function tokenBadge(own) {
  if (!own?.present) return badge('Sin token', 'outline');
  if (!own.expiry) return badge('Token sin caducidad', '');
  const [text, variant] = LEVEL_UI[own.level] || LEVEL_UI.ok;
  return badge(`${text} · ${fmtDate(own.expiry)}`, variant, own.level === 'ok' ? 'key' : 'alert');
}

const when = (epoch) => (epoch ? h('span', { title: fmtDate(epoch) }, fmtDate(epoch)) : h('span', { class: 'muted' }, '—'));

// --- Tokens --------------------------------------------------------------------------------------------------

function openTokenModal(client, info, onDone) {
  const username = h('input', { class: 'input', value: info.username || '', autocomplete: 'username', placeholder: 'usuario sc_admin del stack' });
  const password = h('input', { class: 'input', type: 'password', autocomplete: 'current-password', placeholder: info.has_password ? '•••••••• (la guardada del cliente)' : '' });
  const user = h('input', { class: 'input', value: info.username || '', placeholder: 'Usuario al que pertenece el token' });
  const audience = h('input', { class: 'input', value: 'Splunk Manager' });
  const expires = h('input', { class: 'input mono', value: '+90d' });
  const save = h('input', { type: 'checkbox', checked: true });
  const simulation = h('div');
  const result = h('div');
  const field = (label, input, hint) => h('div', { class: 'field' }, h('label', {}, label, input), hint ? h('span', { class: 'field-hint' }, hint) : null);
  for (const el of [username, password, user, audience, expires]) el.style.marginTop = '6px';

  const send = async (simulate) => {
    const body = { client, username: username.value.trim(), password: password.value, user: user.value.trim(), audience: audience.value.trim(), expires_on: expires.value.trim(), save: save.checked, simulate };
    if (!body.password && !(info.has_password && body.username === info.username)) throw new Error('Indique la contraseña del stack.');
    const data = await post('/api/platform/tokens', body);
    if (simulate) { clear(simulation).append(simulationBox(data)); return false; }
    if (data.saved) {
      toast(`Token creado y guardado en ${client} (caduca ${fmtDate(data.expires_on)})`, 'success');
      await loadClients();
      onDone();
      return true;
    }
    clear(result).append(infoBox([h('strong', {}, 'Copie el token ahora: '), 'ACS no lo vuelve a mostrar.'], 'warn-box'), codeBlock(data.token));
    onDone();
    return false;
  };
  openModal({
    title: `Crear un token en ${client}`,
    subtitle: 'ACS crea el token con usuario y contraseña del stack (solo para esta petición). Se puede guardar directamente como token del cliente.',
    size: 'lg',
    body: [
      h('div', { class: 'cred-block' }, h('div', { class: 'cred-head' }, icon('user'), h('strong', {}, 'Credenciales del stack')),
        h('div', { class: 'field-row' }, field('Usuario', username), field('Contraseña', password, info.has_password ? 'Vacía: se usa la contraseña guardada de este cliente.' : null))),
      h('div', { class: 'field-row' }, field('Usuario del token', user), field('Audiencia', audience, 'Texto libre que identifica para qué es.')),
      field('Caducidad', expires, 'Relativa (+90d, +365d) o fecha AAAA-MM-DDTHH:MM:SS. Por defecto ACS pone 30 días.'),
      h('label', { class: 'check' }, save, `Guardarlo cifrado como token de ${client} en config.py (sustituye al actual)`),
      simulation, result,
    ],
    actions: [
      { label: 'Cerrar' },
      { label: 'Simular', icon: 'eye', onClick: () => send(true) },
      { label: 'Crear token', variant: 'primary', icon: 'key', onClick: () => send(false) },
    ],
  });
}

function tokensCard(client) {
  const body = h('div', {}, loadingRows(3));
  const newBtn = button('Crear token…', { iconName: 'key', variant: 'primary' });
  let info = null;

  const load = async () => {
    try {
      info = await get('/api/platform/tokens', { client });
    } catch (e) {
      clear(body).append(errorBox(e.message));
      return;
    }
    clear(body);
    body.append(h('div', { style: { marginBottom: '12px' } }, kv([
      ['Token del cliente', h('div', { class: 'badges' }, tokenBadge(info.own), info.own.user ? badge(info.own.user, '', 'user') : null, info.own.audience ? badge(String(info.own.audience), 'outline') : null)],
      ['Emitido', info.own.issued ? fmtDate(info.own.issued) : null],
    ])));
    if (info.error) body.append(infoBox(`No se pudieron listar los tokens del stack: ${info.error}`, 'warn-box'));
    if (!info.platform.acs) body.append(infoBox(`ACS no disponible: ${info.platform.reason}. Se puede crear un token con usuario y contraseña del stack para recuperar el acceso.`, 'warn-box'));
    if (info.tokens.length) {
      body.append(new DataTable({
        rows: info.tokens, rowKey: (t) => t.id, compact: true,
        searchText: (t) => `${t.user} ${t.audience} ${t.status}`, searchPlaceholder: 'Buscar usuario o audiencia…',
        initialSort: { key: 'expires', dir: 'asc' },
        columns: [
          { key: 'user', label: 'Usuario', render: (t) => h('div', {}, h('div', { class: 'cell-main' }, t.user, t.is_own ? badge('este cliente', 'accent') : null), h('div', { class: 'cell-sub' }, t.audience)) },
          { key: 'status', label: 'Estado', render: (t) => badge(t.status, t.status === 'enabled' ? 'good' : 'outline') },
          { key: 'expires', label: 'Caduca', sort: (t) => t.expires_on || 0, render: (t) => h('div', {}, when(t.expires_on), t.level && t.level !== 'ok' ? h('div', {}, badge(LEVEL_UI[t.level][0], LEVEL_UI[t.level][1], 'alert')) : null) },
          { key: 'last', label: 'Último uso', sort: (t) => t.last_used || 0, defaultDir: 'desc', render: (t) => h('div', {}, t.last_used ? h('span', { title: fmtDate(t.last_used) }, fmtRelative(t.last_used)) : h('span', { class: 'muted' }, 'nunca'), t.last_used_ip ? h('div', { class: 'cell-sub mono' }, t.last_used_ip) : null) },
          {
            key: 'actions', label: '', className: 'col-actions', sortable: false,
            render: (t) => (t.is_own ? null : button('', { size: 'sm', variant: 'ghost', iconName: 'trash', title: 'Borrar', onClick: async () => {
              const ok = await confirmDialog({ title: `¿Borrar el token de ${t.user} (${t.audience})?`, message: 'Quien lo use dejará de poder autenticarse con él.', confirmLabel: 'Borrar', danger: true });
              if (!ok) return;
              try { await post('/api/platform/tokens/delete', { client, id: t.id }); toast('Token borrado', 'success'); load(); } catch (e) { toast(e.message, 'error'); }
            } })),
          },
        ],
      }).el);
    } else if (info.platform.acs && !info.error) {
      body.append(h('p', { class: 'muted' }, 'ACS no devuelve ningún token para este stack.'));
    }
  };
  newBtn.addEventListener('click', () => openTokenModal(client, info || {}, load));
  load();
  return h('div', { class: 'card', style: { marginTop: '16px' } },
    h('div', { class: 'card-header' },
      h('div', {}, h('h2', { class: 'card-title' }, 'Tokens de autenticación'),
        h('p', { class: 'card-subtitle' }, 'El token con el que se conecta esta herramienta y, según ACS, todos los del stack con su caducidad y último uso.')),
      h('div', { class: 'page-actions' }, newBtn)),
    h('div', { class: 'card-body' }, body));
}

// --- Exportar apps ------------------------------------------------------------------------------------------

export function openExportModal(client, preselected = []) {
  const picker = h('div', { class: 'item-picker-list' }, loadingRows(3));
  const selected = new Set(preselected);
  const filter = h('input', { class: 'input input-sm', type: 'search', placeholder: 'Filtrar apps…', style: { maxWidth: '240px', marginLeft: 'auto' } });
  const counter = h('span', { class: 'muted' });
  const opts = { default: h('input', { type: 'checkbox', checked: true }), local: h('input', { type: 'checkbox', checked: true }), users: h('input', { type: 'checkbox', checked: true }), confs_only: h('input', { type: 'checkbox' }), keep_for_validation: h('input', { type: 'checkbox' }) };
  let apps = [];
  const render = () => {
    clear(picker);
    const q = filter.value.trim().toLowerCase();
    for (const a of apps.filter((x) => `${x.name} ${x.label}`.toLowerCase().includes(q))) {
      const cb = h('input', { type: 'checkbox', checked: selected.has(a.name) });
      cb.addEventListener('change', () => { if (cb.checked) selected.add(a.name); else selected.delete(a.name); counter.textContent = `${selected.size} seleccionadas`; });
      picker.append(h('label', {}, cb, h('span', { class: 'truncate' }, a.label || a.name, ' ', h('span', { class: 'muted mono' }, a.name), a.version ? h('span', { class: 'muted' }, ` · ${a.version}`) : null)));
    }
    counter.textContent = `${selected.size} seleccionadas`;
  };
  filter.addEventListener('input', render);
  loadResource('apps', { client }).then((rows) => { apps = rows.filter((a) => !a.name.startsWith('_') && a.name !== 'search'); render(); }).catch((e) => clear(picker).append(errorBox(e.message)));

  openModal({
    title: `Exportar apps de ${client}`,
    subtitle: 'ACS devuelve la app completa como .tgz (default, local y usuarios). Se guarda en backups/apps/<stack>/. Las apps premium (ES, ITSI) y las de Splunk no se pueden exportar.',
    size: 'lg',
    body: [
      h('div', { class: 'item-picker', style: { marginBottom: '14px' } }, h('div', { class: 'item-picker-head' }, counter, filter), picker),
      h('div', { class: 'badges', style: { gap: '16px', marginBottom: '12px' } },
        h('label', { class: 'check' }, opts.default, 'default/'), h('label', { class: 'check' }, opts.local, 'local/'),
        h('label', { class: 'check' }, opts.users, 'users/'), h('label', { class: 'check' }, opts.confs_only, 'Solo .conf')),
      h('label', { class: 'check' }, opts.keep_for_validation, 'Guardar también en «Validar app» para comparar versiones'),
      infoBox('Requiere la capacidad export_apps en el usuario del token (sc_admin se la puede asignar). Se conservan las 10 últimas exportaciones de cada app.'),
    ],
    actions: [
      { label: 'Cancelar' },
      {
        label: 'Exportar', variant: 'primary', icon: 'download',
        onClick: async () => {
          const names = [...selected];
          if (!names.length) throw new Error('Seleccione al menos una app.');
          await startJob('/api/platform/exports', { client, names, options: Object.fromEntries(Object.entries(opts).map(([k, cb]) => [k, cb.checked])) });
        },
      },
    ],
  });
}

function exportsCard(client) {
  const body = h('div', {}, loadingRows(2));
  const load = async () => {
    try {
      const files = await get('/api/platform/exports', { client });
      clear(body);
      if (!files.length) { body.append(h('p', { class: 'muted', style: { margin: 0 } }, 'Todavía no hay exportaciones de este cliente.')); return; }
      body.append(new DataTable({
        rows: files, rowKey: (f) => f.name, compact: true, searchText: (f) => `${f.app} ${f.name}`, searchPlaceholder: 'Buscar app…',
        initialSort: { key: 'mtime', dir: 'desc' },
        columns: [
          { key: 'app', label: 'App', render: (f) => h('div', {}, h('div', { class: 'cell-main' }, f.app), h('div', { class: 'cell-sub mono' }, f.name)) },
          { key: 'version', label: 'Versión', render: (f) => (f.version ? h('code', {}, f.version) : h('span', { class: 'muted' }, '—')) },
          { key: 'size', label: 'Tamaño', className: 'num', render: (f) => fmtBytes(f.size) },
          { key: 'mtime', label: 'Exportada', defaultDir: 'desc', render: (f) => h('span', { title: fmtDate(f.mtime) }, fmtRelative(f.mtime)) },
          {
            key: 'actions', label: '', className: 'col-actions', sortable: false,
            render: (f) => h('div', { class: 'row-actions' },
              h('a', { class: 'btn btn-sm btn-ghost btn-icon', href: `/api/platform/exports/${enc(f.name)}?client=${enc(client)}`, title: 'Descargar', 'aria-label': 'Descargar' }, icon('download')),
              button('', { size: 'sm', variant: 'ghost', iconName: 'trash', title: 'Borrar', onClick: async () => {
                if (!await confirmDialog({ title: `¿Borrar ${f.name}?`, message: 'Se borra de backups/apps/.', confirmLabel: 'Borrar', danger: true })) return;
                try { await del(`/api/platform/exports/${enc(f.name)}?client=${enc(client)}`); load(); } catch (e) { toast(e.message, 'error'); }
              } })),
          },
        ],
      }).el);
    } catch (e) {
      clear(body).append(errorBox(e.message));
    }
  };
  load();
  const card = h('div', { class: 'card', style: { marginTop: '16px' } },
    h('div', { class: 'card-header' },
      h('div', {}, h('h2', { class: 'card-title' }, 'Exportaciones de apps'),
        h('p', { class: 'card-subtitle' }, 'Paquetes .tgz de apps exportados vía ACS (backup completo de la app, incluida su carpeta local/).')),
      h('div', { class: 'page-actions' }, button('Exportar apps…', { iconName: 'package', variant: 'primary', onClick: () => openExportModal(client) }))),
    h('div', { class: 'card-body' }, body));
  card.reload = load;
  return card;
}

// --- Estado y mantenimiento ----------------------------------------------------------------------------------

function windowsTable(windows) {
  return new DataTable({
    rows: windows, rowKey: (w) => w.id, compact: true, searchText: (w) => `${w.type} ${w.status} ${w.operations.map((o) => o.description).join(' ')}`,
    searchPlaceholder: 'Buscar…', initialSort: { key: 'start', dir: 'asc' },
    emptyIcon: 'calendar', emptyTitle: 'Sin ventanas de mantenimiento', emptyText: 'Splunk no tiene programada ninguna en los próximos 30 días.',
    columns: [
      { key: 'start', label: 'Inicio', sort: (w) => w.start || 0, render: (w) => h('div', {}, when(w.start), h('div', { class: 'cell-sub' }, w.start ? fmtRelative(w.start) : '')) },
      { key: 'end', label: 'Fin', sort: (w) => w.end || 0, render: (w) => when(w.end) },
      { key: 'type', label: 'Tipo', render: (w) => h('div', {}, h('div', { class: 'cell-main' }, w.type || '—'), h('div', { class: 'cell-sub' }, w.operations.map((o) => o.description + (o.target_version ? ` → ${o.target_version}` : '')).join(' · '))) },
      { key: 'status', label: 'Estado', render: (w) => h('div', { class: 'badges' }, badge(w.status, w.status === 'Completed' ? 'good' : 'accent'), w.zero_downtime ? badge('Sin corte', 'outline') : badge('Con corte', 'warn')) },
    ],
  }).el;
}

function statusCard(client, onStatus) {
  const body = h('div', {}, loadingRows(4));
  const restartBtn = button('Reiniciar el stack…', { iconName: 'power', variant: 'danger', disabled: true });
  const refreshBtn = button('Actualizar', { iconName: 'refresh', size: 'sm' });
  let data = null;

  const load = async (force = false) => {
    try {
      data = await get('/api/platform/status', { client, force: force ? '1' : '' });
    } catch (e) {
      clear(body).append(errorBox(e.message));
      return;
    }
    onStatus?.(data);
    clear(body);
    const p = data.platform;
    const s = data.stack;
    restartBtn.disabled = !p.acs;
    const tiles = h('div', { class: 'tiles' },
      tile('Plataforma', platformBadges(p), p.stack ? `stack ${p.stack}` : data.host),
      tile('Estado del stack', s ? badge(s.status || '?', String(s.status).toLowerCase() === 'ready' ? 'good' : 'warn') : h('span', { class: 'muted' }, '—'), s ? `${s.experience || '?'} · versión ${s.version || '?'}` : (p.acs ? 'sin datos de ACS' : p.reason)),
      tile('Reinicio pendiente', s ? (s.restart_required ? badge('Sí', 'warn', 'power') : badge('No', 'good', 'check')) : h('span', { class: 'muted' }, '—'), s && s.restart_required ? 'hay cambios que necesitan reiniciar el search head' : ''),
      tile('Token del cliente', tokenBadge({ present: Boolean(p.token_expiry) || state.clients.find((c) => c.name === client)?.has_token, expiry: p.token_expiry, level: levelOf(p.token_expiry) }), ''));
    body.append(tiles);
    for (const [key, err] of Object.entries(data.errors || {})) body.append(infoBox(`${key}: ${err}`, 'warn-box'));
    if (data.active_freezes?.length) {
      body.append(infoBox(h('div', {}, h('strong', {}, 'Change freeze vigente: '), data.active_freezes.map((f) => `${f.start} → ${f.end} (${f.by}): ${f.reason || f.applies_to}`).join(' · ')), 'warn-box'));
    }
    if (p.acs) {
      body.append(section('Ventanas de mantenimiento de Splunk (próximos 30 días)', windowsTable(data.windows || [])));
      if (data.preferred) {
        body.append(section('Preferencias', kv([
          ['Ventana preferida', `${data.preferred.day} ${data.preferred.start}–${data.preferred.end} UTC`],
          ['Días alternativos', data.preferred.alternate_days.join(', ') || null],
          ['Change freezes definidos', data.freezes.length ? data.freezes.map((f) => `${f.start} → ${f.end}: ${f.reason || f.applies_to}`).join(' · ') : 'ninguno'],
        ])));
      }
    }
  };
  refreshBtn.addEventListener('click', () => withBusy(refreshBtn, () => load(true)));
  restartBtn.addEventListener('click', async () => {
    const simulate = h('input', { type: 'checkbox' });
    let go = false;
    const modal = openModal({
      title: `¿Reiniciar el stack de ${client}?`, size: 'sm',
      body: [
        h('p', { style: { margin: '0 0 10px', color: 'var(--text-2)' } }, 'ACS reinicia el search head (rolling restart si es un cluster). Se interrumpen las búsquedas en curso y Splunk Web no responde durante unos minutos. La tarea espera a que el stack vuelva a estar listo.'),
        h('label', { class: 'check' }, simulate, 'Solo simular (mostrar la petición sin enviarla)'),
      ],
      actions: [{ label: 'Cancelar' }, { label: 'Reiniciar', variant: 'danger', onClick: () => { go = true; } }],
    });
    modal.onClose = async () => {
      if (!go) return;
      await startJob('/api/platform/restart', { client, simulate: simulate.checked });
    };
  });
  load();
  const card = h('div', { class: 'card' },
    h('div', { class: 'card-header' },
      h('div', {}, h('h2', { class: 'card-title' }, 'Estado del stack'), h('p', { class: 'card-subtitle' }, 'Según ACS: experiencia, versión, estado, reinicio pendiente y ventanas de mantenimiento.')),
      h('div', { class: 'page-actions' }, refreshBtn, restartBtn)),
    h('div', { class: 'card-body' }, body));
  card.reload = load;
  return card;
}

function levelOf(expiry) {
  if (!expiry) return null;
  const days = (expiry - Date.now() / 1000) / 86400;
  const t = state.meta?.token_warn_days || { warning: 30, serious: 7 };
  return days < 0 ? 'expired' : days < t.serious ? 'serious' : days < t.warning ? 'warning' : 'ok';
}

function tile(label, value, meta) {
  return h('div', { class: 'tile' }, h('div', { class: 'tile-label' }, label), h('div', { class: 'tile-value', style: { fontSize: '16px', marginTop: '8px' } }, value), h('div', { class: 'tile-meta' }, meta || ' '));
}

// --- Todos los clientes --------------------------------------------------------------------------------------

let overviewCache = null;

function overviewCard() {
  const body = h('div');
  const stamp = h('span', { class: 'muted', style: { fontSize: '12.5px' } });
  const runBtn = button('Comprobar todos los clientes', { iconName: 'refresh' });
  let withAcs = false;

  function render() {
    clear(body);
    if (!overviewCache) {
      body.append(h('p', { class: 'muted', style: { margin: 0 } }, 'La caducidad del token guardado de cada cliente se calcula sin conectar. «Comprobar todos los clientes» consulta además ACS: experiencia, versión, reinicio pendiente, próxima ventana y tokens del stack.'));
      const rows = state.clients.map((c) => ({ client: c.name, host: c.host, platform: c.platform, own: { present: c.has_token, expiry: c.platform?.token_expiry, level: levelOf(c.platform?.token_expiry) } }));
      body.append(quickTable(rows));
      return;
    }
    stamp.textContent = `Datos ${fmtRelative(overviewCache.at / 1000)}`;
    body.append(new DataTable({
      rows: overviewCache.rows, rowKey: (r) => r.client, compact: true,
      searchText: (r) => `${r.client} ${r.host}`, searchPlaceholder: 'Buscar cliente…',
      initialSort: { key: 'token', dir: 'asc' },
      filters: [{
        id: 'f',
        options: [
          { value: 'all', label: 'Todos' },
          { value: 'token', label: 'Token caduca pronto', test: (r) => ['expired', 'serious', 'warning'].includes(levelOf(r.platform?.token_expiry)) },
          { value: 'restart', label: 'Reinicio pendiente', test: (r) => r.stack?.restart_required },
          { value: 'window', label: 'Con ventana próxima', test: (r) => r.windows?.length > 0 },
          { value: 'noacs', label: 'Sin ACS', test: (r) => !r.platform?.acs },
        ],
      }],
      columns: [
        { key: 'client', label: 'Cliente', render: (r) => h('div', {}, h('div', { class: 'cell-main' }, r.client), h('div', { class: 'cell-sub' }, r.host || '')) },
        { key: 'platform', label: 'Plataforma', sortable: false, render: (r) => (r.error ? h('span', { class: 'status status-bad' }, icon('xCircle'), r.error) : platformBadges(r.platform)) },
        { key: 'stack', label: 'Stack', sort: (r) => r.stack?.version || '', render: (r) => (r.stack ? h('div', {}, h('div', { class: 'cell-main' }, r.stack.version || '?'), h('div', { class: 'cell-sub' }, r.stack.status || '')) : h('span', { class: 'muted' }, '—')) },
        { key: 'token', label: 'Token del cliente', sort: (r) => r.platform?.token_expiry || 9e12, render: (r) => tokenBadge({ present: Boolean(r.platform?.token_expiry) || state.clients.find((c) => c.name === r.client)?.has_token, expiry: r.platform?.token_expiry, level: levelOf(r.platform?.token_expiry) }) },
        { key: 'window', label: 'Próxima ventana', sort: (r) => r.windows?.[0]?.start || 9e12, render: (r) => (r.windows?.length ? h('div', {}, when(r.windows[0].start), h('div', { class: 'cell-sub' }, r.windows[0].type)) : h('span', { class: 'muted' }, r.platform?.acs ? 'ninguna' : '—')) },
        { key: 'freeze', label: 'Change freeze', sortable: false, render: (r) => (r.active_freezes?.length ? badge(`hasta ${r.active_freezes[0].end}`, 'warn') : h('span', { class: 'muted' }, '—')) },
      ],
      onRowClick: (r) => { if (r.client !== state.client) selectClient(r.client); window.scrollTo(0, 0); },
      emptyTitle: 'No hay clientes configurados',
    }).el);
  }

  function quickTable(rows) {
    return new DataTable({
      rows, rowKey: (r) => r.client, compact: true, searchText: (r) => `${r.client} ${r.host}`, searchPlaceholder: 'Buscar cliente…',
      initialSort: { key: 'token', dir: 'asc' },
      columns: [
        { key: 'client', label: 'Cliente', render: (r) => h('div', {}, h('div', { class: 'cell-main' }, r.client), h('div', { class: 'cell-sub' }, r.host || '')) },
        { key: 'platform', label: 'Plataforma', sortable: false, render: (r) => platformBadges(r.platform) },
        { key: 'token', label: 'Token del cliente', sort: (r) => r.own.expiry || 9e12, render: (r) => tokenBadge(r.own) },
      ],
      onRowClick: (r) => { if (r.client !== state.client) selectClient(r.client); window.scrollTo(0, 0); },
      emptyTitle: 'No hay clientes configurados',
    }).el;
  }

  runBtn.addEventListener('click', () => withBusy(runBtn, async () => {
    clear(body).append(h('p', { class: 'muted' }, 'Consultando ACS en todos los clientes…'), loadingRows(4));
    try {
      overviewCache = { at: Date.now(), rows: await get('/api/platform/overview') };
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
        h('p', { class: 'card-subtitle' }, 'Tipo de plataforma, caducidad del token, estado del stack y próxima ventana de mantenimiento de cada cliente.')),
      h('div', { class: 'page-actions' }, stamp, runBtn)),
    h('div', { class: 'card-body' }, body));
}

// --- Vista ------------------------------------------------------------------------------------------------------

export default {
  id: 'platform',
  title: 'Plataforma',
  icon: 'cloud',
  needsClient: true,
  render(root) {
    const client = state.client;
    const status = statusCard(client);
    const exports = exportsCard(client);
    clear(root).append(
      pageHeader({
        title: 'Plataforma',
        subtitle: `Estado del stack de ${client} según ACS, ventanas de mantenimiento, tokens de autenticación y exportación de apps.`,
      }),
      status, tokensCard(client), exports, overviewCard());
    return on('job-finished', (job) => {
      if (!job.clients.includes(client)) return;
      if (job.kind === 'platform') status.reload(true);
      if (job.kind === 'export') exports.reload();
    });
  },
};
