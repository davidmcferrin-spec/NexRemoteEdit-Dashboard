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
function fmtBytes(n) {
  n = Number(n);
  if (!n) return '—';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return n.toFixed(i > 1 ? 1 : 0) + ' ' + u[i];
}
function holdPoints(rows, key) {
  let last = null;
  const out = [];
  (rows || []).forEach(r => {
    if (r[key] != null && r[key] !== '') last = Number(r[key]);
    if (last == null || Number.isNaN(last)) return;
    out.push({ t: r.ts, y: last });
  });
  return out;
}
function zeroPoints(rows, key) {
  return (rows || []).map(r => ({
    t: r.ts,
    y: r[key] == null || r[key] === '' ? 0 : Number(r[key]),
  }));
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
  const work = await (await fetch('api/history.php?kind=work&' + qs)).json();
  const wb = document.querySelector('#workTable tbody');
  const kindLabel = { local: 'Console', rdp: 'RDP', jump: 'Jump' };
  wb.innerHTML = (work.intervals || []).map(w => `<tr>
    <td>${esc(fmtWhen(w.start_time))}</td>
    <td>${w.end_time ? esc(fmtWhen(w.end_time)) : 'Open'}</td>
    <td>${esc(w.display_name || w.hostname)}</td>
    <td>${esc(w.username || '—')}</td>
    <td>${esc(kindLabel[w.session_kind] || w.session_kind || 'Console')}</td>
    <td>${esc(fmtDur(w.active_sec))}</td>
    <td>${esc(w.foreground_app || '—')}</td>
    <td>${esc(w.jump_user_email || '—')}</td>
  </tr>`).join('') || '<tr><td colspan="8">No workstation time in range</td></tr>';

  const sess = await (await fetch('api/history.php?kind=sessions&' + qs)).json();
  const tb = document.querySelector('#sessTable tbody');
  tb.innerHTML = (sess.sessions || []).map(s => `<tr>
    <td>${esc(fmtWhen(s.start_time))}</td>
    <td>${esc(s.telegraf_hostname || s.hostname || s.jump_display_name)}</td>
    <td>${esc(s.user_email)}</td>
    <td>${esc(fmtDur(s.duration_sec))}</td>
    <td>${esc(s.transport)}</td>
    <td>${esc(s.client_ip)}</td>
  </tr>`).join('') || '<tr><td colspan="6">No Jump sessions in range</td></tr>';

  const dwellBody = document.querySelector('#dwellTable tbody');
  const appBody = document.querySelector('#appTable tbody');
  if (host) {
    const tel = await (await fetch('api/history.php?kind=telemetry&' + qs)).json();
    const pts = tel.points || [];
    nreChart(document.getElementById('chartCpu'), [{ points: holdPoints(pts, 'cpu_pct') }], { max: 100 });
    nreChart(document.getElementById('chartMem'), [{ points: holdPoints(pts, 'mem_pct') }], { max: 100 });
    nreChart(document.getElementById('chartDisk'), [{ points: holdPoints(pts, 'disk_free_pct') }], { max: 100 });
    nreChart(document.getElementById('chartGpu'), [{ points: holdPoints(pts, 'gpu_pct') }], { max: 100 });
    nreChart(document.getElementById('chartIdle'), [{ points: holdPoints(pts, 'idle_sec') }]);
    nreChart(document.getElementById('chartActive'), [{ points: holdPoints(pts, 'active_pct') }], { max: 100 });
    nreChart(document.getElementById('chartInput'), [{ points: zeroPoints(pts, 'input_n') }]);
    dwellBody.innerHTML = (tel.dwell || []).map(d => `<tr>
      <td>${esc(d.foreground_app)}</td>
      <td>${esc(d.minutes)}</td>
    </tr>`).join('') || '<tr><td colspan="2">No focused-app samples in range</td></tr>';
    appBody.innerHTML = (tel.apps || []).map(a => `<tr>
      <td>${esc(a.name)}</td>
      <td>${a.cpu == null ? '—' : Number(a.cpu).toFixed(1) + '%'}</td>
      <td>${esc(fmtBytes(a.rss))}</td>
    </tr>`).join('') || '<tr><td colspan="3">No pinned apps in range</td></tr>';
  } else {
    dwellBody.innerHTML = '<tr><td colspan="2">Pick a host</td></tr>';
    appBody.innerHTML = '<tr><td colspan="3">Pick a host</td></tr>';
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
