# NexEditorStats

Browser dashboard that correlates **Jump Desktop for Teams**, **Telegraf** (bay workstations), and **coturn Prometheus `/metrics`** into a live board plus 90 days of history.

Stack matches [xpmon-dashboard](https://github.com/davidmcferrin-spec/xpmon-dashboard): Python asyncio bridge, PHP/Apache, vanilla JS, dark theme. No Docker, Node, Composer, or `.env` file.

---

## Architecture

```
Telegraf (bays)  --POST Bearer-->  Apache /api/telemetry/ingest  -->  Python :8766
coturn × N       --GET /metrics-->  Python scrape
Jump API         --poll 45s----->  Python
                                      |
                                      +--> PostgreSQL (local)   source of truth
                                      +--> WebSocket :8765      live diffs only
PHP /opt/nre-web/public               history / mapping / settings
```

Settings live in **`data/config.json`**, edited in **Settings** (admin UI). Auth lives in **`data/auth.json`**. Neither is a `.env`.

---

## Requirements

- Ubuntu
- Python 3.10+
- PostgreSQL 14+
- Apache 2.4 + PHP 8.2 (`php-pgsql`, `php-ldap` for AD login)
- `websockets` and `psycopg[binary]` (see `bridge/requirements.txt`)

---

## Install

On Ubuntu / Debian, from the repo checkout:

```bash
sudo ./setup.sh            # apt, Postgres, /opt/nre-web, Apache, nre-bridge
sudo ./setup.sh --check    # deploy test (login, ingest, WS, schema)
sudo ./setup.sh update     # rsync code, keep data/, restart bridge
sudo ./setup.sh status
```

The script is idempotent. It writes the Postgres password into `data/config.json` (Settings). No `.env`. Existing `data/config.json` and `data/auth.json` are never overwritten on update.

Templates: `deploy/apache-nre.conf`, `deploy/sudoers-nre`. Env overrides: `NRE_PREFIX`, `NRE_SERVER_NAME`, `NRE_INGEST_PORT`, `NRE_WS_PORT`.

First visit to login creates `data/auth.json` with **`admin` / `admin`** (must change password). Browsers need TCP **8765** for the live WebSocket (trusted subnets only — WS is unauthenticated, same as xpmon). Do not expose ingest to the internet.

---

## First-run setup (no .env)

1. Sign in as `admin` / `admin`, change the password.
2. **Settings**
   - PostgreSQL host/db/user/password
   - Jump team ID + **read-only** API token
   - Telegraf: generate ingest token, confirm internal CIDRs
   - TURN servers: one row per coturn, `http://host:9641/metrics`
3. Restart or wait ~10s — the bridge reloads `data/config.json` by mtime.
4. **Mapping** — Jump devices appear after the first poll. Link Telegraf hostnames if they differ. Set **coturn then P2P** or **Jump relay only** per computer.

Copy `data/config.example.json` only as a reference. The live file is created by Settings or the bridge.

---

## Telegraf (Windows bays)

```toml
[[outputs.http]]
  url = "http://nre.yourdomain.local/api/telemetry/ingest"
  data_format = "json"
  [outputs.http.headers]
    Content-Type = "application/json"
    Authorization = "Bearer <token from Settings>"
```

The in-app **Telegraf** page has a copy-paste `telegraf.conf`, ingest URL, and the idle helper. Typical inputs: `cpu`, `mem`, `disk`, `nvidia_smi`, `procstat`, uptime, Windows logon, plus last input:

```toml
[[inputs.cpu]]
  percpu = false
  totalcpu = true
[[inputs.mem]]
[[inputs.disk]]
[[inputs.system]]          # uptime
[[inputs.nvidia_smi]]
[[inputs.procstat]]
  pattern = ".*"
  # optional: include Windows user on each process
  # pid_tag = true

# Console / logged-on user (DOMAIN\\user)
[[inputs.win_wmi]]
  [[inputs.win_wmi.query]]
    namespace = "ROOT\\CIMV2"
    class_name = "Win32_ComputerSystem"
    properties = ["UserName", "Name"]

# Last mouse/keyboard input — written by nre-idle.ps1 in the user session
[[inputs.file]]
  files = ["C:/ProgramData/nre/idle.influx"]
  data_format = "influx"

# Crash / unexpected reboot / user reboot / shutdown / Windows Update
[[inputs.win_eventlog]]
  from_beginning = false
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
```

Live shows every bay that has sent telemetry in the last ~3 minutes, **with or without** a Jump session. A bay is **active** when `idle_sec` is under Settings → Telegraf → **Active if idle under**. Settings → **Key editing processes** pins Premiere / After Effects / etc. to the top of the process list.

**Windows** lists crash (1001), unexpected/forced reboot (41, 6008), user/app reboot or shutdown (1074, 6006, 13), and Windows Update (19 / 20 / 43). Filter by keywords, severity, computer, and kind. Retention is **90 days** with the rest of history. Telegraf must run as Local System to read the System log.

---

## How the three sources join

| Join | Key |
|---|---|
| Telegraf ↔ Jump computer | `device_maps` (Setup → Mapping). Auto-suggested when hostnames match. |
| Session identity | Jump `connectionID`. Active = incoming / auth-ok with no close. |
| Transport | Per-computer policy. `relay_only` → Relayed. `coturn_then_p2p` → P2P if Jump reports a direct IP, else Unknown. |
| IP → user / computer | Jump `peerInfo.ipAddress` + `directConnectionInfo` stored on the session. Mapping “Who was this IP?” |

**coturn `/metrics` cannot identify a person** (shared long-term credential, no client IP on the exporter). v1 stores **per-server aggregate throughput** only. Per-session TURN bytes / which relay would need log tail later — columns exist, unused.

Unmatched telemetry or devices are **kept** and shown on Events / Mapping. Nothing is silently dropped.

Retention: **90 days** of sessions, telemetry, events, and TURN snapshots. Maps and devices are kept.

---

## Pages

| Page | Who |
|---|---|
| Live | Every bay with Telegraf: Windows user, uptime, idle/active, CPU/mem/GPU/disk, pinned processes; Jump overlay when remoted |
| History | Date + host; session table; canvas charts; TURN counters |
| Events | Searchable log + unmatched section |
| Windows | Crash / reboot / shutdown / update; filter by keyword, severity, computer |
| Mapping | Identity, policy, TURN assignment, IP lookup |
| Telegraf | Install agent + idle helper + win_eventlog; copy ingest config |
| Settings | All app config → `data/config.json` |
| Admin | Users, LDAP, session timeout (`data/auth.json`) |
| Bridge | journalctl + start/stop/restart |

Roles: `admin`, `operator`, `viewer`, `bridge_monitor`, `kiosk`.

---

## Design notes (explicit)

- No `.env`. Settings UI writes JSON. Secrets are masked on GET; a newly generated Telegraf token is shown **once**.
- Ingest is internal-only (CIDR + Bearer).
- Multiple TURN servers, metrics URL only.
- Relay policies in scope: `coturn_then_p2p` and `relay_only`.
- Jump’s documented default is often P2P-then-relay; we store **your** per-computer policy instead of assuming Jump’s default.
- WebSocket is live-only and unauthenticated — restrict port 8765.

---

## Logs

```bash
journalctl -u nre-bridge -f
```
