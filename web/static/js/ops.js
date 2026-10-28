// Diálogos de backup y restore reutilizados por las distintas vistas y por las operaciones masivas.
import { get, enc } from './api.js';
import { state, startJob } from './core.js';
import { h, clear, icon, openModal, infoBox, errorBox, fmtNum, fmtDate, fmtBytes, loadingRows } from './ui.js';

export const BACKUP_KINDS = {
  alerts_all: { label: 'Alertas CU-* y TEST', desc: 'Todas las alertas cuyo nombre empieza por CU- o por TEST - CU-, de todas las apps y usuarios.' },
  alerts_tech: { label: 'Por tecnología', desc: 'Alertas CU-* y TEST - CU-* de una tecnología (AD, FW, AV…).' },
  alerts_custom: { label: 'Por texto', desc: 'Alertas CU-* y TEST - CU-* cuyo nombre contiene un texto.' },
  es_all: { label: 'Enterprise Security', desc: 'Casos de uso de ES (app de ES o etiqueta ESCU).' },
  es_custom: { label: 'ES por texto', desc: 'Casos de uso de ES cuyo nombre contiene un texto.' },
  reports: { label: 'Reportes', desc: 'Todas las saved searches que no son alertas.' },
  macros: { label: 'Macros', desc: 'Todas las macros con su definición y argumentos.' },
  users: { label: 'Usuarios', desc: 'Usuarios con roles, email y nombre (sin contraseñas).' },
  roles: { label: 'Roles', desc: 'Roles con capacidades, herencia e índices.' },
};

export const FOLDER_ICONS = { alertas: 'bell', reportes: 'report', macros: 'braces', users: 'users', roles: 'shield', dashboards: 'dashboard' };

export function clientsLabel(clients) {
  return clients.length === 1 ? clients[0] : `${clients.length} clientes`;
}

// --- Backup ------------------------------------------------------------------

/** Campos para elegir un tipo de backup. Devuelve { el, value() } */
export function backupKindPicker(kinds, initial) {
  let kind = initial || kinds[0];
  const techSelect = h('select', { class: 'select' }, (state.meta?.tecnologias || []).map((t) => h('option', { value: t }, t)));
  const textInput = h('input', { class: 'input', placeholder: 'Ej.: Forwarder Down' });
  const extra = h('div');
  const choices = h('div', { class: 'choices' });

  const renderExtra = () => {
    clear(extra);
    if (kind === 'alerts_tech') extra.append(h('div', { class: 'field' }, h('label', {}, 'Tecnología'), techSelect));
    if (kind === 'alerts_custom' || kind === 'es_custom') {
      extra.append(h('div', { class: 'field' }, h('label', {}, 'Texto contenido en el nombre'), textInput,
        h('span', { class: 'field-hint' }, 'Se usa también en el nombre del archivo de backup.')));
    }
  };
  const renderChoices = () => {
    clear(choices);
    for (const k of kinds) {
      const radio = h('input', { type: 'radio', name: 'backup-kind', checked: k === kind });
      const el = h('label', { class: `choice ${k === kind ? 'selected' : ''}` }, radio,
        h('div', {}, h('div', { class: 'choice-title' }, BACKUP_KINDS[k].label), h('div', { class: 'choice-desc' }, BACKUP_KINDS[k].desc)));
      radio.addEventListener('change', () => { kind = k; renderChoices(); renderExtra(); });
      choices.append(el);
    }
  };
  renderChoices();
  renderExtra();

  return {
    el: h('div', {}, kinds.length > 1 ? choices : null, extra),
    value() {
      const options = {};
      if (kind === 'alerts_tech') options.tech = techSelect.value;
      if (kind === 'alerts_custom' || kind === 'es_custom') {
        options.text = textInput.value.trim();
        if (!options.text) throw new Error('Escriba el texto a buscar.');
      }
      return { kind, options };
    },
  };
}

export function openBackupModal({ kinds, initial, clients = [state.client], title = 'Nuevo backup' }) {
  const picker = backupKindPicker(kinds, initial);
  openModal({
    title,
    subtitle: `Cliente: ${clientsLabel(clients)} · se guardará en la carpeta backups/`,
    size: kinds.length > 2 ? 'lg' : '',
    body: picker.el,
    actions: [
      { label: 'Cancelar' },
      {
        label: 'Iniciar backup', variant: 'primary', icon: 'download',
        onClick: async () => {
          const { kind, options } = picker.value();
          await startJob('/api/ops/backup', { clients, kind, options });
        },
      },
    ],
  });
}

export function runBackup(kind, options = {}, clients = [state.client]) {
  return startJob('/api/ops/backup', { clients, kind, options });
}

