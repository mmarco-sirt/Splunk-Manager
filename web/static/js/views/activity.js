// Tareas en segundo plano: panel con el log en vivo y vista de actividad.
import { get, del, enc } from '../api.js';
import { state, on, refreshJobs } from '../core.js';
import {
  h, append, clear, icon, button, openDrawer, pageHeader, DataTable, statusLabel, fmtDate, fmtRelative, fmtDuration, errorBox,
} from '../ui.js';

const STATUS = {
  running: { text: 'En curso', variant: 'muted', icon: 'loader' },
  success: { text: 'Completada', variant: 'good', icon: 'checkCircle' },
  partial: { text: 'Con errores', variant: 'warn', icon: 'alert' },
  error: { text: 'Fallida', variant: 'bad', icon: 'xCircle' },
};

export function jobStatus(job) {
  const s = STATUS[job.status] || STATUS.running;
  const el = statusLabel(s.text, s.variant, job.status === 'running' ? null : s.icon);
  if (job.status === 'running') el.prepend(h('span', { class: 'spinner' }));
  return el;
}

function lineClass(line) {
  if (/❌|\[ERROR\]|\bError\b|\bERROR\b|No se pudo/.test(line)) return 'l-err';
  if (/⚠️|ADVERTENCIA|\bAviso\b/.test(line)) return 'l-warn';
  if (/^━━|^▶|^===|^➡/.test(line.trim())) return 'l-head';
  if (/✅|completad|correctamente|\bcread[oa]\b|\bactualizad[oa]\b|Conectado/.test(line)) return 'l-ok';
  return '';
}

function fileHref(path) {
  const [folder, ...rest] = path.split('/');
  return `/api/backups/${enc(folder)}/${enc(rest.join('/'))}/download`;
}

export function openJobDrawer(jobId) {
  let next = 0;
  let timer = null;
  let stopped = false;
  const consoleEl = h('pre', { class: 'console', 'aria-live': 'polite' }, h('span', { class: 'console-empty' }, 'Esperando salida…'));
  const metaEl = h('div', { class: 'job-meta' });
  const filesEl = h('div', { class: 'job-files' });
  const errorEl = h('div');

  const drawer = openDrawer({
    title: 'Tarea',
    subtitle: '',
    body: [errorEl, metaEl, consoleEl, filesEl],
    width: 760,
  });
  drawer.onClose = () => { stopped = true; clearTimeout(timer); };

  const renderMeta = (job) => {
    drawer.setTitle(job.title, job.clients.join(', '));
    append(clear(metaEl), [
      jobStatus(job),
      h('span', {}, icon('clock'), ' ', fmtDate(job.started)),
      h('span', {}, 'Duración: ', fmtDuration((job.finished || Date.now() / 1000) - job.started)),
      job.errors ? h('span', { class: 'status status-bad' }, `${job.errors} errores`) : null,
      job.warnings ? h('span', { class: 'status status-warn' }, `${job.warnings} avisos`) : null]);
    clear(filesEl);
    if (job.files?.length) {
      filesEl.append(h('h4', { class: 'section-title', style: { marginTop: '0' } }, 'Archivos de backup generados o modificados'),
        h('ul', { class: 'list-plain' }, job.files.map((f) => h('li', {},
          h('a', { class: 'file-link', href: fileHref(f) }, icon('download'), f)))));
    }
  };

  const poll = async () => {
    try {
      const job = await get(`/api/jobs/${enc(jobId)}`, { since: next });
      if (stopped) return;
      if (next === 0 && job.lines.length) clear(consoleEl);
      const atBottom = consoleEl.scrollHeight - consoleEl.scrollTop - consoleEl.clientHeight < 40;
      for (const line of job.lines) {
        consoleEl.append(h('span', { class: lineClass(line) }, line), '\n');
      }
      next = job.next;
      if (atBottom) consoleEl.scrollTop = consoleEl.scrollHeight;
      renderMeta(job);
      if (job.status === 'running') timer = setTimeout(poll, 700);
      else if (next === 0) clear(consoleEl).append(h('span', { class: 'console-empty' }, 'La tarea no produjo salida.'));
    } catch (e) {
      clear(errorEl).append(errorBox(e.message));
    }
  };
  poll();
  return drawer;
}

export default {
  id: 'activity',
  title: 'Actividad',
  icon: 'activity',
  render(root) {
    const table = new DataTable({
      rowKey: (j) => j.id,
      searchText: (j) => `${j.title} ${j.clients.join(' ')}`,
      searchPlaceholder: 'Buscar tareas…',
      initialSort: { key: 'started', dir: 'desc' },
      emptyIcon: 'activity',
      emptyTitle: 'Todavía no hay tareas',
      emptyText: 'Los backups y restores que lance aparecerán aquí con su log completo.',
      onRowClick: (j) => openJobDrawer(j.id),
      filters: [{
        id: 'status',
        options: [
          { value: 'all', label: 'Todas' },
          { value: 'running', label: 'En curso', test: (j) => j.status === 'running' },
          { value: 'problems', label: 'Con problemas', test: (j) => j.status === 'partial' || j.status === 'error' },
        ],
      }],
      columns: [
        { key: 'status', label: 'Estado', render: jobStatus, sort: (j) => j.status },
        { key: 'title', label: 'Tarea', render: (j) => h('div', {}, h('div', { class: 'cell-main' }, j.title), h('div', { class: 'cell-sub' }, j.kind === 'backup' ? 'Backup' : 'Restore')) },
        { key: 'clients', label: 'Clientes', render: (j) => j.clients.join(', '), sort: (j) => j.clients.join(',') },
        { key: 'started', label: 'Inicio', defaultDir: 'desc', render: (j) => h('span', { title: fmtDate(j.started) }, fmtRelative(j.started)) },
        { key: 'duration', label: 'Duración', className: 'num', sort: (j) => (j.finished || Date.now() / 1000) - j.started, render: (j) => fmtDuration((j.finished || Date.now() / 1000) - j.started) },
        { key: 'errors', label: 'Errores', className: 'num', render: (j) => (j.errors ? h('span', { class: 'status status-bad' }, String(j.errors)) : h('span', { class: 'muted' }, '0')) },
        { key: 'files', label: 'Archivos', className: 'num', sort: (j) => j.files.length, render: (j) => String(j.files.length) },
      ],
    });

    const clearBtn = button('Limpiar finalizadas', {
      iconName: 'trash',
      onClick: async () => { await del('/api/jobs'); await refreshJobs(); },
    });

    clear(root).append(
      pageHeader({
        title: 'Actividad',
        subtitle: 'Historial de las tareas lanzadas desde la web durante esta sesión del servidor. Pulse una fila para ver su log.',
        actions: [button('Actualizar', { iconName: 'refresh', onClick: () => refreshJobs() }), clearBtn],
      }),
      table.el);

    table.setRows(state.jobs);
    refreshJobs().catch(() => {});
    return on('jobs', () => table.setRows(state.jobs));
  },
};
