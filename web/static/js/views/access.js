// Acceso de red vía ACS: listas de IP permitidas (IPv4/IPv6) por funcionalidad y puertos de salida, del cliente y de todos
// los clientes (matriz), con alta y baja de subredes en uno o varios clientes a la vez (tarea, con simulación).
import { get } from '../api.js';
import { state, on, startJob, selectClient } from '../core.js';
import {
  h, clear, icon, button, badge, pageHeader, DataTable, openModal, infoBox, errorBox, loadingRows, withBusy, fmtRelative,
  segmented, fmtNum,
} from '../ui.js';
import { acsNotice } from './platform.js';

let overviewCache = null;

const featureLabel = (id) => (state.meta?.allowlist_features || []).find((f) => f.id === id)?.label || id;

function subnetBadges(subnets, max = 8) {
  if (!subnets?.length) return h('span', { class: 'muted' }, 'Vacía');
  const shown = subnets.slice(0, max).map((s) => h('code', { class: 'subnet' }, s));
  if (subnets.length > max) shown.push(badge(`+${subnets.length - max}`, 'outline'));
  return h('div', { class: 'badges' }, shown);
}

function clientChips(clients, selected) {
  const el = h('div', { class: 'client-chips' });
  const render = () => {
    clear(el);
    for (const name of clients) {
      const cb = h('input', { type: 'checkbox', checked: selected.has(name) });
      cb.addEventListener('change', () => { if (cb.checked) selected.add(name); else selected.delete(name); render(); });
      el.append(h('label', { class: `client-chip ${selected.has(name) ? 'selected' : ''}` }, cb, name));
    }
  };
  render();
  return el;
}

/**
 * Diálogo para añadir o quitar subredes. kind: 'allowlist' | 'outbound'; preset: { feature, port, v6, subnets, clients }.
 */
export function openChangeModal({ kind, action, preset = {} }) {
  const cloud = state.clients.filter((c) => c.platform?.acs).map((c) => c.name);
  const selected = new Set((preset.clients || [state.client]).filter((n) => cloud.includes(n)));
  const chips = clientChips(cloud, selected);
  const features = state.meta?.allowlist_features || [];
  const featureSelect = h('select', { class: 'select' }, features.map((f) => h('option', { value: f.id, selected: f.id === preset.feature }, f.label)));
  const portInput = h('input', { class: 'input mono', type: 'number', min: 1, max: 65535, value: preset.port || '', placeholder: '443' });
  const reasonInput = h('input', { class: 'input', value: preset.reason || '', placeholder: 'Motivo (p. ej. webhook hacia SOAR)' });
  const subnetsInput = h('textarea', { class: 'input mono', rows: 4, placeholder: '10.0.0.0/24\n203.0.113.5/32', spellcheck: 'false' }, (preset.subnets || []).join('\n'));
  let v6 = Boolean(preset.v6);
  const versionSeg = segmented([{ value: '4', label: 'IPv4' }, { value: '6', label: 'IPv6' }], v6 ? '6' : '4', (v) => { v6 = v === '6'; });
  const simulate = h('input', { type: 'checkbox' });
  const adding = action === 'add';
  const isList = kind === 'allowlist';

  openModal({
    title: `${adding ? 'Añadir' : 'Quitar'} subredes ${isList ? `${adding ? 'a' : 'de'} una lista de IP` : `${adding ? 'a' : 'de'} un puerto de salida`}`,
    subtitle: adding ? 'Se añaden a lo que ya haya en cada cliente (ACS nunca sustituye la lista entera).' : 'Antes de quitar nada se guarda el estado actual en backups/acceso/.',
    size: 'lg',
    body: [
      h('div', { class: 'field' }, h('span', { class: 'field-label' }, 'Clientes'), chips,
        h('span', { class: 'field-hint' }, cloud.length ? 'Solo los clientes de Splunk Cloud con ACS disponible.' : 'Ningún cliente tiene ACS disponible (hace falta un stack de Splunk Cloud con token de sc_admin).')),
      h('div', { class: 'field-row' },
        isList ? h('div', { class: 'field' }, h('label', {}, 'Funcionalidad', featureSelect)) : h('div', { class: 'field' }, h('label', {}, 'Puerto', portInput)),
        h('div', { class: 'field' }, h('span', { class: 'field-label' }, 'Versión'), h('div', {}, versionSeg))),
      isList || !adding ? null : h('div', { class: 'field' }, h('label', {}, 'Motivo', reasonInput), h('span', { class: 'field-hint' }, 'Queda registrado en Splunk Cloud junto a la regla.')),
      h('div', { class: 'field' }, h('label', {}, 'Subredes (CIDR), una por línea o separadas por comas', subnetsInput),
        h('span', { class: 'field-hint' }, 'Una IP suelta se convierte en /32 (o /128 en IPv6).')),
      h('label', { class: 'check' }, simulate, 'Solo simular: mostrar la petición a ACS sin enviarla'),
      !adding && isList ? infoBox('Cuidado con search-ui y search-api: si quita la subred desde la que trabaja, perderá el acceso a Splunk Web y a esta herramienta.', 'warn-box') : null,
    ],
    actions: [
      { label: 'Cancelar' },
      {
        label: adding ? 'Añadir' : 'Quitar', variant: adding ? 'primary' : 'danger', icon: adding ? 'plus' : 'trash',
        onClick: async () => {
          const clients = cloud.filter((n) => selected.has(n));
          if (!clients.length) throw new Error('Seleccione al menos un cliente.');
          const subnets = subnetsInput.value.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean);
          if (!subnets.length) throw new Error('Indique al menos una subred.');
          const data = isList ? { feature: featureSelect.value, v6, subnets } : { port: Number(portInput.value), v6, subnets, reason: reasonInput.value.trim() };
          if (!isList && !(data.port >= 1 && data.port <= 65535)) throw new Error('Indique un puerto entre 1 y 65535.');
          await startJob('/api/access/change', { kind, action, clients, data, simulate: simulate.checked });
        },
      },
    ],
  });
}

