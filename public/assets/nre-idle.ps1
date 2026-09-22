# NexEditorStats presence helper.
# MUST run in the interactive Windows session (scheduled task:
# "Run only when user is logged on"). Telegraf-as-service cannot see
# the foreground window or last input from session 0.
#
# Writes Influx line protocol to C:\ProgramData\nre\idle.influx:
#   idle_sec, active, foreground process name,
#   mouse moves, clicks, key-down counts (no keystroke content).
# If a low-level input hook cannot be installed, pulses counts
# each time GetLastInputInfo changes instead.

param(
    [switch]$Loop,
    [int]$IntervalSec = 15,
    [int]$ActiveSeconds = 120,
    [string]$OutFile = 'C:\ProgramData\nre\idle.influx',
    [switch]$Stdout
)

if (-not ('NreActivity' -as [type])) {
    Add-Type -ReferencedAssemblies System.Windows.Forms -TypeDefinition @'
using System;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;

public static class NreActivity {
    const int WH_MOUSE_LL = 14;
    const int WH_KEYBOARD_LL = 13;
    const int WM_MOUSEMOVE = 0x0200;
    const int WM_LBUTTONDOWN = 0x0201;
    const int WM_RBUTTONDOWN = 0x0204;
    const int WM_MBUTTONDOWN = 0x0207;
    const int WM_KEYDOWN = 0x0100;
    const int WM_SYSKEYDOWN = 0x0104;

    static int mouseMoves, clicks, keys, pulses;
    static uint lastInputTick;
    static bool sawTick;
    static IntPtr mouseHook, keyHook;
    static HookProc mouseProc, keyProc;
    static bool installed;

    delegate IntPtr HookProc(int nCode, IntPtr wParam, IntPtr lParam);

    [StructLayout(LayoutKind.Sequential)]
    public struct LASTINPUTINFO {
        public uint cbSize;
        public uint dwTime;
    }

    [DllImport("user32.dll")]
    static extern IntPtr SetWindowsHookEx(int id, HookProc proc, IntPtr inst, uint thread);
    [DllImport("user32.dll")]
    static extern IntPtr CallNextHookEx(IntPtr hook, int nCode, IntPtr wParam, IntPtr lParam);
    [DllImport("user32.dll")]
    public static extern bool GetLastInputInfo(ref LASTINPUTINFO plii);
    [DllImport("user32.dll")]
    static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")]
    static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
    [DllImport("kernel32.dll", CharSet = CharSet.Auto)]
    static extern IntPtr GetModuleHandle(string name);

    public static int IdleSec() {
        LASTINPUTINFO info = new LASTINPUTINFO();
        info.cbSize = (uint)Marshal.SizeOf(typeof(LASTINPUTINFO));
        if (!GetLastInputInfo(ref info)) return -1;
        uint idle = unchecked((uint)Environment.TickCount - info.dwTime);
        return (int)(idle / 1000u);
    }

    public static void NoteIdlePulse() {
        LASTINPUTINFO info = new LASTINPUTINFO();
        info.cbSize = (uint)Marshal.SizeOf(typeof(LASTINPUTINFO));
        if (!GetLastInputInfo(ref info)) return;
        if (!sawTick) {
            lastInputTick = info.dwTime;
            sawTick = true;
            return;
        }
        if (info.dwTime != lastInputTick) {
            lastInputTick = info.dwTime;
            Interlocked.Increment(ref pulses);
        }
    }

    public static bool TryInstall() {
        if (installed) return true;
        var ready = new ManualResetEvent(false);
        var pump = new Thread(() => {
            try {
                mouseProc = MouseHook;
                keyProc = KeyHook;
                string modName = Process.GetCurrentProcess().MainModule.ModuleName;
                IntPtr mod = GetModuleHandle(modName);
                mouseHook = SetWindowsHookEx(WH_MOUSE_LL, mouseProc, mod, 0);
                keyHook = SetWindowsHookEx(WH_KEYBOARD_LL, keyProc, mod, 0);
                installed = mouseHook != IntPtr.Zero && keyHook != IntPtr.Zero;
                ready.Set();
                if (installed) Application.Run();
            } catch {
                ready.Set();
            }
        });
        pump.IsBackground = true;
        pump.SetApartmentState(ApartmentState.STA);
        pump.Start();
        ready.WaitOne(3000);
        return installed;
    }

    static IntPtr MouseHook(int nCode, IntPtr wParam, IntPtr lParam) {
        if (nCode >= 0) {
            int msg = wParam.ToInt32();
            if (msg == WM_MOUSEMOVE) Interlocked.Increment(ref mouseMoves);
            else if (msg == WM_LBUTTONDOWN || msg == WM_RBUTTONDOWN || msg == WM_MBUTTONDOWN)
                Interlocked.Increment(ref clicks);
        }
        return CallNextHookEx(mouseHook, nCode, wParam, lParam);
    }

    static IntPtr KeyHook(int nCode, IntPtr wParam, IntPtr lParam) {
        if (nCode >= 0) {
            int msg = wParam.ToInt32();
            if (msg == WM_KEYDOWN || msg == WM_SYSKEYDOWN) Interlocked.Increment(ref keys);
        }
        return CallNextHookEx(keyHook, nCode, wParam, lParam);
    }

    public static int[] TakeCounts() {
        return new int[] {
            Math.Min(100000, Interlocked.Exchange(ref mouseMoves, 0)),
            Math.Min(100000, Interlocked.Exchange(ref clicks, 0)),
            Math.Min(100000, Interlocked.Exchange(ref keys, 0)),
            Math.Min(100000, Interlocked.Exchange(ref pulses, 0))
        };
    }

    public static string ForegroundProcess() {
        try {
            IntPtr hwnd = GetForegroundWindow();
            if (hwnd == IntPtr.Zero) return "";
            uint pid;
            GetWindowThreadProcessId(hwnd, out pid);
            if (pid == 0) return "";
            using (Process proc = Process.GetProcessById((int)pid)) {
                string name = proc.ProcessName ?? "";
                if (name.Length > 80) name = name.Substring(0, 80);
                return name.Replace("\"", "").Replace("\r", "").Replace("\n", "");
            }
        } catch {
            return "";
        }
    }
}
'@
}

