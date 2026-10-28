// Componentes de interfaz sin dependencias: creación de nodos, iconos, avisos, modales, paneles y tablas.

// --- DOM -----------------------------------------------------------------

export function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') el.className = value;
    else if (key === 'style' && typeof value === 'object') {
      for (const [prop, v] of Object.entries(value)) {
        if (prop.startsWith('--')) el.style.setProperty(prop, v); else el.style[prop] = v;
      }
    }
    else if (key === 'dataset') Object.assign(el.dataset, value);
    else if (key.startsWith('on') && typeof value === 'function') el.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === 'value' || key === 'checked' || key === 'indeterminate') el[key] = value;
    else if (typeof value === 'boolean') el.toggleAttribute(key, value);
    else el.setAttribute(key, value);
  }
  append(el, children);
  return el;
}

export function append(el, children) {
  for (const child of [children].flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : String(child));
  }
  return el;
}

export function clear(el) {
  el.replaceChildren();
  return el;
}

// --- Iconos (trazos propios en rejilla de 24px) ---------------------------

const ICONS = {
  home: '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/><path d="M10 21v-6h4v6"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  bell: '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>',
  report: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M8 13h8M8 17h5"/>',
  braces: '<path d="M8 3H7a2 2 0 0 0-2 2v5a2 2 0 0 1-2 2 2 2 0 0 1 2 2v5a2 2 0 0 0 2 2h1"/><path d="M16 21h1a2 2 0 0 0 2-2v-5a2 2 0 0 1 2-2 2 2 0 0 1-2-2V5a2 2 0 0 0-2-2h-1"/>',
  dashboard: '<rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/>',
  users: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21v-1a6 6 0 0 1 6-6h4a6 6 0 0 1 6 6v1"/>',
  shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
  database: '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/><path d="M3 12c0 1.66 4 3 9 3s9-1.34 9-3"/>',
  apps: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  archive: '<rect x="2" y="3" width="20" height="5" rx="1"/><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8"/><path d="M10 12h4"/>',
  layers: '<path d="m12 2 10 5-10 5L2 7z"/><path d="m2 17 10 5 10-5"/><path d="m2 12 10 5 10-5"/>',
  server: '<rect x="2" y="3" width="20" height="8" rx="2"/><rect x="2" y="13" width="20" height="8" rx="2"/><path d="M6 7h.01M6 17h.01"/>',
  activity: '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
  refresh: '<path d="M21 12a9 9 0 0 1-15.5 6.2L3 16"/><path d="M3 12a9 9 0 0 1 15.5-6.2L21 8"/><path d="M21 3v5h-5"/><path d="M3 21v-5h5"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/>',
  upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m17 8-5-5-5 5"/><path d="M12 3v12"/>',
  trash: '<path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>',
  play: '<path d="m7 4 13 8-13 8z"/>',
  power: '<path d="M12 2v10"/><path d="M18.4 6.6a9 9 0 1 1-12.77.04"/>',
  chevronDown: '<path d="m6 9 6 6 6-6"/>',
  chevronRight: '<path d="m9 6 6 6-6 6"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  checkCircle: '<circle cx="12" cy="12" r="10"/><path d="m8 12 3 3 5-6"/>',
  xCircle: '<circle cx="12" cy="12" r="10"/><path d="m15 9-6 6M9 9l6 6"/>',
  alert: '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
  info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>',
  edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
  eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  copy: '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  loader: '<path d="M12 2v4M12 18v4M4.9 4.9l2.8 2.8M16.2 16.2l2.8 2.8M2 12h4M18 12h4M4.9 19.1l2.8-2.8M16.2 7.8l2.8-2.8"/>',
  clock: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
  external: '<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
  plug: '<path d="M12 22v-5"/><path d="M9 8V2M15 8V2"/><path d="M18 8v5a6 6 0 0 1-12 0V8z"/>',
  save: '<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><path d="M17 21v-8H7v8M7 3v5h8"/>',
  restore: '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/>',
  terminal: '<path d="m4 17 6-6-6-6"/><path d="M12 19h8"/>',
  stop: '<rect x="5" y="5" width="14" height="14" rx="2"/>',
  flag: '<path d="M4 22V4a1 1 0 0 1 1-1h12l-2 5 2 5H5"/>',
  lock: '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
  key: '<circle cx="7.5" cy="15.5" r="5.5"/><path d="m21 2-9.6 9.6M15.5 7.5l3 3L22 7l-3-3"/>',
  zap: '<path d="M13 2 3 14h9l-1 8 10-12h-9z"/>',
  file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>',
  heartPulse: '<path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"/><path d="M3.22 12H9.5l.5-1 2 4.5 2-7 1.5 3.5h5.27"/>',
  barChart: '<path d="M3 3v18h18"/><path d="M8 17v-4M12.5 17V8M17 17v-7"/>',
  history: '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l3 2"/>',
  compare: '<circle cx="6" cy="6" r="3"/><circle cx="18" cy="18" r="3"/><path d="M13 6h3a2 2 0 0 1 2 2v7"/><path d="M11 18H8a2 2 0 0 1-2-2V9"/>',
  sliders: '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3"/><path d="M1 14h6M9 8h6M17 16h6"/>',
  printer: '<path d="M6 9V2h12v7"/><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><rect x="6" y="14" width="12" height="8" rx="1"/>',
  sheet: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18"/>',
  fileText: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M8 13h8M8 17h8M8 9h2"/>',
  arrowRight: '<path d="M5 12h14"/><path d="m13 6 6 6-6 6"/>',
  trending: '<path d="m22 7-8.5 8.5-5-5L2 17"/><path d="M16 7h6v6"/>',
  eyeOff: '<path d="M9.9 4.2A10 10 0 0 1 12 4c6.5 0 10 8 10 8a17 17 0 0 1-2.2 3.2M6.6 6.6A16 16 0 0 0 2 12s3.5 8 10 8a9.8 9.8 0 0 0 5.4-1.6"/><path d="m2 2 20 20"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/>',
  package: '<path d="m7.5 4.3 9 5.1"/><path d="M21 8a2 2 0 0 0-1-1.7l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.7l7 4a2 2 0 0 0 2 0l7-4a2 2 0 0 0 1-1.7z"/><path d="M3.3 7 12 12l8.7-5"/><path d="M12 22V12"/>',
};

