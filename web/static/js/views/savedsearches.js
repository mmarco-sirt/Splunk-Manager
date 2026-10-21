// Alertas y reportes (saved searches): listado en vivo, detalle, edición, habilitar/deshabilitar, ejecutar, backup y restore.
import { post } from '../api.js';
import { state, loadResource, cachedAt, navigate } from '../core.js';
import {
  h, clear, icon, button, badge, pageHeader, DataTable, openDrawer, openModal, confirmDialog, toast, kv, section,
  codeBlock, errorBox, loadingRows, fmtRelative, withBusy, fmtNum,
} from '../ui.js';
import { openBackupModal, openRestoreModal, runBackup } from '../ops.js';
import { openEditor, openBulkEditor, setEnabled, mergeRows } from '../alert-editor.js';

const SEVERITY = { 1: ['Debug', ''], 2: ['Info', ''], 3: ['Baja', 'accent'], 4: ['Media', 'warn'], 5: ['Alta', 'bad'], 6: ['Crítica', 'bad'] };

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
  return post('/api/splunk/saved-searches/action', { client: state.client, name: row.name, app: row.app, owner: row.owner, action: name });
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
    subtitle: [row.app, row.owner, row.sharing].filter(Boolean).join(' · '),
    actions,
    body: [
      h('div', { class: 'badges', style: { marginBottom: '16px' } },
        stateBadge(row),
        row.is_alert ? badge('Alerta', 'accent', 'bell') : badge('Reporte', '', 'report'),
        row.is_cu ? badge('CU-') : null,
        row.is_es ? badge('Enterprise Security', 'accent') : null,
        row.scheduled ? badge('Programada', '', 'clock') : null),
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
              rowKey: (r) => `${r.app}/${r.owner}/${r.name}`,
              selectable: true,
              initialQuery: params.q || '',
              searchText: (r) => `${r.name} ${r.es_label} ${r.app} ${r.search}`,
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
                    h('div', { class: 'cell-main' }, r.name),
                    (r.es_label || r.description) ? h('div', { class: 'cell-sub truncate', style: { maxWidth: '460px' } }, r.es_label || r.description) : null),
                },
                { key: 'app', label: 'App', render: (r) => h('div', {}, r.app, h('div', { class: 'cell-sub' }, r.owner)) },
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

const stateFilter = {
  id: 'state',
  options: [
    { value: 'all', label: 'Todas' },
    { value: 'on', label: 'Habilitadas', test: (r) => !r.disabled },
    { value: 'off', label: 'Deshabilitadas', test: (r) => r.disabled },
  ],
};

export const alertsView = makeView({
  id: 'alerts',
  title: 'Alertas',
  iconName: 'bell',
  isAlertView: true,
  subtitle: () => `Alertas y casos de uso de Enterprise Security de ${state.client}. Pulse una fila para ver su SPL y configuración, o edítela con el lápiz.`,
  rowFilter: (r) => r.is_alert || r.is_es,
  filters: [
    {
      id: 'kind',
      options: [
        { value: 'all', label: 'Todas' },
        { value: 'cu', label: 'CU-', test: (r) => r.is_cu },
        { value: 'es', label: 'Enterprise Security', test: (r) => r.is_es },
        { value: 'other', label: 'Otras', test: (r) => !r.is_cu && !r.is_es },
      ],
    },
    stateFilter,
  ],
});

export const reportsView = makeView({
  id: 'reports',
  title: 'Reportes',
  iconName: 'report',
  isAlertView: false,
  subtitle: () => `Saved searches de ${state.client} que no son alertas.`,
  rowFilter: (r) => !r.is_alert && !r.is_es,
  filters: [
    {
      id: 'sched',
      options: [
        { value: 'all', label: 'Todos' },
        { value: 'yes', label: 'Programados', test: (r) => r.scheduled },
        { value: 'no', label: 'No programados', test: (r) => !r.scheduled },
      ],
    },
    stateFilter,
  ],
});
