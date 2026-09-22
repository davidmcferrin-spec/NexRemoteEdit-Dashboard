'use strict';

function tomlString(value) {
  return String(value ?? '').replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}

function telegrafConf(ingestUrl, idleSec, token) {
  const bearer = token ? tomlString(token) : 'YOUR_INGEST_TOKEN';
  return `# NexEditorStats bay agent — interval 30s
[agent]
  interval = "30s"
  round_interval = true
  hostname = ""
  omit_hostname = false
  skip_processors_after_aggregators = true

[[outputs.http]]
  url = "${ingestUrl}"
  method = "POST"
  data_format = "json"
  use_batch_format = true
  [outputs.http.headers]
    Content-Type = "application/json"
    Authorization = "Bearer ${bearer}"

[[inputs.cpu]]
  percpu = false
  totalcpu = true
[[inputs.mem]]
[[inputs.disk]]
  ignore_fs = ["tmpfs", "devtmpfs", "devfs", "iso9660", "overlay", "aufs", "squashfs"]
[[inputs.system]]
# Uncomment on NVIDIA edit bays
# [[inputs.nvidia_smi]]

# Windows has no pgrep — native finder is required (default is pgrep)
[[inputs.procstat]]
  pid_finder = "native"
  pattern = ".*"

[[inputs.win_wmi]]
  [[inputs.win_wmi.query]]
    namespace = "ROOT\\\\CIMV2"
    class_name = "Win32_ComputerSystem"
    properties = ["UserName", "Name"]

# Written by nre-idle.ps1 (user-session scheduled task)
[[inputs.file]]
  files = ["C:/ProgramData/nre/idle.influx"]
  data_format = "influx"

# Crash / reboot / shutdown / Windows Update (System log needs Local System / admin)
[[inputs.win_eventlog]]
  from_beginning = false
  event_size_limit = "64KB"
  xpath_query = '''
  <QueryList>
    <Query Id="0" Path="System">
      <Select Path="System">*[System[(EventID=13 or EventID=41 or EventID=1074 or EventID=6006 or EventID=6008 or EventID=1001)]]</Select>
    </Query>
    <Query Id="1" Path="Application">
      <Select Path="Application">*[System[(EventID=1001)]]</Select>
    </Query>
    <Query Id="2" Path="Microsoft-Windows-WindowsUpdateClient/Operational">
      <Select Path="Microsoft-Windows-WindowsUpdateClient/Operational">*[System[(EventID=19 or EventID=20 or EventID=43)]]</Select>
    </Query>
    <Query Id="3" Path="Security">
      <Select Path="Security">*[System[(EventID=4634 or EventID=4647 or EventID=4800 or EventID=4801)]]</Select>
      <Select Path="Security">*[System[(EventID=4624)]] and *[EventData[Data[@Name='LogonType']='2' or Data[@Name='LogonType']='7' or Data[@Name='LogonType']='10' or Data[@Name='LogonType']='11']]</Select>
    </Query>
  </QueryList>
  '''
`;
}

const TELEGRAF_WIN_VER = '1.40.1';

function zipInstallScript() {
  const ver = TELEGRAF_WIN_VER;
  const zip = `telegraf-${ver}_windows_amd64.zip`;
  return `# Elevated PowerShell. Official package is a ZIP — there is no MSI.
$ver = '${ver}'
$zip = "telegraf-${ver}_windows_amd64.zip"
$dest = 'C:\\Program Files\\InfluxData\\telegraf'
New-Item -ItemType Directory -Path $dest -Force | Out-Null
Invoke-WebRequest "https://dl.influxdata.com/telegraf/releases/$zip" -UseBasicParsing -OutFile "$env:TEMP\\$zip"
Expand-Archive "$env:TEMP\\$zip" -DestinationPath $dest -Force
Get-ChildItem $dest -Recurse -Filter telegraf.exe | Select-Object -First 1 | ForEach-Object {
  if ($_.DirectoryName -ne $dest) { Copy-Item $_.FullName (Join-Path $dest 'telegraf.exe') -Force }
}
Write-Host "telegraf.exe is in $dest — next: save telegraf.conf there (step 3), then step 6."
`;
}

function startServiceScript() {
  return `$dest = 'C:\\Program Files\\InfluxData\\telegraf'
Set-Location $dest
if (-not (Get-Service telegraf -ErrorAction SilentlyContinue)) {
  .\\telegraf.exe --service install --config "$dest\\telegraf.conf"
}
.\\telegraf.exe --service start
Get-Service telegraf
Get-Content "$dest\\telegraf.conf" | Select-Object -First 8
`;
}

function idleTaskScript(scriptUrl, idleSec) {
  return `$dir = 'C:\\ProgramData\\nre'
New-Item -ItemType Directory -Path $dir -Force | Out-Null
icacls $dir /grant Users:M | Out-Null
Invoke-WebRequest -Uri '${scriptUrl}' -OutFile "$dir\\nre-idle.ps1" -UseBasicParsing

$action = New-ScheduledTaskAction -Execute 'powershell.exe' \`
  -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \`"$dir\\nre-idle.ps1\`" -Loop -IntervalSec 15 -ActiveSeconds ${idleSec}"
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName 'NexEditorStats-Idle' -Action $action -Trigger $trigger -Settings $settings -RunLevel Limited -Force
Start-ScheduledTask -TaskName 'NexEditorStats-Idle'`;
}

function fill(meta) {
  const url = meta.ingest_url || 'http://nre.yourdomain.local/api/telemetry/ingest';
  const scriptUrl = meta.idle_script_url || 'assets/nre-idle.ps1';
  const idle = meta.idle_active_seconds || 120;
  document.getElementById('telConf').textContent = telegrafConf(url, idle, meta.ingest_token);
  const zipEl = document.getElementById('telZip');
  if (zipEl) zipEl.textContent = zipInstallScript();
  const startEl = document.getElementById('telStart');
  if (startEl) startEl.textContent = startServiceScript();
  document.getElementById('idleTask').textContent = idleTaskScript(scriptUrl, idle);
  const link = document.getElementById('idleScriptLink');
  if (link) link.href = scriptUrl;
  const th = document.getElementById('idleThreshold');
  if (th) th.textContent = String(idle);
  const st = document.getElementById('tokenStatus');
  if (st) {
    st.textContent = meta.token_set
      ? 'Ingest token is included in the telegraf.conf below. Copy step 3 as-is.'
      : 'No ingest token yet — an admin must generate one in Settings before bays can connect.';
  }
}

function copyTextFallback(text) {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.top = '0';
  ta.style.left = '-9999px';
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  let ok = false;
  try {
    ok = document.execCommand('copy');
  } finally {
    ta.remove();
  }
  if (!ok) throw new Error('copy failed');
}

async function copyText(text) {
  // Run during the click. clipboard.writeText is blocked on http:// LAN hosts.
  try {
    copyTextFallback(text);
    return;
  } catch (e) { /* try the async API while the click is still active */ }
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return;
  }
  throw new Error('copy failed');
}

document.querySelectorAll('[data-copy]').forEach(btn => {
  btn.addEventListener('click', async () => {
    const el = document.getElementById(btn.dataset.copy);
    if (!el) return;
    try {
      await copyText(el.textContent);
      toast('success', 'Copied');
    } catch (e) {
      toast('error', 'Copy failed');
    }
  });
});

(async function load() {
  try {
    const r = await fetch('api/telegraf.php');
    const d = await r.json();
    if (!d.ok) throw new Error(d.error || 'load failed');
    fill(d);
  } catch (e) {
    fill({});
    toast('error', e.message || 'Could not load ingest URL');
  }
})();