export function icon(name, extraClass = '') {
  const span = document.createElement('span');
  span.className = `icon ${extraClass}`.trim();
  span.setAttribute('aria-hidden', 'true');
  span.innerHTML = `<svg viewBox="0 0 24 24">${ICONS[name] || ''}</svg>`;
  return span;
}

// --- Formato ---------------------------------------------------------------

const numberFmt = new Intl.NumberFormat('es-ES');
const compactFmt = new Intl.NumberFormat('es-ES', { notation: 'compact', maximumFractionDigits: 1 });

export const fmtNum = (n) => (n === null || n === undefined || Number.isNaN(n) ? '—' : numberFmt.format(n));
export const fmtCompact = (n) => (n === null || n === undefined ? '—' : n < 10000 ? numberFmt.format(n) : compactFmt.format(n));

export function fmtBytes(bytes) {
  if (bytes === null || bytes === undefined) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = bytes;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) { value /= 1024; i++; }
  return `${value.toLocaleString('es-ES', { maximumFractionDigits: i ? 1 : 0 })} ${units[i]}`;
}

export const fmtMB = (mb) => fmtBytes(mb * 1024 * 1024);

function toDate(value) {
  if (value === null || value === undefined || value === '') return null;
  if (typeof value === 'number') return new Date(value * 1000);
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function fmtDate(value) {
  const d = toDate(value);
  return d ? d.toLocaleString('es-ES', { dateStyle: 'short', timeStyle: 'short' }) : (value || '—');
}

export function fmtRelative(value) {
  const d = toDate(value);
  if (!d) return '—';
  const seconds = Math.round((Date.now() - d.getTime()) / 1000);
  if (seconds < 45) return 'hace un momento';
  const rtf = new Intl.RelativeTimeFormat('es', { numeric: 'auto' });
  if (seconds < 3600) return rtf.format(-Math.round(seconds / 60), 'minute');
  if (seconds < 86400) return rtf.format(-Math.round(seconds / 3600), 'hour');
  if (seconds < 86400 * 30) return rtf.format(-Math.round(seconds / 86400), 'day');
  return fmtDate(value);
}

export function fmtDuration(seconds) {
  if (seconds === null || seconds === undefined) return '—';
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  if (seconds < 60) return `${seconds.toFixed(1).replace('.', ',')} s`;
  const m = Math.floor(seconds / 60);
  return `${m} min ${Math.round(seconds % 60)} s`;
}

export function initials(name) {
  const parts = String(name || '?').split(/[\s_-]+/).filter(Boolean);
  return (parts.length > 1 ? parts[0][0] + parts[1][0] : String(name).slice(0, 2)).toUpperCase();
}

// --- Piezas pequeñas -------------------------------------------------------

export function button(label, { variant = '', size = '', iconName, onClick, title, type = 'button', disabled } = {}) {
  const classes = ['btn', variant && `btn-${variant}`, size && `btn-${size}`, !label && 'btn-icon'].filter(Boolean).join(' ');
  return h('button', { class: classes, type, title: title || null, 'aria-label': !label ? title : null, onclick: onClick, disabled },
    iconName && icon(iconName), label);
}

// Ejecuta una acción asíncrona mostrando el botón como ocupado
export async function withBusy(btn, fn) {
  btn.classList.add('is-busy');
  btn.disabled = true;
  try {
    return await fn();
  } finally {
    btn.classList.remove('is-busy');
    btn.disabled = false;
  }
}

export function badge(text, variant = '', iconName) {
  return h('span', { class: `badge ${variant ? `badge-${variant}` : ''}` }, iconName && icon(iconName), text);
}

export function statusLabel(text, variant, iconName) {
  return h('span', { class: `status status-${variant}` }, iconName && icon(iconName), text);
}

export function pageHeader({ title, subtitle, actions = [] }) {
  return h('div', { class: 'page-header' },
    h('div', {}, h('h1', { class: 'page-title' }, title), subtitle && h('p', { class: 'page-subtitle' }, subtitle)),
    actions.length ? h('div', { class: 'page-actions' }, actions) : null);
}

export function emptyState({ iconName = 'info', title, text, action }) {
  return h('div', { class: 'empty' }, icon(iconName), h('h3', {}, title), text && h('p', {}, text), action);
}

export function errorBox(message) {
  return h('div', { class: 'error-box', role: 'alert' }, icon('alert'), h('div', {}, message));
}

export function infoBox(content, variant = '') {
  return h('div', { class: `info-box ${variant}` }, icon(variant === 'warn-box' ? 'alert' : 'info'), h('div', {}, content));
}

export function loadingRows(n = 6) {
  return h('div', { class: 'loading-rows' },
    Array.from({ length: n }, (_, i) => h('span', { class: 'skeleton', style: { width: `${92 - (i % 3) * 14}%` } })));
}

export function segmented(options, value, onChange) {
  const el = h('div', { class: 'seg', role: 'tablist' });
  const render = (current) => {
    clear(el);
    for (const opt of options) {
      el.append(h('button', {
        type: 'button', role: 'tab', class: opt.value === current ? 'active' : '', 'aria-selected': String(opt.value === current),
        title: (typeof opt.title === 'function' ? opt.title() : opt.title) || null,
        onclick: () => { render(opt.value); onChange(opt.value); },
      }, opt.label, opt.count !== undefined && h('span', { class: 'seg-count' }, fmtNum(opt.count))));
    }
  };
  render(value);
  el.update = (newOptions, current) => { options = newOptions; render(current); };
  return el;
}

export function codeBlock(text, { maxHeight } = {}) {
  const pre = h('pre', { class: 'code', style: maxHeight ? { maxHeight } : null }, text || '');
  const copy = button('', { size: 'sm', variant: 'ghost', iconName: 'copy', title: 'Copiar', onClick: () => copyText(text) });
  copy.classList.add('copy-btn');
  return h('div', { class: 'code-block' }, pre, copy);
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast('Copiado al portapapeles', 'success');
  } catch {
    toast('No se pudo copiar', 'error');
  }
}

