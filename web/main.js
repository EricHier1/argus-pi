// Entry point: wires tabs + keyboard shortcuts and boots the polling loops.
// Importing the feature modules runs their event-listener setup as a side effect.
import { $ } from './api.js';
import { pollStatus, resumeDashFeeds } from './feeds.js';
import { loadLabels, pollAlerts, loadRules, loadActivity, loadAlertsTab } from './dashboard.js';
import { loadGallery } from './gallery.js';
import { loadAnalytics } from './analytics.js';
import { loadQuad, loadSingle, setSingleSource } from './views.js';
import './menu.js';

// clicking a dashboard camera opens it in the View tab
document.addEventListener('argus:viewcam', (e) => {
  setSingleSource(e.detail.source);
  activateTab('single');
});

// --- tabs ------------------------------------------------------------------
function activateTab(name) {
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
  document.querySelectorAll('.tab-view').forEach(v => v.classList.remove('active'));
  $('tab-' + name).classList.add('active');
  if (name === 'dashboard') resumeDashFeeds();   // restart feed (it pauses when hidden)
  else if (name === 'gallery') loadGallery();
  else if (name === 'alerts') loadAlertsTab();
  else if (name === 'analytics') loadAnalytics();
  else if (name === 'quad') loadQuad();
  else if (name === 'single') loadSingle();
}
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => activateTab(btn.dataset.tab));
});
// keyboard shortcuts: 1 Dashboard · 2 Gallery · 3 Alerts · 4 Analytics · 5 Quad · 6 View
document.addEventListener('keydown', (e) => {
  if (e.target.matches('input, select, textarea')) return;  // don't hijack typing
  if (!$('lightbox').hidden) return;                        // lightbox owns arrows/Esc
  const map = { '1': 'dashboard', '2': 'gallery', '3': 'alerts', '4': 'analytics', '5': 'quad', '6': 'single' };
  if (map[e.key]) activateTab(map[e.key]);
});

// --- boot ------------------------------------------------------------------
pollStatus(); loadLabels(); pollAlerts(); loadRules(); loadActivity();
setInterval(pollStatus, 2000);
setInterval(pollAlerts, 3000);
setInterval(loadLabels, 10000);
setInterval(loadActivity, 30000);
