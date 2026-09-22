'use strict';

const state = { hosts: new Map(), unmatched: {}, filter: '', watchlist: [] };
let ws = null;
let rafQueued = false;

function esc(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');
}

function fmtDur(sec) {
  sec = Math.max(0, parseInt(sec || 0, 10));
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${s}s`;
  return `${s}s`;
}

function fmtUptime(sec) {
  if (sec == null || sec === '') return '—';
  sec = parseInt(sec, 10);
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  if (d) return `${d}d ${h}h`;
  return fmtDur(sec);
}

function fmtBytes(n) {
  n = Number(n);
  if (!n) return '—';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return n.toFixed(i > 1 ? 1 : 0) + ' ' + u[i];
}

function metricBar(label, val) {
  const n = val == null ? null : Number(val);
  const pct = n == null ? 0 : Math.max(0, Math.min(100, n));
  const cls = pct >= 90 ? 'crit' : pct >= 75 ? 'warn' : '';
  const shown = n == null ? '—' : pct.toFixed(0) + '%';
  return `<div class="disk-row">
    <span class="disk-label">${esc(label)}</span>
    <div class="disk-bar-wrap"><div class="disk-bar ${cls}" style="width:${pct}%"></div></div>
    <span class="disk-free">${shown}</span>
  </div>`;
}

function hostKey(h) {
  return String(h || '').toLowerCase().split('.')[0];
}

function winUsers(h) {
  const sess = h.windows_sessions || (h.telemetry && h.telemetry.windows_sessions) || [];
  return sess.map(s => s.username).filter(Boolean);
}

function cardHtml(h) {
  const tel = h.telemetry || {};
  const gpu = (tel.gpu && tel.gpu[0]) || {};
  const jump = h.jump;
  const users = winUsers(h);
  const procs = tel.processes || [];
  const watched = procs.filter(p => p.watch && p.watch.length);
  const idleSec = h.idle_sec ?? tel.idle_sec;
  const active = h.active ?? tel.active;
  let cls = jump ? 'connected' : (h.online ? 'checking' : 'offline');
  if (!jump && h.online && active === false) cls = 'idle';
  const disks = (tel.disk || []).map(d => {
    const freePct = d.used_pct != null ? 100 - Number(d.used_pct) : null;
    return `<div class="disk-row">
      <span class="disk-label">${esc(d.volume || '?')}</span>
      <div class="disk-bar-wrap"><div class="disk-bar ${Number(d.used_pct) >= 90 ? 'crit' : ''}" style="width:${Math.min(100, Number(d.used_pct) || 0)}%"></div></div>
      <span class="disk-free">${freePct != null ? freePct.toFixed(0) + '% free' : fmtBytes(d.free)}</span>
    </div>`;
  }).join('');
  const chips = watched.map(p =>
    `<span class="watch-chip" title="${esc(p.name)}">${esc((p.watch || []).join(', ') || p.name)}</span>`
  ).join('');
  const procRows = procs.map(p => {
    const pin = p.watch && p.watch.length;
    return `<li class="app-row ${pin ? 'app-watched' : ''}">
      <span class="app-status-dot"></span>
      <span class="app-name">${esc(p.name)}${pin ? ' <span class="badge badge-update">' + esc(p.watch.join(', ')) + '</span>' : ''}</span>
      <span class="app-version">${esc(p.user || '')}</span>
      <span class="app-state-label">${p.cpu != null ? Number(p.cpu).toFixed(1) + '%' : '—'}</span>
    </li>`;
  }).join('');
  const jumpBlock = jump ? `<div class="canvas-section">
      <div class="canvas-section-title">Jump remote</div>
      <div class="card-meta">${esc(jump.user_email || '—')} · ${esc(jump.transport || '')} · ${fmtDur(jump.duration_sec)} · ${esc(jump.client_ip || '')}</div>
    </div>` : '';
  return `<article class="host-card ${cls}" data-host="${esc(hostKey(h.hostname))}">
    <div class="card-header">
      <span class="status-dot"></span>
      <div class="card-title">
        <div class="card-name">${esc(h.display_name || h.hostname)}</div>
        <div class="card-meta">${esc(users.join(', ') || 'Windows user unknown')} · up ${esc(fmtUptime(h.uptime_sec ?? tel.uptime_sec))}${idleSec != null && active === false ? ' · idle ' + esc(fmtDur(idleSec)) : ''}</div>
      </div>
      <div class="card-badges">
        ${active === true ? '<span class="badge badge-connected">active</span>' : (active === false ? '<span class="badge badge-idle">idle</span>' : '')}
        ${jump ? `<span class="badge badge-connected">${esc(jump.transport || 'jump')}</span>` : '<span class="badge badge-checking">local</span>'}
      </div>
    </div>
    <div class="card-body" style="display:block">
      <div class="disk-section">
        ${metricBar('CPU', tel.cpu_pct)}
        ${metricBar('MEM', tel.mem_pct)}
        ${metricBar('GPU', gpu.util_pct)}
        ${disks}
      </div>
      ${chips ? `<div class="watch-chips">${chips}</div>` : ''}
      ${jumpBlock}
      <div class="app-section">
        <button type="button" class="app-section-toggle" data-toggle-procs="${esc(hostKey(h.hostname))}">
          Processes (${procs.length})${watched.length ? ' · ' + watched.length + ' pinned' : ''}
        </button>
        <ul class="app-list" hidden>${procRows || '<li class="hint">No process list in last sample</li>'}</ul>
      </div>
    </div>
  </article>`;
}

function render() {
  rafQueued = false;
  const grid = document.getElementById('sessionGrid');
  const splash = document.getElementById('loadingSplash');
  if (splash) splash.remove();
  const q = state.filter.toLowerCase();
  const list = [...state.hosts.values()].filter(h => {
    if (!q) return true;
    const tel = h.telemetry || {};
    const procs = (tel.processes || []).map(p => `${p.name} ${(p.watch || []).join(' ')}`).join(' ');
    const act = (h.active ?? (h.telemetry || {}).active);
    const idleLabel = act === true ? 'active' : act === false ? 'idle' : '';
    const blob = `${h.display_name} ${h.hostname} ${winUsers(h).join(' ')} ${h.jump ? h.jump.user_email : ''} ${procs} ${idleLabel}`.toLowerCase();
    return blob.includes(q);
  });
  list.sort((a, b) => {
    const aj = a.jump ? 0 : 1;
    const bj = b.jump ? 0 : 1;
    if (aj !== bj) return aj - bj;
    const aa = (a.active ?? (a.telemetry || {}).active) === true ? 0 : 1;
    const ba = (b.active ?? (b.telemetry || {}).active) === true ? 0 : 1;
    if (aa !== ba) return aa - ba;
    const aw = ((a.telemetry || {}).processes || []).some(p => p.watch && p.watch.length) ? 0 : 1;
    const bw = ((b.telemetry || {}).processes || []).some(p => p.watch && p.watch.length) ? 0 : 1;
    if (aw !== bw) return aw - bw;
    return String(a.display_name || a.hostname).localeCompare(String(b.display_name || b.hostname));
  });
  if (!list.length) {
    grid.innerHTML = '<div class="empty-state"><h3>No bay telemetry yet</h3><p>Install Telegraf on each edit workstation — see <a href="telegraf.php">Telegraf</a>.</p></div>';
  } else {
    grid.innerHTML = list.map(cardHtml).join('');
  }
  const banner = document.getElementById('unmatchedBanner');
  if (banner) {
    const n = list.length;
    const rem = list.filter(h => h.jump).length;
    const actn = list.filter(h => (h.active ?? (h.telemetry || {}).active) === true).length;
    banner.textContent = `${n} bay${n === 1 ? '' : 's'} · ${actn} active · ${rem} remoted via Jump`;
  }
}

function queueRender() {
  if (rafQueued) return;
  rafQueued = true;
  requestAnimationFrame(render);
}

function upsertHost(h) {
  if (!h || !h.hostname) return;
  state.hosts.set(hostKey(h.hostname), h);
}

function applySnapshot(msg) {
  state.hosts.clear();
  state.watchlist = msg.watchlist || [];
  (msg.hosts || []).forEach(upsertHost);
  if (!(msg.hosts || []).length && (msg.sessions || []).length) {
    (msg.sessions || []).forEach(s => {
      upsertHost({
        hostname: s.hostname,
        display_name: s.display_name,
        telemetry: s.telemetry,
        jump: s,
        windows_sessions: [],
        online: true,
      });
    });
  }
  state.unmatched = msg.unmatched || {};
  queueRender();
}

function handleMsg(msg) {
  if (msg.type === 'snapshot') applySnapshot(msg);
  else if (msg.type === 'host_update' && msg.host) {
    upsertHost(msg.host);
    queueRender();
  } else if (msg.type === 'telemetry_update') {
    const hk = hostKey(msg.hostname);
    const cur = state.hosts.get(hk) || { hostname: msg.hostname, display_name: msg.hostname, online: true };
    cur.telemetry = msg.telemetry || cur.telemetry;
    cur.windows_sessions = (msg.telemetry && msg.telemetry.windows_sessions) || cur.windows_sessions;
    cur.uptime_sec = (msg.telemetry && msg.telemetry.uptime_sec) != null ? msg.telemetry.uptime_sec : cur.uptime_sec;
    cur.idle_sec = (msg.telemetry && msg.telemetry.idle_sec) != null ? msg.telemetry.idle_sec : cur.idle_sec;
    if (msg.telemetry && msg.telemetry.active != null) cur.active = msg.telemetry.active;
    cur.online = true;
    state.hosts.set(hk, cur);
    queueRender();
  } else if (msg.type === 'session_started' && msg.session) {
    const hk = hostKey(msg.session.hostname);
    const cur = state.hosts.get(hk) || { hostname: msg.session.hostname, display_name: msg.session.display_name, online: true };
    cur.jump = msg.session;
    state.hosts.set(hk, cur);
    queueRender();
  } else if (msg.type === 'session_closed' && msg.session) {
    const hk = hostKey(msg.session.hostname);
    const cur = state.hosts.get(hk);
    if (cur) { cur.jump = null; state.hosts.set(hk, cur); }
    queueRender();
  }
}

function setWs(cls, label) {
  const el = document.getElementById('wsStatus');
  if (!el) return;
  el.className = 'ws-status ' + cls;
  const lab = el.querySelector('.ws-label');
  if (lab) lab.textContent = label;
}

function connect() {
  const url = window.NRE_WS_URL;
  if (!url) return;
  ws = new WebSocket(url);
  setWs('', 'Connecting…');
  ws.onopen = () => setWs('connected', 'Live');
  ws.onclose = () => {
    setWs('disconnected', 'Disconnected');
    setTimeout(connect, 3000);
  };
  ws.onerror = () => setWs('disconnected', 'Error');
  ws.onmessage = (ev) => {
    try { handleMsg(JSON.parse(ev.data)); } catch (e) { /* ignore */ }
  };
}

document.getElementById('searchInput')?.addEventListener('input', (e) => {
  state.filter = e.target.value;
  queueRender();
});

document.getElementById('sessionGrid')?.addEventListener('click', (e) => {
  const btn = e.target.closest('[data-toggle-procs]');
  if (!btn) return;
  const list = btn.parentElement?.querySelector('.app-list');
  if (!list) return;
  list.hidden = !list.hidden;
  btn.classList.toggle('open', !list.hidden);
});

const btnPw = document.getElementById('btnChangePw');
if (btnPw) {
  btnPw.addEventListener('click', async () => {
    const r = await fetch('api/profile.php', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        action: 'change_password',
        current_password: document.getElementById('pwCurrent').value,
        new_password: document.getElementById('pwNew').value,
      }),
    });
    const d = await r.json();
    if (d.ok) {
      document.getElementById('modalPw').hidden = true;
      toast('success', 'Password changed');
    } else toast('error', d.error || 'Failed');
  });
}

connect();
setInterval(queueRender, 15000);
