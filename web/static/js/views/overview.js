// Resumen: estado del cliente activo, recuento de objetos y gráficos rápidos.
import {
  state, currentClient, selectClient, connectClient, disconnectClient, loadResource, navigate, invalidate,
} from '../core.js';
import {
  h, append, clear, icon, button, pageHeader, emptyState, errorBox, fmtNum, fmtCompact, fmtMB, fmtDate, fmtRelative,
  initials, attachTooltip, loadingRows, withBusy,
} from '../ui.js';
import { loadBackups, FOLDER_ICONS } from '../ops.js';

export function clientCards(onPick) {
  return h('div', { class: 'welcome-grid' }, state.clients.map((c) => h('button', {
    class: 'welcome-card', type: 'button', onclick: () => onPick(c.name),
  },
  h('div', { class: 'wc-top' },
    h('span', { class: 'wc-avatar' }, initials(c.name)),
    h('div', { style: { minWidth: 0, flex: 1 } }, h('div', { class: 'wc-name' }, c.name), h('div', { class: 'wc-host' }, c.host)),
    c.connected ? h('span', { class: 'dot dot-good', title: 'Conectado' }) : null),
  h('div', { class: 'badges' },
    h('span', { class: 'badge' }, icon(c.auth === 'token' ? 'key' : 'user'), c.auth === 'token' ? 'Token' : c.auth === 'user' ? c.username : 'Sin credenciales'),
    c.info?.version ? h('span', { class: 'badge badge-accent' }, `Splunk ${c.info.version}`) : null))));
}

/** Lista de barras horizontales (una sola serie) con tooltip al pasar el ratón. */
function barList(rows, { valueFormat = fmtNum, tooltip, href }) {
  const max = Math.max(...rows.map((r) => r.value), 1);
  return h('div', { class: 'bars' }, rows.map((r) => {
    const row = h(href ? 'a' : 'div', { class: 'bar-row', href: href ? href(r) : null, tabindex: href ? null : '0' },
      h('span', { class: 'bar-label' }, r.label),
      h('span', { class: 'bar-value' }, valueFormat(r.value)),
      h('div', { class: 'bar-track' }, h('div', { class: 'bar-fill', style: { width: `${(r.value / max) * 100}%` } })));
    attachTooltip(row, () => tooltip(r));
    return row;
  }));
}

function tile({ label, iconName, route, resource, compute }) {
  const value = h('div', { class: 'tile-value' }, h('span', { class: 'skeleton', style: { width: '56px', height: '28px' } }));
  const meta = h('div', { class: 'tile-meta' }, ' ');
  const el = h('a', { class: 'tile', href: `#/${route}` },
    h('div', { class: 'tile-label' }, icon(iconName), label), value, meta);
  loadResource(resource).then((rows) => {
    const result = compute(rows);
    value.textContent = fmtCompact(result.value);
    value.title = fmtNum(result.value);
    append(clear(meta), result.meta || ' ');
  }).catch((e) => {
    value.textContent = '—';
    meta.textContent = 'No disponible';
    meta.title = e.message;
  });
  return el;
}

function card(title, subtitle, body) {
  return h('section', { class: 'card' },
    h('div', { class: 'card-header' }, h('div', {}, h('h2', { class: 'card-title' }, title), subtitle && h('p', { class: 'card-subtitle' }, subtitle))),
    h('div', { class: 'card-body' }, body));
}

function asyncBody(promise, renderFn) {
  const el = h('div', {}, loadingRows(5));
  promise.then((data) => clear(el).append(renderFn(data))).catch((e) => clear(el).append(errorBox(e.message)));
  return el;
}