// --- Restore -----------------------------------------------------------------

let backupsCache = null;

export async function loadBackups(force = false) {
  if (!backupsCache || force) backupsCache = get('/api/backups');
  try {
    return await backupsCache;
  } catch (e) {
    backupsCache = null;
    throw e;
  }
}

export function invalidateBackups() {
  backupsCache = null;
}

/** Lista seleccionable de archivos de una carpeta de backups. Devuelve { el, selected(), onChange } */
export function fileListPicker(folder, { multi = true, preselected = [] } = {}) {
  const selected = new Set(preselected);
  const search = h('input', { class: 'input input-sm', type: 'search', placeholder: 'Filtrar archivos…' });
  const list = h('div', { class: 'file-list' }, loadingRows(4));
  const picker = { selected: () => [...selected], onChange: null };
  let files = [];

  const render = () => {
    clear(list);
    const q = search.value.trim().toLowerCase();
    const visible = files.filter((f) => f.name.toLowerCase().includes(q));
    if (!files.length) {
      list.append(h('div', { class: 'empty' }, icon('archive'), h('h3', {}, 'No hay backups en esta carpeta'),
        h('p', {}, 'Haga un backup o importe un archivo desde la sección Backups.')));
      return;
    }
    if (!visible.length) list.append(h('div', { class: 'empty' }, h('p', {}, 'Ningún archivo coincide.')));
    for (const f of visible) {
      const input = h('input', { type: multi ? 'checkbox' : 'radio', name: `files-${folder}`, checked: selected.has(f.name) });
      const row = h('label', { class: `file-item ${selected.has(f.name) ? 'selected' : ''}` }, input,
        h('span', { class: 'fi-name', title: f.name }, f.name),
        h('span', { class: 'fi-meta' }, f.items === null ? 'JSON no válido' : `${fmtNum(f.items)} elem. · ${fmtDate(f.mtime)}`));
      input.addEventListener('change', () => {
        if (!multi) selected.clear();
        if (input.checked) selected.add(f.name); else selected.delete(f.name);
        render();
        picker.onChange?.([...selected]);
      });
      list.append(row);
    }
  };

  search.addEventListener('input', render);
  loadBackups().then((data) => {
    files = [...(data[folder]?.files || [])].sort((a, b) => b.mtime - a.mtime);
    for (const name of [...selected]) if (!files.some((f) => f.name === name)) selected.delete(name);
    render();
    picker.onChange?.([...selected]);
  }).catch((e) => { clear(list).append(errorBox(e.message)); });

  picker.el = h('div', {}, h('div', { style: { marginBottom: '8px' } }, search), list);
  return picker;
}

/** Opciones de restore según la carpeta (sharing, contraseña inicial) y selección de elementos. */
export function restoreOptions(folder) {
  const sharing = h('select', { class: 'select' },
    h('option', { value: 'keep' }, 'Mantener la que tenían'),
    h('option', { value: 'global' }, 'Global (todas las apps)'),
    h('option', { value: 'app' }, 'App'),
    h('option', { value: 'user' }, 'Privado del usuario'));
  const password = h('input', { class: 'input', type: 'password', autocomplete: 'new-password', placeholder: 'Opcional' });
  const itemsSlot = h('div');
  let itemPicker = null;
  const itemized = state.meta?.folders?.[folder]?.itemized;

  const el = h('form', { autocomplete: 'off', onsubmit: (e) => e.preventDefault() },
    (folder === 'alertas' || folder === 'reportes') && h('div', { class: 'field' }, h('label', {}, 'Compartición tras restaurar'), sharing,
      h('span', { class: 'field-hint' }, 'Mantener: las que ya existen conservan la suya (también las privadas) y las nuevas usan la del backup, o global si el backup es antiguo.')),
    folder === 'users' && h('div', { class: 'field' }, h('label', {}, 'Contraseña inicial para usuarios nuevos'), password,
      h('span', { class: 'field-hint' }, 'Los backups no incluyen contraseñas. Sin contraseña inicial, los usuarios que no existan no se crean; los existentes se actualizan igualmente. Se obliga a cambiarla en el primer login.')),
    itemsSlot);

  return {
    el,
    // Se llama cuando cambia la selección de archivos: con un único archivo se pueden elegir elementos concretos
    setFiles(files, version = null) {
      clear(itemsSlot);
      itemPicker = null;
      if (!itemized || files.length !== 1) return;
      const toggle = h('input', { type: 'checkbox' });
      const box = h('div');
      itemsSlot.append(h('label', { class: 'check', style: { marginBottom: '10px' } }, toggle, 'Restaurar solo algunos elementos del archivo'), box);
      toggle.addEventListener('change', () => {
        clear(box);
        itemPicker = null;
        if (toggle.checked) {
          itemPicker = itemsPicker(folder, files[0], version);
          box.append(itemPicker.el);
        }
      });
    },
    value() {
      const options = { sharing: sharing.value, initial_password: password.value };
      if (itemPicker) {
        const items = itemPicker.selected();
        if (!items.length) throw new Error('Seleccione al menos un elemento a restaurar.');
        options.items = items;
      }
      return options;
    },
  };
}

