'use strict';

function pad(n) { return String(n).padStart(2, '0'); }
function toLocalInput(d) {
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
function fmtWhen(s) {
  if (!s) return '—';
  const d = new Date(s);
  return d.toLocaleString();
}
function fmtDur(sec) {
  sec = parseInt(sec || 0, 10);
  if (!sec && sec !== 0) return '—';
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  return h ? `${h}h ${m}m` : `${m}m`;
}

async function loadHosts() {
  const r = await fetch('api/history.php?kind=hosts');
  const d = await r.json();
  const sel = document.getElementById('histHost');
  (d.hosts || []).forEach(h => {
    const o = document.createElement('option');
    o.value = h; o.textContent = h;
    sel.appendChild(o);
  });
}

async function loadHistory() {
  const host = document.getElementById('histHost').value;
  const from = document.getElementById('histFrom').value;
  const to = document.getElementById('histTo').value;
  const qs = new URLSearchParams({ from, to, host });
  const sess = await (await fetch('api/history.php?kind=sessions&' + qs)).json();
  const tb = document.querySelector('#sessTable tbody');
  tb.innerHTML = (sess.sessions || []).map(s => `<tr>
    <td>${esc(fmtWhen(s.start_time))}</td>
    <td>${esc(s.telegraf_hostname || s.hostname || s.jump_display_name)}</td>
    <td>${esc(s.user_email)}</td>
    <td>${esc(fmtDur(s.duration_sec))}</td>
    <td>${esc(s.transport)}</td>
    <td>${esc(s.client_ip)}</td>
  </tr>`).join('') || '<tr><td colspan="6">No sessions in range</td></tr>';

  if (host) {
    const tel = await (await fetch('api/history.php?kind=telemetry&' + qs)).json();
    const pts = tel.points || [];
    nreChart(document.getElementById('chartCpu'), [{ points: nreToPoints(pts, 'cpu_pct') }], { max: 100 });
    nreChart(document.getElementById('chartMem'), [{ points: nreToPoints(pts, 'mem_pct') }], { max: 100 });
    nreChart(document.getElementById('chartGpu'), [{ points: nreToPoints(pts, 'gpu_pct') }], { max: 100 });
    nreChart(document.getElementById('chartIdle'), [{ points: nreToPoints(pts, 'idle_sec') }]);
  }

  const turn = await (await fetch('api/history.php?kind=turn&' + qs)).json();
  const byServer = {};
  (turn.points || []).forEach(p => {
    const id = p.turn_server_id || 'turn';
    if (!byServer[id]) byServer[id] = [];
    byServer[id].push({ y: Number(p.rcvb || 0) / (1024 * 1024) });
  });
  nreChart(document.getElementById('chartTurn'),
    Object.entries(byServer).map(([id, points]) => ({ id, points })));
}

function esc(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');
}

(function init() {
  const to = new Date();
  const from = new Date(to.getTime() - 7 * 86400000);
  document.getElementById('histFrom').value = toLocalInput(from);
  document.getElementById('histTo').value = toLocalInput(to);
  document.getElementById('histFilters').addEventListener('submit', (e) => {
    e.preventDefault();
    loadHistory();
  });
  loadHosts().then(loadHistory);
})();
