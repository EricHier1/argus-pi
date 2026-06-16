// Shared helpers used across all modules.
export const $ = (id) => document.getElementById(id);
export const fmt = (ts) => new Date(ts * 1000).toLocaleString();
export const toEpoch = (v) => (v ? new Date(v).getTime() / 1000 : null);

export async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

// POST JSON convenience wrapper.
export const postJSON = (path, body, method = 'POST') =>
  api(path, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

// Display label for a camera (custom name or "cam <source>").
export const camLabel = (c) => (c && c.name) ? c.name : ('cam ' + (c ? c.source : '?'));

// Turn a button into a two-click "arm then confirm" control (no browser popup).
export function armConfirm(btn, confirmText, action) {
  let armed = false, timer = null;
  const label = btn.textContent;
  const reset = () => { armed = false; btn.classList.remove('arm'); btn.textContent = label; };
  btn.addEventListener('click', async () => {
    if (!armed) {
      armed = true; btn.classList.add('arm'); btn.textContent = confirmText;
      timer = setTimeout(reset, 2500);
      return;
    }
    clearTimeout(timer); reset();
    await action();
  });
}
