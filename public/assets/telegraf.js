'use strict';

function telegrafConf(ingestUrl, idleSec) {
  return `# NexEditorStats bay agent — interval 30s
[agent]
  interval = "30s"
  round_interval = true
  hostname = ""
  omit_hostname = false

[[outputs.http]]
  url = "${ingestUrl}"
  method = "POST"
  data_format = "json"
  use_batch_format = true
  [outputs.http.headers]
    Content-Type = "application/json"
    Authorization = "Bearer YOUR_INGEST_TOKEN"

[[inputs.cpu]]
  percpu = false
  totalcpu = true
[[inputs.mem]]
[[inputs.disk]]
  ignore_fs = ["tmpfs", "devtmpfs", "devfs", "iso9660", "overlay", "aufs", "squashfs"]
[[inputs.system]]
# Uncomment on NVIDIA edit bays
# [[inputs.nvidia_smi]]

[[inputs.procstat]]
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
  </QueryList>
  '''
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
  document.getElementById('telConf').textContent = telegrafConf(url, idle);
  document.getElementById('idleTask').textContent = idleTaskScript(scriptUrl, idle);
  const link = document.getElementById('idleScriptLink');
  if (link) link.href = scriptUrl;
  const th = document.getElementById('idleThreshold');
  if (th) th.textContent = String(idle);
  const st = document.getElementById('tokenStatus');
  if (st) {
    st.textContent = meta.token_set
      ? 'Ingest token is set. Paste it into the config as YOUR_INGEST_TOKEN.'
      : 'No ingest token yet — an admin must generate one in Settings before bays can connect.';
  }
}

document.querySelectorAll('[data-copy]').forEach(btn => {
  btn.addEventListener('click', async () => {
    const el = document.getElementById(btn.dataset.copy);
    if (!el) return;
    try {
      await navigator.clipboard.writeText(el.textContent);
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
