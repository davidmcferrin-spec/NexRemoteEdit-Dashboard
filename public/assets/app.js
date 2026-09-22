'use strict';

const state = { hosts: new Map(), unmatched: {}, filter: '', watchlist: [], expanded: new Set(), procHost: null };
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
  const pct = n == null || Number.isNaN(n) ? 0 : Math.max(0, Math.min(100, n));
  const cls = pct >= 90 ? 'crit' : pct >= 75 ? 'warn' : '';
  const shown = n == null || Number.isNaN(n) ? '—' : String(Math.round(pct));
  return `<div class="meter" title="${esc(label)} ${shown === '—' ? 'unknown' : shown + '%'}">
    <span class="meter-label">${esc(label)}</span>
    <div class="meter-bar"><div class="meter-fill ${cls}" style="width:${pct}%"></div></div>
    <span class="meter-val">${shown}</span>
  </div>`;
}

function userLine(users, fg) {
  const focus = fg ? `<span class="card-focus" title="${esc(fg)}">${esc(fg)}</span>` : '';
  if (!users.length) {
    return `<div class="card-user"><span class="card-user-empty">No one logged on</span>${focus}</div>`;
  }
  const more = users.slice(1);
  return `<div class="card-user">
    <span class="card-user-name">${esc(users[0])}</span>
    ${more.length ? `<span class="card-user-more">${esc(more.join(', '))}</span>` : ''}
    ${focus}
  </div>`;
}

function statusBadge(stale, cls, active) {
  if (stale) return '<span class="badge badge-idle">stale</span>';
  if (cls === 'degraded') return '<span class="badge badge-degraded">unhealthy</span>';
  if (active === true) return '<span class="badge badge-connected">active</span>';
  if (active === false) return '<span class="badge badge-idle">idle</span>';
  return '<span class="badge badge-connected">up</span>';
}

function hostKey(h) {
  return String(h || '').toLowerCase().split('.')[0];
}

function historyLink(hostname) {
  if (!window.NRE_CAN_HISTORY || !hostname) return '';
  const q = new URLSearchParams({ host: hostname, range: '8h' });
  return `<a class="btn btn-sm btn-secondary card-history" href="history.php?${q.toString()}" title="CPU, memory, sessions, and events">History</a>`;
}

const STALE_SEC = 180;

function sampleAge(h) {
  const ts = h.ts || (h.telemetry && h.telemetry.ts);
  if (!ts) return null;
  const ms = Date.now() - new Date(ts).getTime();
  if (!Number.isFinite(ms)) return null;
  return Math.max(0, Math.round(ms / 1000));
}

function fmtAge(sec) {
  if (sec == null) return '';
  if (sec < 90) return 'updated just now';
  return 'updated ' + fmtDur(sec) + ' ago';
}

function inputSummary(tel) {
  const mouse = Number(tel.input_mouse || 0);
  const clicks = Number(tel.input_clicks || 0);
  const keys = Number(tel.input_keys || 0);
  const pulses = Number(tel.input_pulses || 0);
  const parts = [];
  if (mouse) parts.push(mouse + ' moves');
  if (clicks) parts.push(clicks + ' clicks');
  if (keys) parts.push(keys + ' keys');
  if (!parts.length && pulses) parts.push(pulses + ' input changes');
  return parts.join(' · ');
}

function winUsers(h) {
  const sess = h.windows_sessions || (h.telemetry && h.telemetry.windows_sessions) || [];
  return sess.map(s => s.username).filter(Boolean);
}

