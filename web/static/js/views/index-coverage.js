// Cobertura de índices: índices que reciben datos pero que no consulta ningún caso de uso CU-* habilitado.
import { get, put } from '../api.js';
import { state, navigate } from '../core.js';
import {
  h, append, clear, icon, button, badge, pageHeader, DataTable, openDrawer, openModal, toast, section,
  errorBox, infoBox, fmtNum, fmtRelative, fmtDate, withBusy,
} from '../ui.js';

const cache = new Map();
const TAB_KEY = 'sm.indexCoverage.tab';

const STATUS = {
  unused: ['Sin ningún CU', 'bad'],
  inactive_only: ['Solo CU inactivos', 'warn'],
  ignored: ['Ignorado', 'outline'],
  used: ['Cubierto', 'good'],
};
const STATUS_HELP = {
  unused: 'Ningún caso de uso CU-* consulta este índice.',
  inactive_only: 'Solo lo consultan casos de uso deshabilitados o versiones de pruebas (TEST - CU-*): en producción no está cubierto.',
  ignored: 'Está en la lista de índices ignorados.',
  used: 'Lo consulta al menos un caso de uso CU-* habilitado.',
};
const KIND = { active: ['Habilitado', 'good'], disabled: ['Deshabilitado', 'outline'], test: ['Pruebas', 'accent'] };
const MODE = {
  index: ['Índices concretos', 'outline'],
  sourcetype: ['Por sourcetype', 'outline'],
  all: ['index=*', 'warn'],
  default: ['Sin índice', 'warn'],
  no_events: ['No busca eventos', 'outline'],
};
const MODE_HELP = {
  index: 'Nombra índices concretos (directamente, en macros, eventtypes o data models).',
  sourcetype: 'No nombra índices: cubre los índices en los que se han visto sus sourcetypes en el periodo.',
  all: 'Busca en index=* sin restringir el sourcetype: podría cubrir cualquier índice, así que no marca ninguno.',
  default: 'No indica índice ni sourcetype: busca en los índices por defecto de su rol, así que no marca ninguno.',
  no_events: 'Empieza por un comando que no busca eventos (inputlookup, rest, makeresults…).',
};

function load(client, force = false) {
  if (!cache.has(client) || force) {
    const entry = { at: Date.now(), promise: get('/api/index-coverage', { client }) };
    entry.promise.catch(() => { if (cache.get(client) === entry) cache.delete(client); });
    cache.set(client, entry);
  }
  return cache.get(client);
}

function tile(label, iconName, value, meta) {
  return h('div', { class: 'tile' }, h('div', { class: 'tile-label' }, icon(iconName), label), h('div', { class: 'tile-value' }, value), h('div', { class: 'tile-meta' }, meta || ' '));
}

const quote = (v) => `"${String(v).replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`;
const fmtEpoch = (epoch) => (epoch ? fmtDate(new Date(epoch * 1000).toISOString()) : '—');

// --- Lista de índices ignorados ------------------------------------------------------

const toText = (entries) => entries.map((e) => (e.reason ? `${e.index}  # ${e.reason}` : e.index)).join('\n');

function fromText(text) {
  return text.split('\n').map((line) => {
    const [pattern, ...rest] = line.split('#');
    return { index: pattern.trim(), reason: rest.join('#').trim() };
  }).filter((e) => e.index);
}

async function saveIgnored(client, scope, entries) {
  return put('/api/index-coverage/ignored', { client, scope, entries });
}

async function openIgnoredEditor(client, onSaved) {
  const lists = await get('/api/index-coverage/ignored', { client });
  const clientArea = h('textarea', { class: 'textarea mono', rows: 7, spellcheck: 'false', placeholder: 'backup_*  # Solo retención' });
  const globalArea = h('textarea', { class: 'textarea mono', rows: 5, spellcheck: 'false', placeholder: 'archivo_*' });
  clientArea.value = toText(lists.client);
  globalArea.value = toText(lists.global);
  openModal({
    title: 'Índices ignorados',
    subtitle: 'Índices que tienen datos pero que no necesitan casos de uso (retención, cumplimiento, pruebas…). Uno por línea; admite comodines (*) y un motivo tras #.',
    size: 'lg',
    body: [
      h('div', { class: 'field' }, h('label', {}, `Solo en ${client}`, clientArea)),
      h('div', { class: 'field' }, h('label', {}, 'En todos los clientes', globalArea)),
      h('p', { class: 'muted', style: { fontSize: '12.5px', margin: '4px 0 0' } },
        'Se guardan en indices_ignorados.json, junto a config.py; el CLI usa la misma lista. Los índices internos (_*) y de sistema ya se excluyen siempre.'),
    ],
    actions: [
      { label: 'Cancelar' },
      {
        label: 'Guardar', variant: 'primary', icon: 'save',
        onClick: async () => {
          await saveIgnored(client, 'client', fromText(clientArea.value));
          await saveIgnored(client, 'global', fromText(globalArea.value));
          toast('Lista de índices ignorados guardada', 'success');
          onSaved();
        },
      },
    ],
  });
}

