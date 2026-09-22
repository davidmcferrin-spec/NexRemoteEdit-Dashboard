<?php
require_once __DIR__ . '/includes/auth.php';
require_permission('events');
$user = session_user_payload_full();
$nre_active = 'windows';
?>
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Windows — NexEditorStats</title>
  <?php require __DIR__ . '/includes/theme_head.php'; ?>
  <link rel="stylesheet" href="assets/style.css">
</head>
<body>
<?php require __DIR__ . '/includes/nav.php'; ?>
<main class="admin-page">
  <h1 class="page-title">Windows events</h1>
  <p class="hint">Crashes, unexpected reboots, user reboots/shutdowns, and Windows Update installs from Telegraf <code>win_eventlog</code>. Kept for 90 days (same retention as other history). This is event-log metadata — not crash dumps.</p>

  <form id="winFilters" class="filter-bar">
    <label>Keywords
      <input type="search" id="winQ" class="search-input" placeholder="bugcheck, KB, username, event ID…">
    </label>
    <label>Computer
      <select id="winHost"><option value="">All computers</option></select>
    </label>
    <label>Severity
      <select id="winSev">
        <option value="">All</option>
        <option value="critical">Critical</option>
        <option value="error">Error</option>
        <option value="warning">Warning</option>
        <option value="info">Info</option>
        <option value="verbose">Verbose</option>
      </select>
    </label>
    <label>Kind
      <select id="winCat">
        <option value="">All</option>
        <option value="crash">Crash / bugcheck</option>
        <option value="unexpected">Forced / unexpected reboot</option>
        <option value="reboot">User / app reboot</option>
        <option value="shutdown">Shutdown</option>
        <option value="update">Windows Update</option>
        <option value="other">Other</option>
      </select>
    </label>
    <button type="submit" class="btn btn-sm">Apply</button>
  </form>

  <section class="admin-section">
    <div class="admin-table-wrap">
      <table class="admin-table" id="winTable">
        <thead>
          <tr>
            <th>Time</th>
            <th>Computer</th>
            <th>Kind</th>
            <th>Severity</th>
            <th>ID</th>
            <th>Source</th>
            <th>User</th>
            <th>Message</th>
          </tr>
        </thead>
        <tbody></tbody>
      </table>
    </div>
  </section>
</main>
<div class="toast-container" id="toastContainer"></div>
<script src="assets/theme.js"></script>
<script src="assets/windows.js"></script>
</body>
</html>
