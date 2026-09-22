'use strict';

function pad(n) { return String(n).padStart(2, '0'); }
function toLocalInput(d) {
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
function fmtWhen(s) {
  if (!s) return '—';
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString();
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
function esc(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');
}
function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

const PRESETS = { '1h': 3600000, '8h': 8 * 3600000, '24h': 86400000, '7d': 7 * 86400000 };
const PROC_COLORS = ['#e879f9', '#2dd4bf', '#fb7185', '#facc15', '#93c5fd'];
const CAT_LABEL = {
  crash: 'Crash', unexpected: 'Unexpected', reboot: 'Reboot', shutdown: 'Shutdown',
  update: 'Update', logon: 'Logon', logoff: 'Logoff', lock: 'Lock', unlock: 'Unlock', other: 'Other',
};
const CAT_COLOR = {
  crash: '#ef4444', unexpected: '#f97316', reboot: '#00b4d8', shutdown: '#94a3b8',
  update: '#22c55e', logon: '#22c55e', logoff: '#94a3b8', lock: '#f59e0b', unlock: '#00b4d8', other: '#64748b',
};
const KIND_LABEL = { local: 'Console', rdp: 'RDP', jump: 'Jump' };
const POLICY_LABEL = {
  coturn_then_p2p: 'On-prem coturn first, then P2P',
  relay_only: 'Jump relay only',
};

const page = {
  range: '',
  machine: null,
  procMode: 'cpu',
  hover: null,
  pinMs: null,
  hits: [],
  layout: null,
  painting: false,
};

function rangeBounds() {
  const from = new Date(document.getElementById('histFrom').value);
  const to = new Date(document.getElementById('histTo').value);
  return { from, to, fromMs: from.getTime(), toMs: to.getTime() };
}

function markPreset(key) {
  page.range = key || '';
  document.querySelectorAll('[data-range]').forEach(btn => {
    const on = btn.dataset.range === key;
    btn.classList.toggle('btn-secondary', !on);
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
}

function applyPreset(key) {
  const to = new Date();
  const from = new Date(to.getTime() - PRESETS[key]);
  document.getElementById('histFrom').value = toLocalInput(from);
  document.getElementById('histTo').value = toLocalInput(to);
  markPreset(key);
}

function syncUrl(host) {
  const u = new URL(location.href);
  if (host) u.searchParams.set('host', host);
  else u.searchParams.delete('host');
  if (page.range) {
    u.searchParams.set('range', page.range);
    u.searchParams.delete('from');
    u.searchParams.delete('to');
  } else {
    u.searchParams.delete('range');
    const b = rangeBounds();
    if (Number.isFinite(b.fromMs)) u.searchParams.set('from', b.from.toISOString());
    if (Number.isFinite(b.toMs)) u.searchParams.set('to', b.to.toISOString());
  }
  history.replaceState(null, '', u);
}

function selectHost(want) {
  if (!want) return;
  const sel = document.getElementById('histHost');
  const key = want.toLowerCase().split('.')[0];
  let match = '';
  for (const o of sel.options) {
    const v = o.value.toLowerCase();
    if (!v) continue;
    if (v === want.toLowerCase()) { match = o.value; break; }
    if (!match && v.split('.')[0] === key) match = o.value;
  }
  if (!match) {
    const o = document.createElement('option');
    o.value = want;
    o.textContent = want;
    sel.appendChild(o);
    match = want;
  }
  sel.value = match;
}

async function loadHosts() {
  const r = await fetch('api/history.php?kind=hosts');
  const d = await r.json();
  const sel = document.getElementById('histHost');
  (d.hosts || []).forEach(h => {
    const o = document.createElement('option');
    o.value = h;
    o.textContent = h;
    sel.appendChild(o);
  });
}

function renderWork(rows) {
  const wb = document.querySelector('#workTable tbody');
  wb.innerHTML = (rows || []).map(w => `<tr>
    <td>${esc(fmtWhen(w.start_time))}</td>
    <td>${w.end_time ? esc(fmtWhen(w.end_time)) : 'Open'}</td>
    <td>${esc(w.display_name || w.hostname)}</td>
    <td>${esc(w.username || '—')}</td>
    <td>${esc(KIND_LABEL[w.session_kind] || w.session_kind || 'Console')}</td>
    <td>${esc(fmtDur(w.active_sec))}</td>
    <td>${esc(w.foreground_app || '—')}</td>
    <td>${esc(w.jump_user_email || '—')}</td>
  </tr>`).join('') || '<tr><td colspan="8">No workstation time in range</td></tr>';
}

function renderSessions(rows) {
  const tb = document.querySelector('#sessTable tbody');
  tb.innerHTML = (rows || []).map(s => `<tr>
    <td>${esc(fmtWhen(s.start_time))}</td>
    <td>${esc(s.telegraf_hostname || s.hostname || s.jump_display_name)}</td>
    <td>${esc(s.user_email)}</td>
    <td>${esc(fmtDur(s.duration_sec))}</td>
    <td>${esc(s.transport)}</td>
    <td>${esc(s.client_ip)}</td>
  </tr>`).join('') || '<tr><td colspan="6">No Jump sessions in range</td></tr>';
}

function renderDwell(rows, host) {
  const dwellBody = document.querySelector('#dwellTable tbody');
  if (!host) {
    dwellBody.innerHTML = '<tr><td colspan="2">Pick a host</td></tr>';
    return;
  }
  dwellBody.innerHTML = (rows || []).map(d => `<tr>
    <td>${esc(d.foreground_app)}</td>
    <td>${esc(d.minutes)}</td>
  </tr>`).join('') || '<tr><td colspan="2">No focused-app samples in range</td></tr>';
}

function renderApps(rows, host) {
  const appBody = document.querySelector('#appTable tbody');
  if (!host) {
    appBody.innerHTML = '<tr><td colspan="3">Pick a host</td></tr>';
    return;
  }
  appBody.innerHTML = (rows || []).map(a => `<tr>
    <td>${esc(a.name)}</td>
    <td>${a.cpu == null ? '—' : Number(a.cpu).toFixed(1) + '%'}</td>
    <td>${esc(fmtBytes(a.rss))}</td>
  </tr>`).join('') || '<tr><td colspan="3">No pinned apps in range</td></tr>';
}

async function loadTurn(qs) {
  const turn = await (await fetch('api/history.php?kind=turn&' + qs)).json();
  const byServer = {};
  (turn.points || []).forEach(p => {
    const id = p.turn_server_id || 'turn';
    if (!byServer[id]) byServer[id] = [];
    byServer[id].push({ y: Number(p.rcvb || 0) / (1024 * 1024) });
  });
  const canvas = document.getElementById('chartTurn');
  if (canvas && typeof nreChart === 'function') {
    nreChart(canvas, Object.entries(byServer).map(([id, points]) => ({ id, points })));
  }
}

function showMachine(on) {
  document.getElementById('machineIntro').hidden = on;
  document.getElementById('machineLayout').hidden = !on;
  document.getElementById('turnSection').hidden = on;
  if (!on) {
    document.getElementById('machineTitle').textContent = 'Machine timeline';
    document.getElementById('procToggle').hidden = true;
    page.machine = null;
  }
}

function machineHint(d) {
  const bits = ['Breaks in the usage lines are gaps longer than a few minutes without Telegraf.'];
  if (d.process_source === 'minutes') {
    bits.push('Process lines are pinned edit apps from the minute rollup. The busiest five apps are charted from raw samples on ranges up to about a week.');
  } else if (d.process_source === 'samples') {
    bits.push('Process lines are the busiest apps in this window. Pinned edit apps stay on the chart when they ran.');
  } else {
    bits.push('No process samples in this range.');
  }
  bits.push('Per-machine network traffic is not collected. TURN totals stay on the all-hosts view, because a relay cannot attribute bytes to this computer.');
  if (d.events_truncated) bits.push('Showing the latest 400 Windows events in this range.');
  return bits.join(' ');
}

function renderRail(d) {
  const ctx = d.context || {};
  const policy = POLICY_LABEL[ctx.relay_policy] || ctx.relay_policy || '';
  const turns = ctx.turn_servers || [];
  let turnHtml = '';
  if (ctx.on_prem_only) {
    turnHtml = '<p>On-prem only — no Jump profile or TURN pin. Workstation History still records this bay.</p>';
  } else if (!ctx.mapped) {
    turnHtml = '<p>This computer is not linked on Mapping, so Jump sessions are matched by hostname only.</p>';
  } else if (ctx.relay_policy === 'relay_only') {
    turnHtml = '<p>Jump Desktop profile: relay only. Coturn byte counters are for the relay server, not this computer.</p>';
  } else if (turns.length) {
    turnHtml = '<ul class="rail-list">' + turns.map(t =>
      `<li>${esc(t.name || t.id)}${t.enabled ? '' : ' (disabled)'}</li>`
    ).join('') + '</ul>';
  } else {
    turnHtml = '<p>No TURN server is pinned. Any configured relay may be used. Those byte counters are still per server, not per computer.</p>';
  }
  const sessions = d.sessions || [];
  const sessHtml = sessions.slice(0, 8).map(s => {
    const bytes = [];
    if (s.bytes_sent) bytes.push(fmtBytes(s.bytes_sent) + ' sent');
    if (s.bytes_recv) bytes.push(fmtBytes(s.bytes_recv) + ' received');
    return `<div class="rail-session">
      <strong>${esc(s.user_email || 'Unknown user')}</strong><br>
      ${esc(fmtWhen(s.start_time))} – ${s.end_time ? esc(fmtWhen(s.end_time)) : 'open'}<br>
      ${esc(s.transport || 'unknown')} · ${esc(s.client_ip || 'no client IP')} · ${esc(fmtDur(s.duration_sec))}
      ${s.end_reason ? '<br>' + esc(s.end_reason) : ''}
      ${bytes.length ? '<br>' + esc(bytes.join(' · ')) : ''}
    </div>`;
  }).join('') || '<p>No Jump session overlaps this range.</p>';
  const more = sessions.length > 8 ? `<p>${sessions.length - 8} more in the table below.</p>` : '';
  const notes = ctx.notes ? `<p>${esc(ctx.notes)}</p>` : '';
  document.getElementById('machineRail').innerHTML = `
    <h3>Jump / TURN</h3>
    <p>${policy ? esc(policy) : 'No relay policy'}</p>
    ${turnHtml}
    ${notes}
    <h3>Jump sessions</h3>
    ${sessHtml}
    ${more}
    <p>Per-session relay bytes are stored only when Jump reports them.</p>`;
}

function renderEvents(rows) {
  const tb = document.querySelector('#machineEvents tbody');
  tb.innerHTML = (rows || []).map(e => `<tr data-id="${esc(e.id)}" data-ms="${esc(Date.parse(e.ts))}">
    <td>${esc(fmtWhen(e.ts))}</td>
    <td><span class="badge badge-cat-${esc(e.category)}">${esc(CAT_LABEL[e.category] || e.category)}</span></td>
    <td><span class="badge badge-sev-${esc(e.severity)}">${esc(e.severity)}</span></td>
    <td>${esc(e.event_id ?? '')}</td>
    <td>${esc(e.username || '—')}</td>
    <td class="event-msg" title="${esc(e.message)}">${esc(e.message)}</td>
  </tr>`).join('') || '<tr><td colspan="6">No Windows events in this range.</td></tr>';
}

function renderLegend(d) {
  const sw = (color, label, dashed) =>
    `<span><i class="tl-swatch${dashed ? ' dashed' : ''}" style="${dashed ? '' : 'background:' + color}"></i>${esc(label)}</span>`;
  const bits = [
    sw('#00b4d8', 'CPU'),
    sw('#22c55e', 'Memory'),
    sw('#f59e0b', 'GPU'),
  ];
  if ((d.points || []).some(p => p.gpu_mem_pct != null)) bits.push(sw('#a78bfa', 'GPU memory', true));
  if ((d.points || []).some(p => p.disk_hot)) bits.push(sw('#f97316', 'Disk ≥ 90%'));
  document.getElementById('tlLegend').innerHTML = bits.join('');
}

function fillProcLegend(d) {
  const el = document.getElementById('procLegend');
  if (!el) return 0;
  const series = d.processes || [];
  if (!series.length) {
    el.hidden = true;
    el.innerHTML = '';
    return 0;
  }
  el.innerHTML = series.map((p, i) => {
    const label = p.name + (p.pinned ? ' · pinned' : '');
    const color = PROC_COLORS[i % PROC_COLORS.length];
    return `<span><i class="tl-swatch" style="background:${color}"></i>${esc(label)}</span>`;
  }).join('');
  el.hidden = false;
  return el.offsetHeight;
}

function gapMsOf(d) {
  const bucket = Math.max(1, Number(d.bucket_sec) || 60) * 1000;
  const stale = Math.max(1, Number(d.stale_sec) || 180) * 1000;
  return Math.max(stale, bucket * 1.5);
}

function packIntervals(items) {
  const sorted = [...items].sort((a, b) => a.start - b.start);
  const rows = [];
  sorted.forEach(it => {
    let row = rows.find(r => r[r.length - 1].end <= it.start);
    if (!row) {
      row = [];
      rows.push(row);
    }
    row.push(it);
  });
  return rows;
}

function asWork(d, fromMs, toMs, bucketMs) {
  const raw = d.work || [];
  if (raw.length) {
    return raw.map(w => {
      const start = Date.parse(w.start_time);
      const end = w.end_time ? Date.parse(w.end_time) : toMs;
      return {
        start, end: Math.max(start, end),
        username: w.username || 'Unknown user',
        kind: w.session_kind || 'local',
        open: !w.end_time,
      };
    }).filter(w => Number.isFinite(w.start) && w.end > fromMs && w.start < toMs);
  }
  const rows = [];
  let cur = null;
  (d.points || []).forEach(p => {
    const user = (p.username || '').trim();
    const t = Date.parse(p.ts);
    if (!user || !Number.isFinite(t)) {
      if (cur) rows.push(cur);
      cur = null;
      return;
    }
    if (!cur || cur.username !== user || t - cur.end > gapMsOf(d)) {
      if (cur) rows.push(cur);
      cur = { start: t, end: t + bucketMs, username: user, kind: 'local', open: false };
    } else {
      cur.end = t + bucketMs;
    }
  });
  if (cur) rows.push(cur);
  return rows;
}

function asJump(d, fromMs, toMs) {
  return (d.sessions || []).map(s => {
    const start = Date.parse(s.start_time);
    const end = s.end_time ? Date.parse(s.end_time) : toMs;
    return {
      start, end: Math.max(start, end),
      email: s.user_email || 'Jump',
      transport: s.transport || 'unknown',
      open: !s.end_time,
    };
  }).filter(s => Number.isFinite(s.start) && s.end > fromMs && s.start < toMs);
}

function lockedRanges(fromMs, toMs, events, lockedAtStart) {
  const ranges = [];
  let locked = !!lockedAtStart;
  let lockStart = locked ? fromMs : null;
  const evs = (events || [])
    .map(e => ({ cat: e.category, ms: Date.parse(e.ts) }))
    .filter(e => (e.cat === 'lock' || e.cat === 'unlock') && e.ms >= fromMs && e.ms <= toMs)
    .sort((a, b) => a.ms - b.ms);
  evs.forEach(e => {
    if (e.cat === 'lock') {
      if (!locked) lockStart = e.ms;
      locked = true;
    } else if (locked && lockStart != null && e.ms > lockStart) {
      ranges.push([lockStart, e.ms]);
      locked = false;
      lockStart = null;
    }
  });
  if (locked && lockStart != null && toMs > lockStart) ranges.push([lockStart, toMs]);
  return ranges;
}

function focusRuns(points, bucketMs, gap) {
  const runs = [];
  let cur = null;
  (points || []).forEach(p => {
    const value = (p.foreground_app || '').trim();
    const t = Date.parse(p.ts);
    if (!value || !Number.isFinite(t)) {
      if (cur) runs.push(cur);
      cur = null;
      return;
    }
    if (!cur || cur.value !== value || t - (cur.end - bucketMs) > gap) {
      if (cur) runs.push(cur);
      cur = { value, start: t, end: t + bucketMs };
    } else {
      cur.end = t + bucketMs;
    }
  });
  if (cur) runs.push(cur);
  return runs;
}

function hue(name) {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 33 + name.charCodeAt(i)) >>> 0;
  return h % 360;
}

function clipText(ctx, text, maxW) {
  if (ctx.measureText(text).width <= maxW) return text;
  let s = text;
  while (s.length > 1 && ctx.measureText(s + '…').width > maxW) s = s.slice(0, -1);
  return s + '…';
}

function fitCanvas(canvas, cssW, cssH) {
  const dpr = window.devicePixelRatio || 1;
  canvas.style.width = cssW + 'px';
  canvas.style.height = cssH + 'px';
  canvas.width = Math.max(1, Math.round(cssW * dpr));
  canvas.height = Math.max(1, Math.round(cssH * dpr));
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return ctx;
}

function buildLayout(d, cssW, bounds, procLegendH) {
  const bucketMs = Math.max(1, Number(d.bucket_sec) || 60) * 1000;
  const fromMs = bounds.fromMs;
  const toMs = Math.max(bounds.toMs, fromMs + bucketMs);
  const work = asWork(d, fromMs, toMs, bucketMs);
  const jump = asJump(d, fromMs, toMs);
  const winRows = Math.max(1, packIntervals(work).length);
  const jumpRows = Math.max(1, packIntervals(jump).length);
  const lanes = [
    { id: 'usage', h: 150 },
    { id: 'win', h: 10 + winRows * 20 },
    { id: 'jump', h: 10 + jumpRows * 20 },
    { id: 'focus', h: 28 },
    { id: 'procKey', h: Math.max(0, procLegendH || 0) },
    { id: 'proc', h: 128 },
    { id: 'activity', h: 26 },
    { id: 'events', h: 32 },
    { id: 'axis', h: 22 },
  ];
  const padL = 104;
  const padR = 12;
  const gap = 8;
  let y = 8;
  lanes.forEach((lane, i) => {
    lane.y = y;
    lane.x = padL;
    lane.w = Math.max(40, cssW - padL - padR);
    if (lane.h <= 0) return;
    y += lane.h + (i === lanes.length - 1 ? 0 : gap);
  });
  return {
    w: cssW,
    h: y + 6,
    padL, padR,
    fromMs, toMs, bucketMs,
    gap: gapMsOf(d),
    lanes,
    work, jump,
    winPacked: packIntervals(work),
    jumpPacked: packIntervals(jump),
    locks: lockedRanges(fromMs, toMs, d.events, d.locked_at_start),
    focus: focusRuns(d.points, bucketMs, gapMsOf(d)),
  };
}

function xOf(layout, ms) {
  const span = Math.max(1, layout.toMs - layout.fromMs);
  return layout.padL + ((ms - layout.fromMs) / span) * (layout.w - layout.padL - layout.padR);
}

function ticks(layout) {
  const span = layout.toMs - layout.fromMs;
  const n = span > 36 * 3600000 ? 6 : 5;
  const out = [];
  for (let i = 0; i <= n; i++) {
    out.push(layout.fromMs + (span * i) / n);
  }
  return out;
}

function fmtTick(ms, span) {
  const d = new Date(ms);
  if (span >= 36 * 3600000) {
    return `${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function drawGrid(ctx, layout, box) {
  const grid = cssVar('--border', '#1e2433');
  ctx.save();
  ctx.strokeStyle = grid;
  ctx.lineWidth = 1;
  ctx.globalAlpha = 0.85;
  ticks(layout).forEach(ms => {
    const x = Math.round(xOf(layout, ms)) + 0.5;
    ctx.beginPath();
    ctx.moveTo(x, box.y);
    ctx.lineTo(x, box.y + box.h);
    ctx.stroke();
  });
  ctx.restore();
}

function laneById(layout, id) {
  return layout.lanes.find(l => l.id === id);
}

function drawSeries(ctx, layout, box, points, key, color, max, dashed) {
  ctx.save();
  ctx.beginPath();
  ctx.rect(box.x, box.y, box.w, box.h);
  ctx.clip();
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.6;
  ctx.setLineDash(dashed ? [4, 3] : []);
  let pen = false;
  let prevT = null;
  let count = 0;
  (points || []).forEach(p => {
    const v = p[key];
    const t = Date.parse(p.ts);
    if (v == null || v === '' || !Number.isFinite(t) || Number.isNaN(Number(v))) {
      pen = false;
      prevT = Number.isFinite(t) ? t : prevT;
      return;
    }
    if (prevT != null && t - prevT > layout.gap) pen = false;
    const x = xOf(layout, t);
    const y = box.y + box.h - (Math.max(0, Number(v)) / max) * (box.h - 2);
    if (!pen) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
    pen = true;
    prevT = t;
    count++;
  });
  if (count === 1) {
    const only = (points || []).find(p => p[key] != null && p[key] !== '');
    if (only) {
      const x = xOf(layout, Date.parse(only.ts));
      const y = box.y + box.h - (Math.max(0, Number(only[key])) / max) * (box.h - 2);
      ctx.beginPath();
      ctx.arc(x, y, 2.2, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.fill();
    }
  } else if (count > 1) ctx.stroke();
  ctx.restore();
}

function yTicks(ctx, box, max, fmt) {
  ctx.fillStyle = cssVar('--text-muted', '#9ca3af');
  ctx.font = '10px ' + cssVar('--font', 'Segoe UI, sans-serif');
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';
  for (let i = 0; i <= 2; i++) {
    const val = max - (max * i) / 2;
    const y = box.y + ((box.h - 2) * i) / 2;
    ctx.fillText(fmt(val), box.x - 6, Math.min(box.y + box.h - 6, Math.max(box.y + 6, y)));
  }
}

function laneTitle(ctx, box, text) {
  ctx.fillStyle = cssVar('--text-muted', '#9ca3af');
  ctx.font = '11px ' + cssVar('--font', 'Segoe UI, sans-serif');
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';
  ctx.fillText(text, box.x - 8, box.y + box.h / 2);
}

function hatch(ctx, x, y, w, h) {
  ctx.save();
  ctx.beginPath();
  ctx.rect(x, y, w, h);
  ctx.clip();
  ctx.strokeStyle = 'rgba(0,0,0,0.4)';
  ctx.lineWidth = 1;
  for (let i = -h; i < w + h; i += 5) {
    ctx.beginPath();
    ctx.moveTo(x + i, y + h);
    ctx.lineTo(x + i + h, y);
    ctx.stroke();
  }
  ctx.restore();
}

function barInterval(ctx, layout, x, y, w, h, fill, label) {
  const x0 = Math.max(layout.padL, x);
  const x1 = Math.min(layout.w - layout.padR, x + w);
  if (x1 - x0 < 1) return;
  ctx.fillStyle = fill;
  ctx.fillRect(x0, y, x1 - x0, h);
  if (label && x1 - x0 > 46) {
    ctx.save();
    ctx.fillStyle = '#fff';
    ctx.shadowColor = 'rgba(0,0,0,0.65)';
    ctx.shadowBlur = 2;
    ctx.font = '11px ' + cssVar('--font', 'Segoe UI, sans-serif');
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';
    ctx.fillText(clipText(ctx, label, x1 - x0 - 8), x0 + 4, y + h / 2);
    ctx.restore();
  }
}

function emptyLane(ctx, box, text) {
  ctx.fillStyle = cssVar('--text-muted', '#9ca3af');
  ctx.font = '11px ' + cssVar('--font', 'Segoe UI, sans-serif');
  ctx.textAlign = 'left';
  ctx.textBaseline = 'middle';
  ctx.fillText(text, box.x + 6, box.y + box.h / 2);
}

function drawBase(ctx, layout, d) {
  const bg = cssVar('--bg-card', '#303030');
  ctx.clearRect(0, 0, layout.w, layout.h);
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, layout.w, layout.h);
  page.hits = [];

  const usage = laneById(layout, 'usage');
  drawGrid(ctx, layout, usage);
  yTicks(ctx, usage, 100, v => String(Math.round(v)));
  const pts = d.points || [];
  (pts || []).forEach(p => {
    if (!p.disk_hot) return;
    const t = Date.parse(p.ts);
    const x0 = xOf(layout, t);
    const x1 = xOf(layout, t + layout.bucketMs);
    ctx.fillStyle = '#f97316';
    ctx.fillRect(x0, usage.y, Math.max(2, x1 - x0), 4);
  });
  drawSeries(ctx, layout, usage, pts, 'cpu_pct', '#00b4d8', 100, false);
  drawSeries(ctx, layout, usage, pts, 'mem_pct', '#22c55e', 100, false);
  drawSeries(ctx, layout, usage, pts, 'gpu_pct', '#f59e0b', 100, false);
  if (pts.some(p => p.gpu_mem_pct != null)) {
    drawSeries(ctx, layout, usage, pts, 'gpu_mem_pct', '#a78bfa', 100, true);
  }

  const win = laneById(layout, 'win');
  drawGrid(ctx, layout, win);
  laneTitle(ctx, win, 'Windows');
  if (!layout.winPacked.length) emptyLane(ctx, win, 'No Windows session in this range');
  layout.winPacked.forEach((row, ri) => {
    row.forEach(it => {
      const x = xOf(layout, it.start);
      const w = xOf(layout, it.end) - x;
      const y = win.y + 4 + ri * 20;
      const kind = it.kind === 'rdp' ? ' · RDP' : (it.kind === 'jump' ? ' · Jump' : '');
      barInterval(ctx, layout, x, y, w, 16, 'rgba(34,197,94,0.9)', it.username + kind);
      layout.locks.forEach(([a, b]) => {
        const s = Math.max(a, it.start);
        const e = Math.min(b, it.end);
        if (e <= s) return;
        const hx = xOf(layout, s);
        const hw = xOf(layout, e) - hx;
        const x0 = Math.max(layout.padL, hx);
        const x1 = Math.min(layout.w - layout.padR, hx + hw);
        if (x1 > x0) hatch(ctx, x0, y, x1 - x0, 16);
      });
    });
  });

  const jump = laneById(layout, 'jump');
  drawGrid(ctx, layout, jump);
  laneTitle(ctx, jump, 'Jump');
  if (!layout.jumpPacked.length) emptyLane(ctx, jump, 'No Jump session in this range');
  const jumpColor = { relayed: '#f59e0b', p2p: '#22c55e', unknown: '#64748b' };
  layout.jumpPacked.forEach((row, ri) => {
    row.forEach(it => {
      const x = xOf(layout, it.start);
      const w = xOf(layout, it.end) - x;
      const y = jump.y + 4 + ri * 20;
      const label = it.email + (it.transport ? ' · ' + it.transport : '');
      barInterval(ctx, layout, x, y, w, 16, jumpColor[it.transport] || jumpColor.unknown, label);
    });
  });

  const focus = laneById(layout, 'focus');
  drawGrid(ctx, layout, focus);
  laneTitle(ctx, focus, 'Focused');
  if (!layout.focus.length) emptyLane(ctx, focus, 'No focused app');
  layout.focus.forEach(run => {
    const x = xOf(layout, run.start);
    const w = xOf(layout, run.end) - x;
    ctx.fillStyle = `hsl(${hue(run.value)}, 52%, 42%)`;
    const x0 = Math.max(layout.padL, x);
    const x1 = Math.min(layout.w - layout.padR, x + w);
    if (x1 - x0 < 1) return;
    ctx.fillRect(x0, focus.y + 6, x1 - x0, 16);
    if (x1 - x0 > 52) {
      ctx.fillStyle = '#fff';
      ctx.font = '11px ' + cssVar('--font', 'Segoe UI, sans-serif');
      ctx.textAlign = 'left';
      ctx.textBaseline = 'middle';
      ctx.fillText(clipText(ctx, run.value, x1 - x0 - 8), x0 + 4, focus.y + 14);
    }
  });

  const proc = laneById(layout, 'proc');
  drawGrid(ctx, layout, proc);
  const mode = page.procMode;
  const series = d.processes || [];
  let max = mode === 'cpu' ? 100 : 1;
  series.forEach(s => (s.points || []).forEach(p => {
    const v = mode === 'cpu' ? Number(p.cpu) : Number(p.rss);
    if (Number.isFinite(v)) max = Math.max(max, v);
  }));
  if (mode === 'cpu' && max > 100) max = Math.ceil(max / 10) * 10;
  const procFmt = v => mode === 'cpu' ? String(Math.round(v)) : (v <= 0 ? '0' : fmtBytes(v));
  ctx.fillStyle = cssVar('--text-muted', '#9ca3af');
  ctx.font = '10px ' + cssVar('--font', 'Segoe UI, sans-serif');
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';
  ctx.fillText(procFmt(max), proc.x - 6, proc.y + 6);
  ctx.fillText('Processes', proc.x - 6, proc.y + proc.h / 2);
  ctx.fillText(procFmt(0), proc.x - 6, proc.y + proc.h - 6);
  if (!series.length) emptyLane(ctx, proc, 'No process samples in this range');
  series.forEach((s, i) => {
    const points = (s.points || []).map(p => ({
      ts: p.ts,
      y: mode === 'cpu' ? p.cpu : p.rss,
    }));
    drawSeries(ctx, layout, proc, points, 'y', PROC_COLORS[i % PROC_COLORS.length], max, false);
  });

  const act = laneById(layout, 'activity');
  drawGrid(ctx, layout, act);
  laneTitle(ctx, act, 'Input');
  let maxIn = 1;
  pts.forEach(p => { if (Number(p.input_n) > maxIn) maxIn = Number(p.input_n); });
  pts.forEach(p => {
    const t = Date.parse(p.ts);
    if (!Number.isFinite(t)) return;
    const n = Number(p.input_n) || 0;
    const x0 = xOf(layout, t);
    const x1 = xOf(layout, t + layout.bucketMs);
    const alpha = n <= 0 ? 0.12 : 0.2 + 0.8 * Math.min(1, n / maxIn);
    ctx.fillStyle = n <= 0 ? 'rgba(148,163,184,0.18)' : `rgba(0,180,216,${alpha})`;
    ctx.fillRect(x0, act.y + 8, Math.max(1, x1 - x0), 10);
  });

  const ev = laneById(layout, 'events');
  drawGrid(ctx, layout, ev);
  laneTitle(ctx, ev, 'Events');
  ctx.strokeStyle = cssVar('--border-bright', '#2a3148');
  ctx.beginPath();
  ctx.moveTo(ev.x, ev.y + ev.h / 2);
  ctx.lineTo(ev.x + ev.w, ev.y + ev.h / 2);
  ctx.stroke();
  (d.events || []).forEach(e => {
    const ms = Date.parse(e.ts);
    if (!Number.isFinite(ms) || ms < layout.fromMs || ms > layout.toMs) return;
    const x = xOf(layout, ms);
    const y = ev.y + 6;
    ctx.fillStyle = CAT_COLOR[e.category] || CAT_COLOR.other;
    ctx.beginPath();
    ctx.moveTo(x, y);
    ctx.lineTo(x + 5, y + 12);
    ctx.lineTo(x - 5, y + 12);
    ctx.closePath();
    ctx.fill();
    page.hits.push({ x, y: y + 6, id: e.id, ms, category: e.category });
  });

  const axis = laneById(layout, 'axis');
  ctx.fillStyle = cssVar('--text-muted', '#9ca3af');
  ctx.font = '10px ' + cssVar('--font', 'Segoe UI, sans-serif');
  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  const span = layout.toMs - layout.fromMs;
  ticks(layout).forEach(ms => {
    ctx.fillText(fmtTick(ms, span), xOf(layout, ms), axis.y + 2);
  });
}

function drawOverlay(ctx, layout) {
  ctx.clearRect(0, 0, layout.w, layout.h);
  const marks = [];
  if (page.pinMs != null) marks.push({ ms: page.pinMs, color: cssVar('--accent', '#00b4d8') });
  if (page.hover && page.hover.ms != null) marks.push({ ms: page.hover.ms, color: 'rgba(226,232,240,0.85)' });
  marks.forEach(m => {
    const x = Math.round(xOf(layout, m.ms)) + 0.5;
    if (x < layout.padL || x > layout.w - layout.padR) return;
    ctx.strokeStyle = m.color;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x, 8);
    ctx.lineTo(x, layout.h - 24);
    ctx.stroke();
  });
}

function paint() {
  const d = page.machine;
  const wrap = document.getElementById('machineTimeline');
  if (!d || !wrap || wrap.parentElement.hidden) return;
  const cssW = wrap.clientWidth;
  if (cssW < 40) return;
  if (page.painting) return;
  page.painting = true;
  const bounds = rangeBounds();
  const procLegendH = fillProcLegend(d);
  const layout = buildLayout(d, cssW, bounds, procLegendH);
  page.layout = layout;
  const procKey = laneById(layout, 'procKey');
  const procLegend = document.getElementById('procLegend');
  if (procLegend && procKey && procKey.h > 0) procLegend.style.top = procKey.y + 'px';
  const base = fitCanvas(document.getElementById('tlBase'), cssW, layout.h);
  const over = fitCanvas(document.getElementById('tlOverlay'), cssW, layout.h);
  drawBase(base, layout, d);
  drawOverlay(over, layout);
  page.painting = false;
}

function paintOverlay() {
  const layout = page.layout;
  const canvas = document.getElementById('tlOverlay');
  if (!layout || !canvas) return;
  const dpr = window.devicePixelRatio || 1;
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  drawOverlay(ctx, layout);
}

function pointNear(ms, d, layout) {
  let best = null;
  let bestD = Infinity;
  (d.points || []).forEach(p => {
    const t = Date.parse(p.ts);
    const dist = Math.abs(t - ms);
    if (dist < bestD) { bestD = dist; best = p; }
  });
  if (!best || bestD > layout.gap) return null;
  return best;
}

function tooltipHtml(ms) {
  const d = page.machine;
  const layout = page.layout;
  if (!d || !layout) return '';
  const when = new Date(ms).toLocaleString();
  const p = pointNear(ms, d, layout);
  const win = layout.work.find(w => ms >= w.start && ms <= w.end);
  const jump = layout.jump.find(s => ms >= s.start && ms <= s.end);
  const ev = (d.events || [])
    .map(e => ({ e, dist: Math.abs(Date.parse(e.ts) - ms) }))
    .filter(x => x.dist < Math.max(layout.bucketMs, 60000))
    .sort((a, b) => a.dist - b.dist)[0];
  const lines = [`<strong>${esc(when)}</strong>`];
  if (!p) lines.push('No telemetry in this gap');
  else {
    const num = v => v == null || v === '' ? '—' : Math.round(Number(v)) + '%';
    lines.push(`CPU ${num(p.cpu_pct)} · Memory ${num(p.mem_pct)} · GPU ${num(p.gpu_pct)}`);
    if (p.gpu_mem_pct != null) lines.push(`GPU memory ${num(p.gpu_mem_pct)}`);
    if (p.disk_hot) lines.push('Disk at or over 90% used');
    if (p.foreground_app) lines.push('Focused ' + esc(p.foreground_app));
    lines.push('Input ' + esc(p.input_n || 0));
  }
  if (win) lines.push('Windows ' + esc(win.username));
  if (jump) lines.push('Jump ' + esc(jump.email) + ' · ' + esc(jump.transport));
  if (ev) lines.push(esc((CAT_LABEL[ev.e.category] || ev.e.category) + (ev.e.event_id ? ' ' + ev.e.event_id : '')));
  return lines.join('<br>');
}

function localPoint(ev) {
  const rect = ev.target.getBoundingClientRect();
  return {
    x: ev.clientX - rect.left,
    y: ev.clientY - rect.top,
    clientX: ev.clientX,
    clientY: ev.clientY,
  };
}

function msFromX(x, layout) {
  const span = layout.toMs - layout.fromMs;
  const inner = layout.w - layout.padL - layout.padR;
  return layout.fromMs + ((x - layout.padL) / inner) * span;
}

function placeTip(clientX, clientY, html) {
  const tip = document.getElementById('tlTip');
  const wrap = document.getElementById('machineTimeline');
  const rect = wrap.getBoundingClientRect();
  tip.hidden = false;
  tip.innerHTML = html;
  let left = clientX - rect.left + 14;
  let top = clientY - rect.top + 14;
  if (left + 260 > rect.width) left = clientX - rect.left - 264;
  const h = tip.offsetHeight || 80;
  if (top + h > rect.height) top = Math.max(8, clientY - rect.top - h - 10);
  tip.style.left = Math.max(8, left) + 'px';
  tip.style.top = Math.max(8, top) + 'px';
}

function nearestHit(x, y) {
  let best = null;
  let bestD = 12;
  page.hits.forEach(h => {
    const d = Math.hypot(h.x - x, h.y - y);
    if (d < bestD) { bestD = d; best = h; }
  });
  return best;
}

function highlightEvent(id) {
  document.querySelectorAll('#machineEvents tbody tr').forEach(tr => {
    tr.classList.toggle('tl-event-hot', String(tr.dataset.id) === String(id));
  });
  const row = document.querySelector(`#machineEvents tr[data-id="${CSS.escape(String(id))}"]`);
  row?.scrollIntoView({ block: 'nearest' });
}

function onMove(ev) {
  const layout = page.layout;
  if (!layout) return;
  const pt = localPoint(ev);
  if (pt.x < layout.padL || pt.x > layout.w - layout.padR) {
    page.hover = null;
    document.getElementById('tlTip').hidden = true;
    paintOverlay();
    return;
  }
  const ms = msFromX(pt.x, layout);
  page.hover = { ms };
  paintOverlay();
  placeTip(pt.clientX, pt.clientY, tooltipHtml(ms));
}

function onLeave() {
  page.hover = null;
  document.getElementById('tlTip').hidden = true;
  paintOverlay();
}

function onClick(ev) {
  const layout = page.layout;
  if (!layout) return;
  const pt = localPoint(ev);
  const hit = nearestHit(pt.x, pt.y);
  if (hit) {
    page.pinMs = hit.ms;
    highlightEvent(hit.id);
    paintOverlay();
    placeTip(pt.clientX, pt.clientY, tooltipHtml(hit.ms));
    return;
  }
  if (pt.x >= layout.padL && pt.x <= layout.w - layout.padR) {
    page.pinMs = msFromX(pt.x, layout);
    paintOverlay();
  }
}

function renderMachine(d) {
  page.machine = d;
  page.hover = null;
  page.pinMs = null;
  document.getElementById('tlTip').hidden = true;
  document.getElementById('machineTitle').textContent = d.display_name || d.host || 'Machine timeline';
  document.getElementById('machineHint').textContent = machineHint(d);
  document.getElementById('procToggle').hidden = !(d.processes || []).length;
  renderLegend(d);
  renderRail(d);
  renderEvents(d.events);
  showMachine(true);
  requestAnimationFrame(() => requestAnimationFrame(paint));
}

async function loadHistory() {
  const host = document.getElementById('histHost').value;
  const bounds = rangeBounds();
  if (!Number.isFinite(bounds.fromMs) || !Number.isFinite(bounds.toMs)) return;
  syncUrl(host);
  const qs = new URLSearchParams({
    from: bounds.from.toISOString(),
    to: bounds.to.toISOString(),
    host,
  });
  if (!host) {
    showMachine(false);
    const work = await (await fetch('api/history.php?kind=work&' + qs)).json();
    renderWork(work.intervals);
    const sess = await (await fetch('api/history.php?kind=sessions&' + qs)).json();
    renderSessions(sess.sessions);
    renderDwell([], '');
    renderApps([], '');
    await loadTurn(qs);
    return;
  }
  const d = await (await fetch('api/history.php?kind=machine&' + qs)).json();
  if (!d.ok) {
    showMachine(false);
    document.getElementById('machineIntro').hidden = false;
    document.getElementById('machineIntro').textContent = d.error || 'Could not load this computer.';
    return;
  }
  renderWork(d.work);
  renderSessions(d.sessions);
  renderDwell(d.dwell, host);
  renderApps(d.apps, host);
  renderMachine(d);
}

(function init() {
  const params = new URLSearchParams(location.search);
  const range = params.get('range') || '';
  if (PRESETS[range]) applyPreset(range);
  else if (params.get('from') && params.get('to')) {
    markPreset('');
    const from = new Date(params.get('from'));
    const to = new Date(params.get('to'));
    if (!Number.isNaN(from.getTime())) document.getElementById('histFrom').value = toLocalInput(from);
    if (!Number.isNaN(to.getTime())) document.getElementById('histTo').value = toLocalInput(to);
  } else {
    applyPreset('7d');
  }

  document.getElementById('histFilters').addEventListener('submit', (e) => {
    e.preventDefault();
    markPreset('');
    loadHistory();
  });
  document.querySelectorAll('[data-range]').forEach(btn => {
    btn.addEventListener('click', () => {
      applyPreset(btn.dataset.range);
      loadHistory();
    });
  });
  document.getElementById('procCpu').addEventListener('click', () => {
    page.procMode = 'cpu';
    document.getElementById('procCpu').classList.remove('btn-secondary');
    document.getElementById('procMem').classList.add('btn-secondary');
    document.getElementById('procCpu').setAttribute('aria-pressed', 'true');
    document.getElementById('procMem').setAttribute('aria-pressed', 'false');
    paint();
  });
  document.getElementById('procMem').addEventListener('click', () => {
    page.procMode = 'mem';
    document.getElementById('procMem').classList.remove('btn-secondary');
    document.getElementById('procCpu').classList.add('btn-secondary');
    document.getElementById('procMem').setAttribute('aria-pressed', 'true');
    document.getElementById('procCpu').setAttribute('aria-pressed', 'false');
    paint();
  });
  document.getElementById('machineEvents').addEventListener('click', (e) => {
    const tr = e.target.closest('tr[data-id]');
    if (!tr || !tr.dataset.ms) return;
    page.pinMs = Number(tr.dataset.ms);
    highlightEvent(tr.dataset.id);
    paintOverlay();
    const layout = page.layout;
    if (!layout) return;
    const wrap = document.getElementById('machineTimeline').getBoundingClientRect();
    const x = wrap.left + xOf(layout, page.pinMs);
    placeTip(x, wrap.top + 40, tooltipHtml(page.pinMs));
  });
  const overlay = document.getElementById('tlOverlay');
  overlay.addEventListener('mousemove', onMove);
  overlay.addEventListener('mouseleave', onLeave);
  overlay.addEventListener('click', onClick);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && page.pinMs != null) {
      page.pinMs = null;
      paintOverlay();
    }
  });
  let lastW = 0;
  const ro = new ResizeObserver(() => {
    const wrap = document.getElementById('machineTimeline');
    if (!wrap || wrap.parentElement.hidden) return;
    const w = wrap.clientWidth;
    if (!w || w === lastW) return;
    lastW = w;
    if (page.machine) paint();
  });
  ro.observe(document.getElementById('machineTimeline'));

  loadHosts().then(() => {
    selectHost(params.get('host') || '');
    return loadHistory();
  }).catch(() => {
    document.getElementById('machineIntro').textContent = 'Could not load history.';
  });
})();