export function kv(pairs) {
  const dl = h('dl', { class: 'kv' });
  for (const [label, value] of pairs) {
    if (value === undefined) continue;
    dl.append(h('dt', {}, label), h('dd', {}, value === null || value === '' ? h('span', { class: 'muted' }, '—') : value));
  }
  return dl;
}

export function section(title, ...content) {
  return h('div', { class: 'detail-section' }, h('h4', {}, title), content);
}

// --- Tooltip -----------------------------------------------------------------

const tooltipEl = () => document.getElementById('tooltip');

export function attachTooltip(target, content) {
  const show = (event) => {
    const tip = tooltipEl();
    clear(tip);
    append(tip, typeof content === 'function' ? content() : content);
    tip.hidden = false;
    move(event);
  };
  const move = (event) => {
    const tip = tooltipEl();
    const pad = 14;
    let x = event.clientX + pad;
    let y = event.clientY + pad;
    const rect = tip.getBoundingClientRect();
    if (x + rect.width > window.innerWidth - 8) x = event.clientX - rect.width - pad;
    if (y + rect.height > window.innerHeight - 8) y = event.clientY - rect.height - pad;
    tip.style.left = `${Math.max(8, x)}px`;
    tip.style.top = `${Math.max(8, y)}px`;
  };
  const hide = () => { tooltipEl().hidden = true; };
  target.addEventListener('mouseenter', show);
  target.addEventListener('mousemove', move);
  target.addEventListener('mouseleave', hide);
  target.addEventListener('focus', (e) => {
    const r = target.getBoundingClientRect();
    show({ clientX: r.left, clientY: r.bottom });
  });
  target.addEventListener('blur', hide);
}

