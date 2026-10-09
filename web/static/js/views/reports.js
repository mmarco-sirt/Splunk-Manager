// Informes: generación (Excel, HTML imprimible y PDF) de uno o varios clientes y archivos generados.
import { get, del, enc } from '../api.js';
import { state, on, startJob } from '../core.js';
import {
  h, clear, icon, button, badge, pageHeader, DataTable, openModal, confirmDialog, toast, errorBox, loadingRows, segmented,
  fmtBytes, fmtDate, fmtRelative, withBusy, infoBox,
} from '../ui.js';

const PREFS_KEY = 'sm.report';
const FORMAT_UI = {
  xlsx: { label: 'Excel', icon: 'sheet', variant: 'good' },
  html: { label: 'HTML', icon: 'fileText', variant: 'accent' },
  pdf: { label: 'PDF', icon: 'printer', variant: 'bad' },
};
const DEFAULT_SECTIONS = ['health', 'ingest'];

function readPrefs() {
  try { return JSON.parse(localStorage.getItem(PREFS_KEY)) || {}; } catch { return {}; }
}

function savePrefs(prefs) {
  try { localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)); } catch { /* sin almacenamiento */ }
}

// Mismo umbral y fuentes ignoradas que la vista de Salud, para que el informe coincida con lo que se ve allí
function healthOptions() {
  let threshold = 24;
  let ignored = [];
  try {
    threshold = Number(localStorage.getItem('sm.silentHours')) || 24;
    ignored = JSON.parse(localStorage.getItem('sm.silentIgnored')) || [];
  } catch { /* sin almacenamiento */ }
  return { threshold, ignored };
}

const reportUrl = (name, inline = false) => `/api/reports/${enc(name)}${inline ? '?inline=1' : ''}`;

/** Diálogo para generar un informe. clients: preselección (por defecto, los de la última vez o el activo). */
export function openReportModal({ clients, sections } = {}) {
  const prefs = readPrefs();
  const names = state.clients.map((c) => c.name);
  const selected = new Set((clients || prefs.clients || (state.client ? [state.client] : names)).filter((n) => names.includes(n)));
  const chosenSections = new Set(sections || prefs.sections || DEFAULT_SECTIONS);
  const formats = state.meta?.report_formats || { xlsx: 'Excel', html: 'HTML' };
  const chosenFormats = new Set((prefs.formats || ['xlsx', 'pdf']).filter((f) => f in formats));
  if (!chosenFormats.size) chosenFormats.add('xlsx');
  let days = prefs.days || 30;
  const { threshold, ignored } = healthOptions();

  const chips = h('div', { class: 'client-chips' });
  const counter = h('span', { class: 'muted', style: { fontSize: '12.5px' } });
  const renderChips = () => {
    clear(chips);
    for (const name of names) {
      const cb = h('input', { type: 'checkbox', checked: selected.has(name) });
      cb.addEventListener('change', () => { if (cb.checked) selected.add(name); else selected.delete(name); renderChips(); });
      chips.append(h('label', { class: `client-chip ${selected.has(name) ? 'selected' : ''}` }, cb, name));
    }
    counter.textContent = `${selected.size} de ${names.length} clientes`;
  };
  renderChips();

  const daysSlot = h('div');
  const renderDays = () => {
    clear(daysSlot);
    if (!chosenSections.has('ingest')) return;
    daysSlot.append(h('div', { class: 'field' }, h('span', { class: 'field-label' }, 'Periodo de ingesta'),
      h('div', {}, segmented([7, 30, 90].map((d) => ({ value: d, label: `${d} días` })), days, (v) => { days = v; }))));
  };

  const sectionChoices = h('div', { class: 'choices' });
  const renderSections = () => {
    clear(sectionChoices);
    for (const [key, label] of Object.entries(state.meta?.report_sections || {})) {
      const cb = h('input', { type: 'checkbox', checked: chosenSections.has(key) });
      cb.addEventListener('change', () => {
        if (cb.checked) chosenSections.add(key); else chosenSections.delete(key);
        renderSections();
        renderDays();
      });
      const [title, ...rest] = label.split(':');
      sectionChoices.append(h('label', { class: `choice ${chosenSections.has(key) ? 'selected' : ''}` }, cb,
        h('div', {}, h('div', { class: 'choice-title' }, title), rest.length ? h('div', { class: 'choice-desc' }, rest.join(':').trim()) : null)));
    }
  };
  renderSections();
  renderDays();

  const formatChoices = h('div', { class: 'client-chips' });
  const renderFormats = () => {
    clear(formatChoices);
    for (const [key, label] of Object.entries(formats)) {
      const cb = h('input', { type: 'checkbox', checked: chosenFormats.has(key) });
      cb.addEventListener('change', () => { if (cb.checked) chosenFormats.add(key); else chosenFormats.delete(key); renderFormats(); });
      formatChoices.append(h('label', { class: `client-chip ${chosenFormats.has(key) ? 'selected' : ''}` }, cb, icon(FORMAT_UI[key]?.icon || 'file'), label));
    }
  };
  renderFormats();

  openModal({
    title: 'Nuevo informe',
    subtitle: 'Se genera en segundo plano y se guarda en la carpeta informes/.',
    size: 'lg',
    body: [
      h('div', { class: 'field' },
        h('div', { class: 'msearch-head' }, h('strong', {}, 'Clientes'), counter,
          button('Todos', { size: 'sm', onClick: () => { names.forEach((n) => selected.add(n)); renderChips(); } }),
          button('Ninguno', { size: 'sm', variant: 'ghost', onClick: () => { selected.clear(); renderChips(); } })),
        chips),
      h('div', { class: 'field' }, h('span', { class: 'field-label' }, 'Contenido'), sectionChoices),
      daysSlot,
      h('div', { class: 'field' }, h('span', { class: 'field-label' }, 'Formatos'), formatChoices,
        h('span', { class: 'field-hint' }, formats.pdf ? 'El PDF se genera con Microsoft Edge en segundo plano. El HTML se puede abrir en el navegador e imprimir.'
          : 'Para PDF, abra el HTML y use «Imprimir / Guardar como PDF».')),
      infoBox(`La salud usa el mismo criterio que la sección Salud de clientes: fuente sin datos a partir de ${threshold} h${ignored.length ? ` y ${ignored.length} fuentes ignoradas` : ''}.`),
    ],
    actions: [
      { label: 'Cancelar' },
      {
        label: 'Generar informe', variant: 'primary', icon: 'fileText',
        onClick: async () => {
          const clientsList = names.filter((n) => selected.has(n));
          if (!clientsList.length) throw new Error('Seleccione al menos un cliente.');
          if (!chosenSections.size) throw new Error('Seleccione qué incluir en el informe.');
          if (!chosenFormats.size) throw new Error('Seleccione al menos un formato.');
          const body = {
            clients: clientsList, sections: [...chosenSections], formats: [...chosenFormats],
            options: { days, threshold, ignored },
          };
          savePrefs({ clients: clientsList, sections: body.sections, formats: body.formats, days });
          await startJob('/api/reports', body);
        },
      },
    ],
  });
}

