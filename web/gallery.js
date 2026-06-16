// Gallery (grouped by object) and the lightbox (overlay boxes, toggle, nav, swipe).
import { $, fmt, api, postJSON, armConfirm } from './api.js';

// Notify other views (e.g. the Alerts tab) that captures changed (deleted/pinned).
const emitChanged = () => document.dispatchEvent(new CustomEvent('argus:changed'));

const PAGE = 30;
let galleryGroups = [];    // accumulated across pages
let galleryView = 'all';   // 'all' | 'pinned'
let gBefore = null, gLoading = false, gDone = false;

// is a sentinel near/in the viewport (and its tab visible)?
function sentinelNear(id) {
  const el = $(id);
  if (!el || el.offsetParent === null) return false;   // hidden tab
  return el.getBoundingClientRect().top < window.innerHeight + 300;
}

function galleryTile(g, i) {
  return `
    <div class="result ${g.kept ? 'kept' : ''} ${g.n > 1 ? 'stack' : ''}" data-i="${i}"
         tabindex="0" role="button" aria-label="${(g.labels || 'object')}, ${g.n} picture${g.n === 1 ? '' : 's'}">
      ${g.kept ? '<span class="pin-badge">PIN</span>' : ''}
      ${g.n > 1 ? `<span class="count-badge">${g.n}</span>` : ''}
      <button class="del-badge" data-action="delete" title="Delete object" aria-label="Delete">&times;</button>
      <img src="/snapshots/${g.snapshot}" loading="lazy" alt="">
      <div class="label">${(g.labels || '').split(',').join(', ')}</div>
      <div class="muted">${fmt(g.ts)}${g.n > 1 ? ' · ' + g.n + ' pics' : ''}</div>
    </div>`;
}

// `reset` starts a fresh load; otherwise append the next page (infinite scroll).
async function loadGalleryPage(reset) {
  if (gLoading || (gDone && !reset)) return;
  if (reset) { galleryGroups = []; gBefore = null; gDone = false; $('gallery-grid').innerHTML = ''; }
  gLoading = true;
  const params = new URLSearchParams();
  const label = $('gallery-label').value;
  if (label) params.set('label', label);
  if (galleryView === 'pinned') params.set('pinned', 'true');
  params.set('limit', String(PAGE));
  if (gBefore != null) params.set('before', gBefore);

  let page;
  try { page = await api('/api/gallery?' + params.toString()); }
  catch (e) {
    gLoading = false;
    if (reset) $('gallery-grid').innerHTML = '<span class="muted">Could not load gallery.</span>';
    return;
  }
  const start = galleryGroups.length;
  galleryGroups.push(...page);
  if (page.length) {
    $('gallery-grid').insertAdjacentHTML('beforeend', page.map((g, k) => galleryTile(g, start + k)).join(''));
    gBefore = page[page.length - 1].ts;
  }
  if (page.length < PAGE) gDone = true;
  if (!galleryGroups.length) {
    $('gallery-grid').innerHTML = galleryView === 'pinned'
      ? '<span class="muted">No pinned captures yet. Open an object and tap Pin.</span>'
      : '<span class="muted">No captures yet. Objects you detect will appear here.</span>';
  }
  gLoading = false;
  // keep filling until the viewport is covered (sentinel scrolled out of view)
  if (!gDone && sentinelNear('gallery-sentinel')) setTimeout(() => loadGalleryPage(false), 60);
}
export function loadGallery() { loadGalleryPage(true); }

new IntersectionObserver((e) => { if (e[0].isIntersecting) loadGalleryPage(false); },
  { rootMargin: '300px' }).observe($('gallery-sentinel'));

document.querySelectorAll('.gtab').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.gtab').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    galleryView = btn.dataset.view;
    loadGallery();
  });
});

// event delegation — no inline onclick / window globals
$('gallery-grid').addEventListener('click', (e) => {
  const card = e.target.closest('.result');
  if (!card) return;
  const i = +card.dataset.i;
  if (e.target.closest('[data-action="delete"]')) deleteGroup(i);
  else openGroup(i);
});
$('gallery-grid').addEventListener('keydown', (e) => {
  const card = e.target.closest('.result');
  if (card && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); openGroup(+card.dataset.i); }
});

