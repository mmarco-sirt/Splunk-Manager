// Operaciones masivas: un mismo backup o restore sobre varios clientes en una sola tarea.
import { state, startJob } from '../core.js';
import { h, clear, icon, button, pageHeader, segmented, confirmDialog, toast, initials, infoBox, withBusy } from '../ui.js';
import { backupKindPicker, fileListPicker, restoreOptions, BACKUP_KINDS, FOLDER_ICONS } from '../ops.js';

export default {
  id: 'batch',
  title: 'Operaciones masivas',
  icon: 'layers',
  render(root) {
    const selected = new Set(state.client ? [state.client] : []);
    let mode = 'backup';
    let folder = 'alertas';
    let backupPicker = null;
    let filePicker = null;
    let options = null;

    const clientGrid = h('div', { class: 'client-grid' });
    const counter = h('span', { class: 'muted' });
    const operationBox = h('div');
    const launchLabel = h('span');
    const launchBtn = h('button', { class: 'btn btn-primary', type: 'button' }, icon('play'), launchLabel);

    function renderClients() {
      clear(clientGrid);
      for (const c of state.clients) {
        const cb = h('input', { type: 'checkbox', checked: selected.has(c.name) });
        cb.addEventListener('change', () => {
          if (cb.checked) selected.add(c.name); else selected.delete(c.name);
          renderClients();
        });
        clientGrid.append(h('label', { class: `client-card ${selected.has(c.name) ? 'selected' : ''}` }, cb,
          h('span', { class: 'wc-avatar', style: { width: '28px', height: '28px', fontSize: '11px', borderRadius: '7px', display: 'grid', placeItems: 'center', background: 'var(--surface-3)', fontWeight: 700 } }, initials(c.name)),
          h('span', { class: 'cc-text' }, h('strong', {}, c.name), h('span', { class: 'cc-host' }, c.host)),
          c.connected ? h('span', { class: 'dot dot-good', title: 'Conectado', style: { marginLeft: 'auto' } }) : null));
      }
      counter.textContent = `${selected.size} de ${state.clients.length} seleccionados`;
      launchLabel.textContent = mode === 'backup'
        ? `Lanzar backup en ${selected.size} clientes`
        : `Lanzar restore en ${selected.size} clientes`;
      launchBtn.disabled = selected.size === 0;
    }

    function renderOperation() {
      clear(operationBox);
      backupPicker = filePicker = options = null;
      if (mode === 'backup') {
        backupPicker = backupKindPicker(Object.keys(BACKUP_KINDS));
        operationBox.append(backupPicker.el);
      } else {
        const folders = Object.entries(state.meta?.folders || {});
        const seg = segmented(folders.map(([key, meta]) => ({ value: key, label: meta.label })), folder, (v) => { folder = v; renderOperation(); });
        filePicker = fileListPicker(folder, { multi: true });
        options = restoreOptions(folder);
        filePicker.onChange = (files) => options.setFiles(files);
        operationBox.append(
          h('div', { class: 'field' }, h('span', { class: 'field-label' }, 'Tipo de objeto'), h('div', {}, seg)),
          h('div', { class: 'field' }, h('span', { class: 'field-label' }, h('span', {}, icon(FOLDER_ICONS[folder] || 'file')), ' Archivos a restaurar'), filePicker.el),
          options.el,
          infoBox('El restore se aplica en cada cliente seleccionado, uno detrás de otro. Los objetos existentes con el mismo nombre se sobrescriben.', 'warn-box'));
      }
      renderClients();
    }

    launchBtn.addEventListener('click', () => withBusy(launchBtn, async () => {
      const clients = state.clients.map((c) => c.name).filter((n) => selected.has(n));
      if (!clients.length) return;
      try {
        if (mode === 'backup') {
          const { kind, options: opts } = backupPicker.value();
          await startJob('/api/ops/backup', { clients, kind, options: opts });
        } else {
          const files = filePicker.selected();
          if (!files.length) throw new Error('Seleccione al menos un archivo de backup.');
          const opts = options.value();
          const ok = await confirmDialog({
            title: `¿Restaurar en ${clients.length} clientes?`,
            message: `Se restaurarán ${files.length} archivo(s) en: ${clients.join(', ')}.`,
            confirmLabel: 'Restaurar', danger: true,
          });
          if (!ok) return;
          await startJob('/api/ops/restore', { clients, folder, files, options: opts }, { folder });
        }
      } catch (e) {
        toast(e.message, 'error');
      }
    }));

    const modeSeg = segmented([{ value: 'backup', label: 'Backup' }, { value: 'restore', label: 'Restore' }], mode, (v) => { mode = v; renderOperation(); });

    clear(root).append(
      pageHeader({
        title: 'Operaciones masivas',
        subtitle: 'Lance el mismo backup o restore en varios clientes a la vez. Se ejecuta como una única tarea con el log de cada cliente.',
      }),
      h('div', { class: 'card' }, h('div', { class: 'card-body steps', style: { paddingTop: '22px' } },
        h('div', { class: 'step' },
          h('div', { style: { display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap', marginBottom: '12px' } },
            h('h2', { class: 'step-title', style: { margin: 0 } }, 'Clientes'), counter,
            h('div', { class: 'toolbar-spacer' }),
            button('Todos', { size: 'sm', onClick: () => { state.clients.forEach((c) => selected.add(c.name)); renderClients(); } }),
            button('Ninguno', { size: 'sm', variant: 'ghost', onClick: () => { selected.clear(); renderClients(); } })),
          clientGrid),
        h('div', { class: 'step' },
          h('div', { style: { display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '12px' } }, h('h2', { class: 'step-title', style: { margin: 0 } }, 'Operación'), modeSeg),
          operationBox),
        h('div', { class: 'step', style: { marginBottom: 0 } },
          h('h2', { class: 'step-title' }, 'Lanzar'),
          h('p', { class: 'text-2', style: { margin: '0 0 12px' } }, 'La tarea sigue en marcha aunque cambie de sección; puede consultarla en Actividad.'),
          launchBtn))));

    renderOperation();
  },
};
