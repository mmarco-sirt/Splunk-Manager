// Filtros comunes de los listados. Se combinan entre sí: la tabla aplica todos a la vez.
import { state, currentClient } from './core.js';

/** Id de la app de SIRT en el cliente activo: su campo «App SIRT» (en Clientes) o la de por defecto. */
export function sirtApp() {
  return currentClient()?.sirt_app || state.meta?.sirt_app || 'sirt_soc_app_for_splunk';
}

/** App: todas o solo la de SIRT (filas con 'app'). */
export const appFilter = {
  id: 'app',
  label: 'App',
  options: [
    { value: 'all', label: 'Todas' },
    { value: 'sirt', label: 'App SIRT', title: () => `Solo los de la app ${sirtApp()}`, test: (r) => r.app === sirtApp() },
  ],
};

/** Tipo de alerta: casos de uso en producción (CU-), en pruebas (TEST - CU-), Enterprise Security u otras. */
export const alertKindFilter = {
  id: 'kind',
  label: 'Tipo',
  options: [
    { value: 'all', label: 'Todas' },
    { value: 'cu', label: 'CU-', title: 'Casos de uso en producción (CU-…)', test: (r) => r.is_cu && !r.is_test },
    { value: 'test', label: 'TEST', title: 'Casos de uso en pruebas (TEST - CU-…)', test: (r) => r.is_test },
    { value: 'es', label: 'Enterprise Security', test: (r) => r.is_es },
    { value: 'other', label: 'Otras', test: (r) => !r.is_cu && !r.is_es },
  ],
};

/** Estado: habilitadas o deshabilitadas (filas con 'disabled'). */
export const enabledFilter = {
  id: 'state',
  label: 'Estado',
  options: [
    { value: 'all', label: 'Todas' },
    { value: 'on', label: 'Habilitadas', test: (r) => !r.disabled },
    { value: 'off', label: 'Deshabilitadas', test: (r) => r.disabled },
  ],
};