async function openGroup(i) {
  const g = galleryGroups[i];
  if (!g) return;
  if (g.n > 1) {
    lbItems = await api('/api/gallery/group?gkey=' + encodeURIComponent(g.gkey));
    showLightboxAt(lbItems.length - 1);   // newest pic of the stack
  } else {
    lbItems = galleryGroups.map(x => ({ snapshot: x.snapshot, ts: x.ts, labels: x.labels, kept: x.kept }));
    showLightboxAt(i);
  }
}

async function deleteGroup(i) {
  const g = galleryGroups[i];
  if (!g) return;
  const msg = g.n > 1 ? `Delete all ${g.n} pictures of this object?` : 'Delete this capture?';
  if (!confirm(msg)) return;
  await api('/api/gallery/group?gkey=' + encodeURIComponent(g.gkey), { method: 'DELETE' });
  loadGallery();
  emitChanged();
}

export { sentinelNear };

export function syncGalleryFilter() {
  const src = $('search-label'), dst = $('gallery-label'), cur = dst.value;
  dst.innerHTML = src.innerHTML.replace('>any<', '>all<');
  dst.value = cur;
}

// --- lightbox (overlay boxes, toggle, prev/next, swipe) ---------------------
let lbItems = [], lbIndex = -1;
let boxesOn = localStorage.getItem('argusBoxes') !== '0';   // default ON, remembered
const lbBoxCache = {};   // snapshot -> boxes array

// Open the lightbox on an arbitrary list of pictures (used by the Alerts tab).
export function openLightboxItems(items, index = 0) {
  lbItems = items;
  showLightboxAt(index);
}

function showLightboxAt(j) {
  if (j < 0 || j >= lbItems.length) return;
  lbIndex = j;
  const it = lbItems[j];
  const img = $('lightbox-img');
  img.onload = () => drawOverlay(it.snapshot);
  img.src = '/snapshots/' + it.snapshot;
  img.classList.toggle('kept-img', !!it.kept);
  $('lightbox-caption').innerHTML =
    `${it.kept ? '<b style="color:var(--green)">PINNED</b> · ' : ''}` +
    `${(it.labels || '').split(',').join(', ')} · ${fmt(it.ts)} · ${j + 1}/${lbItems.length}`;
  $('lb-download').href = '/snapshots/' + it.snapshot + '?download=1';
  updatePinButton(it.kept);
  updateBoxesToggle();
  const multi = lbItems.length > 1;
  $('lb-prev').hidden = !multi;
  $('lb-next').hidden = !multi;
  $('lightbox').hidden = false;
}

async function drawOverlay(snapshot) {
  const img = $('lightbox-img'), canvas = $('lightbox-canvas');
  const ctx = canvas.getContext('2d');
  const w = canvas.width = img.clientWidth, h = canvas.height = img.clientHeight;
  ctx.clearRect(0, 0, w, h);
  if (!boxesOn || !img.naturalWidth) return;
  let boxes = lbBoxCache[snapshot];
  if (boxes === undefined) {
    try { boxes = await api('/api/snapshot-boxes?name=' + encodeURIComponent(snapshot)); }
    catch (e) { boxes = []; }
    lbBoxCache[snapshot] = boxes;
  }
  if (snapshot !== (lbItems[lbIndex] || {}).snapshot) return;   // navigated away
  const sx = w / img.naturalWidth, sy = h / img.naturalHeight;
  ctx.lineWidth = 2; ctx.font = '600 13px sans-serif'; ctx.textBaseline = 'bottom';
  for (const b of boxes) {
    const col = b.conf >= 0.75 ? '#01BB4E' : b.conf >= 0.5 ? '#F2DF32' : '#F86660';
    const x = b.x1 * sx, y = b.y1 * sy, bw = (b.x2 - b.x1) * sx, bh = (b.y2 - b.y1) * sy;
    ctx.strokeStyle = col; ctx.strokeRect(x, y, bw, bh);
    const tag = `${b.label} ${Math.round(b.conf * 100)}%`;
    const tw = ctx.measureText(tag).width + 8;
    ctx.fillStyle = col; ctx.fillRect(x, Math.max(0, y - 16), tw, 16);
    ctx.fillStyle = '#04200F'; ctx.fillText(tag, x + 4, Math.max(14, y - 2));
  }
}

