// Settings menu: video source / cameras, storage cleanup, and server shutdown.
import { $, api, postJSON } from './api.js';
import { pollStatus, forceFeedRebuild } from './feeds.js';
import { loadGallery } from './gallery.js';

const menuBtn = $('menu-btn'), menuPanel = $('menu-panel');
function setMenu(open) {
  menuPanel.hidden = !open;
  menuBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
  if (open) { loadCameras(); loadSettings(); }
}
menuBtn.addEventListener('click', (e) => { e.stopPropagation(); setMenu(menuPanel.hidden); });
document.addEventListener('click', (e) => {
  if (!menuPanel.hidden && !menuPanel.contains(e.target) && e.target !== menuBtn) setMenu(false);
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !menuPanel.hidden) setMenu(false);   // Esc closes the menu
});

// --- storage settings ------------------------------------------------------
async function loadSettings() {
  const s = await api('/api/settings');
  $('retention').value = String(s.retention_days);
  $('max-snapshots').value = s.max_snapshots ? String(s.max_snapshots) : '';
}
$('retention').addEventListener('change', async () => {
  await postJSON('/api/settings', { retention_days: parseInt($('retention').value) });
  $('cleanup-status').textContent = parseInt($('retention').value) === 0
    ? 'Keeping everything. Pinned snapshots are never deleted.'
    : `Auto-deleting data older than ${$('retention').value} day(s). Pinned items kept forever.`;
});
$('max-snapshots').addEventListener('change', async () => {
  await postJSON('/api/settings', { max_snapshots: parseInt($('max-snapshots').value) || 0 });
});
$('clean-now').addEventListener('click', async () => {
  const btn = $('clean-now'); btn.disabled = true; btn.textContent = 'Cleaning…';
  try {
    const r = await api('/api/cleanup', { method: 'POST' });
    const removed = (r.detections_deleted || 0);
    const files = (r.files_deleted || 0) + (r.count_pruned || 0);
    $('cleanup-status').textContent = `Removed ${removed} records and ${files} image files.`;
    if (!$('tab-gallery').hidden) loadGallery();
  } finally {
    btn.disabled = false; btn.textContent = 'Clean now';
  }
});

