// Alertas y reportes (saved searches): listado en vivo, detalle, dependencias, edición, habilitar/deshabilitar, ejecutar,
// backup y restore.
import { get, post } from '../api.js';
import { state, loadResource, cachedAt, navigate } from '../core.js';
import {
  h, append, clear, icon, button, badge, pageHeader, DataTable, openDrawer, openModal, confirmDialog, toast, kv, section,
  codeBlock, errorBox, loadingRows, fmtRelative, withBusy, fmtNum,
} from '../ui.js';
import { openBackupModal, openRestoreModal, runBackup } from '../ops.js';
import { openEditor, openBulkEditor, setEnabled, mergeRows, savedSearchRef, ownerText, SHARING_LABELS } from '../alert-editor.js';
import { alertKindFilter, appFilter, enabledFilter } from '../filters.js';

const SEVERITY = { 1: ['Debug', ''], 2: ['Info', ''], 3: ['Baja', 'accent'], 4: ['Media', 'warn'], 5: ['Alta', 'bad'], 6: ['Crítica', 'bad'] };

/** Etiqueta de la versión de pruebas de un caso de uso («TEST - CU-…»); 'inline' para ponerla junto al nombre. */
export function testBadge(row, { inline = false } = {}) {
  if (!row.is_test) return null;
  const el = badge('TEST', 'warn');
  if (inline) el.classList.add('name-badge');
  return el;
}

// --- Dependencias: macros (también las anidadas) y lookups que necesita una búsqueda ----------------------------

const DEP_STATUS = {
  ok: ['OK', 'good', 'check'],
  disabled: ['Deshabilitada', 'warn', 'power'],
  not_visible: ['No visible desde su app', 'bad', 'eyeOff'],
  missing: ['No existe', 'bad', 'xCircle'],
  file_missing: ['Falta el archivo CSV', 'bad', 'xCircle'],
  file_not_visible: ['El CSV no se ve', 'bad', 'eyeOff'],
  created: ['Lo crea la búsqueda', 'outline', 'plus'],
};

const depWhere = (d) => [d.app, d.sharing === 'user' ? `privada de ${d.owner}` : SHARING_LABELS[d.sharing] || d.sharing].filter(Boolean).join(' · ');

function depHint(d, context) {
  if (d.status === 'missing') {
    return d.other_arities?.length ? `No existe con ese nº de argumentos; sí hay: ${d.other_arities.join(', ')}` : 'No está definida en este cliente.';
  }
  if (d.status === 'not_visible') return `Solo existe en ${d.not_visible_in.map(depWhere).join(' | ')}: desde ${context.app} no se ve.`;
  if (d.status === 'file_missing') return `${depWhere(d)} · su archivo ${d.filename} no existe.`;
  if (d.status === 'file_not_visible') return `${depWhere(d)} · su archivo ${d.filename} no se ve desde ${context.app}.`;
  if (d.status === 'created') return 'No existe todavía: outputlookup lo crea al ejecutarse.';
  const parts = [depWhere(d)];
  if (d.also?.length) parts.push(`tapa a otra definición en ${d.also.map(depWhere).join(' | ')}`);
  return parts.join(' · ');
}

function depItem(d, label, context, onOpen, extra) {
  const [text, variant, iconName] = DEP_STATUS[d.status] || [d.status, ''];
  return h('li', { class: 'dep-item', style: { '--depth': d.depth || 0 } },
    h('div', { class: 'dep-head' },
      d.depth ? h('span', { class: 'dep-branch', 'aria-hidden': 'true' }, '↳') : null,
      h('a', { href: '#', title: d.definition || null, onclick: (e) => { e.preventDefault(); onOpen(); } }, h('code', {}, label)),
      badge(text, variant, iconName), extra),
    h('div', { class: 'cell-sub' }, depHint(d, context)));
}