function updateBoxesToggle() {
  const btn = $('lb-boxes');
  btn.textContent = 'Boxes: ' + (boxesOn ? 'ON' : 'OFF');
  btn.classList.toggle('pinned', boxesOn);   // green when on
}
$('lb-boxes').addEventListener('click', () => {
  boxesOn = !boxesOn;
  localStorage.setItem('argusBoxes', boxesOn ? '1' : '0');
  updateBoxesToggle();
  if (lbItems[lbIndex]) drawOverlay(lbItems[lbIndex].snapshot);
});

function updatePinButton(kept) {
  const btn = $('lb-pin');
  btn.classList.toggle('pinned', !!kept);
  btn.textContent = kept ? 'Pinned' : 'Pin';
}
$('lb-pin').addEventListener('click', async () => {
  const it = lbItems[lbIndex];
  if (!it) return;
  const newKept = !it.kept;
  await postJSON('/api/keep', { snapshot: it.snapshot, kept: newKept });
  it.kept = newKept ? 1 : 0;
  updatePinButton(it.kept);
  loadGallery();
});
$('lb-delete').addEventListener('click', async () => {
  const it = lbItems[lbIndex];
  if (!it || !confirm('Delete this picture?')) return;
  await api('/api/snapshot/' + encodeURIComponent(it.snapshot), { method: 'DELETE' });
  lbItems.splice(lbIndex, 1);
  loadGallery();
  emitChanged();
  if (!lbItems.length) closeLightbox();
  else showLightboxAt(Math.min(lbIndex, lbItems.length - 1));
});
function moveLightbox(delta) {
  if ($('lightbox').hidden) return;
  const next = lbIndex + delta;
  if (next >= 0 && next < lbItems.length) showLightboxAt(next);
}
function closeLightbox() { $('lightbox').hidden = true; lbIndex = -1; lbItems = []; }

$('lb-prev').addEventListener('click', e => { e.stopPropagation(); moveLightbox(-1); });
$('lb-next').addEventListener('click', e => { e.stopPropagation(); moveLightbox(1); });
$('lightbox').addEventListener('click', closeLightbox);            // tap backdrop closes
document.querySelector('.lightbox-stage').addEventListener('click', e => e.stopPropagation());
document.querySelector('.lightbox-controls').addEventListener('click', e => e.stopPropagation());
document.querySelector('.lightbox-close').addEventListener('click', closeLightbox);

// swipe left/right to change pictures (mobile)
let touchX = null, touchY = null;
const stage = document.querySelector('.lightbox-stage');
stage.addEventListener('touchstart', e => { const t = e.changedTouches[0]; touchX = t.clientX; touchY = t.clientY; }, { passive: true });
stage.addEventListener('touchend', e => {
  if (touchX === null) return;
  const t = e.changedTouches[0], dx = t.clientX - touchX, dy = t.clientY - touchY;
  touchX = null;
  if (Math.abs(dx) > 45 && Math.abs(dx) > Math.abs(dy)) moveLightbox(dx < 0 ? 1 : -1);
}, { passive: true });

document.addEventListener('keydown', e => {
  if ($('lightbox').hidden) return;
  if (e.key === 'Escape') closeLightbox();
  else if (e.key === 'ArrowRight') moveLightbox(1);
  else if (e.key === 'ArrowLeft') moveLightbox(-1);
});
window.addEventListener('resize', () => {
  if (!$('lightbox').hidden && lbItems[lbIndex]) drawOverlay(lbItems[lbIndex].snapshot);
});

$('gallery-form').addEventListener('submit', e => { e.preventDefault(); loadGallery(); });
$('gallery-refresh').addEventListener('click', loadGallery);
armConfirm($('gallery-delete-all'), 'Click again to delete ALL', async () => {
  await api('/api/gallery/all', { method: 'DELETE' });
  loadGallery();
  emitChanged();
});