function machineHot(tel) {
  const mem = tel.mem_pct == null ? null : Number(tel.mem_pct);
  const diskHot = (tel.disk || []).some(d => d.used_pct != null && Number(d.used_pct) >= 90);
  return diskHot || (mem != null && !Number.isNaN(mem) && mem >= 95);
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
  const age = sampleAge(h);
  const stale = age != null && age > STALE_SEC;
  const fg = tel.foreground_app || h.foreground_app || '';
  const inputs = inputSummary(tel);
  const uptimeSec = h.uptime_sec ?? tel.uptime_sec;
  const uptimeLabel = uptimeSec != null && uptimeSec !== '' ? fmtUptime(uptimeSec) : '';
  let cls = 'offline';
  if (stale) cls = 'stale';
  else if (h.online || age != null) cls = machineHot(tel) ? 'degraded' : 'connected';
  const key = hostKey(h.hostname);
  const expanded = state.expanded.has(key);
  const disks = (tel.disk || []).map(d => {
    const freePct = d.used_pct != null ? 100 - Number(d.used_pct) : null;
    return `<div class="disk-row">
      <span class="disk-label">${esc(d.volume || '?')}</span>
      <div class="disk-bar-wrap"><div class="disk-bar ${Number(d.used_pct) >= 90 ? 'crit' : ''}" style="width:${Math.min(100, Number(d.used_pct) || 0)}%"></div></div>
      <span class="disk-free">${freePct != null ? freePct.toFixed(0) + '% free' : fmtBytes(d.free)}</span>
    </div>`;
  }).join('');
  const hotDisks = (tel.disk || []).filter(d => d.used_pct != null && Number(d.used_pct) >= 90).map(d => {
    const free = Math.max(0, 100 - Number(d.used_pct));
    return `${d.volume || '?'} ${free.toFixed(0)}% free`;
  });
  const chips = watched.map(p =>
    `<span class="watch-chip" title="${esc(p.name)}">${esc((p.watch || []).join(', ') || p.name)}</span>`
  ).join('');
  const jumpBlock = jump ? `<div class="canvas-section">
      <div class="canvas-section-title">Jump remote</div>
      <div class="card-meta">${esc(jump.user_email || '—')} · ${esc(jump.transport || '')} · ${fmtDur(jump.duration_sec)} · ${esc(jump.client_ip || '')}</div>
    </div>` : '';
  const detailBits = [];
  if (uptimeLabel) detailBits.push('up ' + uptimeLabel);
  if (idleSec != null && active === false) detailBits.push('idle ' + fmtDur(idleSec));
  if (age != null) detailBits.push(fmtAge(age));
  if (inputs) detailBits.push(inputs);
  return `<article class="host-card ${cls}${expanded ? ' expanded' : ''}" data-host="${esc(key)}">
    <div class="card-header">
      <div class="card-header-top">
        <span class="status-dot"></span>
        <div class="card-name">${esc(h.display_name || h.hostname)}</div>
        <div class="card-badges">
          ${historyLink(h.hostname)}
          ${statusBadge(stale, cls, active)}
          ${jump ? `<span class="badge badge-connected">${esc(jump.transport || 'jump')}</span>` : '<span class="badge badge-checking">local</span>'}
        </div>
        <span class="card-expand-icon" aria-hidden="true">▾</span>
      </div>
      ${userLine(users, fg)}
    </div>
    <div class="card-meters">
      <div class="meter-row">
        ${metricBar('CPU', tel.cpu_pct)}
        ${metricBar('MEM', tel.mem_pct)}
        ${metricBar('GPU', gpu.util_pct)}
      </div>
      ${hotDisks.length ? `<div class="disk-hot-line">${esc(hotDisks.join(' · '))}</div>` : ''}
    </div>
    <div class="card-body">
      ${detailBits.length ? `<div class="card-details">${esc(detailBits.join(' · '))}</div>` : ''}
      ${disks ? `<div class="disk-section">${disks}</div>` : ''}
      ${chips ? `<div class="watch-chips">${chips}</div>` : ''}
      ${jumpBlock}
      <div class="app-section">
        <button type="button" class="app-section-toggle" data-open-procs="${esc(key)}">
          Processes (${procs.length})${watched.length ? ' · ' + watched.length + ' pinned' : ''}
        </button>
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
    const actn = list.filter(h => !((sampleAge(h) || 0) > STALE_SEC) && (h.active ?? (h.telemetry || {}).active) === true).length;
    const staleN = list.filter(h => (sampleAge(h) || 0) > STALE_SEC).length;
    banner.textContent = `${n} bay${n === 1 ? '' : 's'} · ${actn} active · ${rem} remoted via Jump${staleN ? ' · ' + staleN + ' stale' : ''}`;
  }
  renderProcModal();
}

function closeProcModal() {
  state.procHost = null;
  const overlay = document.getElementById('procModal');
  if (overlay) overlay.hidden = true;
}

function renderProcModal() {
  const overlay = document.getElementById('procModal');
  if (!overlay) return;
  if (!state.procHost) {
    overlay.hidden = true;
    return;
  }
  const h = state.hosts.get(state.procHost);
  if (!h) {
    closeProcModal();
    return;
  }
  overlay.hidden = false;
  const title = document.getElementById('procTitle');
  const sub = document.getElementById('procSub');
  if (title) title.textContent = h.display_name || h.hostname || 'Processes';
  const users = winUsers(h);
  const age = sampleAge(h);
  if (sub) {
    sub.innerHTML = `<span class="proc-user${users.length ? '' : ' proc-user-empty'}">${esc(users.length ? users.join(', ') : 'No one logged on')}</span>`
      + (age != null ? `<span class="proc-age">${esc(fmtAge(age))}</span>` : '');
  }
  const tel = h.telemetry || {};
  const procs = tel.processes || [];
  const scroll = document.getElementById('procScroll');
  const top = scroll ? scroll.scrollTop : 0;
  const tb = document.querySelector('#procTable tbody');
  if (!tb) return;
  tb.innerHTML = procs.map(p => {
    const pin = p.watch && p.watch.length;
    const cpu = p.cpu != null && p.cpu !== '' ? Number(p.cpu).toFixed(1) + '%' : '—';
    return `<tr class="${pin ? 'proc-pinned' : ''}">
      <td>${esc(p.name || '—')}${pin ? ' <span class="badge badge-update">' + esc(p.watch.join(', ')) + '</span>' : ''}</td>
      <td>${esc(p.user || '—')}</td>
      <td class="num">${esc(cpu)}</td>
      <td class="num">${esc(p.rss ? fmtBytes(p.rss) : '—')}</td>
    </tr>`;
  }).join('') || '<tr><td colspan="4">No process list in the last sample</td></tr>';
  if (scroll) scroll.scrollTop = top;
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
    const tel = msg.telemetry || {};
    cur.telemetry = tel;
    cur.windows_sessions = tel.windows_sessions || [];
    if (tel.uptime_sec != null) cur.uptime_sec = tel.uptime_sec;
    if (tel.idle_sec != null) cur.idle_sec = tel.idle_sec;
    if (tel.active != null) cur.active = tel.active;
    if (tel.ts) cur.ts = tel.ts;
    cur.foreground_app = tel.foreground_app || '';
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
  const procBtn = e.target.closest('[data-open-procs]');
  if (procBtn) {
    state.procHost = procBtn.dataset.openProcs || null;
    renderProcModal();
    return;
  }
  if (e.target.closest('a')) return;
  const header = e.target.closest('.card-header');
  if (!header) return;
  const card = header.closest('.host-card');
  const key = card?.dataset.host;
  if (!card || !key) return;
  card.classList.toggle('expanded');
  if (card.classList.contains('expanded')) state.expanded.add(key);
  else state.expanded.delete(key);
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

document.getElementById('procClose')?.addEventListener('click', closeProcModal);
document.getElementById('procModal')?.addEventListener('click', (e) => {
  if (e.target.id === 'procModal') closeProcModal();
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && state.procHost) closeProcModal();
});

connect();
setInterval(queueRender, 15000);
