"""Config and pure helpers shared by the bridge process."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("nre_bridge")

ROOT = Path(__file__).resolve().parent.parent
if Path("/opt/nre-web/data").is_dir():
    DATA_DIR = Path("/opt/nre-web/data")
    SCHEMA_PATH = Path("/opt/nre-web/schema.sql")
else:
    DATA_DIR = ROOT / "data"
    SCHEMA_PATH = ROOT / "schema.sql"
CONFIG_PATH = DATA_DIR / "config.json"

WS_MAX_MESSAGE_SIZE = 256 * 1024
TEL_EPS = 0.5
LIVE_FRESH_SEC = 180
LIVE_KEEP_HOURS = 24
WORK_GAP_SEC = 20 * 60


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def parse_ts(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n > 1e14:
        n = n / 1e9
    elif n > 1e12:
        n = n / 1e3
    return datetime.fromtimestamp(n, tz=timezone.utc)


def host_key(name: Optional[str]) -> str:
    s = (name or "").strip().lower()
    if not s:
        return ""
    if "." in s:
        s = s.split(".", 1)[0]
    return s


def enabled_watchlist(cfg: dict) -> list[dict]:
    out = []
    for w in cfg.get("process_watchlist") or []:
        if not isinstance(w, dict):
            continue
        if w.get("enabled", True) is False:
            continue
        match = (w.get("match") or "").strip()
        if not match:
            continue
        out.append({
            "label": (w.get("label") or match).strip(),
            "match": match.lower(),
        })
    return out


def process_watch_labels(name: Optional[str], watches: list[dict]) -> list[str]:
    n = (name or "").lower()
    if not n:
        return []
    return [w["label"] for w in watches if w["match"] and w["match"] in n]


def _field(tags: dict, fields: dict, *names: str) -> Any:
    for n in names:
        if n in fields and fields[n] not in (None, ""):
            return fields[n]
        if n in tags and tags[n] not in (None, ""):
            return tags[n]
        low = n.lower()
        for src in (fields, tags):
            for k, v in src.items():
                if str(k).lower() == low and v not in (None, ""):
                    return v
    return None


def classify_win_event(event_id: Optional[int], source: str, message: str) -> str:
    src = (source or "").lower()
    msg = (message or "").lower()
    eid = int(event_id or 0)
    if eid == 1001 or "bugcheck" in src or "bugcheck" in msg:
        return "crash"
    if eid in (41, 6008):
        return "unexpected"
    if eid in (19, 20, 43) or "windowsupdate" in src or "windows update" in src:
        return "update"
    if eid == 6006 or eid == 13:
        return "shutdown"
    if eid == 1074:
        if "shutdown" in msg or "power off" in msg:
            return "shutdown"
        return "reboot"
    if eid == 4624:
        return "logon"
    if eid in (4634, 4647):
        return "logoff"
    if eid == 4800:
        return "lock"
    if eid == 4801:
        return "unlock"
    return "other"


def logon_type(message: str, fields: Optional[dict] = None) -> Optional[int]:
    fields = fields or {}
    raw = fields.get("LogonType") or fields.get("logon_type") or fields.get("Logon Type")
    if raw is not None and str(raw).strip().isdigit():
        return int(str(raw).strip())
    m = re.search(r"Logon Type:\s*(\d+)", message or "", re.I)
    if not m:
        return None
    return int(m.group(1))


def accept_security_event(event_id: Optional[int], message: str, fields: Optional[dict] = None) -> bool:
    """Keep interactive logons. Drop service and network 4624 noise."""
    if int(event_id or 0) != 4624:
        return True
    kind = logon_type(message, fields)
    return kind in (2, 7, 10, 11)


def logon_account(message: str, category: str) -> str:
    text = message or ""
    if category == "logon":
        m = re.search(r"New Logon:\s*.*?Account Name:\s*([^\r\n]+)", text, re.I | re.S)
    else:
        m = re.search(r"Account Name:\s*([^\r\n]+)", text, re.I)
    user = m.group(1).strip() if m else ""
    if "\\" in user:
        user = user.split("\\", 1)[1]
    if user.lower() in ("", "-", "system", "anonymous logon", "local service", "network service"):
        return ""
    return user[:128]


def primary_user(sessions: Optional[list]) -> str:
    for s in sessions or []:
        name = str((s or {}).get("username") or "").strip()
        if name:
            return name[:128]
    return ""


def session_kind_for(sessions: Optional[list], jump_email: str) -> str:
    if jump_email:
        return "jump"
    for s in sessions or []:
        label = str((s or {}).get("session_name") or "").lower()
        if "rdp" in label:
            return "rdp"
    return "local"


def disk_free_pct(disks: Optional[list]) -> Optional[float]:
    vals = []
    for d in disks or []:
        if not isinstance(d, dict):
            continue
        used = d.get("used_pct")
        if used is not None:
            try:
                vals.append(max(0.0, min(100.0, 100.0 - float(used))))
                continue
            except (TypeError, ValueError):
                pass
        free, total = d.get("free"), d.get("total")
        try:
            if free is not None and total:
                vals.append(max(0.0, min(100.0, 100.0 * float(free) / float(total))))
        except (TypeError, ValueError, ZeroDivisionError):
            continue
    if not vals:
        return None
    return round(min(vals), 2)


def watched_brief(procs: Optional[list]) -> list[dict]:
    out = []
    for p in procs or []:
        if not isinstance(p, dict) or not p.get("watch"):
            continue
        out.append({
            "name": p.get("name"),
            "cpu": p.get("cpu"),
            "rss": p.get("rss"),
            "user": p.get("user") or "",
            "watch": p.get("watch") or [],
        })
        if len(out) >= 12:
            break
    return out


def merge_telemetry(prev: Optional[dict], sample: dict) -> dict:
    """Fill gaps from the previous reading. Interval counters are not carried."""
    prev = prev or {}
    out = {
        "cpu_pct": sample.get("cpu_pct"),
        "mem_pct": sample.get("mem_pct"),
        "mem_used_bytes": sample.get("mem_used_bytes"),
        "mem_total_bytes": sample.get("mem_total_bytes"),
        "disk": list(sample.get("disk") or []),
        "gpu": list(sample.get("gpu") or []),
        "processes": list(sample.get("processes") or []),
        "uptime_sec": sample.get("uptime_sec"),
        "windows_sessions": list(sample.get("windows_sessions") or []),
        "idle_sec": sample.get("idle_sec"),
        "active": sample.get("active"),
        "foreground_app": (sample.get("foreground_app") or "")[:80],
        "input_mouse": sample.get("input_mouse"),
        "input_clicks": sample.get("input_clicks"),
        "input_keys": sample.get("input_keys"),
        "input_pulses": sample.get("input_pulses"),
        "user_cleared": bool(prev.get("user_cleared")),
    }
    for key in ("cpu_pct", "mem_pct", "mem_used_bytes", "mem_total_bytes", "uptime_sec", "idle_sec"):
        if out.get(key) is None and prev.get(key) is not None:
            out[key] = prev[key]
    for key in ("disk", "gpu", "processes"):
        if out[key]:
            continue
        if key == "processes" and sample.get("saw_procs"):
            continue
        if prev.get(key):
            out[key] = prev[key]
    if out["windows_sessions"]:
        out["user_cleared"] = False
    elif out["user_cleared"]:
        out["windows_sessions"] = []
    elif prev.get("windows_sessions"):
        out["windows_sessions"] = prev["windows_sessions"]
    if not out["foreground_app"]:
        out["foreground_app"] = (prev.get("foreground_app") or "")[:80]
    if out["active"] is None:
        out["active"] = prev.get("active")
    for key in ("input_mouse", "input_clicks", "input_keys", "input_pulses"):
        if out.get(key) is None and prev.get(key) is not None:
            out[key] = prev[key]
    return out


def win_event_severity(level: Any, level_text: str) -> str:
    text = (level_text or "").lower()
    if "crit" in text:
        return "critical"
    if "err" in text:
        return "error"
    if "warn" in text:
        return "warning"
    if "verb" in text:
        return "verbose"
    try:
        n = int(level)
    except (TypeError, ValueError):
        n = 4
    return {1: "critical", 2: "error", 3: "warning", 4: "info", 5: "verbose"}.get(n, "info")


def is_win_event_metric(name: str, fields: dict) -> bool:
    n = (name or "").lower()
    if n in ("win_eventlog", "eventlog", "windows_eventlog"):
        return True
    return fields.get("EventID") is not None or fields.get("event_id") is not None


def rank_processes(procs: list[dict], watches: list[dict], rest_limit: int = 20) -> list[dict]:
    scored = []
    for p in procs:
        item = dict(p)
        item["watch"] = process_watch_labels(item.get("name"), watches)
        scored.append(item)
    watched = [p for p in scored if p.get("watch")]
    rest = [p for p in scored if not p.get("watch")]
    rest.sort(key=lambda p: (p.get("cpu") or 0), reverse=True)
    return watched + rest[:rest_limit]


def token_fingerprint(token: str) -> str:
    if not token:
        return "(empty)"
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def default_config() -> dict:
    return {
        "postgres": {
            "host": "127.0.0.1",
            "port": 5432,
            "database": "nre",
            "user": "nre",
            "password": "",
        },
        "jump": {
            "base_url": "https://api.jumpdesktop.com",
            "team_id": "",
            "api_token": "",
        },
        "telegraf": {
            "ingest_token": "",
            "ingest_cidrs": ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.1/32"],
            "idle_active_seconds": 120,
        },
        "turn_servers": [],
        "process_watchlist": [
            {"label": "Premiere Pro", "match": "premiere", "enabled": True},
            {"label": "After Effects", "match": "afterfx", "enabled": True},
            {"label": "Media Encoder", "match": "media encoder", "enabled": True},
            {"label": "Photoshop", "match": "photoshop", "enabled": True},
            {"label": "DaVinci Resolve", "match": "resolve", "enabled": True},
        ],
        "bridge": {
            "ws_host": "0.0.0.0",
            "ws_port": 8765,
            "ingest_host": "127.0.0.1",
            "ingest_port": 8766,
            "jump_poll_seconds": 45,
            "turn_poll_seconds": 15,
            "retention_days": 90,
            "stale_session_hours": 18,
            "correlation_window_seconds": 90,
        },
    }


def deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and not _is_listy(v):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _is_listy(v: Any) -> bool:
    return isinstance(v, list)


def dsn_from_cfg(cfg: dict) -> str:
    p = cfg["postgres"]
    user = p.get("user") or "nre"
    password = p.get("password") or ""
    host = p.get("host") or "127.0.0.1"
    port = int(p.get("port") or 5432)
    db = p.get("database") or "nre"
    # Avoid logging the password; callers must not print this.
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"

class ConfigStore:
    def __init__(self) -> None:
        self._cfg = default_config()
        self._mtime = 0.0
        self._lock = asyncio.Lock()

    def load(self) -> dict:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if not CONFIG_PATH.exists():
            self._cfg = default_config()
            if not self._cfg["telegraf"]["ingest_token"]:
                self._cfg["telegraf"]["ingest_token"] = os.urandom(32).hex()
                self._write(self._cfg)
                log.info(
                    "generated telegraf ingest token fingerprint=%s (value written to data/config.json only)",
                    token_fingerprint(self._cfg["telegraf"]["ingest_token"]),
                )
            else:
                self._write(self._cfg)
            self._mtime = CONFIG_PATH.stat().st_mtime
            return self._cfg
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        self._cfg = deep_merge(default_config(), raw if isinstance(raw, dict) else {})
        changed = False
        if not self._cfg["telegraf"].get("ingest_token"):
            self._cfg["telegraf"]["ingest_token"] = os.urandom(32).hex()
            changed = True
            log.info(
                "generated telegraf ingest token fingerprint=%s",
                token_fingerprint(self._cfg["telegraf"]["ingest_token"]),
            )
        if changed:
            self._write(self._cfg)
        self._mtime = CONFIG_PATH.stat().st_mtime
        return self._cfg

    def _write(self, cfg: dict) -> None:
        tmp = CONFIG_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        tmp.replace(CONFIG_PATH)

    def get(self) -> dict:
        return self._cfg

    def maybe_reload(self) -> bool:
        if not CONFIG_PATH.exists():
            return False
        m = CONFIG_PATH.stat().st_mtime
        if m <= self._mtime:
            return False
        self.load()
        log.info("reloaded data/config.json")
        return True


def _as_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _as_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None