function itemsPicker(folder, file, version) {
  const selected = new Set();
  const list = h('div', { class: 'item-picker-list' }, loadingRows(3));
  const counter = h('span', { class: 'muted' });
  const filter = h('input', { class: 'input input-sm', type: 'search', placeholder: 'Filtrar…', style: { maxWidth: '220px', marginLeft: 'auto' } });
  const allBox = h('input', { type: 'checkbox', checked: true });
  let items = [];

  const render = () => {
    clear(list);
    const q = filter.value.trim().toLowerCase();
    for (const name of items.filter((n) => n.toLowerCase().includes(q))) {
      const cb = h('input', { type: 'checkbox', checked: selected.has(name) });
      cb.addEventListener('change', () => { if (cb.checked) selected.add(name); else selected.delete(name); update(); });
      list.append(h('label', {}, cb, h('span', { class: 'truncate' }, name)));
    }
    update();
  };
  const update = () => {
    counter.textContent = `${fmtNum(selected.size)} de ${fmtNum(items.length)} seleccionados`;
    allBox.checked = selected.size === items.length;
    allBox.indeterminate = selected.size > 0 && selected.size < items.length;
  };
  allBox.addEventListener('change', () => {
    if (allBox.checked) items.forEach((n) => selected.add(n)); else selected.clear();
    render();
  });
  filter.addEventListener('input', render);

  const url = `/api/backups/${enc(folder)}/${enc(file)}${version ? `/versions/${enc(version)}` : ''}`;
  get(url).then((data) => {
    items = data.items;
    items.forEach((n) => selected.add(n));
    render();
  }).catch((e) => clear(list).append(errorBox(e.message)));

  return {
    el: h('div', { class: 'item-picker', style: { marginBottom: '16px' } },
      h('div', { class: 'item-picker-head' }, allBox, counter, filter), list),
    selected: () => [...selected],
  };
}

/**
 * Diálogo de restore. Con 'version' ({ id, time }) restaura esa versión del historial del único archivo indicado.
 */
export function openRestoreModal({ folder, clients = [state.client], files = [], version = null }) {
  const label = state.meta?.folders?.[folder]?.label || folder;
  const options = restoreOptions(folder);
  let picker = null;
  let source;
  if (version) {
    options.setFiles([files[0]], version.id);
    source = h('div', { class: 'field' }, h('span', { class: 'field-label' }, 'Origen'),
      h('div', { class: 'file-item selected', style: { border: '1px solid var(--border)', borderRadius: 'var(--radius)', cursor: 'default' } },
        icon('history'), h('span', { class: 'fi-name' }, files[0]), h('span', { class: 'fi-meta' }, `versión del ${fmtDate(version.time)}`)));
  } else {
    picker = fileListPicker(folder, { multi: true, preselected: files });
    picker.onChange = (selected) => options.setFiles(selected);
    source = h('div', { class: 'field' }, h('span', { class: 'field-label' }, 'Archivos de backup'), picker.el);
  }

  openModal({
    title: version ? `Restaurar versión de ${label.toLowerCase()}` : `Restaurar ${label.toLowerCase()}`,
    subtitle: `Destino: ${clientsLabel(clients)}`,
    size: 'lg',
    body: [
      infoBox(`Los objetos del backup se crean en ${clientsLabel(clients)} y los que ya existan con el mismo nombre se sobrescriben.`, 'warn-box'),
      source,
      options.el,
    ],
    actions: [
      { label: 'Cancelar' },
      {
        label: `Restaurar en ${clientsLabel(clients)}`, variant: 'primary', icon: 'restore',
        onClick: async () => {
          const selected = picker ? picker.selected() : [files[0]];
          if (!selected.length) throw new Error('Seleccione al menos un archivo.');
          const opts = { ...options.value(), ...(version ? { version: version.id } : {}) };
          await startJob('/api/ops/restore', { clients, folder, files: selected, options: opts }, { folder });
        },
      },
    ],
  });
}

export function fileMeta(f) {
  return `${f.items === null ? 'JSON no válido' : `${fmtNum(f.items)} elementos`} · ${fmtBytes(f.size)}`;
}
