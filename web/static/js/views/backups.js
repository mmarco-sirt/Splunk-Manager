// Archivos de backups/: inventario por carpeta, visor, descarga, importación, borrado y restore.
import { get, post, del, enc } from '../api.js';
import { state, navigate } from '../core.js';
import {
  h, clear, icon, button, badge, pageHeader, DataTable, openDrawer, confirmDialog, toast, errorBox, loadingRows,
  section, codeBlock, fmtNum, fmtBytes, fmtDate, fmtRelative, withBusy,
} from '../ui.js';
import { loadBackups, invalidateBackups, openRestoreModal, FOLDER_ICONS } from '../ops.js';

const MAX_PREVIEW = 300000;

const downloadUrl = (folder, name) => `/api/backups/${enc(folder)}/${enc(name)}/download`;

function requireClient() {
  if (state.client) return true;
  toast('Seleccione antes un cliente en la barra superior.', 'warn');
  return false;
}

async function deleteFiles(folder, names, onDone) {
  const ok = await confirmDialog({
    title: names.length === 1 ? '¿Eliminar el archivo de backup?' : `¿Eliminar ${names.length} archivos de backup?`,
    message: names.length === 1 ? `Se borrará ${names[0]} de la carpeta backups/${folder}. No se puede deshacer.`
      : `Se borrarán de la carpeta backups/${folder}. No se puede deshacer.`,
    confirmLabel: 'Eliminar', danger: true,
  });
  if (!ok) return;
  const failed = [];
  for (const name of names) {
    try { await del(`/api/backups/${enc(folder)}/${enc(name)}`); } catch (e) { failed.push(`${name}: ${e.message}`); }
  }
  invalidateBackups();
  if (failed.length) toast(`No se pudieron eliminar: ${failed.join(' · ')}`, 'error');
  else toast(names.length === 1 ? 'Archivo eliminado' : `${names.length} archivos eliminados`, 'success');
  onDone();
}

function openViewer(folder, file, onChanged) {
  const drawer = openDrawer({
    title: file.name,
    subtitle: `backups/${folder} · ${fmtBytes(file.size)} · ${fmtDate(file.mtime)}`,
    width: 820,
    actions: [
      h('a', { class: 'btn btn-sm', href: downloadUrl(folder, file.name) }, icon('download'), 'Descargar'),
      button('Restaurar…', { size: 'sm', variant: 'primary', iconName: 'restore', onClick: () => { if (requireClient()) { drawer.close(); openRestoreModal({ folder, files: [file.name] }); } } }),
      button('Eliminar', { size: 'sm', variant: 'ghost', iconName: 'trash', onClick: () => deleteFiles(folder, [file.name], () => { drawer.close(); onChanged(); }) }),
    ],
    body: loadingRows(8),
  });

  get(`/api/backups/${enc(folder)}/${enc(file.name)}`).then((data) => {
    const json = JSON.stringify(data.data, null, 2);
    drawer.setBody([
      data.items.length ? section(`Elementos (${fmtNum(data.items.length)})`,
        h('div', { class: 'badges', style: { maxHeight: '180px', overflow: 'auto' } }, data.items.map((n) => badge(n)))) : null,
      section('Contenido', json.length > MAX_PREVIEW
        ? [codeBlock(`${json.slice(0, MAX_PREVIEW)}\n…`, { maxHeight: '60vh' }), h('p', { class: 'muted' }, 'Vista previa truncada. Descargue el archivo para verlo completo.')]
        : codeBlock(json, { maxHeight: '60vh' })),
    ]);
  }).catch((e) => drawer.setBody(errorBox(e.message)));
}

function importFiles(folder, onDone) {
  const input = h('input', { type: 'file', accept: '.json,application/json', multiple: true, style: { display: 'none' } });
  input.addEventListener('change', async () => {
    const files = [...input.files];
    input.remove();
    let ok = 0;
    for (const file of files) {
      try {
        const content = await file.text();
        const res = await post(`/api/backups/${enc(folder)}`, { filename: file.name, content });
        ok++;
        if (res.name !== file.name) toast(`${file.name} se ha guardado como ${res.name}`, 'info');
      } catch (e) {
        toast(`${file.name}: ${e.message}`, 'error');
      }
    }
    if (ok) toast(`${ok} archivo(s) importados en backups/${folder}`, 'success');
    invalidateBackups();
    onDone();
  });
  document.body.append(input);
  input.click();
}

