'use strict';

let cfg = null;
let generatedToken = '';

function hint(el, meta) {
  if (!el) return;
  el.textContent = meta && meta.set ? `currently ${meta.hint}` : 'not set';
}

function fill() {
  const p = cfg.postgres || {};
  document.getElementById('pgHost').value = p.host || '';
  document.getElementById('pgPort').value = p.port || 5432;
  document.getElementById('pgDb').value = p.database || '';
  document.getElementById('pgUser').value = p.user || '';
  document.getElementById('pgPass').value = '';
  hint(document.getElementById('pgPassHint'), p.password_meta);

  const j = cfg.jump || {};
  document.getElementById('jumpBase').value = j.base_url || '';
  document.getElementById('jumpTeam').value = j.team_id || '';
  document.getElementById('jumpToken').value = '';
  hint(document.getElementById('jumpTokHint'), j.api_token_meta);

  const t = cfg.telegraf || {};
  document.getElementById('telToken').value = generatedToken || '';
  hint(document.getElementById('telTokHint'), t.ingest_token_meta);
  document.getElementById('telCidrs').value = (t.ingest_cidrs || []).join('\n');
  document.getElementById('telIdleActive').value = t.idle_active_seconds || 120;

  renderTurns(cfg.turn_servers || []);
  renderWatch(cfg.process_watchlist || []);

  const b = cfg.bridge || {};
  document.getElementById('brWsPort').value = b.ws_port || 8765;
  document.getElementById('brIngestHost').value = b.ingest_host || '127.0.0.1';
  document.getElementById('brIngestPort').value = b.ingest_port || 8766;
  document.getElementById('brJumpPoll').value = b.jump_poll_seconds || 45;
  document.getElementById('brTurnPoll').value = b.turn_poll_seconds || 15;
  document.getElementById('brRetention').value = b.retention_days || 90;
  document.getElementById('brStale').value = b.stale_session_hours || 18;
}

function renderTurns(list) {
  const box = document.getElementById('turnList');
  box.innerHTML = list.map((s, i) => `
    <div class="turn-row" data-i="${i}">
      <label>ID <input type="text" data-f="id" value="${esc(s.id || '')}"></label>
      <label>Name <input type="text" data-f="name" value="${esc(s.name || '')}"></label>
      <label>Metrics URL <input type="text" data-f="metrics_url" value="${esc(s.metrics_url || '')}" placeholder="http://10.x.x.x:9641/metrics"></label>
      <label class="checkbox-label"><input type="checkbox" data-f="enabled" ${s.enabled !== false ? 'checked' : ''}> <span>Enabled</span></label>
      <button type="button" class="btn btn-sm btn-danger" data-rm="${i}">Remove</button>
    </div>`).join('') || '<p class="hint">No TURN servers yet.</p>';
  box.querySelectorAll('[data-rm]').forEach(btn => {
    btn.addEventListener('click', () => {
      const next = collectTurns();
      next.splice(Number(btn.dataset.rm), 1);
      renderTurns(next);
    });
  });
}

function renderWatch(list) {
  const box = document.getElementById('watchList');
  if (!box) return;
  box.innerHTML = list.map((w, i) => `
    <div class="turn-row watch-row" data-i="${i}">
      <label>Label <input type="text" data-f="label" value="${esc(w.label || '')}" placeholder="Premiere Pro"></label>
      <label>Match <input type="text" data-f="match" value="${esc(w.match || '')}" placeholder="premiere"></label>
      <label class="checkbox-label"><input type="checkbox" data-f="enabled" ${w.enabled !== false ? 'checked' : ''}> <span>Pin to top</span></label>
      <button type="button" class="btn btn-sm btn-danger" data-rm-watch="${i}">Remove</button>
    </div>`).join('') || '<p class="hint">No watchlist entries.</p>';
  box.querySelectorAll('[data-rm-watch]').forEach(btn => {
    btn.addEventListener('click', () => {
      const next = collectWatch();
      next.splice(Number(btn.dataset.rmWatch), 1);
      renderWatch(next);
    });
  });
}

