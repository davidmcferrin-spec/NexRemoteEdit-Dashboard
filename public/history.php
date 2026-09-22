<?php
require_once __DIR__ . '/includes/auth.php';
require_permission('history');
$user = session_user_payload_full();
$nre_active = 'history';
?>
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>History — NexEditorStats</title>
  <?php require __DIR__ . '/includes/theme_head.php'; ?>
  <link rel="stylesheet" href="assets/style.css">
</head>
<body>
<?php require __DIR__ . '/includes/nav.php'; ?>
<main class="admin-page">
  <h1 class="page-title">History</h1>
  <p class="hint">Last 90 days for every editor, including bays that are not in Jump. Work intervals come from the Windows user on the machine. Jump sessions are the remote overlay. Pick a host to chart CPU, memory, disk, GPU, idle, and input.</p>
  <form id="histFilters" class="filter-bar">
    <label>Host
      <select id="histHost"><option value="">All mapped hosts</option></select>
    </label>
    <label>From
      <input type="datetime-local" id="histFrom">
    </label>
    <label>To
      <input type="datetime-local" id="histTo">
    </label>
    <button type="submit" class="btn btn-sm">Apply</button>
  </form>

  <section class="admin-section">
    <h2>At the workstation</h2>
    <p class="hint">Who was logged on, whether they were at the console, on RDP, or coming in through Jump, and how long the keyboard was active.</p>
    <div class="admin-table-wrap">
      <table class="admin-table" id="workTable">
        <thead>
          <tr>
            <th>Start</th><th>End</th><th>Host</th><th>Windows user</th>
            <th>Where</th><th>Active</th><th>Focused app</th><th>Jump user</th>
          </tr>
        </thead>
        <tbody></tbody>
      </table>
    </div>
  </section>

  <section class="admin-section">
    <h2>Jump sessions</h2>
    <div class="admin-table-wrap">
      <table class="admin-table" id="sessTable">
        <thead>
          <tr>
            <th>Start</th><th>Host</th><th>User</th><th>Duration</th>
            <th>Transport</th><th>Client IP</th>
          </tr>
        </thead>
        <tbody></tbody>
      </table>
    </div>
  </section>

  <section class="admin-section">
    <h2>Host performance</h2>
    <div class="chart-grid">
      <div class="chart-card"><h3>CPU %</h3><canvas id="chartCpu" width="640" height="180"></canvas></div>
      <div class="chart-card"><h3>Memory %</h3><canvas id="chartMem" width="640" height="180"></canvas></div>
      <div class="chart-card"><h3>Tightest disk free %</h3><canvas id="chartDisk" width="640" height="180"></canvas></div>
      <div class="chart-card"><h3>GPU util %</h3><canvas id="chartGpu" width="640" height="180"></canvas></div>
      <div class="chart-card"><h3>Idle seconds</h3><canvas id="chartIdle" width="640" height="180"></canvas></div>
      <div class="chart-card"><h3>Active %</h3><canvas id="chartActive" width="640" height="180"></canvas></div>
      <div class="chart-card"><h3>Input events</h3><canvas id="chartInput" width="640" height="180"></canvas></div>
    </div>
  </section>

  <section class="admin-section">
    <h2>Focused app</h2>
    <p class="hint">Minutes the logged-on user had each process in the foreground. Requires the idle helper.</p>
    <div class="admin-table-wrap">
      <table class="admin-table" id="dwellTable">
        <thead><tr><th>Process</th><th>Minutes</th></tr></thead>
        <tbody></tbody>
      </table>
    </div>
  </section>

  <section class="admin-section">
    <h2>Pinned edit apps</h2>
    <p class="hint">Premiere, After Effects, and the other apps on the Settings watch list. CPU is the average while the app was running; memory is the peak working set.</p>
    <div class="admin-table-wrap">
      <table class="admin-table" id="appTable">
        <thead><tr><th>Process</th><th>Avg CPU</th><th>Peak memory</th></tr></thead>
        <tbody></tbody>
      </table>
    </div>
  </section>

  <section class="admin-section">
    <h2>TURN throughput</h2>
    <div class="chart-card"><h3>Relay bytes (cumulative counters)</h3><canvas id="chartTurn" width="960" height="200"></canvas></div>
  </section>
</main>
<div class="toast-container" id="toastContainer"></div>
<script src="assets/theme.js"></script>
<script src="assets/charts.js"></script>
<script src="assets/history.js"></script>
</body>
</html>
