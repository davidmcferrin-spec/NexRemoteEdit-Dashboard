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
<main class="admin-page history-page">
  <h1 class="page-title">History</h1>
  <p class="hint">Last 90 days for every editor, including bays that are not in Jump. Pick a host for one timeline: CPU, memory, GPU, who was at the console, Jump, the focused app, process usage, keyboard and mouse, and Windows events.</p>
  <form id="histFilters" class="filter-bar">
    <label>Host
      <select id="histHost"><option value="">All hosts</option></select>
    </label>
    <label>From
      <input type="datetime-local" id="histFrom">
    </label>
    <label>To
      <input type="datetime-local" id="histTo">
    </label>
    <div class="range-presets" role="group" aria-label="Time range">
      <button type="button" class="btn btn-sm btn-secondary" data-range="1h">1 hour</button>
      <button type="button" class="btn btn-sm btn-secondary" data-range="8h">8 hours</button>
      <button type="button" class="btn btn-sm btn-secondary" data-range="24h">24 hours</button>
      <button type="button" class="btn btn-sm btn-secondary" data-range="7d">7 days</button>
    </div>
    <button type="submit" class="btn btn-sm">Apply</button>
  </form>

  <section class="admin-section" id="machineSection">
    <div class="machine-head">
      <h2 id="machineTitle">Machine timeline</h2>
      <div id="procToggle" hidden>
        <span class="proc-toggle-label">Top processes</span>
        <button type="button" id="procCpu" class="btn btn-sm" aria-pressed="true">CPU</button>
        <button type="button" id="procMem" class="btn btn-sm btn-secondary" aria-pressed="false">Memory</button>
      </div>
    </div>
    <p class="hint" id="machineIntro">Select a host to put usage, the Windows session, Jump, the focused app, processes, keyboard and mouse, and Windows events on one timeline.</p>
    <div id="machineLayout" class="machine-layout" hidden>
      <div class="machine-main">
        <div id="tlLegend" class="tl-legend"></div>
        <div class="machine-timeline" id="machineTimeline">
          <canvas id="tlBase" aria-label="Machine timeline"></canvas>
          <canvas id="tlOverlay"></canvas>
          <div id="procLegend" class="tl-legend proc-legend" hidden></div>
          <div id="tlTip" class="tl-tip" hidden></div>
        </div>
        <p class="hint" id="machineHint"></p>
        <h3 class="machine-subhead">Windows events in this range</h3>
        <div class="admin-table-wrap machine-events">
          <table class="admin-table" id="machineEvents">
            <thead>
              <tr>
                <th>Time</th><th>Kind</th><th>Severity</th><th>Event</th><th>User</th><th>Message</th>
              </tr>
            </thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
      <aside class="machine-rail" id="machineRail"></aside>
    </div>
  </section>

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

  <section class="admin-section" id="turnSection">
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