function openIgnoreOne(client, row, onSaved) {
  const reason = h('input', { class: 'input', type: 'text', placeholder: 'Ej.: solo se guarda por retención legal' });
  let scope = 'client';
  const scopeSelect = h('select', { class: 'select', onchange: () => { scope = scopeSelect.value; } },
    h('option', { value: 'client' }, `Solo en ${client}`), h('option', { value: 'global' }, 'En todos los clientes'));
  openModal({
    title: `Ignorar el índice ${row.name}`,
    subtitle: 'Dejará de aparecer como índice sin casos de uso. Se puede deshacer desde «Índices ignorados».',
    size: 'sm',
    body: [h('div', { class: 'field' }, h('label', {}, 'Motivo', reason)), h('div', { class: 'field' }, h('label', {}, 'Dónde', scopeSelect))],
    actions: [
      { label: 'Cancelar' },
      {
        label: 'Ignorar', variant: 'primary', icon: 'eyeOff',
        onClick: async () => {
          const lists = await get('/api/index-coverage/ignored', { client });
          const current = scope === 'global' ? lists.global : lists.client;
          await saveIgnored(client, scope, [...current, { index: row.name, reason: reason.value.trim() }]);
          toast(`${row.name} ignorado`, 'success');
          onSaved();
        },
      },
    ],
  });
}

async function unignore(client, row, onSaved) {
  const lists = await get('/api/index-coverage/ignored', { client });
  const scope = row.ignored.scope === 'global' ? 'global' : 'client';
  const current = scope === 'global' ? lists.global : lists.client;
  await saveIgnored(client, scope, current.filter((e) => e.index !== row.ignored.index));
  toast(row.ignored.index === row.name ? `${row.name} ya no se ignora` : `Quitada la regla ${row.ignored.index}`, 'success');
  onSaved();
}

// --- Vista ------------------------------------------------------------------------