/** Macros y lookups que necesita una saved search y si están bien, vistos desde su app y su propietario. */
function dependenciesSection(row, close) {
  const slot = h('div', {}, loadingRows(2));
  get('/api/splunk/saved-searches/dependencies', { client: state.client, ...savedSearchRef(row) }).then((deps) => {
    const { macros, lookups, context } = deps;
    if (!macros.length && !lookups.length) {
      clear(slot).append(h('p', { class: 'muted', style: { margin: 0 } }, 'No usa macros ni lookups.'));
      return;
    }
    const open = (view, q) => { close(); navigate(view, { q }); };
    append(clear(slot), [
      h('div', { class: 'badges', style: { marginBottom: '6px' } },
        badge(`${fmtNum(macros.length)} ${macros.length === 1 ? 'macro' : 'macros'}`, '', 'braces'),
        badge(`${fmtNum(lookups.length)} ${lookups.length === 1 ? 'lookup' : 'lookups'}`, '', 'sheet'),
        deps.problems ? badge(`${fmtNum(deps.problems)} con problemas`, 'bad', 'alert') : badge('Todo correcto', 'good', 'check')),
      macros.length ? [h('div', { class: 'dep-title' }, 'Macros'),
        h('ul', { class: 'dep-list' }, macros.map((m) => depItem(m, m.stanza, context, () => open('macros', m.stanza))))] : null,
      lookups.length ? [h('div', { class: 'dep-title' }, 'Lookups'),
        h('ul', { class: 'dep-list' }, lookups.map((l) => depItem(l, l.name, context, () => open('lookups', l.name),
          h('span', { class: 'muted', style: { fontSize: '12px' } }, [l.commands.join(', '), l.via ? `en ${l.via}` : null].filter(Boolean).join(' · ')))))] : null,
      h('p', { class: 'field-hint', style: { marginTop: '10px' } },
        `Vistas desde la app ${context.app} como ${context.owner}, igual que cuando se ejecuta. Las macros que llama otra macro aparecen debajo, sangradas.`),
    ]);
  }).catch((e) => clear(slot).append(errorBox(e.message)));
  return section('Dependencias', slot);
}

function sharingBadge(row) {
  if (row.sharing === 'user') return badge(`Privada de ${row.owner}`, 'outline', 'lock');
  if (row.sharing === 'app') return badge('Solo en su app', 'outline');
  return null;
}

function severityBadge(row) {
  const [text, variant] = SEVERITY[row.severity] || [];
  return text ? badge(text, variant) : h('span', { class: 'muted' }, '—');
}

function stateBadge(row) {
  return row.disabled ? badge('Deshabilitada', 'outline') : badge('Habilitada', 'good', 'check');
}

function scheduleCell(row) {
  if (!row.scheduled) return h('span', { class: 'muted' }, 'No programada');
  return h('div', {}, h('code', {}, row.cron || '—'), row.next_run ? h('div', { class: 'cell-sub' }, `Próx.: ${row.next_run}`) : null);
}

async function action(row, name) {
  return post('/api/splunk/saved-searches/action', { client: state.client, ...savedSearchRef(row), action: name });
}

async function dispatchNow(row) {
  const result = await action(row, 'dispatch');
  toast(`«${row.name}» lanzada en ${state.client}`, 'success', {
    action: { label: 'Ver resultados', onClick: () => navigate('search', { sid: result.sid }) },
  });
}

// Habilita o deshabilita en una sola petición (con backup previo) y actualiza las filas
async function toggleRows(rows, enabled, refreshTable) {
  try {
    mergeRows(rows, await setEnabled(rows, enabled));
  } catch (e) {
    toast(e.message, 'error');
  }
  refreshTable();
}

/** Abre la edición en varios clientes con esas saved searches. */
export function editInClients(rows) {
  navigate('multi-edit', { names: JSON.stringify([...new Set(rows.map((r) => r.name))]) });
}

function backupSelection(rows) {
  const labelInput = h('input', { class: 'input', value: 'seleccion', placeholder: 'seleccion' });
  openModal({
    title: `Backup de ${fmtNum(rows.length)} saved searches`,
    subtitle: `Cliente: ${state.client}`,
    body: [
      h('div', { class: 'field' }, h('label', {}, 'Etiqueta del archivo'), labelInput,
        h('span', { class: 'field-hint' }, 'El archivo se guardará como backups/alertas/alertas_<etiqueta>_<host>_backup.json')),
      h('ul', { class: 'list-plain', style: { maxHeight: '200px', overflow: 'auto', fontSize: '13px' } },
        rows.map((r) => h('li', { class: 'truncate' }, r.name))),
    ],
    actions: [
      { label: 'Cancelar' },
      {
        label: 'Iniciar backup', variant: 'primary', icon: 'download',
        onClick: () => runBackup('alerts_selected', { names: rows.map((r) => r.name), label: labelInput.value.trim() || 'seleccion' }),
      },
    ],
  });
}

