// Archivos de backups/: inventario por carpeta, visor, descarga, importación, borrado y restore.
import { get, post, del, enc } from '../api.js';
import { state, navigate } from '../core.js';
import {
  h, append, clear, icon, button, badge, pageHeader, DataTable, openDrawer, confirmDialog, toast, errorBox, loadingRows,
  section, codeBlock, fmtNum, fmtBytes, fmtDate, fmtRelative, withBusy, emptyState, infoBox,
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

const fileUrl = (folder, name) => `/api/backups/${enc(folder)}/${enc(name)}`;
const versionUrl = (folder, name, version) => `${fileUrl(folder, name)}/versions/${enc(version)}`;

function contentView(data) {
  const json = JSON.stringify(data.data, null, 2);
  return [
    data.items.length ? section(`Elementos (${fmtNum(data.items.length)})`,
      h('div', { class: 'badges', style: { maxHeight: '180px', overflow: 'auto' } }, data.items.map((n) => badge(n)))) : null,
    section('Contenido', json.length > MAX_PREVIEW
      ? [codeBlock(`${json.slice(0, MAX_PREVIEW)}\n…`, { maxHeight: '60vh' }), h('p', { class: 'muted' }, 'Vista previa truncada. Descargue el archivo para verlo completo.')]
      : codeBlock(json, { maxHeight: '60vh' })),
  ];
}

function diffLines(lines) {
  return h('pre', { class: 'diff-lines' }, lines.map((line) => h('span', {
    class: line.startsWith('+') ? 'add' : line.startsWith('-') ? 'del' : line.startsWith('@@') ? 'hunk' : '',
  }, line || ' ')));
}

function diffView(diff) {
  const total = diff.added.length + diff.removed.length + diff.changed.length;
  const summary = h('div', { class: 'diff-summary' },
    badge(`${fmtNum(diff.added.length)} añadidos`, diff.added.length ? 'good' : ''),
    badge(`${fmtNum(diff.removed.length)} eliminados`, diff.removed.length ? 'bad' : ''),
    badge(`${fmtNum(diff.changed.length)} modificados`, diff.changed.length ? 'warn' : ''),
    badge(`${fmtNum(diff.unchanged)} sin cambios`));
  if (!total) {
    return [summary, emptyState({ iconName: 'checkCircle', title: 'Sin diferencias', text: 'Las dos versiones tienen la misma configuración (se ignoran campos que Splunk cambia solo, como la próxima ejecución).' })];
  }
  return [
    summary,
    diff.added.length ? section('Añadidos', h('div', { class: 'badges' }, diff.added.map((n) => badge(n, 'good')))) : null,
    diff.removed.length ? section('Eliminados', h('div', { class: 'badges' }, diff.removed.map((n) => badge(n, 'bad')))) : null,
    diff.changed.length ? section('Modificados', diff.changed.map((item, i) => h('details', { class: 'diff-item', open: i < 3 },
      h('summary', {}, icon('chevronRight'), item.name, h('span', { class: 'muted' }, `${item.fields.length} ${item.fields.length === 1 ? 'campo' : 'campos'}`)),
      h('div', { class: 'diff-fields' }, item.fields.map((f) => h('div', {},
        h('div', { class: 'diff-field-name' }, f.field),
        f.diff
          ? diffLines(f.diff)
          : h('div', { class: 'diff-values' },
            h('div', { class: 'diff-old' }, f.before === null ? '(no existía)' : f.before || '(vacío)'),
            h('span', { class: 'arrow' }, '→'),
            h('div', { class: 'diff-new' }, f.after === null ? '(eliminado)' : f.after || '(vacío)')))))))) : null,
  ];
}

function openViewer(folder, file, onChanged) {
  let tab = 'content';
  const tabs = h('div', { class: 'tabs', role: 'tablist' });
  const content = h('div');
  const restoreFile = (version) => {
    if (!requireClient()) return;
    drawer.close();
    openRestoreModal({ folder, files: [file.name], version });
  };

  const drawer = openDrawer({
    title: file.name,
    subtitle: `backups/${folder} · ${fmtBytes(file.size)} · ${fmtDate(file.mtime)}`,
    width: 880,
    actions: [
      h('a', { class: 'btn btn-sm', href: downloadUrl(folder, file.name) }, icon('download'), 'Descargar'),
      button('Restaurar…', { size: 'sm', variant: 'primary', iconName: 'restore', onClick: () => restoreFile(null) }),
      button('Eliminar', { size: 'sm', variant: 'ghost', iconName: 'trash', onClick: () => deleteFiles(folder, [file.name], () => { drawer.close(); onChanged(); }) }),
    ],
    body: [tabs, content],
  });

  function renderTabs() {
    clear(tabs);
    for (const [key, label, iconName] of [['content', 'Contenido', 'file'], ['history', `Historial (${fmtNum(file.versions || 0)})`, 'history']]) {
      tabs.append(h('button', {
        class: `tab ${tab === key ? 'active' : ''}`, type: 'button', role: 'tab', 'aria-selected': String(tab === key),
        onclick: () => { tab = key; renderTabs(); if (key === 'content') showContent(); else showHistory(); },
      }, icon(iconName), label));
    }
  }

  function showContent(version) {
    clear(content).append(loadingRows(8));
    get(version ? versionUrl(folder, file.name, version.id) : fileUrl(folder, file.name)).then((data) => {
      append(clear(content), [
        version ? infoBox([`Versión del ${fmtDate(version.time)}. `,
          h('a', { href: '#', onclick: (e) => { e.preventDefault(); showContent(); } }, 'Ver el archivo actual')]) : null,
        contentView(data)]);
    }).catch((e) => clear(content).append(errorBox(e.message)));
  }

  async function showHistory() {
    clear(content).append(loadingRows(6));
    let versions;
    try {
      versions = await get(`${fileUrl(folder, file.name)}/versions`);
    } catch (e) {
      clear(content).append(errorBox(e.message));
      return;
    }
    if (!versions.length) {
      clear(content).append(emptyState({
        iconName: 'history', title: 'Todavía no hay historial',
        text: 'Cada vez que se haga un backup de este archivo (desde la web, el CLI o la aplicación) se guardará una versión con fecha.',
      }));
      return;
    }

    const describe = (v) => `${fmtDate(v.time)} · ${v.items === null ? 'JSON no válido' : `${fmtNum(v.items)} elem.`}`;
    const fromSelect = h('select', { class: 'select' }, versions.map((v) => h('option', { value: v.id }, describe(v))));
    const toSelect = h('select', { class: 'select' }, h('option', { value: 'current' }, 'Archivo actual'), versions.map((v) => h('option', { value: v.id }, describe(v))));
    fromSelect.value = (versions[1] || versions[0]).id;
    const diffSlot = h('div');

    const compare = async (from, to) => {
      fromSelect.value = from;
      toSelect.value = to;
      clear(diffSlot).append(loadingRows(4));
      try {
        const diff = await get(`${fileUrl(folder, file.name)}/diff`, { from, to });
        append(clear(diffSlot), diffView(diff));
        diffSlot.scrollIntoView({ behavior: 'smooth', block: 'start' });
      } catch (e) {
        clear(diffSlot).append(errorBox(e.message));
      }
    };
    const compareBtn = button('Comparar', { iconName: 'compare', variant: 'primary' });
    compareBtn.addEventListener('click', () => withBusy(compareBtn, () => compare(fromSelect.value, toSelect.value)));

    const table = new DataTable({
      rows: versions,
      rowKey: (v) => v.id,
      compact: true,
      searchText: (v) => fmtDate(v.time),
      searchPlaceholder: 'Buscar por fecha…',
      initialSort: { key: 'time', dir: 'desc' },
      columns: [
        { key: 'time', label: 'Versión', defaultDir: 'desc', render: (v) => h('div', {}, h('div', { class: 'cell-main' }, fmtDate(v.time)), h('div', { class: 'cell-sub' }, v.id === versions[0].id ? 'la más reciente' : fmtRelative(v.time))) },
        { key: 'items', label: 'Elementos', className: 'num', render: (v) => (v.items === null ? badge('JSON no válido', 'bad') : fmtNum(v.items)) },
        { key: 'size', label: 'Tamaño', className: 'num', render: (v) => fmtBytes(v.size) },
        {
          key: 'actions', label: '', className: 'col-actions', sortable: false,
          render: (v) => {
            const index = versions.findIndex((x) => x.id === v.id);
            const previous = versions[index + 1];
            return h('div', { class: 'row-actions' },
              button('', { size: 'sm', variant: 'ghost', iconName: 'eye', title: 'Ver esta versión', onClick: () => { tab = 'content'; renderTabs(); showContent(v); } }),
              previous ? button('', { size: 'sm', variant: 'ghost', iconName: 'history', title: 'Comparar con la versión anterior', onClick: () => compare(previous.id, v.id) }) : null,
              button('', { size: 'sm', variant: 'ghost', iconName: 'compare', title: 'Comparar con el archivo actual', onClick: () => compare(v.id, 'current') }),
              h('a', { class: 'btn btn-sm btn-ghost btn-icon', href: `${versionUrl(folder, file.name, v.id)}/download`, title: 'Descargar esta versión', 'aria-label': 'Descargar esta versión' }, icon('download')),
              button('', { size: 'sm', variant: 'ghost', iconName: 'restore', title: 'Restaurar esta versión', onClick: () => restoreFile(v) }));
          },
        },
      ],
    });

    clear(content).append(
      h('p', { class: 'muted', style: { margin: '0 0 12px', fontSize: '12.5px' } },
        'Se guarda una versión cada vez que el backup cambia (las 30 más recientes). Al comparar se ignoran los campos que Splunk cambia solo.'),
      h('div', { class: 'compare-bar' },
        h('div', { class: 'field' }, h('label', {}, 'Desde'), fromSelect),
        h('div', { class: 'field' }, h('label', {}, 'Hasta'), toSelect),
        compareBtn),
      table.el,
      h('div', { style: { marginTop: '16px' } }, diffSlot));
  }

  renderTabs();
  showContent();
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
          {
            key: 'versions', label: 'Historial', className: 'num', defaultDir: 'desc',
            render: (f) => (f.versions ? h('span', { title: 'Versiones guardadas' }, icon('history'), ` ${fmtNum(f.versions)}`) : h('span', { class: 'muted' }, '—')),
          },
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