function ConvertTo-InfluxField {
    param([string]$Value)
    if (-not $Value) { return '' }
    return ($Value -replace '\\', '\\' -replace '"', '\"')
}

$script:Hooked = $false
try {
    $script:Hooked = [NreActivity]::TryInstall()
} catch {
    $script:Hooked = $false
}

function Write-NreIdle {
    $idle = [NreActivity]::IdleSec()
    if ($idle -lt 0) { return }
    $active = if ($idle -lt $ActiveSeconds) { 1 } else { 0 }
    $counts = [NreActivity]::TakeCounts()
    $mouse = $counts[0]; $clicks = $counts[1]; $keys = $counts[2]; $pulses = $counts[3]
    $fg = [NreActivity]::ForegroundProcess()
    $fgField = ConvertTo-InfluxField $fg
    $hostName = $env:COMPUTERNAME
    if (-not $hostName) { $hostName = 'unknown' }
    $hostTag = ($hostName -replace ' ', '\ ' -replace ',', '\,')
    $line = "nre_idle,host=$hostTag idle_sec=${idle}i,active=${active}i,mouse=${mouse}i,clicks=${clicks}i,keys=${keys}i,pulses=${pulses}i"
    if ($fgField) { $line += ",foreground=`"$fgField`"" }
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

function Wait-NreInterval {
    param([int]$Seconds)
    $wait = [Math]::Max(5, $Seconds)
    $elapsed = 0
    while ($elapsed -lt $wait) {
        if (-not $script:Hooked) { [NreActivity]::NoteIdlePulse() }
        Start-Sleep -Seconds 1
        $elapsed++
    }
}

if ($Loop) {
    while ($true) {
        Write-NreIdle
        Wait-NreInterval -Seconds $IntervalSec
    }
} else {
    if (-not $script:Hooked) { [NreActivity]::NoteIdlePulse() }
    Write-NreIdle
}
