// Analytics tab: totals, top objects, per-camera breakdown, hour-of-day chart.
import { $, api } from './api.js';

export async function loadAnalytics() {
  let d;
  try { d = await api('/api/analytics'); }
  catch (e) { $('stat-cards').innerHTML = '<span class="muted">Could not load analytics.</span>'; return; }

  const t = d.totals || {};
  $('stat-cards').innerHTML = [
    ['Detections', t.detections], ['Alerts', t.alerts],
    ['Snapshots', t.snapshots], ['Object types', t.types],
  ].map(([label, n]) => `
    <div class="stat-card"><div class="stat-n">${(n ?? 0).toLocaleString()}</div><div class="stat-l">${label}</div></div>`
  ).join('');

  renderBars($('top-objects'), (d.by_label || []).map(r => [r.label, r.n]));
  renderBars($('by-camera'), (d.by_camera || []).map(r => ['cam ' + r.source, r.n]));
  drawHourChart(d.by_hour || []);
}

function renderBars(el, rows) {
  if (!rows.length) { el.innerHTML = '<span class="muted">No data yet.</span>'; return; }
  const max = Math.max(1, ...rows.map(r => r[1]));
  el.innerHTML = rows.map(([label, n]) => `
    <div class="bar-row">
      <span class="bar-label">${label}</span>
      <span class="bar-track"><span class="bar-fill" style="width:${(n / max * 100).toFixed(1)}%"></span></span>
      <span class="bar-n">${n.toLocaleString()}</span>
    </div>`).join('');
}

function drawHourChart(hours) {
  const c = $('hour-chart');
  if (!c) return;
  const ctx = c.getContext('2d');
  const w = c.width = c.clientWidth || 600, h = c.height;
  ctx.clearRect(0, 0, w, h);
  const n = hours.length || 24;
  const max = Math.max(1, ...hours);
  const bw = w / n;
  ctx.fillStyle = '#01BB4E';
  hours.forEach((v, i) => {
    const bh = (v / max) * (h - 18);
    ctx.fillRect(i * bw + 1, h - bh - 14, Math.max(1, bw - 2), bh);
  });
  ctx.fillStyle = '#9CA3AF'; ctx.font = '10px sans-serif'; ctx.textAlign = 'center';
  for (let i = 0; i < n; i += 3) {
    ctx.fillText(String(i).padStart(2, '0'), i * bw + bw / 2, h - 2);
  }
}
