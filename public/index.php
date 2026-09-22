<?php
require_once __DIR__ . '/includes/auth.php';
require_once __DIR__ . '/includes/config.php';
require_permission('dashboard');

$user = session_user_payload_full();
$canHistory = !empty($user['permissions']['history']);
$cfg = nre_load_config();
$ws_host = getenv('NRE_WS_HOST') ?: $_SERVER['HTTP_HOST'];
$ws_host = preg_replace('/:\d+$/', '', $ws_host);
$ws_port = (int)($cfg['bridge']['ws_port'] ?? 8765);
$ws_url = "ws://{$ws_host}:{$ws_port}";
$nre_active = 'live';
$isKiosk = !empty($user['is_kiosk']);
?>
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Live — NexEditorStats</title>
  <?php require __DIR__ . '/includes/theme_head.php'; ?>
  <link rel="stylesheet" href="assets/style.css">
</head>
<body class="<?= $isKiosk ? 'kiosk-mode' : '' ?>">
<?php require __DIR__ . '/includes/nav.php'; ?>

<main class="dashboard" id="dashboard">
  <div class="page-toolbar">
    <div>
      <h1 class="page-title">Live bays</h1>
      <p class="hint" id="unmatchedBanner"></p>
    </div>
    <input type="search" id="searchInput" class="search-input" placeholder="Filter host, Windows user, process, active, idle…" autocomplete="off">
  </div>
  <div class="group-hosts" id="sessionGrid">
    <div class="loading-splash" id="loadingSplash">
      <div class="spinner"></div>
      <p>Connecting to bridge…</p>
    </div>
  </div>
</main>

<div class="modal-overlay" id="procModal" hidden>
  <div class="modal modal-procs" role="dialog" aria-modal="true" aria-labelledby="procTitle">
    <div class="modal-header">
      <div>
        <h2 id="procTitle">Processes</h2>
        <p class="proc-sub" id="procSub"></p>
      </div>
      <button type="button" class="modal-close" id="procClose" aria-label="Close">✕</button>
    </div>
    <div class="proc-table-wrap" id="procScroll">
      <table class="admin-table" id="procTable">
        <thead>
          <tr>
            <th>Process</th>
            <th>User</th>
            <th class="num">CPU</th>
            <th class="num">Memory</th>
          </tr>
        </thead>
        <tbody></tbody>
      </table>
    </div>
  </div>
</div>

<?php if (!empty($user['must_change_password'])): ?>
<div class="modal-overlay" id="modalPw">
  <div class="modal">
    <div class="modal-header"><h2>Change password</h2></div>
    <div class="modal-body">
      <p class="hint">First login — set a new local password (6+ characters).</p>
      <label>Current password <input type="password" id="pwCurrent"></label>
      <label>New password <input type="password" id="pwNew"></label>
    </div>
    <div class="modal-footer">
      <button class="btn" id="btnChangePw">Save</button>
    </div>
  </div>
</div>
<?php endif; ?>

<div class="toast-container" id="toastContainer"></div>
<script>window.NRE_WS_URL = <?= json_encode($ws_url) ?>; window.NRE_CAN_HISTORY = <?= $canHistory ? 'true' : 'false' ?>;</script>
<script src="assets/theme.js"></script>
<script src="assets/app.js"></script>
</body>
</html>
