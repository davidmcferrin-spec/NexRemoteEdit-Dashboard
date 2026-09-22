'use strict';

let mapData = null;

function esc(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');
}

async function loadMaps() {
  const r = await fetch('api/mapping.php');
  mapData = await r.json();
  if (!mapData.ok) { toast('error', mapData.error || 'Load failed'); return; }

  const dl = document.getElementById('telHosts');
  dl.innerHTML = (mapData.telegraf_hosts || []).map(h => `<option value="${esc(h)}">`).join('');

  const tb = document.querySelector('#mapTable tbody');
  tb.innerHTML = (mapData.maps || []).map(m => {
    const tel = m.telegraf_hostname || '';
    const missing = !tel ? ' style="color:var(--yellow)"' : '';
    return `<tr>
      <td>${esc(m.jump_display_name)}</td>
      <td>${esc(m.jump_hostname)}</td>
      <td${missing}>${esc(tel || '— unmapped —')}</td>
      <td>${esc((m.aliases || []).join(', '))}</td>
      <td>${esc(m.relay_policy)}</td>
      <td>${esc((m.turn_server_ids || []).join(', ') || 'any')}</td>
      <td><button class="btn btn-sm btn-secondary" data-edit="${m.id}">Edit</button></td>
    </tr>`;
  }).join('') || '<tr><td colspan="7">No Jump devices yet — start the bridge after Settings.</td></tr>';

  tb.querySelectorAll('[data-edit]').forEach(btn => {
    btn.addEventListener('click', () => openMap(Number(btn.dataset.edit)));
  });

  const ul = document.getElementById('unmappedTel');
  const um = mapData.unmapped_telegraf || [];
  ul.innerHTML = um.length
    ? um.map(t => `<li class="admin-list-item"><span>${esc(t.hostname)}</span><span class="hint">last ${esc(t.last_ts)}</span></li>`).join('')
    : '<li class="hint">None in the last 24 hours.</li>';
}

function openMap(id) {
  const m = (mapData.maps || []).find(x => Number(x.id) === id);
  if (!m) return;
  document.getElementById('mapId').value = id;
  document.getElementById('mapTelHost').value = m.telegraf_hostname || '';
  document.getElementById('mapAliases').value = (m.aliases || []).join(', ');
  document.getElementById('mapPolicy').value = m.relay_policy || 'coturn_then_p2p';
  document.getElementById('mapNotes').value = m.notes || '';
  const box = document.getElementById('mapTurns');
  const selected = new Set(m.turn_server_ids || []);
  box.innerHTML = (mapData.turn_servers || []).map(s => `
    <label class="checkbox-label">
      <input type="checkbox" value="${esc(s.id)}" ${selected.has(s.id) ? 'checked' : ''}>
      <span>${esc(s.name || s.id)}</span>
    </label>`).join('') || '<p class="hint">Add TURN servers in Settings first.</p>';
  document.getElementById('modalMap').removeAttribute('hidden');
}

document.getElementById('btnSaveMap').addEventListener('click', async () => {
  const ids = [...document.querySelectorAll('#mapTurns input:checked')].map(c => c.value);
  const r = await fetch('api/mapping.php', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      id: Number(document.getElementById('mapId').value),
      telegraf_hostname: document.getElementById('mapTelHost').value.trim(),
      aliases: document.getElementById('mapAliases').value,
      relay_policy: document.getElementById('mapPolicy').value,
      notes: document.getElementById('mapNotes').value,
      turn_server_ids: ids,
    }),
  });
  const d = await r.json();
  if (d.ok) {
    toast('success', 'Mapping saved');
    document.getElementById('modalMap').setAttribute('hidden', '');
    loadMaps();
  } else toast('error', d.error || 'Save failed');
});

document.querySelectorAll('[data-modal]').forEach(btn => {
  btn.addEventListener('click', () => document.getElementById(btn.dataset.modal).setAttribute('hidden', ''));
});

document.getElementById('ipForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const ip = document.getElementById('lookupIp').value.trim();
  const at = document.getElementById('lookupAt').value;
  const qs = new URLSearchParams({ action: 'lookup', ip, at });
  const d = await (await fetch('api/mapping.php?' + qs)).json();
  const tb = document.querySelector('#ipTable tbody');
  if (!d.ok) { toast('error', d.error || 'Lookup failed'); return; }
  tb.innerHTML = (d.rows || []).map(r => `<tr>
    <td>${esc(r.connection_id)}</td>
    <td>${esc(r.user_email)}</td>
    <td>${esc(r.telegraf_hostname || r.hostname || r.jump_display_name)}</td>
    <td>${esc(r.start_time)} → ${esc(r.end_time || 'active')}</td>
    <td>${esc(r.source)} ${esc(r.ip)}</td>
  </tr>`).join('') || '<tr><td colspan="5">No session owned that IP at that time.</td></tr>';
});

loadMaps();