function renderDashboard(root, client) {
  const info = client.info || {};
  const hostTag = client.host.split('.')[0];

  const reconnect = button('Reconectar', { iconName: 'refresh', size: 'sm' });
  reconnect.addEventListener('click', () => withBusy(reconnect, () => connectClient(client.name)));
  const disconnect = button('Desconectar', { iconName: 'power', size: 'sm', variant: 'ghost', onClick: () => disconnectClient(client.name) });

  const banner = h('div', { class: 'server-banner' },
    h('div', { class: 'server-name' },
      h('span', { class: 'dot dot-good' }),
      h('div', {}, h('h2', {}, client.name), h('div', { class: 'muted' }, client.host))),
    kvInline('Versión', info.version ? `Splunk ${info.version}` : '—'),
    kvInline('Servidor', info.serverName || '—'),
    kvInline('Tipo', [info.product_type, info.instance_type].filter(Boolean).join(' · ') || '—'),
    kvInline('Conectado', fmtRelative(client.connected_at)),
    h('div', { class: 'page-actions' }, reconnect, disconnect));

  const tiles = h('div', { class: 'tiles' },
    tile({
      label: 'Alertas', iconName: 'bell', route: 'alerts', resource: 'saved-searches',
      compute: (rows) => {
        const alerts = rows.filter((r) => r.is_alert || r.is_es);
        const cu = alerts.filter((r) => r.is_cu).length;
        const es = alerts.filter((r) => r.is_es).length;
        const off = alerts.filter((r) => r.disabled).length;
        return { value: alerts.length, meta: [h('b', {}, fmtNum(cu)), ' CU- · ', h('b', {}, fmtNum(es)), ' ES · ', h('b', {}, fmtNum(off)), ' deshab.'] };
      },
    }),
    tile({
      label: 'Reportes', iconName: 'report', route: 'reports', resource: 'saved-searches',
      compute: (rows) => {
        const reports = rows.filter((r) => !r.is_alert && !r.is_es);
        return { value: reports.length, meta: [h('b', {}, fmtNum(reports.filter((r) => r.scheduled).length)), ' programados'] };
      },
    }),
    tile({ label: 'Macros', iconName: 'braces', route: 'macros', resource: 'macros', compute: (rows) => ({ value: rows.length }) }),
    tile({ label: 'Dashboards', iconName: 'dashboard', route: 'dashboards', resource: 'dashboards', compute: (rows) => ({ value: rows.length, meta: [h('b', {}, fmtNum(new Set(rows.map((r) => r.app)).size)), ' apps'] }) }),
    tile({ label: 'Usuarios', iconName: 'users', route: 'users', resource: 'users', compute: (rows) => ({ value: rows.length }) }),
    tile({ label: 'Roles', iconName: 'shield', route: 'roles', resource: 'roles', compute: (rows) => ({ value: rows.length }) }),
    tile({
      label: 'Índices', iconName: 'database', route: 'indexes', resource: 'indexes',
      compute: (rows) => {
        const own = rows.filter((r) => !r.name.startsWith('_'));
        const raw = rows.some((r) => r.size_kind === 'raw');
        return { value: own.length, meta: [h('b', {}, fmtMB(rows.reduce((s, r) => s + (r.size_mb || 0), 0))), raw ? ' sin comprimir' : ' en total'] };
      },
    }),
    tile({ label: 'Apps', iconName: 'apps', route: 'apps', resource: 'apps', compute: (rows) => ({ value: rows.length, meta: [h('b', {}, fmtNum(rows.filter((r) => !r.disabled).length)), ' habilitadas'] }) }));

  const fired = card('Alertas más disparadas', 'Alertas disparadas que Splunk conserva ahora mismo (según su expiración)',
    asyncBody(loadResource('fired-alerts'), (rows) => {
      if (!rows.length) return emptyState({ iconName: 'zap', title: 'Sin alertas disparadas', text: 'Splunk no tiene alertas disparadas registradas en este momento.' });
      return barList(rows.slice(0, 10).map((r) => ({ label: r.name, value: r.count })), {
        tooltip: (r) => [h('b', {}, r.label), h('br'), `${fmtNum(r.value)} disparos`],
        href: (r) => `#/alerts?q=${encodeURIComponent(r.label)}`,
      });
    }));

  const indexes = card('Índices por tamaño', 'Los 10 índices que más ocupan (en Splunk Cloud, datos sin comprimir)',
    asyncBody(loadResource('indexes'), (rows) => {
      const top = [...rows].sort((a, b) => (b.size_mb || 0) - (a.size_mb || 0)).slice(0, 10).filter((r) => r.size_mb > 0);
      if (!top.length) return emptyState({ iconName: 'database', title: 'Sin datos de tamaño', text: 'Splunk no devolvió tamaños de índices.' });
      return barList(top.map((r) => ({ label: r.name, value: r.size_mb, events: r.events })), {
        valueFormat: fmtMB,
        tooltip: (r) => [h('b', {}, r.label), h('br'), `${fmtMB(r.value)} · ${fmtNum(r.events)} eventos`],
        href: (r) => `#/indexes?q=${encodeURIComponent(r.label)}`,
      });
    }));

  const backups = card('Backups de este cliente', `Archivos de backups/ cuyo nombre incluye «${hostTag}»`,
    asyncBody(loadBackups(true), (data) => {
      const files = [];
      for (const [folder, meta] of Object.entries(data)) {
        for (const f of meta.files) if (f.name.toLowerCase().includes(hostTag.toLowerCase())) files.push({ ...f, folder, label: meta.label });
      }
      if (!files.length) {
        return emptyState({ iconName: 'archive', title: 'Sin backups de este cliente', text: 'Lance un backup desde cualquier sección o desde Operaciones masivas.' });
      }
      files.sort((a, b) => b.mtime - a.mtime);
      return h('ul', { class: 'list-plain' }, files.slice(0, 8).map((f) => h('li', {},
        h('a', { class: 'bar-row', href: `#/backups?folder=${f.folder}`, style: { gridTemplateColumns: 'minmax(0,1fr) auto' } },
          h('span', { class: 'bar-label' }, icon(FOLDER_ICONS[f.folder] || 'file'), ' ', f.name),
          h('span', { class: 'muted nowrap', style: { fontSize: '12.5px' } }, `${f.label} · ${fmtDate(f.mtime)}`)))));
    }));

  const refresh = button('Actualizar datos', { iconName: 'refresh', onClick: () => { invalidate(client.name); renderDashboard(root, client); } });

  clear(root).append(
    pageHeader({ title: 'Resumen', subtitle: `Estado general de ${client.name}.`, actions: [refresh] }),
    banner, tiles,
    h('div', { class: 'grid-2' }, fired, indexes),
    h('div', { style: { marginTop: '16px' } }, backups));
}