function openDetail(row, refreshTable, { isAlertView }) {
  const toggleBtn = button(row.disabled ? 'Habilitar' : 'Deshabilitar', { iconName: 'power', size: 'sm' });
  toggleBtn.addEventListener('click', () => withBusy(toggleBtn, async () => {
    await toggleRows([row], row.disabled, refreshTable);
    drawer.close();
  }));
  const runBtn = button('Ejecutar ahora', { iconName: 'play', size: 'sm' });
  runBtn.addEventListener('click', () => withBusy(runBtn, () => dispatchNow(row).catch((e) => toast(e.message, 'error'))));

  const editBtn = button('Editar', { iconName: 'edit', size: 'sm', variant: 'primary' });
  editBtn.addEventListener('click', () => editRow(row, refreshTable, { isAlertView, reopen: true }));

  const actions = [
    editBtn,
    runBtn,
    button('Abrir en Búsqueda', { iconName: 'search', size: 'sm', onClick: () => { drawer.close(); navigate('search', { q: row.search }); } }),
    toggleBtn,
    button('En varios clientes…', { iconName: 'layers', size: 'sm', onClick: () => { drawer.close(); editInClients([row]); } }),
  ];
  if (isAlertView) {
    actions.push(button('Backup', { iconName: 'download', size: 'sm', onClick: () => runBackup('alerts_selected', { names: [row.name], label: 'seleccion' }) }));
  }

  const drawer = openDrawer({
    title: row.name,
    subtitle: [row.app, ownerText(row)].filter(Boolean).join(' · '),
    actions,
    body: [
      h('div', { class: 'badges', style: { marginBottom: '16px' } },
        stateBadge(row),
        row.is_alert ? badge('Alerta', 'accent', 'bell') : badge('Reporte', '', 'report'),
        row.is_cu ? badge('CU-') : null,
        testBadge(row),
        row.is_es ? badge('Enterprise Security', 'accent') : null,
        row.scheduled ? badge('Programada', '', 'clock') : null,
        sharingBadge(row)),
      section('Configuración', kv([
        ['Programación', row.scheduled ? h('code', {}, row.cron) : 'No programada'],
        ['Próxima ejecución', row.next_run || null],
        ['Ventana', row.earliest || row.latest ? `${row.earliest || '—'} → ${row.latest || 'now'}` : null],
        ['Tipo de alerta', row.alert_type || null],
        ['Severidad', row.severity ? severityBadge(row) : null],
        ['Acciones', row.actions || null],
        ['Etiqueta ES', row.es_label || undefined],
        ['Descripción', row.description || null],
      ])),
      section('Búsqueda SPL', codeBlock(row.search)),
      dependenciesSection(row, () => drawer.close()),
    ],
  });
}

function editRow(row, refreshTable, { isAlertView, reopen = false } = {}) {
  openEditor(row, {
    onSaved: (updated) => {
      Object.assign(row, updated);
      refreshTable();
      if (reopen) openDetail(row, refreshTable, { isAlertView });
    },
  });
}

function bulkEdit(rows, allRows, refreshTable) {
  openBulkEditor(rows, {
    onSaved: (updated) => {
      mergeRows(allRows(), updated);
      refreshTable();
    },
  });
}

