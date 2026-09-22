<?php
require_once __DIR__ . '/includes/auth.php';
require_permission('events');
$user = session_user_payload_full();
$nre_active = 'events';
?>
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Events — NexEditorStats</title>
  <?php require __DIR__ . '/includes/theme_head.php'; ?>
  <link rel="stylesheet" href="assets/style.css">
</head>
<body>
<?php require __DIR__ . '/includes/nav.php'; ?>
<main class="admin-page">
  <h1 class="page-title">Event log</h1>
  <form id="evFilters" class="filter-bar">
    <label>Search <input type="search" id="evQ" class="search-input" placeholder="host, user, kind…"></label>
    <label>Kind
      <select id="evKind">
        <option value="">All</option>
        <option value="unmatched">Unmatched only</option>
        <option value="session_start">session_start</option>
        <option value="session_end">session_end</option>
        <option value="host_seen">host_seen</option>
        <option value="telemetry_no_device">telemetry_no_device</option>
        <option value="device_unmapped">device_unmapped</option>
        <option value="ingest_reject">ingest_reject</option>
        <option value="poller_error">poller_error</option>
      </select>
    </label>
    <button type="submit" class="btn btn-sm">Apply</button>
  </form>

  <section class="admin-section" id="unmatchedSection">
    <h2>Unmatched</h2>
    <p class="hint">Correlation misses — never dropped. Useful for debugging mapping and ingest.</p>
    <div class="admin-table-wrap">
      <table class="admin-table" id="unmatchedTable">
        <thead><tr><th>Time</th><th>Kind</th><th>Host</th><th>Detail</th></tr></thead>
        <tbody></tbody>
      </table>
    </div>
  </section>

  <section class="admin-section">
    <h2>All events</h2>
    <div class="admin-table-wrap">
      <table class="admin-table" id="evTable">
        <thead><tr><th>Time</th><th>Kind</th><th>Host</th><th>Session</th><th>Detail</th></tr></thead>
        <tbody></tbody>
      </table>
    </div>
  </section>
</main>
<div class="toast-container" id="toastContainer"></div>
<script src="assets/theme.js"></script>
<script src="assets/events.js"></script>
</body>
</html>
