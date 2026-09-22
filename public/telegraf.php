<?php
require_once __DIR__ . '/includes/auth.php';
require_permission('dashboard');
$user = session_user_payload_full();
$nre_active = 'telegraf';
?>
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Telegraf — NexEditorStats</title>
  <?php require __DIR__ . '/includes/theme_head.php'; ?>
  <link rel="stylesheet" href="assets/style.css">
</head>
<body>
<?php require __DIR__ . '/includes/nav.php'; ?>
<main class="admin-page">
  <h1 class="page-title">Install Telegraf on a bay</h1>
  <p class="hint">Each edit workstation runs Telegraf and posts metrics here. After the first sample the bay appears on <a href="index.php">Live</a> — Jump is not required. Do this once per machine as a local administrator. InfluxData ships Windows Telegraf as a <strong>ZIP</strong>, not an MSI.</p>

  <section class="admin-section">
    <h2>1. Ingest token</h2>
    <p class="hint">An admin generates the Bearer token in Settings → Telegraf ingest. The same token is used on every bay and is filled into the config in step 3.</p>
    <p id="tokenStatus" class="hint"></p>
    <?php if (!empty($user['permissions']['settings'])): ?>
    <p><a class="btn btn-sm" href="settings.php">Open Settings</a></p>
    <?php endif; ?>
  </section>

  <section class="admin-section">
    <h2>2. Download the ZIP and install the service</h2>
    <p class="hint">Official packages are <code>telegraf-&lt;version&gt;_windows_amd64.zip</code> from <a href="https://docs.influxdata.com/telegraf/v1/install/?t=Windows" target="_blank" rel="noopener">Telegraf’s Windows install docs</a> or <a href="https://github.com/influxdata/telegraf/releases/latest" target="_blank" rel="noopener">GitHub Releases</a>. There is no MSI. Run this in an <strong>elevated</strong> PowerShell on the bay. Check the docs for a newer version number if the URL 404s.</p>
    <div class="copy-toolbar">
      <button type="button" class="btn btn-sm" data-copy="telZip">Copy install script</button>
    </div>
    <pre class="copy-block" id="telZip"></pre>
    <p class="hint">Optional: <code>winget install -e --id InfluxData.Telegraf</code> also installs from the same ZIP. Put <code>telegraf.exe</code> and <code>telegraf.conf</code> in <code>C:\Program Files\InfluxData\telegraf</code>, then run the <code>--service install</code> line if winget did not register the service.</p>
    <p class="hint">NVIDIA GPUs: install the NVIDIA driver so <code>nvidia-smi</code> works. Leave <code>inputs.nvidia_smi</code> commented in the config if the bay has no NVIDIA card.</p>
  </section>

  <section class="admin-section">
    <h2>3. telegraf.conf</h2>
    <p class="hint">The Bearer token is filled in from Settings when one exists. Save as <code>C:\Program Files\InfluxData\telegraf\telegraf.conf</code> (overwrite the sample that shipped in the ZIP). The copied config uses <code>pid_finder = "native"</code> so <code>inputs.procstat</code> starts on Windows (the default <code>pgrep</code> finder is Unix-only and will exit the agent). Then start or restart the service (step 6).</p>
    <div class="copy-toolbar">
      <button type="button" class="btn btn-sm" data-copy="telConf">Copy config</button>
    </div>
    <pre class="copy-block" id="telConf"></pre>
  </section>

  <section class="admin-section">
    <h2>4. Idle helper (mouse / keyboard / focused app)</h2>
    <p class="hint">Windows only reports last input and the foreground process from the <strong>logged-on user’s session</strong>. Telegraf running as a service cannot see them. This helper writes idle time, the focused process name, and input counts (moves, clicks, keys — never keystroke content) every 15 seconds. Live marks the bay <strong>active</strong> when idle is under <span id="idleThreshold">120</span> seconds (Settings). The same helper is what History uses for on-prem editors that are not in Jump.</p>
    <ol class="install-steps">
      <li>Download <a id="idleScriptLink" href="assets/nre-idle.ps1" download>nre-idle.ps1</a> and save it to <code>C:\ProgramData\nre\nre-idle.ps1</code>.</li>
      <li>In an elevated PowerShell <strong>on the bay, while logged on as the editor</strong>, register a logon task (it must run in that user’s session, not as the Telegraf service):</li>
    </ol>
    <div class="copy-toolbar">
      <button type="button" class="btn btn-sm" data-copy="idleTask">Copy task script</button>
    </div>
    <pre class="copy-block" id="idleTask"></pre>
    <p class="hint">Sign out and back in (or run the task once) so <code>C:\ProgramData\nre\idle.influx</code> appears. Until then Live shows CPU/memory but no Active/Idle badge.</p>
  </section>

  <section class="admin-section">
    <h2>5. Windows crash / reboot / update events</h2>
    <p class="hint">The copied config includes <code>inputs.win_eventlog</code> for crashes, reboots, shutdowns, Windows Update, and interactive logon / logoff / lock / unlock (Security log). Register the service with <code>telegraf.exe --service install</code> so it runs as <strong>Local System</strong> and can read those logs. Events show on <a href="windows.php">Windows</a> and are kept for 90 days. Logon rows are interactive sessions only (console, unlock, RDP), not service accounts. This is log metadata — not crash dumps. Collection starts from install time (<code>from_beginning = false</code>).</p>
  </section>

  <section class="admin-section">
    <h2>6. Start and confirm</h2>
    <p class="hint">If you already saved <code>telegraf.conf</code>, install (once) and start the Windows service:</p>
    <div class="copy-toolbar">
      <button type="button" class="btn btn-sm" data-copy="telStart">Copy commands</button>
    </div>
    <pre class="copy-block" id="telStart"></pre>
    <ol class="install-steps">
      <li>The bay hostname must be allowed to reach this dashboard (internal CIDRs in Settings).</li>
      <li>Within about a minute the host shows on <a href="index.php">Live</a>. After a reboot or update, the row appears on <a href="windows.php">Windows</a>.</li>
      <li>If the Windows name differs from the Jump computer name, link them on <a href="mapping.php">Mapping</a>.</li>
    </ol>
  </section>
</main>
<div class="toast-container" id="toastContainer"></div>
<script src="assets/theme.js"></script>
<script src="assets/telegraf.js"></script>
</body>
</html>
