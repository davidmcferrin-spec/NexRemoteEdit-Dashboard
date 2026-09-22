# NexEditorStats idle helper — last mouse/keyboard input.
# MUST run in the interactive Windows session (scheduled task:
# "Run only when user is logged on"). Telegraf-as-service cannot call
# GetLastInputInfo correctly from session 0.
#
# Writes Influx line protocol to C:\ProgramData\nre\idle.influx
# Telegraf [[inputs.file]] picks it up. The dashboard derives `active`
# from idle_sec vs Settings → "Active if idle under".

param(
    [switch]$Loop,
    [int]$IntervalSec = 15,
    [int]$ActiveSeconds = 120,
    [string]$OutFile = 'C:\ProgramData\nre\idle.influx',
    [switch]$Stdout
)

if (-not ('NreIdle' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class NreIdle {
    [StructLayout(LayoutKind.Sequential)]
    public struct LASTINPUTINFO {
        public uint cbSize;
        public uint dwTime;
    }
    [DllImport("user32.dll")]
    public static extern bool GetLastInputInfo(ref LASTINPUTINFO plii);
    public static int IdleSec() {
        LASTINPUTINFO info = new LASTINPUTINFO();
        info.cbSize = (uint)Marshal.SizeOf(typeof(LASTINPUTINFO));
        if (!GetLastInputInfo(ref info)) return -1;
        uint idle = unchecked((uint)Environment.TickCount - info.dwTime);
        return (int)(idle / 1000u);
    }
}
'@
}

function Write-NreIdle {
    $idle = [NreIdle]::IdleSec()
    if ($idle -lt 0) { return }
    $active = if ($idle -lt $ActiveSeconds) { 1 } else { 0 }
    $hostName = $env:COMPUTERNAME
    if (-not $hostName) { $hostName = 'unknown' }
    $line = "nre_idle,host=$hostName idle_sec=${idle}i,active=${active}i"
    if ($Stdout) {
        Write-Output $line
        return
    }
    $dir = Split-Path -Parent $OutFile
    if (-not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    Set-Content -LiteralPath $OutFile -Value $line -Encoding ascii
}

if ($Loop) {
    while ($true) {
        Write-NreIdle
        Start-Sleep -Seconds ([Math]::Max(5, $IntervalSec))
    }
} else {
    Write-NreIdle
}