// --- camera management ------------------------------------------------------
let selectedSource = null;
async function loadCameras() {
  const { devices, active, names = {} } = await api('/api/devices');
  const activeSet = new Set(active.map(String));
  const esc = (v) => String(v).replace(/"/g, '&quot;');

  $('active-cameras').innerHTML = active.length
    ? active.map(s => {
        const isUrl = /[^0-9]/.test(String(s));         // numeric = local index, else a stream URL
        const sub = isUrl ? String(s) : `local index ${s}`;
        return `<div class="device-opt active-cam">
          <span class="cam-info">
            <input class="cam-rename" data-source="${encodeURIComponent(s)}"
                   value="${esc(names[String(s)] || '')}" placeholder="Camera ${isUrl ? '' : s}" aria-label="Camera name">
            <span class="cam-sub muted" title="${esc(s)}">${sub}</span>
          </span>
          <button class="ghost" data-action="remove" data-source="${encodeURIComponent(s)}">remove</button>
        </div>`;
      }).join('')
    : '<span class="muted">No active cameras.</span>';

  const list = $('device-list');
  if (!devices.length) {
    list.innerHTML = '<span class="muted">No cameras detected automatically. Use the field below (e.g. 0).</span>';
  } else {
    list.innerHTML = devices.map(d => {
      const val = String(d.index);
      const inUse = activeSet.has(val);
      return `<label class="device-opt ${inUse ? 'selected' : ''}" data-val="${val}">
        <input type="radio" name="device" value="${val}" ${inUse ? 'disabled' : ''}>
        <span>${d.name}</span><span class="muted">#${d.index}</span>
        ${inUse ? '<span class="current">added</span>' : ''}
      </label>`;
    }).join('');
    list.querySelectorAll('input[name=device]').forEach(r =>
      r.addEventListener('change', () => { selectedSource = r.value; $('custom-source').value = ''; }));
  }
}

async function addCamera() {
  const source = $('custom-source').value.trim() || selectedSource;
  if (!source) return;
  const btn = $('add-camera'); btn.disabled = true; btn.textContent = 'Adding…';
  try {
    await postJSON('/api/cameras', { source });
    $('custom-source').value = ''; selectedSource = null;
    forceFeedRebuild();        // include the new camera in the feed grid
    loadCameras(); pollStatus();
  } catch (e) {
    alert('Could not add camera: ' + e.message);
  } finally {
    btn.disabled = false; btn.textContent = 'Add camera';
  }
}
async function removeCamera(encSource) {
  await postJSON('/api/cameras/remove', { source: decodeURIComponent(encSource) });
  forceFeedRebuild();
  loadCameras(); pollStatus();
}
$('active-cameras').addEventListener('click', (e) => {
  const btn = e.target.closest('button[data-action="remove"]');
  if (btn) removeCamera(btn.dataset.source);
});
// rename a camera (saves on change/blur) — no popup
$('active-cameras').addEventListener('change', async (e) => {
  const inp = e.target.closest('input.cam-rename');
  if (!inp) return;
  await postJSON('/api/cameras/name', { source: decodeURIComponent(inp.dataset.source), name: inp.value });
  pollStatus();   // refresh names in the status bar / feeds
});
$('add-camera').addEventListener('click', addCamera);
$('rescan').addEventListener('click', () => {
  $('device-list').innerHTML = '<span class="muted">scanning…</span>';
  loadCameras();
  loadDiscover();
});

// --- ONVIF network camera discovery + detect-by-IP -------------------------
async function loadDiscover() {
  const box = $('discovered');
  box.innerHTML = '<span class="muted">searching network for ONVIF cameras…</span>';
  let found;
  try { found = (await api('/api/discover')).found || []; }
  catch (e) { box.innerHTML = ''; return; }
  const active = new Set((await api('/api/devices')).active.map(String));
  box.innerHTML = found.length
    ? found.map(f => `<div class="device-opt">
        <span class="cam-info"><span>${f.ip}</span><span class="cam-sub muted">ONVIF network camera</span></span>
        <button class="ghost" data-onvif-ip="${f.ip}">use</button>
      </div>`).join('')
    : '';   // none on this segment — the IP form below covers other subnets
}
$('discovered').addEventListener('click', (e) => {
  const b = e.target.closest('button[data-onvif-ip]');
  if (b) { $('onvif-ip').value = b.dataset.onvifIp; $('onvif-user').focus(); }
});

$('onvif-detect').addEventListener('click', async () => {
  const ip = $('onvif-ip').value.trim();
  if (!ip) return;
  const btn = $('onvif-detect'), lbl = btn.textContent;
  btn.disabled = true; btn.textContent = 'Detecting…';
  const box = $('onvif-results');
  box.innerHTML = '<span class="muted">querying camera…</span>';
  try {
    const { urls } = await postJSON('/api/cameras/probe', {
      ip, user: $('onvif-user').value, password: $('onvif-pass').value,
    });
    box.innerHTML = urls.length
      ? urls.map((u, i) => `<div class="device-opt">
          <span class="cam-info">
            <span>${i === 0 ? 'Main stream' : 'Stream ' + (i + 1)}</span>
            <span class="cam-sub muted">${u.replace(/:[^:@/]*@/, ':•••@')}</span>
          </span>
          <button data-add-url="${encodeURIComponent(u)}">Add</button>
        </div>`).join('')
      : '<span class="muted">No stream found. Check the IP/login, or the camera may not speak ONVIF — paste its RTSP URL in the field above instead.</span>';
  } catch (e) {
    box.innerHTML = '<span class="muted">Detect failed: ' + e.message + '</span>';
  } finally {
    btn.disabled = false; btn.textContent = lbl;
  }
});
$('onvif-results').addEventListener('click', async (e) => {
  const b = e.target.closest('button[data-add-url]');
  if (!b) return;
  await postJSON('/api/cameras', { source: decodeURIComponent(b.dataset.addUrl) });
  forceFeedRebuild(); loadCameras(); pollStatus();
  $('onvif-results').innerHTML = '<span class="muted">Added — see Active cameras above.</span>';
});

// --- shutdown (double-click to confirm) ------------------------------------
let shutArmed = false, shutTimer = null;
$('shutdown').addEventListener('click', async () => {
  const btn = $('shutdown');
  if (!shutArmed) {
    shutArmed = true;
    btn.classList.add('arm');
    btn.textContent = 'Confirm shutdown';
    shutTimer = setTimeout(() => { shutArmed = false; btn.classList.remove('arm'); btn.textContent = 'Shut down'; }, 2500);
    return;
  }
  clearTimeout(shutTimer); shutArmed = false;
  try { await api('/api/shutdown', { method: 'POST' }); } catch (e) { /* process exits */ }
  document.body.innerHTML =
    '<div style="padding:48px;font:16px/1.6 sans-serif;color:#fff;background:#000;height:100vh">' +
    'Argus has shut down.<br>Restart with <code>.venv/bin/python run.py</code> and reload.</div>';
});
