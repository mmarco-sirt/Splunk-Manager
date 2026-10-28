// Estado compartido de la aplicación: cliente activo, caché de datos de Splunk, tareas y navegación.
import { get, post, enc } from './api.js';
import { toast } from './ui.js';

const CLIENT_KEY = 'sm.client';

export const state = {
  meta: null,
  clients: [],
  client: null,        // nombre del cliente activo
  connecting: null,    // nombre del cliente que se está conectando
  jobs: [],
};

// --- Eventos -------------------------------------------------------------

const listeners = new Map();

export function on(event, fn) {
  if (!listeners.has(event)) listeners.set(event, new Set());
  listeners.get(event).add(fn);
  return () => listeners.get(event).delete(fn);
}

export function emit(event, data) {
  for (const fn of listeners.get(event) || []) {
    try { fn(data); } catch (e) { console.error(e); }
  }
}

// --- Navegación (hash: #/vista?param=valor) -------------------------------

export function parseHash() {
  const raw = location.hash.replace(/^#\/?/, '');
  const [name, query = ''] = raw.split('?');
  return { name: name || 'overview', params: Object.fromEntries(new URLSearchParams(query)) };
}

export function navigate(name, params) {
  const qs = params ? new URLSearchParams(params).toString() : '';
  const target = `#/${name}${qs ? `?${qs}` : ''}`;
  if (location.hash === target) emit('route');
  else location.hash = target;
}

// --- Clientes y conexión ------------------------------------------------------

export function currentClient() {
  return state.clients.find((c) => c.name === state.client) || null;
}

export function isConnected() {
  return Boolean(currentClient()?.connected);
}

export async function loadClients() {
  state.clients = await get('/api/clients');
  if (state.client && !state.clients.some((c) => c.name === state.client)) state.client = null;
  if (!state.client) {
    let saved = null;
    try { saved = localStorage.getItem(CLIENT_KEY); } catch { /* sin almacenamiento */ }
    if (saved && state.clients.some((c) => c.name === saved)) state.client = saved;
  }
  emit('clients');
}

function updateClient(data) {
  const i = state.clients.findIndex((c) => c.name === data.name);
  if (i >= 0) state.clients[i] = data; else state.clients.push(data);
}

export async function selectClient(name) {
  const changed = state.client !== name;
  state.client = name;
  try { localStorage.setItem(CLIENT_KEY, name); } catch { /* sin almacenamiento */ }
  if (changed) emit('client');
  const client = currentClient();
  if (client && !client.connected) await connectClient(name);
}

export async function connectClient(name, { quiet = false } = {}) {
  state.connecting = name;
  emit('connection');
  try {
    const data = await post(`/api/clients/${enc(name)}/connect`);
    updateClient(data);
    invalidate(name);
    if (!quiet) toast(`Conectado a ${name}${data.info?.version ? ` · Splunk ${data.info.version}` : ''}`, 'success');
    return true;
  } catch (e) {
    toast(e.message, 'error');
    return false;
  } finally {
    state.connecting = null;
    emit('connection');
    if (name === state.client) emit('client');
  }
}

export async function disconnectClient(name) {
  await post(`/api/clients/${enc(name)}/disconnect`);
  const client = state.clients.find((c) => c.name === name);
  if (client) client.connected = false;
  invalidate(name);
  emit('connection');
  if (name === state.client) emit('client');
}

// --- Caché de recursos de Splunk (por cliente) ------------------------------

const cache = new Map();

export function loadResource(resource, { force = false, client = state.client } = {}) {
  if (!client) return Promise.reject(new Error('Seleccione un cliente.'));
  const key = `${client}::${resource}`;
  const hit = cache.get(key);
  if (hit && !force) return hit.promise;
  const promise = get(`/api/splunk/${resource}`, { client });
  const entry = { promise, at: Date.now() };
  cache.set(key, entry);
  promise.catch(() => { if (cache.get(key) === entry) cache.delete(key); });
  return promise;
}

export function cachedAt(resource, client = state.client) {
  return cache.get(`${client}::${resource}`)?.at || null;
}

export function invalidate(client = state.client, resources = null) {
  for (const key of [...cache.keys()]) {
    const [c, r] = key.split('::');
    if (c === client && (!resources || resources.includes(r))) cache.delete(key);
  }
}

// --- Tareas en segundo plano -----------------------------------------------------

const RESTORE_RESOURCES = {
  alertas: ['saved-searches'], reportes: ['saved-searches'], macros: ['macros'],
  users: ['users'], roles: ['roles'], dashboards: ['dashboards'],
};

let pollTimer = null;
const known = new Map();

export async function startJob(endpoint, body, { open = true, folder } = {}) {
  const job = await post(endpoint, body);
  job.folder = folder;
  known.set(job.id, job);
  state.jobs = [job, ...state.jobs.filter((j) => j.id !== job.id)];
  emit('jobs');
  if (open) emit('open-job', job.id);
  schedulePoll(800);
  return job;
}

export async function refreshJobs() {
  const jobs = await get('/api/jobs');
  for (const job of jobs) {
    const before = known.get(job.id);
    if (before && before.status === 'running' && job.status !== 'running') onJobFinished({ ...job, folder: before.folder });
    known.set(job.id, { ...job, folder: before?.folder });
  }
  state.jobs = jobs;
  emit('jobs');
  return jobs;
}

function onJobFinished(job) {
  if (job.kind === 'restore' || job.kind === 'copy' || job.kind === 'edit') {
    for (const client of job.clients) invalidate(client, RESTORE_RESOURCES[job.folder] || null);
  }
  emit('job-finished', job);
  const text = {
    success: `Tarea completada: ${job.title}`,
    partial: `Tarea completada con ${job.errors} ${job.errors === 1 ? 'error' : 'errores'}: ${job.title}`,
    error: `La tarea falló: ${job.title}`,
    cancelled: `Tarea cancelada: ${job.title}`,
  }[job.status] || job.title;
  const type = { success: 'success', partial: 'warn', error: 'error' }[job.status] || 'info';
  toast(text, type, { action: { label: 'Ver', onClick: () => emit('open-job', job.id) } });
}

function schedulePoll(delay = 1500) {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(async () => {
    try {
      const jobs = await refreshJobs();
      if (jobs.some((j) => j.status === 'running')) schedulePoll();
    } catch {
      schedulePoll(5000);
    }
  }, delay);
}

/** Carga las tareas y, si alguna sigue en marcha, sigue consultándolas hasta que terminen. */
export async function watchJobs() {
  const jobs = await refreshJobs();
  if (jobs.some((j) => j.status === 'running')) schedulePoll();
}

export function runningJobs() {
  return state.jobs.filter((j) => j.status === 'running').length;
}