export default {
  id: 'informes',
  title: 'Informes',
  icon: 'fileText',
  render(root) {
    const body = h('div', {}, loadingRows(5));
    let table = null;

    const reload = async () => {
      try {
        const files = await get('/api/reports');
        if (!table) {
          table = new DataTable({
            rows: files,
            selectable: true,
            searchPlaceholder: 'Buscar informe…',
            initialSort: { key: 'mtime', dir: 'desc' },
            emptyIcon: 'fileText',
            emptyTitle: 'Todavía no hay informes',
            emptyText: 'Genere el primero con «Nuevo informe».',
            onRowClick: (f) => window.open(f.format === 'xlsx' ? reportUrl(f.name) : reportUrl(f.name, true), '_blank', 'noopener'),
            filters: [{
              id: 'format',
              options: [{ value: 'all', label: 'Todos' }, ...Object.entries(FORMAT_UI).map(([k, v]) => ({ value: k, label: v.label, test: (f) => f.format === k }))],
            }],
            selectionActions: [{ label: 'Eliminar', icon: 'trash', onClick: (sel) => removeFiles(sel.map((f) => f.name)) }],
            columns: [
              { key: 'name', label: 'Informe', render: (f) => h('span', { class: 'mono' }, f.name) },
              { key: 'format', label: 'Formato', render: (f) => badge(FORMAT_UI[f.format]?.label || f.format, FORMAT_UI[f.format]?.variant || '', FORMAT_UI[f.format]?.icon) },
              { key: 'size', label: 'Tamaño', className: 'num', defaultDir: 'desc', render: (f) => fmtBytes(f.size) },
              { key: 'mtime', label: 'Generado', defaultDir: 'desc', render: (f) => h('span', { title: fmtDate(f.mtime) }, fmtRelative(f.mtime)) },
              {
                key: 'actions', label: '', className: 'col-actions', sortable: false,
                render: (f) => h('div', { class: 'row-actions' },
                  f.format !== 'xlsx' ? h('a', { class: 'btn btn-sm btn-ghost btn-icon', href: reportUrl(f.name, true), target: '_blank', rel: 'noopener', title: 'Abrir', 'aria-label': 'Abrir' }, icon('external')) : null,
                  h('a', { class: 'btn btn-sm btn-ghost btn-icon', href: reportUrl(f.name), title: 'Descargar', 'aria-label': 'Descargar' }, icon('download')),
                  button('', { size: 'sm', variant: 'ghost', iconName: 'trash', title: 'Eliminar', onClick: () => removeFiles([f.name]) })),
              },
            ],
          });
          clear(body).append(table.el);
        } else {
          table.setRows(files);
        }
      } catch (e) {
        clear(body).append(errorBox(e.message));
      }
    };

    async function removeFiles(names) {
      const ok = await confirmDialog({
        title: names.length === 1 ? '¿Eliminar el informe?' : `¿Eliminar ${names.length} informes?`,
        message: names.length === 1 ? `Se borrará ${names[0]} de la carpeta informes/.` : 'Se borrarán de la carpeta informes/.',
        confirmLabel: 'Eliminar', danger: true,
      });
      if (!ok) return;
      const failed = [];
      for (const name of names) {
        try { await del(`/api/reports/${enc(name)}`); } catch (e) { failed.push(`${name}: ${e.message}`); }
      }
      if (failed.length) toast(`No se pudieron eliminar: ${failed.join(' · ')}`, 'error');
      else toast(names.length === 1 ? 'Informe eliminado' : `${names.length} informes eliminados`, 'success');
      table?.clearSelection();
      reload();
    }

    const refreshBtn = button('Actualizar', { iconName: 'refresh' });
    refreshBtn.addEventListener('click', () => withBusy(refreshBtn, reload));

    clear(root).append(
      pageHeader({
        title: 'Informes',
        subtitle: 'Informes de uno o varios clientes con su salud, la ingesta, el inventario de alertas, los índices y los usuarios, en Excel, HTML o PDF.',
        actions: [refreshBtn, button('Nuevo informe', { variant: 'primary', iconName: 'plus', onClick: () => openReportModal() })],
      }),
      body);
    reload();

    return on('job-finished', (job) => { if (job.kind === 'report') reload(); });
  },
};
