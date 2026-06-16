// Extra live views: Quad (grid of all cameras) and Single (one camera, full-screen).
// Both reuse the gated frame-pump from feeds.js, so they only poll while visible.
import { $, api, camLabel } from './api.js';
import { pumpFrame, isActive } from './feeds.js';

let cams = [];
async function fetchCams() {
  try { cams = (await api('/api/status')).cameras || []; } catch (e) { /* keep last */ }
  return cams;
}

// --- Quad view -------------------------------------------------------------
let quadKey = null, quadGen = 0;
function renderQuad(force) {
  const grid = $('quad-grid');
  const key = cams.map(c => c.source).join('|');
  const n = cams.length || 1;
  const cols = Math.max(1, Math.ceil(Math.sqrt(n)));   // 1→1, 2→2, 3-4→2, 5-9→3 …
  const rows = Math.max(1, Math.ceil(n / cols));
  grid.style.setProperty('--cols', cols);
  grid.style.setProperty('--rows', rows);
  const changed = key !== quadKey;
  if (changed) {
    quadKey = key;
    grid.innerHTML = cams.length ? cams.map(c => `
      <div class="quad-cell">
        <img alt="${camLabel(c)}">
        <span class="quad-tag" title="${decodeURIComponent(String(c.source))}">${camLabel(c)}</span>
      </div>`).join('')
      : '<span class="muted">No cameras. Add cameras from the settings menu (top-right).</span>';
  }
  if ((changed || force) && cams.length) {
    quadGen++;
    const g = quadGen;
    grid.querySelectorAll('.quad-cell img').forEach((img, i) =>
      pumpFrame(img, cams[i].source, () => g === quadGen && isActive('tab-quad')));
  }
}
export async function loadQuad() { await fetchCams(); renderQuad(true); }

// --- Single full-screen view ----------------------------------------------
let singleSource = null, singleGen = 0;
function startSinglePump() {
  if (singleSource == null) return;
  singleGen++;
  const g = singleGen;
  pumpFrame($('single-feed'), singleSource, () => g === singleGen && isActive('tab-single'));
}
function renderSingle(force) {
  const sel = $('single-select');
  if (!cams.length) {
    sel.innerHTML = '';
    $('single-empty').hidden = false;
    $('single-feed').style.display = 'none';
    return;
  }
  $('single-empty').hidden = true;
  $('single-feed').style.display = '';
  const prev = singleSource != null ? String(singleSource) : sel.value;
  sel.innerHTML = cams.map(c =>
    `<option value="${c.source}">${camLabel(c)}</option>`).join('');
  singleSource = cams.some(c => String(c.source) === prev) ? prev : cams[0].source;
  sel.value = singleSource;
  if (force) startSinglePump();
}
export async function loadSingle() { await fetchCams(); renderSingle(true); }
export function setSingleSource(source) { singleSource = String(source); }

$('single-select').addEventListener('change', () => {
  singleSource = $('single-select').value;
  startSinglePump();
});
// CSS-based fullscreen — works everywhere incl. iOS Safari (the native Fullscreen
// API doesn't work on an <img> there).
function setFullscreen(on) {
  $('single-stage').classList.toggle('fs', on);
  $('single-fullscreen').textContent = on ? 'Exit' : 'Fullscreen';
}
$('single-fullscreen').addEventListener('click', () =>
  setFullscreen(!$('single-stage').classList.contains('fs')));
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && $('single-stage').classList.contains('fs')) setFullscreen(false);
});

// pick up camera add/remove while a live view is open (no churn when unchanged)
setInterval(async () => {
  if (!isActive('tab-quad') && !isActive('tab-single')) return;
  await fetchCams();
  if (isActive('tab-quad')) renderQuad(false);
  if (isActive('tab-single')) renderSingle(false);
}, 3000);
