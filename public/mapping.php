<?php
require_once __DIR__ . '/includes/auth.php';
require_permission('mapping');
$user = session_user_payload_full();
$nre_active = 'mapping';
?>
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Mapping — NexEditorStats</title>
  <?php require __DIR__ . '/includes/theme_head.php'; ?>
  <link rel="stylesheet" href="assets/style.css">
</head>
<body>
<?php require __DIR__ . '/includes/nav.php'; ?>
<main class="admin-page">
  <h1 class="page-title">Setup — Mapping</h1>
  <p class="hint">The live unit is the bay (Telegraf). Jump is optional. <strong>Jump Desktop profiles</strong> are what actually send a remote session to on-prem coturn first or force Jump relay — this page does not write those profiles. Record the same intent here so Live and History interpret the session. On-prem-only editors never need Jump, a profile, or a TURN pin.</p>

  <section class="admin-section">
    <h2>Device identity</h2>
    <div class="admin-table-wrap">
      <table class="admin-table" id="mapTable">
        <thead>
          <tr>
            <th>Jump name</th>
            <th>Jump hostname</th>
            <th>Telegraf host</th>
            <th>Aliases</th>
            <th>Policy</th>
            <th>TURN servers</th>
            <th></th>
          </tr>
        </thead>
        <tbody></tbody>
      </table>
    </div>
  </section>

  <section class="admin-section">
    <h2>Unmapped Telegraf hosts</h2>
    <p class="hint">Seen in the last 24 hours with no mapping row. On-prem-only bays usually auto-create a host row on first sample. Use this list when the Windows name and a Jump computer name differ.</p>
    <ul class="admin-list" id="unmappedTel"></ul>
  </section>

  <section class="admin-section">
    <h2>Who was this IP?</h2>
    <form id="ipForm" class="filter-bar">
      <label>IP <input type="text" id="lookupIp" placeholder="203.0.113.10"></label>
      <label>At
        <input type="datetime-local" id="lookupAt">
      </label>
      <button type="submit" class="btn btn-sm">Look up</button>
    </form>
    <div class="admin-table-wrap">
      <table class="admin-table" id="ipTable">
        <thead><tr><th>Session</th><th>User</th><th>Computer</th><th>When</th><th>IP source</th></tr></thead>
        <tbody></tbody>
      </table>
    </div>
  </section>
</main>

<div class="modal-overlay" id="modalMap" hidden>
  <div class="modal">
    <div class="modal-header">
      <h2>Edit mapping</h2>
      <button class="modal-close" data-modal="modalMap">✕</button>
    </div>
    <div class="modal-body">
      <input type="hidden" id="mapId">
      <label>Telegraf hostname
        <input type="text" id="mapTelHost" list="telHosts">
      </label>
      <datalist id="telHosts"></datalist>
      <label>Aliases <span class="hint-inline">comma-separated</span>
        <input type="text" id="mapAliases">
      </label>
      <label>Relay policy <span class="hint-inline">match the Jump Desktop profile; unused for on-prem-only</span>
        <select id="mapPolicy">
          <option value="coturn_then_p2p">On-prem coturn first, then P2P</option>
          <option value="relay_only">Jump relay only</option>
        </select>
      </label>
      <div class="edit-section-title">TURN servers for this computer</div>
      <p class="hint">Pin which coturn <code>/metrics</code> URLs belong to this Jump profile. Leave all unchecked for on-prem-only editors.</p>
      <div id="mapTurns" class="checkbox-grid"></div>
      <label>Notes
        <input type="text" id="mapNotes">
      </label>
    </div>
    <div class="modal-footer">
      <button class="btn" id="btnSaveMap">Save</button>
      <button class="btn btn-secondary" data-modal="modalMap">Cancel</button>
    </div>
  </div>
</div>

<div class="toast-container" id="toastContainer"></div>
<script src="assets/theme.js"></script>
<script src="assets/mapping.js"></script>
</body>
</html>
