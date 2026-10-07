// Punto de entrada: navegación, selector de cliente, indicador de tareas y tema.
import { get } from './api.js';
import {
  state, on, parseHash, navigate, loadClients, currentClient, isConnected, selectClient, connectClient,
  watchJobs, runningJobs,
} from './core.js';
import { h, append, clear, icon, button, errorBox, emptyState } from './ui.js';
import { invalidateBackups } from './ops.js';
import overview, { clientCards } from './views/overview.js';
import search from './views/search.js';
import health from './views/health.js';
import ingest from './views/ingest.js';
import { alertsView, reportsView } from './views/savedsearches.js';
import { macrosView, dashboardsView, usersView, rolesView, indexesView, appsView } from './views/objects.js';
import backups from './views/backups.js';
import batch from './views/batch.js';
import clients from './views/clients.js';
import activity, { openJobDrawer } from './views/activity.js';

const NAV = [
  { title: 'General', items: [overview, health, search] },
  { title: 'Objetos', items: [alertsView, reportsView, macrosView, dashboardsView] },
  { title: 'Acceso', items: [usersView, rolesView] },
  { title: 'Plataforma', items: [indexesView, ingest, appsView] },
  { title: 'Gestión', items: [backups, batch, clients, activity] },
];
const VIEWS = new Map(NAV.flatMap((g) => g.items).map((v) => [v.id, v]));

const $ = (id) => document.getElementById(id);

// --- Barra lateral -----------------------------------------------------------

const mobileLayout = matchMedia('(max-width: 960px)');

const isCollapsed = () => document.documentElement.dataset.sidebar === 'collapsed';

function renderNav(activeId) {
  const nav = clear($('nav'));
  const collapsed = isCollapsed() && !mobileLayout.matches;
  for (const group of NAV) {
    nav.append(h('div', { class: 'nav-group' },
      h('div', { class: 'nav-group-title' }, group.title),
      group.items.map((v) => h('a', {
        class: `nav-item ${v.id === activeId ? 'active' : ''} ${v.needsClient ? 'needs-client' : ''}`,
        href: `#/${v.id}`,
        title: collapsed ? v.title : null,
        'aria-current': v.id === activeId ? 'page' : null,
      }, icon(v.icon), h('span', { class: 'nav-label' }, v.title)))));
  }
}

// En móvil la barra lateral se abre por encima del contenido
function setSidebar(open) {
  $('sidebar').classList.toggle('open', open);
  $('sidebar-scrim').classList.toggle('open', open);
}

// En escritorio se pliega a una columna de iconos y se recuerda la preferencia
function setCollapsed(collapsed) {
  if (collapsed) document.documentElement.dataset.sidebar = 'collapsed';
  else delete document.documentElement.dataset.sidebar;
  try { localStorage.setItem('sm.sidebar', collapsed ? 'collapsed' : 'expanded'); } catch { /* sin almacenamiento */ }
  renderNav(currentView().id);
  renderThemeToggle();
}

function toggleSidebar() {
  if (mobileLayout.matches) setSidebar(!$('sidebar').classList.contains('open'));
  else setCollapsed(!isCollapsed());
}

// --- Tema --------------------------------------------------------------------

function effectiveTheme() {
  return document.documentElement.dataset.theme
    || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
}

function renderThemeToggle() {
  const dark = effectiveTheme() === 'dark';
  const label = dark ? 'Tema claro' : 'Tema oscuro';
  const toggle = clear($('theme-toggle'));
  toggle.title = isCollapsed() && !mobileLayout.matches ? label : '';
  toggle.append(icon(dark ? 'sun' : 'moon'), h('span', { class: 'nav-label' }, label));
}

function toggleTheme() {
  const next = effectiveTheme() === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem('sm.theme', next); } catch { /* sin almacenamiento */ }
  renderThemeToggle();
}

// --- Selector de cliente -------------------------------------------------------

let popover = null;

