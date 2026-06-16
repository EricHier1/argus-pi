// Dashboard widgets: detection labels, alerts feed, alert rules, search, activity.
import { $, fmt, api, postJSON, toEpoch, armConfirm } from './api.js';
import { syncGalleryFilter, openLightboxItems, sentinelNear } from './gallery.js';

// --- labels (populate filters + datalist) ----------------------------------
export async function loadLabels() {
  const labels = await api('/api/labels');
  const sel = $('search-label'), dl = $('label-options'), liveChips = $('live-detections');
  sel.innerHTML = '<option value="">any</option>';
  dl.innerHTML = '';
  liveChips.innerHTML = '';
  for (const l of labels) {
    sel.insertAdjacentHTML('beforeend', `<option value="${l.label}">${l.label} (${l.n})</option>`);
    dl.insertAdjacentHTML('beforeend', `<option value="${l.label}">`);
    liveChips.insertAdjacentHTML('beforeend', `<span class="chip">${l.label} · ${l.n}</span>`);
  }
  syncGalleryFilter();
}

// --- alerts (dashboard panel + paginated Alerts tab) -----------------------
const ALERT_PAGE = 30;
let lastAlertTs = 0;
let dashAlerts = [];                                  // dashboard panel (recent, polled)
let tabAlerts = [], aBefore = null, aLoading = false, aDone = false;   // Alerts tab (paginated)

export async function pollAlerts() {
  const [alerts, c] = await Promise.all([
    api('/api/alerts?limit=50'),
    api('/api/alerts/count').catch(() => ({ count: null })),
  ]);
  dashAlerts = alerts;
  if (c.count != null) $('alerts-count').textContent = c.count;
  const list = $('alerts-list');
  if (!alerts.length) { list.textContent = 'No alerts yet.'; return; }
  list.innerHTML = alerts.map((a, i) => `
    <div class="alert-item ${a.snapshot ? 'clickable' : ''}"
         ${a.snapshot ? `data-i="${i}" tabindex="0" role="button"` : ''}>
      ${a.snapshot ? `<img src="/snapshots/${a.snapshot}">` : ''}
      <div class="meta">
        <div class="label">${a.message || a.label}</div>
        <div class="muted">${fmt(a.ts)}</div>
      </div>
    </div>`).join('');
  if (alerts[0].ts > lastAlertTs && lastAlertTs > 0) {
    document.title = '(!) Argus — alert!';
    setTimeout(() => document.title = 'Argus', 4000);
  }
  lastAlertTs = alerts[0].ts;
}

function alertTile(a, i) {
  return `<div class="result" data-i="${i}" tabindex="0" role="button" aria-label="${a.message || a.label}">
    <img src="/snapshots/${a.snapshot}" loading="lazy" alt="">
    <div class="label">${a.message || a.label}</div>
    <div class="muted">${fmt(a.ts)}</div>
  </div>`;
}

async function loadAlertsPage(reset) {
  if (aLoading || (aDone && !reset)) return;
  if (reset) { tabAlerts = []; aBefore = null; aDone = false; $('alerts-grid').innerHTML = ''; }
  aLoading = true;
  const params = new URLSearchParams();
  params.set('limit', String(ALERT_PAGE));
  if (aBefore != null) params.set('before', aBefore);
  let page;
  try { page = await api('/api/alerts?' + params.toString()); }
  catch (e) {
    aLoading = false;
    if (reset) $('alerts-grid').innerHTML = '<span class="muted">Could not load alerts.</span>';
    return;
  }
  if (page.length) aBefore = page[page.length - 1].ts;   // advance cursor past oldest in page
  if (page.length < ALERT_PAGE) aDone = true;
  const withPics = page.filter(a => a.snapshot);
  const start = tabAlerts.length;
  tabAlerts.push(...withPics);
  if (withPics.length) $('alerts-grid').insertAdjacentHTML('beforeend', withPics.map((a, k) => alertTile(a, start + k)).join(''));
  if (!tabAlerts.length && aDone) {
    $('alerts-grid').innerHTML = '<span class="muted">No alert captures yet. Add an alert rule on the dashboard; matches will appear here.</span>';
  }
  aLoading = false;
  if (!aDone && sentinelNear('alerts-sentinel')) setTimeout(() => loadAlertsPage(false), 60);
}
export function loadAlertsTab() { loadAlertsPage(true); }

new IntersectionObserver((e) => { if (e[0].isIntersecting) loadAlertsPage(false); },
  { rootMargin: '300px' }).observe($('alerts-sentinel'));

// Open an alert's picture in the lightbox, browsable across that list's pics.
function openAlertFrom(list, i) {
  const a = list[i];
  if (!a || !a.snapshot) return;
  const withPics = list.filter(x => x.snapshot);
  const items = withPics.map(x => ({ snapshot: x.snapshot, ts: x.ts, labels: x.label, kept: 0 }));
  const idx = withPics.findIndex(x => x === a);
  openLightboxItems(items, idx < 0 ? 0 : idx);
}