export default {
  id: 'backups',
  title: 'Backups',
  icon: 'archive',
  render(root, params) {
    let folder = params.folder || 'alertas';
    let data = null;
    const tabs = h('div', { class: 'tabs', role: 'tablist' });
    const body = h('div', {}, loadingRows(6));

    const reload = async (force = true) => {
      try {
        data = await loadBackups(force);
        if (!data[folder]) folder = Object.keys(data)[0];
        renderTabs();
        renderTable();
      } catch (e) {
        clear(body).append(errorBox(e.message));
      }
    };

    function renderTabs() {
      clear(tabs);
      for (const [key, meta] of Object.entries(data)) {
        tabs.append(h('button', {
          class: `tab ${key === folder ? 'active' : ''}`, role: 'tab', 'aria-selected': String(key === folder), type: 'button',
          onclick: () => { folder = key; navigate('backups', { folder: key }); },
        }, icon(FOLDER_ICONS[key] || 'file'), meta.label, badge(fmtNum(meta.files.length))));
      }
    }

    function renderTable() {
      const meta = data[folder];
      const table = new DataTable({
        rows: meta.files,
        selectable: true,
        searchPlaceholder: 'Buscar archivo…',
        initialSort: { key: 'mtime', dir: 'desc' },
        emptyIcon: 'archive',
        emptyTitle: `No hay backups de ${meta.label.toLowerCase()}`,
        emptyText: 'Haga un backup desde la sección correspondiente o importe un archivo JSON.',
        onRowClick: (f) => openViewer(folder, f, () => reload()),
        selectionActions: [
          { label: 'Restaurar…', icon: 'restore', variant: 'primary', onClick: (sel) => { if (requireClient()) openRestoreModal({ folder, files: sel.map((f) => f.name) }); } },
          { label: 'Eliminar', icon: 'trash', onClick: (sel) => deleteFiles(folder, sel.map((f) => f.name), () => reload()) },
        ],
        columns: [
          { key: 'name', label: 'Archivo', render: (f) => h('span', { class: 'mono' }, f.name) },
          { key: 'items', label: 'Elementos', className: 'num', defaultDir: 'desc', sort: (f) => f.items ?? -1, render: (f) => (f.items === null ? badge('JSON no válido', 'bad') : fmtNum(f.items)) },
          { key: 'size', label: 'Tamaño', className: 'num', defaultDir: 'desc', render: (f) => fmtBytes(f.size) },
          { key: 'mtime', label: 'Modificado', defaultDir: 'desc', render: (f) => h('span', { title: fmtDate(f.mtime) }, fmtRelative(f.mtime)) },
          {
            key: 'actions', label: '', className: 'col-actions', sortable: false,
            render: (f) => h('div', { class: 'row-actions' },
              button('', { size: 'sm', variant: 'ghost', iconName: 'eye', title: 'Ver contenido', onClick: () => openViewer(folder, f, () => reload()) }),
              h('a', { class: 'btn btn-sm btn-ghost btn-icon', href: downloadUrl(folder, f.name), title: 'Descargar', 'aria-label': 'Descargar' }, icon('download')),
              button('', { size: 'sm', variant: 'ghost', iconName: 'restore', title: 'Restaurar', onClick: () => { if (requireClient()) openRestoreModal({ folder, files: [f.name] }); } }),
              button('', { size: 'sm', variant: 'ghost', iconName: 'trash', title: 'Eliminar', onClick: () => deleteFiles(folder, [f.name], () => reload()) })),
          },
        ],
      });
      clear(body).append(table.el);
    }

    const refreshBtn = button('Actualizar', { iconName: 'refresh' });
    refreshBtn.addEventListener('click', () => withBusy(refreshBtn, () => reload(true)));
    const importBtn = button('Importar JSON…', { iconName: 'upload', onClick: () => importFiles(folder, () => reload()) });

    clear(root).append(
      pageHeader({
        title: 'Backups',
        subtitle: 'Archivos guardados en la carpeta backups/ del servidor. Los mismos que usan el CLI y la aplicación de escritorio.',
        actions: [refreshBtn, importBtn],
      }),
      tabs, body);
    reload(false);
  },
};