function closePopover() {
  popover?.remove();
  popover = null;
  document.removeEventListener('mousedown', onOutside);
}

function onOutside(e) {
  if (!$('client-switcher').contains(e.target)) closePopover();
}

function renderSwitcher() {
  const root = clear($('client-switcher'));
  const client = currentClient();
  const connecting = state.connecting && state.connecting === state.client;
  const dot = connecting ? 'dot dot-busy' : client?.connected ? 'dot dot-good' : client ? 'dot dot-bad' : 'dot';
  const status = connecting ? 'Conectando…' : client ? `${client.host}${client.info?.version ? ` · Splunk ${client.info.version}` : client.connected ? '' : ' · sin conexión'}` : 'Ningún cliente seleccionado';

  const trigger = h('button', { class: 'client-trigger', type: 'button', 'aria-haspopup': 'listbox', 'aria-expanded': 'false', title: 'Cambiar de cliente' },
    h('span', { class: dot }),
    h('span', { class: 'ct-text' }, h('span', { class: 'ct-name' }, client ? client.name : 'Seleccionar cliente'), h('span', { class: 'ct-host' }, status)),
    icon('chevronDown'));
  trigger.addEventListener('click', () => (popover ? closePopover() : openPopover(trigger)));
  root.append(trigger);
}

function openPopover(trigger) {
  const input = h('input', { class: 'input input-sm', type: 'search', placeholder: 'Buscar cliente…', 'aria-label': 'Buscar cliente' });
  const list = h('div', { class: 'popover-list', role: 'listbox' });

  const render = () => {
    clear(list);
    const q = input.value.trim().toLowerCase();
    const items = state.clients.filter((c) => `${c.name} ${c.host}`.toLowerCase().includes(q));
    if (!items.length) list.append(h('div', { class: 'muted', style: { padding: '10px' } }, 'Ningún cliente coincide.'));
    for (const c of items) {
      list.append(h('button', {
        class: `popover-item ${c.name === state.client ? 'current' : ''}`, type: 'button', role: 'option',
        'aria-selected': String(c.name === state.client),
        onclick: () => { closePopover(); selectClient(c.name); },
      },
      h('span', { class: c.connected ? 'dot dot-good' : 'dot' }),
      h('span', { class: 'pi-text' }, h('strong', {}, c.name), h('span', { class: 'pi-host' }, c.host)),
      c.info?.version ? h('span', { class: 'badge' }, c.info.version) : null));
    }
    return items;
  };

  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      const first = render()[0];
      if (first) { closePopover(); selectClient(first.name); }
    } else if (e.key === 'Escape') {
      closePopover();
      trigger.focus();
    }
  });
  input.addEventListener('input', render);
  render();

  popover = h('div', { class: 'popover' },
    h('div', { class: 'popover-search' }, input),
    list,
    h('div', { class: 'popover-footer' },
      h('a', { class: 'popover-item', href: '#/clients', onclick: () => closePopover() }, icon('server'), 'Gestionar clientes')));
  $('client-switcher').append(popover);
  trigger.setAttribute('aria-expanded', 'true');
  document.addEventListener('mousedown', onOutside);
  input.focus();
}

// --- Indicador de tareas --------------------------------------------------------

function renderTasksButton() {
  const running = runningJobs();
  const btn = clear($('tasks-btn'));
  btn.classList.toggle('running', running > 0);
  btn.title = running ? `${running} tareas en curso` : 'Actividad';
  append(btn, [icon(running ? 'loader' : 'activity'), h('span', { class: 'tasks-label' }, 'Tareas'),
    running ? h('span', { class: 'tasks-count' }, String(running)) : null]);
}

// --- Enrutado ----------------------------------------------------------------------

let cleanup = null;
let renderQueued = false;
let showingPlaceholder = false;