// --- Matriz de todos los clientes -------------------------------------------------------------------------

function overviewCard() {
  const body = h('div');
  const stamp = h('span', { class: 'muted', style: { fontSize: '12.5px' } });
  const runBtn = button('Comprobar todos los clientes', { iconName: 'refresh' });

  function render() {
    clear(body);
    if (!overviewCache) {
      body.append(h('p', { class: 'muted', style: { margin: 0 } }, 'Pulse «Comprobar todos los clientes» para ver qué subredes tiene permitidas cada cliente en cada funcionalidad y qué puertos de salida tiene abiertos.'));
      return;
    }
    stamp.textContent = `Datos ${fmtRelative(overviewCache.at / 1000)}`;
    const rows = [];
    for (const r of overviewCache.rows) {
      if (r.error) { rows.push({ client: r.client, host: r.host, error: r.error, key: 'error', label: '', subnets: [] }); continue; }
      for (const a of r.allowlists) if (!a.error) rows.push({ client: r.client, host: r.host, kind: 'allowlist', feature: a.feature, v6: a.version === 6, key: `${a.feature}/${a.version}`, label: `${a.label} · IPv${a.version}`, subnets: a.subnets });
      for (const p of r.outbound) rows.push({ client: r.client, host: r.host, kind: 'outbound', port: p.port, v6: p.version === 6, key: `puerto/${p.port}/${p.version}`, label: `Puerto ${p.port} · IPv${p.version}`, subnets: p.subnets });
    }
    // Subredes que aparecen en varios clientes: ayuda a ver qué rangos son "de SIRT" y cuáles no
    const seen = new Map();
    for (const r of rows) for (const s of r.subnets) seen.set(s, (seen.get(s) || 0) + 1);
    const table = new DataTable({
      rows, rowKey: (r) => `${r.client}/${r.key}`, compact: true,
      searchText: (r) => `${r.client} ${r.label} ${r.subnets.join(' ')}`,
      searchPlaceholder: 'Buscar cliente, funcionalidad o subred…',
      initialSort: { key: 'label', dir: 'asc' },
      filters: [{
        id: 'kind', label: 'Mostrar',
        options: [
          { value: 'nonempty', label: 'Con subredes', test: (r) => r.subnets.length > 0 || r.error },
          { value: 'lists', label: 'Listas de IP', test: (r) => r.kind === 'allowlist' },
          { value: 'ports', label: 'Puertos de salida', test: (r) => r.kind === 'outbound' },
          { value: 'v6', label: 'IPv6', test: (r) => r.v6 },
          { value: 'all', label: 'Todo' },
        ],
      }],
      columns: [
        { key: 'label', label: 'Lista', render: (r) => (r.error ? h('span', { class: 'status status-bad' }, icon('xCircle'), r.error) : h('span', { class: 'cell-main' }, r.label)) },
        { key: 'client', label: 'Cliente', render: (r) => h('div', {}, h('div', { class: 'cell-main' }, r.client), h('div', { class: 'cell-sub' }, r.host || '')) },
        { key: 'count', label: 'Subredes', className: 'num', sort: (r) => r.subnets.length, defaultDir: 'desc', render: (r) => (r.error ? '' : fmtNum(r.subnets.length)) },
        {
          key: 'subnets', label: 'Contenido', sortable: false,
          render: (r) => (r.error ? '' : h('div', { class: 'badges' }, r.subnets.map((s) => h('code', { class: `subnet ${seen.get(s) > 1 ? 'shared' : ''}`, title: seen.get(s) > 1 ? `En ${seen.get(s)} listas` : 'Solo en esta lista' }, s)))),
        },
      ],
      onRowClick: (r) => { if (r.client !== state.client) selectClient(r.client); window.scrollTo(0, 0); },
      emptyTitle: 'No hay clientes configurados',
    });
    body.append(h('p', { class: 'muted', style: { margin: '0 0 10px', fontSize: '12.5px' } }, 'Las subredes resaltadas aparecen en más de una lista (normalmente las de SIRT); las demás son de un solo cliente o proyecto.'), table.el);
  }

  runBtn.addEventListener('click', () => withBusy(runBtn, async () => {
    clear(body).append(h('p', { class: 'muted' }, 'Consultando todos los clientes (unas 18 peticiones a ACS por cliente)…'), loadingRows(4));
    try {
      overviewCache = { at: Date.now(), rows: await get('/api/access/overview') };
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
        h('p', { class: 'card-subtitle' }, 'Qué subredes puede usar cada cliente en cada funcionalidad. Pulse una fila para pasar a ese cliente.')),
      h('div', { class: 'page-actions' }, stamp, runBtn)),
    h('div', { class: 'card-body' }, body));
}

// --- Vista --------------------------------------------------------------------------------------------------

export default {
  id: 'access',
  title: 'Acceso de red',
  icon: 'globe',
  needsClient: true,
  render(root) {
    const client = state.client;
    const stamp = h('span', { class: 'muted', style: { fontSize: '12.5px' } });
    const body = h('div', {}, loadingRows(6));
    const refreshBtn = button('Actualizar', { iconName: 'refresh' });
    const addListBtn = button('Añadir a una lista…', { iconName: 'plus', variant: 'primary', onClick: () => openChangeModal({ kind: 'allowlist', action: 'add' }) });
    const addPortBtn = button('Abrir puerto de salida…', { iconName: 'plus', onClick: () => openChangeModal({ kind: 'outbound', action: 'add' }) });
    const backupBtn = button('Backup', { iconName: 'download', onClick: () => startJob('/api/access/backup', { clients: [client] }) });

    const load = async () => {
      const platform = state.clients.find((c) => c.name === client)?.platform;
      if (platform && !platform.acs) {
        clear(body).append(acsNotice(client, platform));
        addListBtn.disabled = addPortBtn.disabled = true;
        return;
      }
      try {
        const data = await get('/api/access', { client });
        stamp.textContent = `Datos ${fmtRelative(Date.now() / 1000)}`;
        clear(body);
        const unavailable = data.allowlists.filter((a) => a.error);
        if (unavailable.length) {
          body.append(infoBox(`ACS no devuelve en este stack: ${unavailable.map((a) => `${a.label} IPv${a.version}`).join(', ')}.`, ''));
        }
        for (const [key, err] of Object.entries(data.errors || {})) body.append(infoBox(`Puertos de salida IPv${key.slice(-1)}: ${err}`, 'warn-box'));
        const lists = data.allowlists.filter((a) => !a.error);
        const listTable = new DataTable({
          rows: lists, rowKey: (a) => `${a.feature}/${a.version}`, compact: true,
          searchText: (a) => `${a.label} ${a.feature} ${a.subnets.join(' ')}`, searchPlaceholder: 'Buscar funcionalidad o subred…',
          initialSort: { key: 'label', dir: 'asc' },
          filters: [{ id: 'v', options: [{ value: 'nonempty', label: 'Con subredes', test: (a) => a.subnets.length > 0 }, { value: 'all', label: 'Todas' }] }],
          emptyIcon: 'globe', emptyTitle: 'Sin listas de IP',
          columns: [
            { key: 'label', label: 'Funcionalidad', render: (a) => h('div', {}, h('div', { class: 'cell-main' }, a.label), h('div', { class: 'cell-sub' }, `${a.feature} · IPv${a.version}`)) },
            { key: 'count', label: 'Subredes', className: 'num', sort: (a) => a.subnets.length, defaultDir: 'desc', render: (a) => fmtNum(a.subnets.length) },
            { key: 'subnets', label: 'Subredes permitidas', sortable: false, render: (a) => subnetBadges(a.subnets, 12) },
            {
              key: 'actions', label: '', className: 'col-actions', sortable: false,
              render: (a) => h('div', { class: 'row-actions' },
                button('', { size: 'sm', variant: 'ghost', iconName: 'plus', title: 'Añadir subredes', onClick: () => openChangeModal({ kind: 'allowlist', action: 'add', preset: { feature: a.feature, v6: a.version === 6 } }) }),
                a.subnets.length ? button('', { size: 'sm', variant: 'ghost', iconName: 'trash', title: 'Quitar subredes', onClick: () => openChangeModal({ kind: 'allowlist', action: 'remove', preset: { feature: a.feature, v6: a.version === 6, subnets: a.subnets } }) }) : null),
            },
          ],
        });
        const portTable = new DataTable({
          rows: data.outbound, rowKey: (p) => `${p.port}/${p.version}`, compact: true,
          searchText: (p) => `${p.port} ${p.subnets.join(' ')}`, searchPlaceholder: 'Buscar puerto o destino…',
          initialSort: { key: 'port', dir: 'asc' },
          emptyIcon: 'globe', emptyTitle: 'Sin puertos de salida abiertos', emptyText: 'Solo los puertos estándar de Splunk Cloud pueden salir hacia Internet.',
          columns: [
            { key: 'port', label: 'Puerto', render: (p) => h('div', {}, h('span', { class: 'cell-main mono' }, String(p.port)), h('div', { class: 'cell-sub' }, `IPv${p.version}`)) },
            { key: 'subnets', label: 'Destinos permitidos', sortable: false, render: (p) => subnetBadges(p.subnets, 12) },
            {
              key: 'actions', label: '', className: 'col-actions', sortable: false,
              render: (p) => h('div', { class: 'row-actions' },
                button('', { size: 'sm', variant: 'ghost', iconName: 'plus', title: 'Añadir destinos', onClick: () => openChangeModal({ kind: 'outbound', action: 'add', preset: { port: p.port, v6: p.version === 6 } }) }),
                button('', { size: 'sm', variant: 'ghost', iconName: 'trash', title: 'Quitar destinos', onClick: () => openChangeModal({ kind: 'outbound', action: 'remove', preset: { port: p.port, v6: p.version === 6, subnets: p.subnets } }) })),
            },
          ],
        });
        body.append(
          h('h2', { class: 'section-title' }, 'Listas de IP permitidas'),
          listTable.el,
          h('h2', { class: 'section-title', style: { marginTop: '20px' } }, 'Puertos de salida'),
          portTable.el);
      } catch (e) {
        clear(body).append(errorBox(e.message));
      }
    };
    refreshBtn.addEventListener('click', () => withBusy(refreshBtn, load));

    clear(root).append(
      pageHeader({
        title: 'Acceso de red',
        subtitle: `Subredes que pueden entrar en ${client} por cada funcionalidad (Splunk Web, API, HEC, forwarders…) y puertos de salida abiertos hacia destinos externos, según ACS.`,
        actions: [stamp, refreshBtn, backupBtn, addPortBtn, addListBtn],
      }),
      body, overviewCard());
    load();
    return on('job-finished', (job) => { if (job.kind === 'access' && job.clients.includes(client)) { load(); overviewCache = null; } });
  },
};

export { featureLabel };
