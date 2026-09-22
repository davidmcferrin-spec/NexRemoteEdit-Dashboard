<?php
require_once __DIR__ . '/includes/auth.php';
require_permission('settings');
$user = session_user_payload_full();
$nre_active = 'settings';
?>
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Settings — NexEditorStats</title>
  <?php require __DIR__ . '/includes/theme_head.php'; ?>
  <link rel="stylesheet" href="assets/style.css">
</head>
<body>
<?php require __DIR__ . '/includes/nav.php'; ?>
<main class="admin-page">
  <h1 class="page-title">Settings</h1>
  <p class="hint">All application settings are stored in <code>data/config.json</code> on this server. There is no <code>.env</code> file. Secrets are never shown in full after save. The bridge reloads this file within ~10 seconds.</p>

  <section class="admin-section">
    <h2>PostgreSQL</h2>
    <form id="pgForm" class="admin-form">
      <div class="admin-form-grid">
        <label>Host <input type="text" id="pgHost"></label>
        <label>Port <input type="number" id="pgPort"></label>
        <label>Database <input type="text" id="pgDb"></label>
        <label>User <input type="text" id="pgUser"></label>
        <label>Password <span class="hint-inline" id="pgPassHint"></span>
          <input type="password" id="pgPass" placeholder="leave blank to keep" autocomplete="new-password">
        </label>
      </div>
      <button type="submit" class="btn btn-sm">Save PostgreSQL</button>
    </form>
  </section>

  <section class="admin-section">
    <h2>Jump Desktop for Teams</h2>
    <form id="jumpForm" class="admin-form">
      <label>API base URL <input type="text" id="jumpBase"></label>
      <label>Team ID <input type="text" id="jumpTeam" placeholder="T-…"></label>
      <label>API token <span class="hint-inline" id="jumpTokHint">read-only token recommended</span>
        <input type="password" id="jumpToken" placeholder="leave blank to keep" autocomplete="new-password">
      </label>
      <button type="submit" class="btn btn-sm">Save Jump API</button>
    </form>
  </section>

  <section class="admin-section">
    <h2>Telegraf ingest</h2>
    <p class="hint">Internal-only. Apache should also <code>Require ip</code> these CIDRs. Token is sent as <code>Authorization: Bearer</code>.</p>
    <form id="telForm" class="admin-form">
      <label>Ingest token <span class="hint-inline" id="telTokHint"></span>
        <input type="text" id="telToken" placeholder="leave blank to keep">
      </label>
      <div class="admin-toolbar">
        <button type="button" class="btn btn-sm btn-secondary" id="btnGenToken">Generate new token</button>
      </div>
      <label>Allowed CIDRs <span class="hint-inline">one per line</span>
        <textarea id="telCidrs" rows="5" class="admin-textarea"></textarea>
      </label>
      <label>Active if idle under (seconds)
        <input type="number" id="telIdleActive" min="5" max="86400">
      </label>
      <p class="hint">Live marks a bay <strong>active</strong> when last mouse/keyboard input is newer than this. See <a href="telegraf.php">Telegraf</a> to install the idle helper on each bay.</p>
      <button type="submit" class="btn btn-sm">Save Telegraf</button>
    </form>
  </section>

  <section class="admin-section">
    <h2>Key editing processes</h2>
    <p class="hint">These names are pinned to the top of each bay’s process list and shown as chips on Live. Match is a case-insensitive substring of the Windows process / exe name (e.g. <code>premiere</code> matches <code>Adobe Premiere Pro.exe</code>).</p>
    <div class="admin-toolbar">
      <button type="button" class="btn btn-sm" id="btnAddWatch">+ Add process</button>
    </div>
    <div id="watchList"></div>
    <button type="button" class="btn btn-sm" id="btnSaveWatch">Save process watchlist</button>
  </section>

  <section class="admin-section">
    <h2>TURN servers</h2>
    <p class="hint">Prometheus <code>/metrics</code> URLs only. Add one row per coturn instance.</p>
    <div class="admin-toolbar">
      <button type="button" class="btn btn-sm" id="btnAddTurn">+ Add TURN server</button>
    </div>
    <div id="turnList"></div>
    <button type="button" class="btn btn-sm" id="btnSaveTurns">Save TURN servers</button>
  </section>

  <section class="admin-section">
    <h2>Bridge</h2>
    <form id="brForm" class="admin-form">
      <div class="admin-form-grid">
        <label>WebSocket port <input type="number" id="brWsPort"></label>
        <label>Ingest bind host <input type="text" id="brIngestHost"></label>
        <label>Ingest port <input type="number" id="brIngestPort"></label>
        <label>Jump poll (sec) <input type="number" id="brJumpPoll"></label>
        <label>TURN poll (sec) <input type="number" id="brTurnPoll"></label>
        <label>Retention (days) <input type="number" id="brRetention"></label>
        <label>Stale session (hours) <input type="number" id="brStale"></label>
      </div>
      <button type="submit" class="btn btn-sm">Save bridge</button>
    </form>
  </section>
</main>
<div class="toast-container" id="toastContainer"></div>
<script src="assets/theme.js"></script>
<script src="assets/settings.js"></script>
</body>
</html>
