// Fichas de cliente: hoja resumen («cheat sheet») por cliente en HTML y Markdown, una carpeta por cliente.
import { state, startJob } from '../core.js';
import { get } from '../api.js';
import { h, clear, button, pageHeader, infoBox, toast } from '../ui.js';

const KEY = 'sm.fichas';
const read = () => { try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch { return {}; } };
const save = (v) => { try { localStorage.setItem(KEY, JSON.stringify(v)); } catch { /* sin almacenamiento */ } };

export default {
  id: 'fichas',
  title: 'Fichas de cliente',
  icon: 'fileText',
  render(root) {
    const prefs = read();
    const names = state.clients.map((c) => c.name);
    const selected = new Set((prefs.clients || names).filter((n) => names.includes(n)));
    const formats = new Set(prefs.formats || ['html', 'md']);
    const output = h('input', { type: 'text', class: 'input', value: prefs.output || '', placeholder: 'Vacío = informes/fichas' });

    const chips = h('div', { class: 'client-chips' });
    const renderChips = () => {
      clear(chips);
      for (const name of names) {
        const cb = h('input', { type: 'checkbox', checked: selected.has(name) });
        cb.addEventListener('change', () => { if (cb.checked) selected.add(name); else selected.delete(name); renderChips(); });
        chips.append(h('label', { class: `client-chip ${selected.has(name) ? 'selected' : ''}` }, cb, name));
      }
    };
    renderChips();

    const formatBox = h('div', { class: 'client-chips' });
    const renderFormats = () => {
      clear(formatBox);
      for (const [key, label] of [['html', 'HTML (con buscador)'], ['md', 'Markdown']]) {
        const cb = h('input', { type: 'checkbox', checked: formats.has(key) });
        cb.addEventListener('change', () => { if (cb.checked) formats.add(key); else formats.delete(key); renderFormats(); });
        formatBox.append(h('label', { class: `client-chip ${formats.has(key) ? 'selected' : ''}` }, cb, label));
      }
    };
    renderFormats();

    const excludedNote = h('p', { class: 'field-hint' }, 'Índices omitidos: cargando…');
    get('/api/fichas/config').then((c) => {
      excludedNote.textContent = `Índices omitidos de las fichas (config.py → fichas → indices_excluidos): ${c.excluded_indexes.join(', ') || 'ninguno'}.`;
    }).catch(() => { excludedNote.textContent = 'No se pudo leer la lista de índices omitidos.'; });

    root.append(
      pageHeader({ title: 'Fichas de cliente', subtitle: 'Hoja resumen por cliente: fuentes por índice, casos de uso por tecnología, apps, integraciones y roles.' }),
      infoBox('Es una instantánea: no incluye tamaños ni volúmenes, que cambian a diario. Crea una carpeta por cliente y un index.html con todos.'),
      h('div', { class: 'field' }, h('span', { class: 'field-label' }, 'Clientes'),
        h('div', {}, button('Todos', { size: 'sm', onClick: () => { names.forEach((n) => selected.add(n)); renderChips(); } }),
          button('Ninguno', { size: 'sm', variant: 'ghost', onClick: () => { selected.clear(); renderChips(); } })), chips),
      h('div', { class: 'field' }, h('span', { class: 'field-label' }, 'Formatos'), formatBox),
      h('div', { class: 'field' }, h('span', { class: 'field-label' }, 'Carpeta de salida'), output),
      excludedNote,
      button('Generar fichas', {
        variant: 'primary', iconName: 'fileText',
        onClick: async () => {
          const clients = names.filter((n) => selected.has(n));
          if (!clients.length) { toast('Seleccione al menos un cliente.', 'error'); return; }
          save({ clients, formats: [...formats], output: output.value });
          try {
            await startJob('/api/fichas', { clients, options: { output: output.value, formats: [...formats] } });
          } catch (e) { toast(e.message, 'error'); }
        },
      }),
    );
  },
};