// event delegation — dashboard alert rows + Alerts-tab tiles
$('alerts-list').addEventListener('click', (e) => {
  const row = e.target.closest('.alert-item[data-i]');
  if (row) openAlertFrom(dashAlerts, +row.dataset.i);
});
$('alerts-grid').addEventListener('click', (e) => {
  const tile = e.target.closest('.result[data-i]');
  if (tile) openAlertFrom(tabAlerts, +tile.dataset.i);
});
['alerts-list', 'alerts-grid'].forEach(id => $(id).addEventListener('keydown', (e) => {
  const t = e.target.closest('[data-i]');
  if (t && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); t.click(); }
}));

// When captures are deleted elsewhere (e.g. the gallery), refresh the alert views
// so deleted pictures don't linger in the Alerts panel/tab.
document.addEventListener('argus:changed', () => {
  pollAlerts();   // refresh dashboard panel + total count
  if ($('tab-alerts').classList.contains('active')) loadAlertsTab();
});

armConfirm($('alerts-delete-all'), 'Click again to delete ALL', async () => {
  await api('/api/alerts/all', { method: 'DELETE' });
  loadAlertsTab();
  pollAlerts();
});

// --- rules (event-delegated) -----------------------------------------------
export async function loadRules() {
  const rules = await api('/api/rules');
  const list = $('rules-list');
  if (!rules.length) { list.innerHTML = '<span class="muted">No rules. Add one above.</span>'; return; }
  list.innerHTML = rules.map(r => {
    const when = (r.start_hour == null || r.end_hour == null)
      ? 'any time' : `${r.start_hour}:00 to ${r.end_hour}:00`;
    return `<div class="rule-item ${r.active ? '' : 'inactive'}" data-id="${r.id}">
      <div class="meta">
        <div class="label">${r.label} <span class="badge">conf ≥ ${r.min_conf}</span></div>
        <div class="muted">${when}</div>
      </div>
      <button class="ghost" data-action="toggle" data-active="${r.active ? 0 : 1}">${r.active ? 'pause' : 'enable'}</button>
      <button class="ghost" data-action="delete">delete</button>
    </div>`;
  }).join('');
}
async function toggleRule(id, active) {
  await postJSON(`/api/rules/${id}/toggle`, { active: !!active });
  loadRules();
}
async function deleteRule(id) {
  await api(`/api/rules/${id}`, { method: 'DELETE' });
  loadRules();
}
$('rules-list').addEventListener('click', (e) => {
  const btn = e.target.closest('button[data-action]');
  if (!btn) return;
  const id = +btn.closest('.rule-item').dataset.id;
  if (btn.dataset.action === 'toggle') toggleRule(id, +btn.dataset.active);
  else if (btn.dataset.action === 'delete') deleteRule(id);
});

$('rule-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  await postJSON('/api/rules', {
    label: $('rule-label').value.trim(),
    min_conf: parseFloat($('rule-conf').value) || 0.5,
    start_hour: $('rule-start').value === '' ? null : parseInt($('rule-start').value),
    end_hour: $('rule-end').value === '' ? null : parseInt($('rule-end').value),
  });
  $('rule-form').reset();
  $('rule-conf').value = '0.5';
  loadRules();
});

// --- search ----------------------------------------------------------------
$('search-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const params = new URLSearchParams();
  const label = $('search-label').value;
  const start = toEpoch($('search-start').value);
  const end = toEpoch($('search-end').value);
  if (label) params.set('label', label);
  if (start) params.set('start', start);
  if (end) params.set('end', end);
  params.set('limit', '200');
  const rows = await api('/api/detections?' + params.toString());
  const out = $('search-results');
  if (!rows.length) { out.innerHTML = '<span class="muted">No matches.</span>'; return; }
  out.innerHTML = rows.map(r => `
    <div class="result">
      ${r.snapshot ? `<img src="/snapshots/${r.snapshot}">` : '<div class="muted">no snapshot</div>'}
      <div class="label">${r.label}</div>
      <div class="muted">${(r.confidence * 100).toFixed(0)}% · ${fmt(r.ts)}</div>
    </div>`).join('');
});

// --- activity timeline ------------------------------------------------------
export async function loadActivity() {
  let data;
  try { data = await api('/api/activity?hours=24&buckets=24'); } catch (e) { return; }
  drawActivity(data);
}
function drawActivity(data) {
  const c = $('activity-chart');
  if (!c) return;
  const ctx = c.getContext('2d');
  const w = c.width = c.clientWidth || 600, h = c.height;
  ctx.clearRect(0, 0, w, h);
  const counts = data.counts || [];
  const n = counts.length || 1;
  const max = Math.max(1, ...counts);
  const bw = w / n;
  ctx.fillStyle = '#01BB4E';
  counts.forEach((v, i) => {
    const bh = (v / max) * (h - 18);
    ctx.fillRect(i * bw + 1, h - bh - 14, Math.max(1, bw - 2), bh);
  });
  ctx.fillStyle = '#9CA3AF'; ctx.font = '10px sans-serif'; ctx.textAlign = 'center';
  const step = Math.max(1, Math.ceil(n / 4));
  for (let i = 0; i < n; i += step) {
    const t = new Date((data.start + i * data.width_s) * 1000);
    ctx.fillText(t.getHours() + ':00', i * bw + bw / 2, h - 2);
  }
}