export default {
  id: 'index-coverage',
  title: 'Índices sin CU',
  icon: 'layers',
  needsClient: true,
  render(root) {
    const client = state.client;
    let alive = true;
    let data = null;
    let tab = 'indexes';
    try { tab = localStorage.getItem(TAB_KEY) || 'indexes'; } catch { /* sin almacenamiento */ }
    const body = h('div');
    const stamp = h('span', { class: 'muted', style: { fontSize: '12.5px' } });

    function loading() {
      clear(body).append(h('div', { class: 'card' }, h('div', { class: 'empty' },
        h('span', { class: 'spinner', style: { width: '28px', height: '28px', borderWidth: '3px' } }),
        h('h3', {}, 'Revisando los casos de uso y los índices…'),
        h('p', {}, 'Se leen las alertas CU-*, sus macros, eventtypes, tags y data models, y los eventos de los últimos 7 días por índice y sourcetype (tstats).'))));
    }

    async function show(force = false) {
      const entry = load(client, force);
      loading();
      try {
        data = await entry.promise;
        if (!alive) return;
        stamp.textContent = `Datos ${fmtRelative(entry.at / 1000)}`;
        render();
      } catch (e) {
        if (alive) clear(body).append(errorBox(e.message));
      }
    }

    const reload = () => show(true);

    function usersList(row) {
      if (!row.used_by.length) return h('p', { class: 'muted' }, 'Ningún caso de uso consulta este índice.');
      return h('ul', { class: 'list-plain' }, row.used_by.map((u) => h('li', { style: { marginBottom: '6px' } },
        h('div', {}, u.name, ' ', badge(...KIND[u.kind])),
        h('div', { class: 'cell-sub' }, [u.app, u.via ? `vía ${u.via}` : 'índice escrito en la búsqueda o en sus macros'].filter(Boolean).join(' · ')))));
    }

    function openIndex(row) {
      const spl = `| tstats count max(_time) as ultimo where index=${quote(row.name)} earliest=-${data.period_days}d by sourcetype source\n| convert ctime(ultimo)\n| sort - count`;
      const drawer = openDrawer({
        title: row.name,
        subtitle: `${fmtNum(row.events)} eventos en ${data.period_days} días · último ${fmtEpoch(row.last)}`,
        width: 640,
        actions: [
          button('Ver en Búsqueda', { iconName: 'search', size: 'sm', onClick: () => { drawer.close(); navigate('search', { q: spl }); } }),
          row.status === 'ignored'
            ? button('Dejar de ignorar', { iconName: 'eye', size: 'sm', onClick: async () => { drawer.close(); await unignore(client, row, reload); } })
            : row.status !== 'used'
              ? button('Ignorar', { iconName: 'eyeOff', size: 'sm', onClick: () => { drawer.close(); openIgnoreOne(client, row, reload); } })
              : null,
        ].filter(Boolean),
        body: [
          infoBox([badge(...STATUS[row.status]), ' ', STATUS_HELP[row.status],
            row.ignored ? ` Regla «${row.ignored.index}» (${row.ignored.scope === 'global' ? 'todos los clientes' : client})${row.ignored.reason ? `: ${row.ignored.reason}` : ''}.` : '']),
          section('Sourcetypes del periodo', h('table', { class: 'data compact' },
            h('thead', {}, h('tr', {}, h('th', {}, 'Sourcetype'), h('th', { class: 'num' }, 'Eventos'))),
            h('tbody', {}, row.sourcetypes.map((s) => h('tr', {}, h('td', {}, s.name), h('td', { class: 'num' }, fmtNum(s.events))))))),
          section('Casos de uso que lo consultan', usersList(row)),
        ],
      });
    }

    function openAlert(a) {
      openDrawer({
        title: a.name,
        subtitle: [a.app, a.owner].filter(Boolean).join(' · '),
        width: 620,
        body: [
          h('div', { style: { display: 'flex', gap: '6px', flexWrap: 'wrap', marginBottom: '12px' } }, badge(...KIND[a.kind]), badge(...MODE[a.mode])),
          infoBox(MODE_HELP[a.mode]),
          section('Índices que cubre', a.indexes.length ? h('p', {}, a.indexes.join(', ')) : h('p', { class: 'muted' }, 'Ninguno')),
          section('Filtros encontrados',
            h('p', {}, h('strong', {}, 'index: '), a.index_filters.join(', ') || '—'),
            h('p', {}, h('strong', {}, 'sourcetype: '), a.sourcetype_filters.join(', ') || '—')),
          a.issues.length ? section('No se pudo resolver todo', h('ul', { class: 'list-plain help-list' }, a.issues.map((t) => h('li', {}, t)))) : null,
        ],
      });
    }

    function indexesTable(rows) {
      return new DataTable({
        rows,
        compact: true,
        searchText: (r) => `${r.name} ${r.sourcetypes.map((s) => s.name).join(' ')}`,
        searchPlaceholder: 'Buscar índice o sourcetype…',
        filters: [{
          id: 'status', label: 'Estado', value: 'pending',
          options: [
            { value: 'pending', label: 'Sin cubrir', test: (r) => r.status === 'unused' || r.status === 'inactive_only' },
            { value: 'unused', label: 'Sin ningún CU', test: (r) => r.status === 'unused' },
            { value: 'ignored', label: 'Ignorados', test: (r) => r.status === 'ignored' },
            { value: 'used', label: 'Cubiertos', test: (r) => r.status === 'used' },
            { value: 'all', label: 'Todos' },
          ],
        }],
        emptyIcon: 'checkCircle',
        emptyTitle: 'Nada que mostrar',
        emptyText: 'Con este filtro no hay índices.',
        onRowClick: openIndex,
        columns: [
          { key: 'name', label: 'Índice', render: (r) => h('div', {}, h('div', { class: 'cell-main' }, r.name), r.disabled ? h('div', { class: 'cell-sub' }, 'Deshabilitado') : null) },
          { key: 'status', label: 'Estado', sort: (r) => ['unused', 'inactive_only', 'ignored', 'used'].indexOf(r.status), render: (r) => badge(...STATUS[r.status]) },
          { key: 'events', label: `Eventos ${data.period_days} d`, className: 'num', defaultDir: 'desc', render: (r) => fmtNum(r.events) },
          { key: 'last', label: 'Último evento', defaultDir: 'desc', sort: (r) => r.last || 0, render: (r) => fmtEpoch(r.last) },
          {
            key: 'sourcetypes', label: 'Sourcetypes', sortable: false,
            render: (r) => h('div', { class: 'cell-wrap', style: { maxWidth: '320px', fontSize: '12.5px' } },
              r.sourcetypes.slice(0, 4).map((s) => s.name).join(', ') + (r.sourcetypes.length > 4 ? ` y ${r.sourcetypes.length - 4} más` : '')),
          },
          {
            key: 'users', label: 'Casos de uso', defaultDir: 'desc', sort: (r) => r.active_count * 1000 + r.used_by.length,
            render: (r) => {
              if (r.status === 'ignored') return h('span', { class: 'muted', style: { fontSize: '12.5px' } }, r.ignored.reason || `Regla ${r.ignored.index}`);
              if (!r.used_by.length) return h('span', { class: 'muted' }, '—');
              const inactive = r.used_by.length - r.active_count;
              return h('span', {}, `${fmtNum(r.active_count)} habilitados`, inactive ? h('span', { class: 'muted' }, ` · ${fmtNum(inactive)} inactivos`) : null);
            },
          },
        ],
      });
    }

    function alertsTable(rows) {
      return new DataTable({
        rows,
        compact: true,
        rowKey: (a) => `${a.app}/${a.owner}/${a.name}`,
        searchText: (a) => `${a.name} ${a.app} ${a.indexes.join(' ')} ${a.index_filters.join(' ')} ${a.sourcetype_filters.join(' ')}`,
        searchPlaceholder: 'Buscar caso de uso o índice…',
        filters: [
          {
            id: 'kind', label: 'Estado', value: 'active',
            options: [
              { value: 'active', label: 'Habilitados', test: (a) => a.kind === 'active' },
              { value: 'inactive', label: 'Deshabilitados y pruebas', test: (a) => a.kind !== 'active' },
              { value: 'all', label: 'Todos' },
            ],
          },
          {
            id: 'mode', label: 'Alcance',
            options: [
              { value: 'all', label: 'Todos' },
              { value: 'broad', label: 'Cualquier índice', title: 'index=* o sin índice: podrían cubrir cualquier índice', test: (a) => a.mode === 'all' || a.mode === 'default' },
              { value: 'issues', label: 'Sin resolver del todo', test: (a) => a.issues.length > 0 },
            ],
          },
        ],
        initialSort: { key: 'name', dir: 'asc' },
        emptyIcon: 'checkCircle',
        emptyTitle: 'Nada que mostrar',
        onRowClick: openAlert,
        columns: [
          { key: 'name', label: 'Caso de uso', render: (a) => h('div', { style: { minWidth: '220px', maxWidth: '440px' } }, h('div', { class: 'cell-main' }, a.name), h('div', { class: 'cell-sub truncate' }, a.app)) },
          { key: 'kind', label: 'Estado', sort: (a) => a.kind, render: (a) => badge(...KIND[a.kind]) },
          { key: 'mode', label: 'Alcance', sort: (a) => a.mode, render: (a) => badge(...MODE[a.mode]) },
          {
            key: 'indexes', label: 'Índices que cubre', defaultDir: 'desc', sort: (a) => a.indexes.length,
            render: (a) => h('div', { class: 'cell-wrap', style: { maxWidth: '340px', fontSize: '12.5px' } }, a.indexes.join(', ') || h('span', { class: 'muted' }, '—')),
          },
          { key: 'issues', label: 'Avisos', className: 'num', defaultDir: 'desc', sort: (a) => a.issues.length, render: (a) => (a.issues.length ? badge(fmtNum(a.issues.length), 'warn', 'alert') : '') },
        ],
      });
    }

    function render() {
      const s = data.summary;
      const tiles = h('div', { class: 'tiles tiles-5' },
        tile('Sin ningún CU', 'xCircle', fmtNum(s.unused), 'índices con datos que nadie consulta'),
        tile('Solo CU inactivos', 'alert', fmtNum(s.inactive_only), 'deshabilitados o de pruebas'),
        tile('Cubiertos', 'checkCircle', fmtNum(s.used), `de ${fmtNum(s.with_data)} con datos en ${data.period_days} días`),
        tile('Casos de uso', 'bell', fmtNum(s.active_alerts), `habilitados de ${fmtNum(s.alerts)} CU-*`),
        tile('Ignorados', 'eyeOff', fmtNum(s.ignored), `+ ${fmtNum(s.excluded)} internos o de sistema`));

      const notes = [];
      if (s.broad_alerts) {
        notes.push(infoBox([h('strong', {}, `${fmtNum(s.broad_alerts)} ${s.broad_alerts === 1 ? 'caso de uso busca' : 'casos de uso buscan'} en cualquier índice `),
          '(index=* o sin índice ni sourcetype). No marcan ningún índice como cubierto; revíselos en la pestaña Casos de uso con el filtro «Cualquier índice».'], 'warn-box'));
      }
      if (data.warnings.length) notes.push(infoBox(['No se pudo leer todo; el resultado puede tener falsos «sin CU»: ', data.warnings.join(' · ')], 'warn-box'));

      const tabs = h('div', { class: 'tabs', role: 'tablist', style: { margin: '0', padding: '0 14px' } });
      const slot = h('div');
      const TABS = {
        indexes: ['Índices', data.indexes.length, () => indexesTable(data.indexes)],
        alerts: ['Casos de uso', data.alerts.length, () => alertsTable(data.alerts)],
      };
      const renderTabs = () => {
        clear(tabs);
        for (const [key, [label, count]] of Object.entries(TABS)) {
          tabs.append(h('button', {
            class: `tab ${tab === key ? 'active' : ''}`, type: 'button', role: 'tab', 'aria-selected': String(tab === key),
            onclick: () => { tab = key; try { localStorage.setItem(TAB_KEY, tab); } catch { /* sin almacenamiento */ } renderTabs(); renderTable(); },
          }, label, h('span', { class: 'badge' }, fmtNum(count))));
        }
      };
      const renderTable = () => clear(slot).append((TABS[tab] || TABS.indexes)[2]().el);
      renderTabs();
      renderTable();

      append(clear(body), [
        tiles,
        ...notes,
        h('section', { class: 'card', style: { marginTop: '16px' } },
          h('div', { class: 'card-header' }, h('div', {},
            h('h2', { class: 'card-title' }, 'Cobertura'),
            h('p', { class: 'card-subtitle' }, 'Pulse una fila para ver los sourcetypes del índice y qué casos de uso lo consultan, o cómo se ha resuelto un caso de uso.'))),
          tabs, slot),
        h('details', { class: 'bulk-details', style: { marginTop: '16px' } },
          h('summary', {}, '¿Cómo se calcula?'),
          h('ul', { class: 'list-plain help-list' },
            h('li', {}, `Índices con datos: eventos de los últimos ${data.period_days} días por índice y sourcetype (| tstats count where index=*). Se excluyen los internos y de sistema (${data.system_patterns.join(', ')}), los de métricas y los ignorados.`),
            h('li', {}, 'Casos de uso: las alertas CU-* (y TEST - CU-*) de todas las apps. Solo cuentan como cobertura las de producción habilitadas.'),
            h('li', {}, 'De cada búsqueda se sustituyen sus macros (las que ve desde su app) y se siguen sus eventtype=, tag= y data models (tstats from datamodel=…, | datamodel, | from datamodel:…) hasta llegar a index= y sourcetype=. Los filtros negados (NOT index=, index!=) y el index= de | collect no cuentan.'),
            h('li', {}, 'Si una búsqueda (o una rama: un eventtype, un tag…) nombra índices, cubre esos (con comodines: index=win*). Si solo filtra por sourcetype, cubre los índices donde se ha visto ese sourcetype en el periodo.'),
            h('li', {}, 'index=* sin sourcetype o una búsqueda sin índice ni sourcetype podrían cubrir cualquier índice: se señalan aparte y no marcan ninguno.'),
            h('li', {}, `Objetos leídos: ${fmtNum(data.knowledge.macros)} macros, ${fmtNum(data.knowledge.eventtypes)} eventtypes, ${fmtNum(data.knowledge.tags)} stanzas de tags y ${fmtNum(data.knowledge.datamodels)} data models.`))),
      ]);
    }

    const ignoredBtn = button('Índices ignorados', { iconName: 'eyeOff' });
    ignoredBtn.addEventListener('click', () => withBusy(ignoredBtn, () => openIgnoredEditor(client, reload)));
    const refreshBtn = button('Actualizar', { iconName: 'refresh' });
    refreshBtn.addEventListener('click', () => withBusy(refreshBtn, () => show(true)));

    clear(root).append(
      pageHeader({
        title: 'Índices sin casos de uso',
        subtitle: `Índices de ${client} que reciben datos pero que no consulta ningún caso de uso CU-* habilitado.`,
        actions: [stamp, ignoredBtn, refreshBtn],
      }),
      body);
    show();
    return () => { alive = false; };
  },
};
