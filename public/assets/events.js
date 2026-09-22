'use strict';

function esc(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');
}
function fmtWhen(s) {
  if (!s) return '—';
  return new Date(s).toLocaleString();
}
function detail(ev) {
  const p = ev.payload_json || ev.payload || {};
  try {
    return esc(typeof p === 'string' ? p : JSON.stringify(p));
  } catch (e) {
    return '';
  }
}

function fillTable(id, rows, extra) {
  const tb = document.querySelector('#' + id + ' tbody');
  tb.innerHTML = (rows || []).map(ev => extra
    ? `<tr><td>${esc(fmtWhen(ev.ts))}</td><td>${esc(ev.kind)}</td><td>${esc(ev.hostname)}</td><td>${esc(ev.session_id || '')}</td><td class="mono">${detail(ev)}</td></tr>`
    : `<tr><td>${esc(fmtWhen(ev.ts))}</td><td>${esc(ev.kind)}</td><td>${esc(ev.hostname)}</td><td class="mono">${detail(ev)}</td></tr>`
  ).join('') || '<tr><td colspan="8">None</td></tr>';
}

async function loadEvents() {
  const q = document.getElementById('evQ').value.trim();
  const kind = document.getElementById('evKind').value;
  const qs = new URLSearchParams();
  if (q) qs.set('q', q);
  if (kind) qs.set('kind', kind);
  const all = await (await fetch('api/events.php?' + qs)).json();
  fillTable('evTable', all.events, true);
  const um = await (await fetch('api/events.php?kind=unmatched')).json();
  fillTable('unmatchedTable', um.events, false);
}

document.getElementById('evFilters').addEventListener('submit', (e) => {
  e.preventDefault();
  loadEvents();
});
loadEvents();
