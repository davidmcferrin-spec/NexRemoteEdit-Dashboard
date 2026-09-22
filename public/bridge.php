<?php
require_once __DIR__ . '/includes/auth.php';
require_permission('bridge_view');
$user = session_user_payload_full();
$canControl = !empty($user['permissions']['bridge_control']);
$nre_active = 'bridge';
?>
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Bridge — NexEditorStats</title>
  <?php require __DIR__ . '/includes/theme_head.php'; ?>
  <link rel="stylesheet" href="assets/style.css">
</head>
<body>
<?php require __DIR__ . '/includes/nav.php'; ?>
<main class="admin-page">
  <div class="bridge-header">
    <h1 class="page-title">Bridge</h1>
    <div class="service-status" id="svcStatus">
      <span class="service-dot"></span>
      <span class="service-label">Checking…</span>
    </div>
    <?php if ($canControl): ?>
    <div class="bridge-controls">
      <button class="btn btn-sm btn-success" data-svc="start">Start</button>
      <button class="btn btn-sm btn-warning" data-svc="stop">Stop</button>
      <button class="btn btn-sm" data-svc="restart">Restart</button>
    </div>
    <?php endif; ?>
  </div>
  <p class="hint">systemd unit <code>nre-bridge</code>. <code>setup.sh</code> installs <code>/etc/sudoers.d/nre-bridge</code> and an Apache drop-in so PHP can <code>sudo -n</code> inside the sandbox (<code>RestrictSUIDSGID=no</code>, sudoers not on <code>InaccessiblePaths</code>).</p>
  <div class="log-panel">
    <div class="log-toolbar">
      <label>Lines
        <select id="logLines">
          <option>50</option>
          <option selected>100</option>
          <option>200</option>
          <option>500</option>
        </select>
      </label>
      <label class="checkbox-label"><input type="checkbox" id="logPause"> <span>Pause</span></label>
    </div>
    <pre class="log-view" id="logView"></pre>
  </div>
</main>
<script src="assets/theme.js"></script>
<script src="assets/bridge.js"></script>
</body>
</html>