function collectWatch() {
  return [...document.querySelectorAll('#watchList .watch-row')].map(row => ({
    label: row.querySelector('[data-f=label]').value.trim(),
    match: row.querySelector('[data-f=match]').value.trim(),
    enabled: row.querySelector('[data-f=enabled]').checked,
  }));
}

function collectTurns() {
  return [...document.querySelectorAll('#turnList .turn-row')].map(row => ({
    id: row.querySelector('[data-f=id]').value.trim(),
    name: row.querySelector('[data-f=name]').value.trim(),
    metrics_url: row.querySelector('[data-f=metrics_url]').value.trim(),
    enabled: row.querySelector('[data-f=enabled]').checked,
  }));
}

function esc(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');
}

async function save(section, payload) {
  const r = await fetch('api/settings.php', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ section, ...payload }),
  });
  const d = await r.json();
  if (!d.ok) { toast('error', d.error || 'Save failed'); return null; }
  toast('success', 'Saved to data/config.json');
  cfg = d.config;
  if (d.config.telegraf && d.config.telegraf.ingest_token_once) {
    generatedToken = d.config.telegraf.ingest_token;
    toast('info', 'Copy the new Telegraf token now — it will not be shown again.');
  }
  fill();
  return d;
}

async function load() {
  const r = await fetch('api/settings.php');
  const d = await r.json();
  if (!d.ok) { toast('error', d.error || 'Load failed'); return; }
  cfg = d.config;
  fill();
}

document.getElementById('pgForm').addEventListener('submit', (e) => {
  e.preventDefault();
  save('postgres', {
    host: document.getElementById('pgHost').value,
    port: Number(document.getElementById('pgPort').value),
    database: document.getElementById('pgDb').value,
    user: document.getElementById('pgUser').value,
    password: document.getElementById('pgPass').value,
  });
});

document.getElementById('jumpForm').addEventListener('submit', (e) => {
  e.preventDefault();
  save('jump', {
    base_url: document.getElementById('jumpBase').value,
    team_id: document.getElementById('jumpTeam').value,
    api_token: document.getElementById('jumpToken').value,
  });
});

document.getElementById('telForm').addEventListener('submit', (e) => {
  e.preventDefault();
  save('telegraf', {
    ingest_token: document.getElementById('telToken').value,
    ingest_cidrs: document.getElementById('telCidrs').value.split(/\r?\n/),
    idle_active_seconds: Number(document.getElementById('telIdleActive').value),
  });
});

document.getElementById('btnGenToken').addEventListener('click', () => {
  if (!confirm('Generate a new ingest token? Update Telegraf on every bay afterward.')) return;
  save('telegraf', { generate_token: true });
});

document.getElementById('btnAddTurn').addEventListener('click', () => {
  const next = collectTurns();
  next.push({ id: 'turn-' + (next.length + 1), name: 'TURN ' + (next.length + 1), metrics_url: '', enabled: true });
  renderTurns(next);
});

document.getElementById('btnSaveTurns').addEventListener('click', () => {
  save('turn_servers', { turn_servers: collectTurns() });
});

document.getElementById('btnAddWatch').addEventListener('click', () => {
  const next = collectWatch();
  next.push({ label: '', match: '', enabled: true });
  renderWatch(next);
});

document.getElementById('btnSaveWatch').addEventListener('click', () => {
  save('process_watchlist', { process_watchlist: collectWatch() });
});

document.getElementById('brForm').addEventListener('submit', (e) => {
  e.preventDefault();
  save('bridge', {
    ws_port: Number(document.getElementById('brWsPort').value),
    ingest_host: document.getElementById('brIngestHost').value,
    ingest_port: Number(document.getElementById('brIngestPort').value),
    jump_poll_seconds: Number(document.getElementById('brJumpPoll').value),
    turn_poll_seconds: Number(document.getElementById('brTurnPoll').value),
    retention_days: Number(document.getElementById('brRetention').value),
    stale_session_hours: Number(document.getElementById('brStale').value),
  });
});

load();
