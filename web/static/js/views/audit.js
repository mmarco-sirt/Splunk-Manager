// Registro de auditoría: qué se ha cambiado, en qué cliente, cuándo, desde dónde (web o CLI) y con qué resultado.
import { get } from '../api.js';
import { state } from '../core.js';
import {
  h, clear, button, badge, pageHeader, DataTable, openDrawer, errorBox, loadingRows, withBusy, fmtDate, fmtRelative, kv,
  section, codeBlock, infoBox,
} from '../ui.js';

const RESULT_UI = {
  ok: ['Correcto', 'good'], iniciado: ['Iniciada', 'accent'], success: ['Correcta', 'good'], partial: ['Con errores', 'warn'],
  error: ['Error', 'bad'], cancelled: ['Cancelada', 'outline'], simulado: ['Simulación', 'outline'],
};
const GROUPS = {
  alert: 'Alertas y reportes', macro: 'Macros', hec: 'Tokens HEC', allowlist: 'Listas de IP', outbound: 'Puertos de salida',
  token: 'Tokens', stack: 'Stack', app: 'Apps', addons: 'Add-ons', job: 'Tareas', client: 'Clientes', restore: 'Restores',
  backup: 'Backups', splunkbase: 'Splunkbase',
};

const groupOf = (action) => GROUPS[String(action || '').split('.')[0]] || action;

function resultBadge(result) {
  const [text, variant] = RESULT_UI[result] || [result || '—', ''];
  return badge(text, variant);
}

function clientsText(c) {
  return Array.isArray(c) ? c.join(', ') : c || '';
}

function openEntry(e) {
  openDrawer({
    title: e.title,
    subtitle: `${fmtDate(e.time)} · ${e.source} · ${e.user || ''}`,
    width: 640,
    body: [
      section('Datos', kv([
        ['Acción', h('code', {}, e.action)], ['Cliente', clientsText(e.client) || null], ['Resultado', resultBadge(e.result)],
        ['Origen', e.source === 'web' ? 'Interfaz web' : 'Consola (CLI)'], ['Usuario de Windows', e.user || null], ['Tarea', e.job ? h('code', {}, e.job) : null],
      ])),
      e.detail && Object.keys(e.detail).length ? section('Detalle', codeBlock(JSON.stringify(e.detail, null, 2))) : null,
    ],
  });
}

export default {
  id: 'audit',
  title: 'Auditoría',
  icon: 'clipboard',
  render(root) {
    const body = h('div', {}, loadingRows(6));
    let table = null;
    const clientSelect = h('select', { class: 'select', style: { width: 'auto' }, 'aria-label': 'Cliente' },
      h('option', { value: '' }, 'Todos los clientes'), state.clients.map((c) => h('option', { value: c.name }, c.name)));
    const actionSelect = h('select', { class: 'select', style: { width: 'auto' }, 'aria-label': 'Acción' }, h('option', { value: '' }, 'Todas las acciones'));
    const exportLink = h('a', { class: 'btn', href: '/api/audit/export' }, 'Exportar CSV');
    const refreshBtn = button('Actualizar', { iconName: 'refresh' });

    const params = () => ({ client: clientSelect.value, action: actionSelect.value, limit: 1000 });
    const updateExport = () => {
      const q = new URLSearchParams(Object.entries(params()).filter(([k, v]) => v && k !== 'limit')).toString();
      exportLink.href = `/api/audit/export${q ? `?${q}` : ''}`;
    };

    const load = async () => {
      try {
        const data = await get('/api/audit', params());
        updateExport();
        const current = actionSelect.value;
        clear(actionSelect).append(h('option', { value: '' }, 'Todas las acciones'),
          [...new Set(data.actions.map((a) => a.split('.')[0]))].sort().map((g) => h('option', { value: g, selected: g === current }, groupOf(g))));
        if (!table) {
          table = new DataTable({
            rows: data.entries, rowKey: (e) => `${e.time}/${e.action}/${e.title}`, compact: true,
            searchText: (e) => `${e.title} ${e.action} ${clientsText(e.client)} ${e.user} ${JSON.stringify(e.detail || {})}`,
            searchPlaceholder: 'Buscar en el registro…',
            initialSort: { key: 'time', dir: 'desc' },
            emptyIcon: 'clipboard', emptyTitle: 'Todavía no hay entradas', emptyText: 'Aquí aparecerán las ediciones, restores, copias y operaciones de ACS hechas desde la web o el CLI.',
            onRowClick: openEntry,
            filters: [{
              id: 'result',
              options: [
                { value: 'all', label: 'Todo' },
                { value: 'changes', label: 'Cambios', test: (e) => !['iniciado', 'simulado'].includes(e.result) && !e.action.startsWith('job.report') },
                { value: 'errors', label: 'Con error', test: (e) => ['error', 'partial'].includes(e.result) },
                { value: 'sim', label: 'Simulaciones', test: (e) => e.result === 'simulado' },
              ],
            }],
            columns: [
              { key: 'time', label: 'Cuándo', defaultDir: 'desc', render: (e) => h('div', {}, h('div', { class: 'cell-main', title: fmtDate(e.time) }, fmtRelative(e.time)), h('div', { class: 'cell-sub' }, fmtDate(e.time))) },
              { key: 'title', label: 'Qué', render: (e) => h('div', { style: { maxWidth: '460px' } }, h('div', { class: 'cell-main truncate', title: e.title }, e.title), h('div', { class: 'cell-sub' }, groupOf(e.action), ' · ', h('code', {}, e.action))) },
              { key: 'client', label: 'Cliente', sort: (e) => clientsText(e.client), render: (e) => clientsText(e.client) || h('span', { class: 'muted' }, '—') },
              { key: 'source', label: 'Origen', render: (e) => badge(e.source === 'web' ? 'Web' : 'CLI', e.source === 'web' ? 'accent' : '') },
              { key: 'user', label: 'Usuario', render: (e) => e.user || h('span', { class: 'muted' }, '—') },
              { key: 'result', label: 'Resultado', render: (e) => resultBadge(e.result) },
            ],
          });
          clear(body).append(table.el);
        } else {
          table.setRows(data.entries);
        }
      } catch (e) {
        clear(body).append(errorBox(e.message));
      }
    };
    clientSelect.addEventListener('change', load);
    actionSelect.addEventListener('change', load);
    refreshBtn.addEventListener('click', () => withBusy(refreshBtn, load));

    clear(root).append(
      pageHeader({
        title: 'Auditoría',
        subtitle: 'Registro de todos los cambios hechos con Splunk Manager (web y CLI): ediciones, restores, copias entre clientes, add-ons y operaciones de ACS. Se guarda en logs/auditoria.jsonl.',
        actions: [clientSelect, actionSelect, exportLink, refreshBtn],
      }),
      infoBox('Las credenciales nunca se registran. Los backups «antes de editar» guardan el contenido anterior; aquí queda quién, qué, cuándo y el resultado.'),
      h('div', { style: { marginTop: '16px' } }, body));
    load();
  },
};
