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

Templates: `deploy/apache-nre.conf`, `deploy/sudoers-nre` (`/etc/sudoers.d/nre-bridge`), `deploy/apache2-nre-sudo.conf` (Apache sandbox: `RestrictSUIDSGID=no` plus sudoers readable — Option B). Env overrides: `NRE_PREFIX`, `NRE_SERVER_NAME`, `NRE_INGEST_PORT`, `NRE_WS_PORT`.

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

The in-app **Telegraf** page has a copy-paste Windows ZIP install (there is no MSI), `telegraf.conf`, ingest URL, and the idle helper. Typical inputs: `cpu`, `mem`, `disk`, `nvidia_smi`, `procstat` (Windows must set `pid_finder = "native"` — the default `pgrep` finder is Unix-only), uptime, Windows logon, plus last input:

```toml
[agent]
  skip_processors_after_aggregators = true

[[inputs.cpu]]
  percpu = false
  totalcpu = true
[[inputs.mem]]
[[inputs.disk]]
[[inputs.system]]          # uptime
[[inputs.nvidia_smi]]
[[inputs.procstat]]
  pid_finder = "native"   # required on Windows; default pgrep is Unix-only
  pattern = ".*"
  # optional: include Windows user on each process
  # pid_tag = true

# Console / logged-on user (DOMAIN\\user)
[[inputs.win_wmi]]
  [[inputs.win_wmi.query]]
    namespace = "ROOT\\CIMV2"
    class_name = "Win32_ComputerSystem"
    properties = ["UserName", "Name"]

# Last input, focused process, and input counts — nre-idle.ps1 in the user session
[[inputs.file]]
  files = ["C:/ProgramData/nre/idle.influx"]
  data_format = "influx"

# Crash / reboot / shutdown / Windows Update / interactive logon
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
    <Query Id="3" Path="Security">
      <Select Path="Security">*[System[(EventID=4634 or EventID=4647 or EventID=4800 or EventID=4801)]]</Select>
      <Select Path="Security">*[System[(EventID=4624)]] and *[EventData[Data[@Name='LogonType']='2' or Data[@Name='LogonType']='7' or Data[@Name='LogonType']='10' or Data[@Name='LogonType']='11']]</Select>
    </Query>
  </QueryList>
  '''
```

Live keeps the last CPU, memory, disk, user, and process list for 24 hours, so a partial Telegraf post cannot blank the card. A bay is marked **stale** when nothing has arrived for 3 minutes. It is shown **with or without** a Jump session. A bay is **active** when `idle_sec` is under Settings → Telegraf → **Active if idle under**. Settings → **Key editing processes** pins Premiere / After Effects / etc. to the top of the process list. History records a work interval for every logged-on editor, including machines that are not in Jump.

**Windows** lists crash (1001), unexpected/forced reboot (41, 6008), user/app reboot or shutdown (1074, 6006, 13), Windows Update (19 / 20 / 43), and interactive logon / logoff / lock / unlock (4624 types 2, 7, 10, 11, plus 4634, 4647, 4800, 4801). Filter by keywords, severity, computer, and kind. Retention is **90 days** with the rest of history. Telegraf must run as Local System to read the System and Security logs.

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

Retention: **90 days** of sessions, work intervals, telemetry, minute rollups, events, and TURN snapshots. Maps and devices are kept.

---

## Pages

| Page | Who |
|---|---|
| Live | Every bay with Telegraf, including on-prem editors with no Jump session. Last-known CPU/mem/GPU/disk, Windows user, focused app, idle/active; stale after 3 minutes instead of disappearing |
| History | Workstation intervals for every editor, Jump sessions, CPU/memory/disk/GPU/idle/active/input charts, focused-app time, pinned-app CPU and memory |
| Events | Searchable log + unmatched section |
| Windows | Crash / reboot / shutdown / update; filter by keyword, severity, computer |
| Mapping | Identity; record of the Jump Desktop TURN profile; TURN pin; IP lookup. On-prem-only bays need no Jump row |
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
- Relay policies in scope: `coturn_then_p2p` and `relay_only`. Jump Desktop **profiles** are the source of truth (coturn first vs Jump relay). Mapping stores that same intent for Live/History; it does not push a profile to Jump.
- On-prem-only editors have no Jump device, profile, or TURN pin. Telegraf + idle helper is enough.
- Jump’s documented default is often P2P-then-relay; we store **your** per-computer policy instead of assuming Jump’s default.
- WebSocket is live-only and unauthenticated — restrict port 8765.

---

## Logs

```bash
journalctl -u nre-bridge -f
```
