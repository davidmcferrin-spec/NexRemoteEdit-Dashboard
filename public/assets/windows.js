'use strict';

function esc(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');
}
function fmtWhen(s) {
  if (!s) return '—';
  return new Date(s).toLocaleString();
}

const CAT_LABEL = {
  crash: 'Crash',
  unexpected: 'Unexpected',
  reboot: 'Reboot',
  shutdown: 'Shutdown',
  update: 'Update',
  other: 'Other',
};

async function loadHosts() {
  const r = await fetch('api/windows.php?kind=hosts');
  const d = await r.json();
  const sel = document.getElementById('winHost');
  (d.hosts || []).forEach(h => {
    const o = document.createElement('option');
    o.value = h.hostname;
    o.textContent = h.display_name || h.hostname;
    sel.appendChild(o);
  });
}

async function loadEvents() {
  const qs = new URLSearchParams();
  const q = document.getElementById('winQ').value.trim();
  const host = document.getElementById('winHost').value;
  const sev = document.getElementById('winSev').value;
  const cat = document.getElementById('winCat').value;
  if (q) qs.set('q', q);
  if (host) qs.set('host', host);
  if (sev) qs.set('severity', sev);
  if (cat) qs.set('category', cat);
  const d = await (await fetch('api/windows.php?' + qs)).json();
  const tb = document.querySelector('#winTable tbody');
  const rows = d.events || [];
  tb.innerHTML = rows.map(e => `<tr>
    <td>${esc(fmtWhen(e.ts))}</td>
    <td>${esc(e.display_name || e.computer || e.hostname)}</td>
    <td><span class="badge badge-cat-${esc(e.category)}">${esc(CAT_LABEL[e.category] || e.category)}</span></td>
    <td><span class="badge badge-sev-${esc(e.severity)}">${esc(e.severity)}</span></td>
    <td>${esc(e.event_id ?? '')}</td>
    <td>${esc(e.source)}</td>
    <td>${esc(e.username || '—')}</td>
    <td class="event-msg" title="${esc(e.message)}">${esc(e.message)}</td>
  </tr>`).join('') || '<tr><td colspan="8">No Windows events yet. Install <code>win_eventlog</code> from the Telegraf page.</td></tr>';
}

document.getElementById('winFilters').addEventListener('submit', (e) => {
  e.preventDefault();
  loadEvents();
});

loadHosts().then(loadEvents);
setInterval(loadEvents, 30000);