// --- Avisos ------------------------------------------------------------------

const TOAST_ICONS = { success: 'checkCircle', error: 'xCircle', warn: 'alert', info: 'info' };

export function toast(message, type = 'info', { action, duration } = {}) {
  const root = document.getElementById('toast-root');
  const el = h('div', { class: `toast toast-${type}`, role: type === 'error' ? 'alert' : 'status' },
    icon(TOAST_ICONS[type] || 'info'),
    h('div', { class: 'toast-text' }, message),
    action && button(action.label, { size: 'sm', onClick: () => { action.onClick(); close(); } }),
    button('', { size: 'sm', variant: 'ghost', iconName: 'x', title: 'Cerrar', onClick: () => close() }));
  const close = () => el.remove();
  root.append(el);
  setTimeout(close, duration || (type === 'error' ? 9000 : 4500));
  return close;
}

// --- Modal ---------------------------------------------------------------------

export function openModal({ title, subtitle, body, actions = [], size = '' }) {
  const root = document.getElementById('modal-root');
  const previousFocus = document.activeElement;
  const errorSlot = h('div', { class: 'modal-error' });
  const footer = h('div', { class: 'modal-footer' });
  const bodyEl = h('div', { class: 'modal-body' });
  const dialog = h('div', { class: `modal ${size ? `modal-${size}` : ''}`, role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
    h('div', { class: 'modal-header' },
      h('div', { style: { flex: 1 } }, h('h3', {}, title), subtitle && h('p', {}, subtitle)),
      button('', { size: 'sm', variant: 'ghost', iconName: 'x', title: 'Cerrar', onClick: () => close() })),
    bodyEl, footer);
  const backdrop = h('div', { class: 'modal-backdrop' }, dialog);

  let closed = false;
  const onKey = (e) => { if (e.key === 'Escape') close(); };
  function close() {
    if (closed) return;
    closed = true;
    backdrop.remove();
    document.removeEventListener('keydown', onKey);
    previousFocus?.focus?.();
    modal.onClose?.();
  }

  const modal = {
    el: dialog, body: bodyEl, close, onClose: null,
    setError(message) { clear(errorSlot); if (message) errorSlot.append(errorBox(message)); },
    setActions(list) {
      clear(footer);
      footer.append(errorSlot);
      for (const action of list) {
        const btn = button(action.label, { variant: action.variant || '', iconName: action.icon, disabled: action.disabled });
        btn.addEventListener('click', async () => {
          if (!action.onClick) return close();
          modal.setError('');
          try {
            const keepOpen = await withBusy(btn, () => action.onClick(modal));
            if (keepOpen !== false) close();
          } catch (e) {
            modal.setError(e.message);
          }
        });
        if (action.ref) action.ref(btn);
        footer.append(btn);
      }
    },
  };

  append(bodyEl, typeof body === 'function' ? body(modal) : body);
  modal.setActions(actions.length ? actions : [{ label: 'Cerrar' }]);
  backdrop.addEventListener('mousedown', (e) => { if (e.target === backdrop) close(); });
  document.addEventListener('keydown', onKey);
  root.append(backdrop);
  (dialog.querySelector('.modal-body input, .modal-body select, .modal-body textarea') || dialog.querySelector('.modal-footer .btn:last-child'))?.focus();
  return modal;
}

export function confirmDialog({ title, message, confirmLabel = 'Confirmar', danger = false }) {
  return new Promise((resolve) => {
    let result = false;
    const modal = openModal({
      title, size: 'sm',
      body: h('p', { style: { margin: '0 0 8px', color: 'var(--text-2)' } }, message),
      actions: [
        { label: 'Cancelar' },
        { label: confirmLabel, variant: danger ? 'danger' : 'primary', onClick: () => { result = true; } },
      ],
    });
    modal.onClose = () => resolve(result);
  });
}

// --- Panel lateral -------------------------------------------------------------

let currentDrawer = null;

export function openDrawer({ title, subtitle, actions, body, width }) {
  currentDrawer?.close();
  const root = document.getElementById('drawer-root');
  const titleEl = h('h3', {}, title);
  const subtitleEl = h('p', {}, subtitle || '');
  const actionsEl = h('div', { class: 'drawer-actions' });
  const bodyEl = h('div', { class: 'drawer-body' });
  const panel = h('aside', { class: 'drawer', role: 'dialog', 'aria-label': title, style: width ? { width: `min(${width}px, 100vw)` } : null },
    h('div', { class: 'drawer-header' },
      h('div', { class: 'dh-text' }, titleEl, subtitleEl),
      button('', { variant: 'ghost', size: 'sm', iconName: 'x', title: 'Cerrar', onClick: () => close() })),
    actionsEl, bodyEl);
  const backdrop = h('div', { class: 'drawer-backdrop', onclick: () => close() });
  const onKey = (e) => { if (e.key === 'Escape' && !document.querySelector('.modal-backdrop')) close(); };

  let closed = false;
  function close() {
    if (closed) return;
    closed = true;
    panel.remove();
    backdrop.remove();
    document.removeEventListener('keydown', onKey);
    if (currentDrawer === drawer) currentDrawer = null;
    drawer.onClose?.();
  }

  const drawer = {
    el: panel, body: bodyEl, close, onClose: null,
    setTitle(t, s) { titleEl.textContent = t; subtitleEl.textContent = s || ''; },
    setActions(list) { clear(actionsEl); append(actionsEl, list); actionsEl.hidden = !list || !list.length; },
    setBody(content) { clear(bodyEl); append(bodyEl, content); },
  };
  drawer.setActions(actions || []);
  drawer.setBody(body);
  document.addEventListener('keydown', onKey);
  root.append(backdrop, panel);
  return drawer;
}

// --- Tabla de datos --------------------------------------------------------------

/**
 * Tabla con búsqueda, filtros segmentados, ordenación, selección múltiple y paginación incremental.
 * columns: [{ key, label, render(row), sort(row), className, sortable }]
 * filters: [{ id, label, value, options: [{ value, label, title, test(row) }] }]
 * Los filtros se combinan (se aplican todos a la vez). Si tienen 'label', van en su propia fila con su etiqueta.
 */
export class DataTable {
  constructor(opts) {
    this.opts = {
      rowKey: (r) => r.name,
      searchText: (r) => r.name,
      searchPlaceholder: 'Buscar…',
      filters: [],
      selectionActions: [],
      pageSize: 100,
      emptyTitle: 'Sin resultados',
      emptyText: '',
      ...opts,
    };
    this.rows = opts.rows || [];
    this.query = opts.initialQuery || '';
    this.filterValues = Object.fromEntries(this.opts.filters.map((f) => [f.id, f.value ?? f.options[0].value]));
    this.sort = opts.initialSort || null;
    this.limit = this.opts.pageSize;
    this.selected = new Set();
    this.build();
    this.render();
  }

  build() {
    const o = this.opts;
    this.searchInput = h('input', {
      class: 'input input-sm', type: 'search', placeholder: o.searchPlaceholder, value: this.query, 'aria-label': o.searchPlaceholder,
      oninput: () => { this.query = this.searchInput.value; this.limit = o.pageSize; this.render(); },
    });
    this.countEl = h('span', { class: 'count' });
    this.filterEls = o.filters.map((f) => {
      const seg = segmented(f.options, this.filterValues[f.id], (v) => { this.filterValues[f.id] = v; this.limit = o.pageSize; this.render(); });
      seg.filterDef = f;
      return seg;
    });
    const labelled = o.filters.some((f) => f.label);
    const filterGroups = this.filterEls.map((seg) => (seg.filterDef.label
      ? h('div', { class: 'filter-group' }, h('span', { class: 'filter-label' }, seg.filterDef.label), seg)
      : seg));
    this.toolbar = h('div', { class: 'toolbar' },
      h('label', { class: 'search-input' }, icon('search'), this.searchInput),
      labelled ? null : filterGroups,
      h('div', { class: 'toolbar-spacer' }),
      this.countEl,
      o.toolbarExtra,
      labelled ? h('div', { class: 'toolbar-filters' }, filterGroups) : null);
    this.selectionBar = h('div', { class: 'selection-bar', hidden: true });
    this.headCheck = o.selectable ? h('input', { type: 'checkbox', 'aria-label': 'Seleccionar todo', onchange: () => this.toggleAll() }) : null;
    this.thead = h('thead');
    this.tbody = h('tbody');
    this.table = h('table', { class: `data ${o.compact ? 'compact' : ''}` }, this.thead, this.tbody);
    this.emptyEl = h('div');
    this.footer = h('div', { class: 'table-footer', hidden: true });
    this.el = h('div', { class: 'table-card' }, this.toolbar, this.selectionBar, h('div', { class: 'table-scroll' }, this.table), this.emptyEl, this.footer);
    this.renderHead();
  }

  renderHead() {
    const o = this.opts;
    const tr = h('tr');
    if (o.selectable) tr.append(h('th', { class: 'col-check' }, this.headCheck));
    for (const col of o.columns) {
      const sortable = col.sortable !== false && col.label;
      const active = this.sort?.key === col.key;
      const th = h('th', { class: [sortable && 'sortable', col.className].filter(Boolean).join(' '), scope: 'col' },
        col.label || '', sortable && h('span', { class: 'sort-ind' }, active ? (this.sort.dir === 'asc' ? ' ↑' : ' ↓') : ''));
      if (sortable) {
        th.addEventListener('click', () => {
          const flipped = this.sort?.dir === 'asc' ? 'desc' : 'asc';
          this.sort = { key: col.key, dir: active ? flipped : (col.defaultDir || 'asc') };
          this.renderHead();
          this.render();
        });
      }
      tr.append(th);
    }
    clear(this.thead).append(tr);
  }

  setRows(rows) {
    this.rows = rows || [];
    const keys = new Set(this.rows.map(this.opts.rowKey));
    for (const k of [...this.selected]) if (!keys.has(k)) this.selected.delete(k);
    this.render();
  }

  filtered() {
    const q = this.query.trim().toLowerCase();
    let rows = this.rows;
    for (const f of this.opts.filters) {
      const opt = f.options.find((o) => o.value === this.filterValues[f.id]);
      if (opt?.test) rows = rows.filter(opt.test);
    }
    if (q) rows = rows.filter((r) => String(this.opts.searchText(r) || '').toLowerCase().includes(q));
    if (this.sort) {
      const col = this.opts.columns.find((c) => c.key === this.sort.key);
      const value = col?.sort || ((r) => r[this.sort.key]);
      const dir = this.sort.dir === 'asc' ? 1 : -1;
      rows = [...rows].sort((a, b) => {
        const va = value(a);
        const vb = value(b);
        if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * dir;
        return String(va ?? '').localeCompare(String(vb ?? ''), 'es', { numeric: true, sensitivity: 'base' }) * dir;
      });
    }
    return rows;
  }

  render() {
    const o = this.opts;
    const rows = this.filtered();
    this.visibleRows = rows;

    // Contadores de cada opción de filtro (sobre el resto de filtros y la búsqueda)
    for (const seg of this.filterEls) {
      const f = seg.filterDef;
      const q = this.query.trim().toLowerCase();
      let base = this.rows;
      for (const other of o.filters) {
        if (other === f) continue;
        const opt = other.options.find((x) => x.value === this.filterValues[other.id]);
        if (opt?.test) base = base.filter(opt.test);
      }
      if (q) base = base.filter((r) => String(o.searchText(r) || '').toLowerCase().includes(q));
      seg.update(f.options.map((opt) => ({ ...opt, count: opt.test ? base.filter(opt.test).length : base.length })), this.filterValues[f.id]);
    }

    this.countEl.textContent = rows.length === this.rows.length ? `${fmtNum(rows.length)} elementos` : `${fmtNum(rows.length)} de ${fmtNum(this.rows.length)}`;
    clear(this.tbody);
    const fragment = document.createDocumentFragment();
    for (const row of rows.slice(0, this.limit)) fragment.append(this.renderRow(row));
    this.tbody.append(fragment);

    clear(this.emptyEl);
    if (!rows.length) {
      this.emptyEl.append(emptyState({
        iconName: this.rows.length ? 'search' : (o.emptyIcon || 'info'),
        title: this.rows.length ? 'Nada coincide con el filtro' : o.emptyTitle,
        text: this.rows.length ? 'Pruebe con otro texto de búsqueda u otro filtro.' : o.emptyText,
      }));
    }

    clear(this.footer);
    this.footer.hidden = rows.length <= this.limit;
    if (rows.length > this.limit) {
      this.footer.append(
        h('span', {}, `Mostrando ${fmtNum(this.limit)} de ${fmtNum(rows.length)}`),
        button('Mostrar más', { size: 'sm', onClick: () => { this.limit += o.pageSize * 2; this.render(); } }),
        button('Mostrar todo', { size: 'sm', variant: 'ghost', onClick: () => { this.limit = Infinity; this.render(); } }));
    }
    this.renderSelection();
  }

  renderRow(row) {
    const o = this.opts;
    const key = o.rowKey(row);
    const tr = h('tr', { class: [o.onRowClick && 'clickable', this.selected.has(key) && 'selected'].filter(Boolean).join(' ') });
    if (o.selectable) {
      const cb = h('input', { type: 'checkbox', checked: this.selected.has(key), 'aria-label': 'Seleccionar fila' });
      cb.addEventListener('change', () => {
        if (cb.checked) this.selected.add(key); else this.selected.delete(key);
        tr.classList.toggle('selected', cb.checked);
        this.renderSelection();
      });
      tr.append(h('td', { class: 'col-check' }, cb));
    }
    for (const col of o.columns) {
      const content = col.render ? col.render(row) : row[col.key];
      tr.append(h('td', { class: col.className || null }, content ?? ''));
    }
    if (o.onRowClick) {
      tr.addEventListener('click', (e) => {
        if (e.target.closest('button, a, input, label, select')) return;
        o.onRowClick(row);
      });
    }
    return tr;
  }

  toggleAll() {
    const keys = this.visibleRows.map(this.opts.rowKey);
    const allSelected = keys.length && keys.every((k) => this.selected.has(k));
    for (const k of keys) { if (allSelected) this.selected.delete(k); else this.selected.add(k); }
    this.render();
  }

  getSelected() {
    return this.rows.filter((r) => this.selected.has(this.opts.rowKey(r)));
  }

  clearSelection() {
    this.selected.clear();
    this.render();
  }

  renderSelection() {
    const o = this.opts;
    if (!o.selectable) return;
    const count = this.selected.size;
    const visibleKeys = (this.visibleRows || []).map(o.rowKey);
    const visibleSelected = visibleKeys.filter((k) => this.selected.has(k)).length;
    this.headCheck.checked = visibleKeys.length > 0 && visibleSelected === visibleKeys.length;
    this.headCheck.indeterminate = visibleSelected > 0 && visibleSelected < visibleKeys.length;
    clear(this.selectionBar);
    this.selectionBar.hidden = count === 0;
    if (!count) return;
    this.selectionBar.append(h('strong', {}, `${fmtNum(count)} seleccionados`));
    for (const action of o.selectionActions) {
      const btn = button(action.label, { size: 'sm', variant: action.variant || '', iconName: action.icon });
      btn.addEventListener('click', () => withBusy(btn, () => action.onClick(this.getSelected())));
      this.selectionBar.append(btn);
    }
    this.selectionBar.append(button('Quitar selección', { size: 'sm', variant: 'ghost', onClick: () => this.clearSelection() }));
  }
}
