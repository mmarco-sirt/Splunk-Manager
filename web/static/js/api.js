// Cliente HTTP de la API. La cabecera X-Splunk-Manager es obligatoria en las peticiones que modifican algo.
const HEADERS = { 'Content-Type': 'application/json', 'X-Splunk-Manager': '1' };

export async function api(path, { method = 'GET', body, params, raw } = {}) {
  let url = path;
  if (params) {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null && value !== '') query.append(key, value);
    }
    const qs = query.toString();
    if (qs) url += (url.includes('?') ? '&' : '?') + qs;
  }

  let response;
  try {
    response = await fetch(url, {
      method,
      // raw: un archivo tal cual (p. ej. un paquete de app), con la misma cabecera anti-CSRF
      headers: raw ? { 'Content-Type': 'application/octet-stream', 'X-Splunk-Manager': '1' } : HEADERS,
      body: raw ?? (body === undefined ? undefined : JSON.stringify(body)),
    });
  } catch (e) {
    throw new Error('No se puede contactar con el servidor de Splunk Manager. ¿Sigue en marcha main_web.py?');
  }

  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { error: text };
  }
  if (!response.ok) {
    const error = new Error(data?.error || `Error ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return data;
}

export const get = (path, params) => api(path, { params });
export const post = (path, body = {}) => api(path, { method: 'POST', body });
export const put = (path, body = {}) => api(path, { method: 'PUT', body });
export const del = (path) => api(path, { method: 'DELETE' });
export const upload = (path, file, params) => api(path, { method: 'POST', raw: file, params });

export const enc = encodeURIComponent;