function kvInline(label, value) {
  return h('div', { class: 'kv-inline' }, h('span', {}, label), h('span', {}, value));
}

export default {
  id: 'overview',
  title: 'Resumen',
  icon: 'home',
  render(root) {
    const client = currentClient();
    if (!state.clients.length) {
      clear(root).append(
        pageHeader({ title: 'Bienvenido a Splunk Manager' }),
        h('div', { class: 'card' }, emptyState({
          iconName: 'server',
          title: 'No hay clientes configurados',
          text: 'Añada el primer cliente (host de Splunk y credenciales) para empezar. Se guardará en config.py.',
          action: button('Añadir cliente', { variant: 'primary', iconName: 'plus', onClick: () => navigate('clients', { new: '1' }) }),
        })));
      return;
    }
    if (!client || (!client.connected && state.connecting !== client.name)) {
      clear(root).append(
        pageHeader({
          title: 'Elija un cliente',
          subtitle: client
            ? `No hay conexión con ${client.name}. Pulse su tarjeta para conectar o elija otro cliente.`
            : 'Seleccione el cliente de Splunk con el que quiere trabajar. La conexión se mantiene mientras el servidor esté en marcha.',
        }),
        clientCards((name) => selectClient(name)));
      return;
    }
    if (!client.connected) {
      clear(root).append(pageHeader({ title: 'Resumen' }),
        h('div', { class: 'card' }, emptyState({ iconName: 'plug', title: `Conectando con ${client.name}…`, text: client.host, action: h('span', { class: 'spinner' }) })));
      return;
    }
    renderDashboard(root, client);
  },
};
