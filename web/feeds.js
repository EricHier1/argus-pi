// Live camera feeds, status bar, and the power / boxes toggles.
import { $, api, postJSON, camLabel } from './api.js';

let renderedSources = null;   // null forces the first render even with 0 cameras
let feedGen = 0;              // bumped on every rebuild so old frame-pumps stop
let dashCams = [];            // last cameras rendered into the dashboard feed
export const FRAME_MS = 120;  // ~8 fps live view (polled JPEG, works on every browser)

export function forceFeedRebuild() { renderedSources = null; }
export const isActive = (tabId) => {
  const el = document.getElementById(tabId);
  return !!(el && el.classList.contains('active'));
};

// Click a dashboard feed -> open that camera in the View tab.
$('feed-grid').addEventListener('click', (e) => {
  const card = e.target.closest('.feed-card[data-source]');
  if (card) document.dispatchEvent(new CustomEvent('argus:viewcam',
    { detail: { source: decodeURIComponent(card.dataset.source) } }));
});

// Continuously poll the latest JPEG for one camera into the <img>, while alive()
// is true. Replaces MJPEG, which iOS Safari renders unreliably. `alive` lets a
// view stop polling when its tab is hidden (saves bandwidth).
export function pumpFrame(img, source, alive) {
  let curUrl = null;
  const schedule = (ms) => { if (alive()) setTimeout(tick, ms); else if (curUrl) URL.revokeObjectURL(curUrl); };
  async function tick() {
    if (!alive()) { if (curUrl) URL.revokeObjectURL(curUrl); return; }
    try {
      const r = await fetch(`/frame?source=${encodeURIComponent(source)}&t=${Date.now()}`, { cache: 'no-store' });
      if (!alive()) { if (curUrl) URL.revokeObjectURL(curUrl); return; }
      if (r.ok) {
        const url = URL.createObjectURL(await r.blob());
        img.src = url;
        if (curUrl) URL.revokeObjectURL(curUrl);
        curUrl = url;
        schedule(FRAME_MS);
      } else {
        schedule(450);   // 204 = no frame yet / detection paused — keep last image
      }
    } catch (e) {
      schedule(700);     // network hiccup — back off and retry
    }
  }
  tick();
}

export async function pollStatus() {
  try {
    const s = await api('/api/status');
    const cameras = s.cameras || [];
    renderFeeds(cameras);
    updateStatusBar(cameras);
    updatePowerButton(cameras.length > 0 && cameras.every(c => c.paused));
    updateBoxesButton(s.show_boxes !== false);
  } catch (e) {
    $('status').className = 'status error';
    $('status').textContent = 'server unreachable';
  }
}

function startDashPumps() {
  feedGen++;
  const g = feedGen;
  $('feed-grid').querySelectorAll('.feed-card img').forEach((img, i) =>
    pumpFrame(img, dashCams[i].source, () => g === feedGen && isActive('tab-dashboard')));
}
// re-pump the dashboard feed after returning to the Dashboard tab
export function resumeDashFeeds() { if (dashCams.length) startDashPumps(); }

function renderFeeds(cameras) {
  dashCams = cameras;
  const grid = $('feed-grid');
  grid.classList.toggle('multi', cameras.length > 1);
  // Only rebuild <img> elements when the set of sources changes.
  const key = cameras.map(c => c.source).join('|');
  if (key !== renderedSources) {
    renderedSources = key;
    grid.innerHTML = cameras.length ? cameras.map(c => `
      <div class="feed-card" data-source="${encodeURIComponent(c.source)}" title="Open in View tab">
        <div class="feed-video"><img alt="${camLabel(c)}"></div>
        <div class="feed-cap"><span class="feed-name">${camLabel(c)}</span><span class="feed-stat muted"></span></div>
      </div>`).join('')
      : '<span class="muted">No cameras. Add one from the settings menu.</span>';
    if (cameras.length) startDashPumps();
    else feedGen++;
  }
  const cards = grid.querySelectorAll('.feed-card');
  cameras.forEach((c, i) => {
    const stat = cards[i] && cards[i].querySelector('.feed-stat');
    if (!stat) return;
    stat.textContent = c.error ? 'error' : c.paused ? 'paused'
      : `${c.fps} fps · ${c.last_detection_count} obj · ${c.device}`;
    stat.className = 'feed-stat ' + (c.error ? 'err' : 'muted');
  });
}

function updateStatusBar(cameras) {
  const el = $('status');
  if (!cameras.length) {
    el.className = 'status'; el.innerHTML = '<span class="dot"></span>no cameras — add one in settings';
    return;
  }
  const err = cameras.find(c => c.error);
  if (err) { el.className = 'status error'; el.innerHTML = `<span class="dot"></span>${err.error}`; return; }
  if (cameras.every(c => c.paused)) {
    el.className = 'status'; el.innerHTML = '<span class="dot"></span>detection off';
    return;
  }
  const live = cameras.filter(c => c.running).length;
  const objs = cameras.reduce((a, c) => a + (c.last_detection_count || 0), 0);
  el.className = 'status live';
  el.innerHTML = `<span class="dot"></span>${live}/${cameras.length} cam live · ${cameras[0].device} · ${objs} object${objs === 1 ? '' : 's'} in frame`;
}

// --- boxes toggle (live feed) ----------------------------------------------
function updateBoxesButton(on) {
  const btn = $('boxes-toggle');
  if (!btn) return;
  btn.textContent = 'Boxes: ' + (on ? 'ON' : 'OFF');
  btn.setAttribute('aria-pressed', on ? 'true' : 'false');
  btn.dataset.on = on ? '1' : '0';
}
$('boxes-toggle').addEventListener('click', async () => {
  const on = $('boxes-toggle').dataset.on !== '0';
  const r = await postJSON('/api/boxes', { on: !on });
  updateBoxesButton(r.show_boxes);
});

// --- power toggle (double-click to confirm) --------------------------------
let powerArmed = false, powerArmTimer = null;
function updatePowerButton(paused) {
  const btn = $('power-toggle');
  if (!btn || powerArmed) return;   // don't overwrite the "confirm" label while armed
  btn.className = paused ? 'power off' : 'power on';
  btn.textContent = paused ? 'Detection OFF' : 'Detection ON';
  btn.dataset.paused = paused ? '1' : '0';
}
$('power-toggle').addEventListener('click', async () => {
  const btn = $('power-toggle');
  const turningOn = btn.dataset.paused === '1';
  if (!powerArmed) {                 // first click: arm
    powerArmed = true;
    btn.classList.add('arm');
    btn.textContent = turningOn ? 'Confirm: turn ON' : 'Confirm: turn OFF';
    powerArmTimer = setTimeout(() => { powerArmed = false; btn.classList.remove('arm'); pollStatus(); }, 2500);
    return;
  }
  clearTimeout(powerArmTimer); powerArmed = false; btn.classList.remove('arm');
  await postJSON('/api/power', { on: turningOn });   // no source = all cameras
  pollStatus();   // the frame-pump auto-recovers when frames resume
});
