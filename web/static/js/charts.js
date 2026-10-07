// Gráficos sencillos sin dependencias: columnas diarias y minigráficas de tendencia.
import { h, attachTooltip, fmtBytes } from './ui.js';

const UNIT = 1024;

/** Escala "bonita" para el eje Y: devuelve los valores de las marcas (0 incluido). */
function niceTicks(max, count = 4) {
  if (max <= 0) return [0, 1];
  let unit = 1;
  while (max / unit >= UNIT) unit *= UNIT;
  const raw = max / unit / count;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= raw) * unit;
  const ticks = [];
  for (let v = 0; v <= max + step * 0.001; v += step) ticks.push(v);
  if (ticks[ticks.length - 1] < max) ticks.push(ticks[ticks.length - 1] + step);
  return ticks;
}

/**
 * Columnas de una sola serie (p. ej. GB por día) con rejilla, línea de media y tooltip por barra.
 * points: [{ label, value, partial }]
 */
export function columnChart(points, { format = fmtBytes, tooltipLabel = (p) => p.label, average, height = 220, xLabel = (p) => p.label } = {}) {
  const max = Math.max(...points.map((p) => p.value), average || 0, 1);
  const ticks = niceTicks(max);
  const top = ticks[ticks.length - 1];
  const pct = (v) => `${(v / top) * 100}%`;

  const grid = h('div', { class: 'cc-grid', 'aria-hidden': 'true' },
    ticks.map((t, i) => h('div', { class: `cc-gridline ${i === 0 ? 'baseline' : ''}`, style: { bottom: pct(t) } },
      h('span', { class: 'cc-tick' }, i === 0 ? '0' : format(t)))));

  const bars = h('div', { class: 'cc-bars' }, points.map((p) => {
    const col = h('div', { class: 'cc-col', tabindex: '0', 'aria-label': `${tooltipLabel(p)}: ${format(p.value)}` },
      h('div', { class: `cc-bar ${p.partial ? 'partial' : ''}`, style: { height: p.value > 0 ? `max(2px, ${pct(p.value)})` : '0' } }));
    attachTooltip(col, () => [h('b', {}, tooltipLabel(p)), h('br'), format(p.value), p.partial ? ' · día en curso' : '']);
    return col;
  }));

  const avgLine = average
    ? h('div', { class: 'cc-avg', style: { bottom: pct(average) } }, h('span', {}, `media ${format(average)}`))
    : null;

  const step = Math.max(1, Math.ceil(points.length / 8));
  const labels = h('div', { class: 'cc-xlabels', 'aria-hidden': 'true' },
    points.map((p, i) => h('span', {}, i % step === 0 || i === points.length - 1 ? xLabel(p) : '')));

  return h('div', { class: 'col-chart', style: { '--chart-h': `${height}px` } },
    h('div', { class: 'cc-plot' }, grid, bars, avgLine),
    labels);
}

/** Minigráfica de tendencia (línea de 1,5 px; el último punto en el color de acento). */
export function sparkline(values, { width = 110, height = 26, label = '' } = {}) {
  const wrap = h('span', { class: 'sparkline', role: 'img', 'aria-label': label || 'Tendencia' });
  if (!values.length) return wrap;
  const max = Math.max(...values, 1);
  const stepX = values.length > 1 ? (width - 4) / (values.length - 1) : 0;
  const y = (v) => (height - 3 - (v / max) * (height - 6)).toFixed(1);
  const points = values.map((v, i) => `${(2 + i * stepX).toFixed(1)},${y(v)}`).join(' ');
  const last = values.length - 1;
  wrap.innerHTML = `<svg viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" aria-hidden="true">`
    + `<polyline points="${points}" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/>`
    + `<circle cx="${(2 + last * stepX).toFixed(1)}" cy="${y(values[last])}" r="2.5" class="spark-dot"/></svg>`;
  return wrap;
}