function needsClientPlaceholder(root, view) {
  const client = currentClient();
  clear(root);
  root.append(h('div', { class: 'page-header' }, h('div', {}, h('h1', { class: 'page-title' }, view.title))));
  if (client && state.connecting === client.name) {
    root.append(h('div', { class: 'card' }, emptyState({ iconName: 'plug', title: `Conectando con ${client.name}…`, text: client.host, action: h('span', { class: 'spinner' }) })));
  } else if (client) {
    const btn = button(`Conectar con ${client.name}`, { variant: 'primary', iconName: 'plug', onClick: () => connectClient(client.name) });
    root.append(h('div', { class: 'card' }, emptyState({ iconName: 'plug', title: 'Sin conexión', text: `Esta sección necesita conexión con ${client.name}.`, action: btn })));
  } else if (state.clients.length) {
    root.append(h('p', { class: 'page-subtitle', style: { marginBottom: '16px' } }, 'Esta sección trabaja sobre un cliente. Elija uno:'), clientCards((name) => selectClient(name)));
  } else {
    root.append(h('div', { class: 'card' }, emptyState({
      iconName: 'server', title: 'No hay clientes configurados',
      action: button('Añadir cliente', { variant: 'primary', iconName: 'plus', onClick: () => navigate('clients', { new: '1' }) }),
    })));
  }
}

function renderRoute({ scroll = false } = {}) {
  renderQueued = false;
  const { name, params } = parseHash();
  const view = VIEWS.get(name) || overview;
  cleanup?.();
  cleanup = null;
  renderNav(view.id);
  setSidebar(false);
  document.title = `${view.title} · Splunk Manager`;
  const root = $('view');
  showingPlaceholder = Boolean(view.needsClient && !isConnected());
  try {
    if (showingPlaceholder) needsClientPlaceholder(root, view);
    else cleanup = view.render(root, params) || null;
  } catch (e) {
    console.error(e);
    clear(root).append(errorBox(`Error al mostrar la sección: ${e.message}`));
  }
  if (scroll) window.scrollTo(0, 0);
}

function scheduleRender() {
  if (renderQueued) return;
  renderQueued = true;
  queueMicrotask(() => renderRoute());
}

function currentView() {
  return VIEWS.get(parseHash().name) || overview;
}

// --- Arranque ------------------------------------------------------------------------

async function init() {
  renderNav(parseHash().name);
  renderThemeToggle();
  renderTasksButton();
  $('theme-toggle').addEventListener('click', toggleTheme);
  $('menu-btn').append(icon('menu'));
  $('menu-btn').addEventListener('click', toggleSidebar);
  mobileLayout.addEventListener('change', () => { setSidebar(false); renderNav(currentView().id); renderThemeToggle(); });
  $('sidebar-scrim').addEventListener('click', () => setSidebar(false));
  $('tasks-btn').addEventListener('click', () => navigate('activity'));

  try {
    state.meta = await get('/api/meta');
    await loadClients();
  } catch (e) {
    clear($('view')).append(errorBox(e.message));
    return;
  }

  on('clients', renderSwitcher);
  on('connection', () => {
    renderSwitcher();
    const view = currentView();
    if ((view.needsClient && showingPlaceholder) || (view.id === 'overview' && state.connecting === state.client)) scheduleRender();
  });
  on('client', () => {
    renderSwitcher();
    const view = currentView();
    if (view.needsClient || view.id === 'overview') scheduleRender();
  });
  on('jobs', renderTasksButton);
  on('open-job', (id) => openJobDrawer(id));
  on('job-finished', (job) => {
    if (job.kind === 'backup') invalidateBackups();
    if (currentView().id === 'backups') scheduleRender();
  });
  on('route', () => renderRoute({ scroll: true }));
  window.addEventListener('hashchange', () => renderRoute({ scroll: true }));

  renderSwitcher();
  renderRoute();
  watchJobs().catch(() => {});

  // Reconecta en segundo plano con el último cliente usado si el servidor no tiene ya la conexión
  const client = currentClient();
  if (client && !client.connected) connectClient(client.name, { quiet: true });
}

init();