function makeView({ id, title, iconName, subtitle, rowFilter, filters, isAlertView }) {
  return {
    id, title, icon: iconName, needsClient: true,
    render(root, params) {
      const body = h('div', {}, loadingRows(8));
      const stamp = h('span', { class: 'muted', style: { fontSize: '12.5px' } });
      let rows = [];
      let table = null;

      const refreshTable = () => table?.setRows(rows);

      const load = async (force = false) => {
        try {
          const all = await loadResource('saved-searches', { force });
          rows = all.filter(rowFilter);
          stamp.textContent = `Datos ${fmtRelative(cachedAt('saved-searches') / 1000)}`;
          if (!table) {
            table = new DataTable({
              rows,
              rowKey: (r) => `${r.app}/${r.owner}/${r.sharing}/${r.name}`,
              selectable: true,
              initialQuery: params.q || '',
              searchText: (r) => `${r.name} ${r.es_label} ${r.app} ${r.owner} ${r.search}`,
              searchPlaceholder: 'Buscar por nombre, app o SPL…',
              filters,
              initialSort: { key: 'name', dir: 'asc' },
              emptyIcon: iconName,
              emptyTitle: `No hay ${title.toLowerCase()}`,
              onRowClick: (r) => openDetail(r, refreshTable, { isAlertView }),
              selectionActions: [
                { label: 'Modificar…', icon: 'sliders', variant: 'primary', onClick: (sel) => bulkEdit(sel, () => rows, refreshTable) },
                { label: 'En varios clientes…', icon: 'layers', onClick: (sel) => editInClients(sel) },
                isAlertView && { label: 'Backup de la selección', icon: 'download', onClick: (sel) => backupSelection(sel) },
                { label: 'Habilitar', icon: 'power', onClick: (sel) => toggleRows(sel, true, refreshTable) },
                {
                  label: 'Deshabilitar', icon: 'power',
                  onClick: async (sel) => {
                    const ok = await confirmDialog({
                      title: `¿Deshabilitar ${fmtNum(sel.length)} ${title.toLowerCase()}?`,
                      message: `Dejarán de ejecutarse en ${state.client} hasta que se vuelvan a habilitar.`,
                      confirmLabel: 'Deshabilitar', danger: true,
                    });
                    if (ok) await toggleRows(sel, false, refreshTable);
                  },
                },
              ].filter(Boolean),
              columns: [
                {
                  key: 'name', label: 'Nombre',
                  render: (r) => h('div', { style: { minWidth: '220px' } },
                    h('div', { class: 'cell-main' }, r.name, testBadge(r, { inline: true })),
                    (r.es_label || r.description) ? h('div', { class: 'cell-sub truncate', style: { maxWidth: '460px' } }, r.es_label || r.description) : null),
                },
                { key: 'app', label: 'App', render: (r) => h('div', {}, r.app, h('div', { class: 'cell-sub' }, ownerText(r))) },
                { key: 'cron', label: 'Programación', render: scheduleCell, sort: (r) => (r.scheduled ? r.cron : '~') },
                isAlertView && { key: 'severity', label: 'Severidad', render: severityBadge, sort: (r) => Number(r.severity) || 0, defaultDir: 'desc' },
                { key: 'disabled', label: 'Estado', render: stateBadge, sort: (r) => (r.disabled ? 1 : 0) },
                {
                  key: 'actions', label: '', className: 'col-actions', sortable: false,
                  render: (r) => {
                    const run = button('', { size: 'sm', variant: 'ghost', iconName: 'play', title: 'Ejecutar ahora' });
                    run.addEventListener('click', () => withBusy(run, () => dispatchNow(r).catch((e) => toast(e.message, 'error'))));
                    const toggle = button('', { size: 'sm', variant: 'ghost', iconName: 'power', title: r.disabled ? 'Habilitar' : 'Deshabilitar' });
                    toggle.addEventListener('click', () => withBusy(toggle, () => toggleRows([r], r.disabled, refreshTable)));
                    const edit = button('', { size: 'sm', variant: 'ghost', iconName: 'edit', title: 'Editar' });
                    edit.addEventListener('click', () => editRow(r, refreshTable, { isAlertView }));
                    return h('div', { class: 'row-actions' }, edit, run, toggle,
                      button('', { size: 'sm', variant: 'ghost', iconName: 'eye', title: 'Ver detalle', onClick: () => openDetail(r, refreshTable, { isAlertView }) }));
                  },
                },
              ].filter(Boolean),
            });
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

      const actions = isAlertView
        ? [
          stamp, refreshBtn,
          button('Restaurar…', { iconName: 'restore', onClick: () => openRestoreModal({ folder: 'alertas' }) }),
          button('Backup…', {
            iconName: 'download', variant: 'primary',
            onClick: () => openBackupModal({ kinds: ['alerts_all', 'alerts_tech', 'alerts_custom', 'es_all', 'es_custom'], title: 'Backup de alertas' }),
          }),
        ]
        : [
          stamp, refreshBtn,
          button('Restaurar…', { iconName: 'restore', onClick: () => openRestoreModal({ folder: 'reportes' }) }),
          button('Backup de reportes', { iconName: 'download', variant: 'primary', onClick: () => runBackup('reports') }),
        ];

      clear(root).append(pageHeader({ title, subtitle: subtitle(), actions }), body);
      load();
    },
  };
}

export const alertsView = makeView({
  id: 'alerts',
  title: 'Alertas',
  iconName: 'bell',
  isAlertView: true,
  subtitle: () => `Alertas y casos de uso de Enterprise Security de ${state.client}, de todas las apps y usuarios. Pulse una fila para ver su SPL, su configuración y sus dependencias, o edítela con el lápiz.`,
  rowFilter: (r) => r.is_alert || r.is_es,
  filters: [alertKindFilter, enabledFilter, appFilter],
});

export const reportsView = makeView({
  id: 'reports',
  title: 'Reportes',
  iconName: 'report',
  isAlertView: false,
  subtitle: () => `Saved searches de ${state.client} que no son alertas, de todas las apps y usuarios.`,
  rowFilter: (r) => !r.is_alert && !r.is_es,
  filters: [
    {
      id: 'sched',
      label: 'Programación',
      options: [
        { value: 'all', label: 'Todos' },
        { value: 'yes', label: 'Programados', test: (r) => r.scheduled },
        { value: 'no', label: 'No programados', test: (r) => !r.scheduled },
      ],
    },
    enabledFilter,
    appFilter,
  ],
});
