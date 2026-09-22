#!/usr/bin/env python3
"""
nre_bridge.py — NexEditorStats asyncio service.

Telegraf HTTP ingest + Jump Desktop poller + coturn /metrics scrapes
+ PostgreSQL persistence + diff-based WebSocket broadcast.

Settings live in data/config.json (Admin → Settings). No .env.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import ipaddress
import json
import logging
import os
import re
import signal
import ssl
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json
import websockets

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


class App:
    def __init__(self) -> None:
        self.cfgstore = ConfigStore()
        self.cfg = self.cfgstore.load()
        self.pool: Optional[psycopg.AsyncConnection] = None
        self.ws_clients: set = set()
        self._hashes: dict[str, str] = {}
        self._last_jump_start: Optional[datetime] = None
        self._tel_latest: dict[str, dict] = {}
        self._db_lock = asyncio.Lock()
        self._stop = asyncio.Event()

    async def connect_db(self) -> None:
        self.pool = await psycopg.AsyncConnection.connect(
            dsn_from_cfg(self.cfg),
            autocommit=True,
            row_factory=dict_row,
        )
        await self.apply_schema()
        await self.backfill_latest()

    async def backfill_latest(self) -> None:
        """Seed last-known rows from recent samples so a restart does not blank Live."""
        await self.exec(
            """INSERT INTO telemetry_latest
               (hostname, ts, cpu_pct, mem_pct, mem_used_bytes, mem_total_bytes,
                disk_json, gpu_json, processes_json, uptime_sec, windows_sessions,
                idle_sec, active, foreground_app, input_mouse, input_clicks,
                input_keys, input_pulses)
               SELECT DISTINCT ON (hostname)
                      hostname, ts, cpu_pct, mem_pct, mem_used_bytes, mem_total_bytes,
                      disk_json, gpu_json, processes_json, uptime_sec, windows_sessions,
                      idle_sec, active, COALESCE(foreground_app, ''),
                      input_mouse, input_clicks, input_keys, input_pulses
               FROM telemetry_samples
               WHERE ts > now() - interval '24 hours'
               ORDER BY hostname, ts DESC
               ON CONFLICT (hostname) DO NOTHING"""
        )

    async def apply_schema(self) -> None:
        sql = SCHEMA_PATH.read_text(encoding="utf-8")
        async with self.pool.cursor() as cur:
            await cur.execute(sql)
        log.info("schema applied")

    async def exec(self, sql: str, params: Any = None) -> None:
        async with self._db_lock:
            async with self.pool.cursor() as cur:
                await cur.execute(sql, params)

    async def fetchall(self, sql: str, params: Any = None) -> list[dict]:
        async with self._db_lock:
            async with self.pool.cursor() as cur:
                await cur.execute(sql, params)
                rows = await cur.fetchall()
        return list(rows)

    async def fetchone(self, sql: str, params: Any = None) -> Optional[dict]:
        async with self._db_lock:
            async with self.pool.cursor() as cur:
                await cur.execute(sql, params)
                return await cur.fetchone()

    # ---- events / broadcast ----

    async def add_event(self, kind: str, hostname: Optional[str] = None,
                        session_id: Optional[int] = None, payload: Optional[dict] = None) -> None:
        safe = dict(payload or {})
        for k in list(safe.keys()):
            if "token" in k.lower() or "password" in k.lower() or "secret" in k.lower():
                safe.pop(k, None)
        await self.exec(
            """INSERT INTO events (kind, hostname, session_id, payload_json)
               VALUES (%s, %s, %s, %s)""",
            (kind, hostname, session_id, Json(safe)),
        )
        await self.broadcast({"type": "event", "event": {
            "kind": kind, "hostname": hostname, "session_id": session_id,
            "ts": iso(utcnow()), "payload": safe,
        }})

    async def broadcast(self, msg: dict) -> None:
        if not self.ws_clients:
            return
        data = json.dumps(msg, default=str)
        dead = []
        for ws in list(self.ws_clients):
            try:
                await ws.send(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.ws_clients.discard(ws)

    def _hash(self, obj: Any) -> str:
        raw = json.dumps(obj, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha1(raw).hexdigest()

    async def broadcast_if_changed(self, key: str, msg: dict) -> None:
        h = self._hash(msg)
        if self._hashes.get(key) == h:
            return
        self._hashes[key] = h
        await self.broadcast(msg)

    # ---- snapshot ----

    async def build_snapshot(self) -> dict:
        sessions = await self.fetchall(
            """SELECT s.*, m.telegraf_hostname, m.relay_policy, m.jump_display_name
               FROM sessions s
               LEFT JOIN device_maps m ON m.jump_device_id = s.device_id
               WHERE s.end_time IS NULL
               ORDER BY s.start_time DESC"""
        )
        devices = await self.fetchall(
            """SELECT d.*, m.telegraf_hostname, m.relay_policy, m.jump_display_name
               FROM devices d
               LEFT JOIN device_maps m ON m.jump_device_id = d.id
               ORDER BY d.display_name"""
        )
        latest = await self.fetchall(
            """SELECT * FROM telemetry_latest
               WHERE ts > now() - (%s || ' hours')::interval""",
            (str(LIVE_KEEP_HOURS),),
        )
        for row in latest:
            self._tel_latest[host_key(row.get("hostname"))] = self._row_to_tel(row)
        unmatched_tel = await self.fetchone(
            """SELECT count(*)::int AS n FROM (
                 SELECT DISTINCT t.hostname
                 FROM telemetry_samples t
                 WHERE t.ts > now() - interval '1 hour'
                   AND NOT EXISTS (
                     SELECT 1 FROM device_maps m
                     WHERE lower(m.telegraf_hostname) = lower(t.hostname)
                        OR EXISTS (
                          SELECT 1 FROM jsonb_array_elements_text(m.aliases) a
                          WHERE lower(a) = lower(t.hostname)
                        )
                   )
               ) x"""
        )
        unmatched_maps = await self.fetchone(
            """SELECT count(*)::int AS n FROM devices d
               WHERE NOT EXISTS (SELECT 1 FROM device_maps m WHERE m.jump_device_id = d.id)
                  OR EXISTS (
                    SELECT 1 FROM device_maps m
                    WHERE m.jump_device_id = d.id AND (m.telegraf_hostname IS NULL OR m.telegraf_hostname = '')
                  )"""
        )
        turn_now = []
        for srv in self.cfg.get("turn_servers") or []:
            if not srv.get("enabled", True):
                continue
            row = await self.fetchone(
                """SELECT rcvb, sentb, allocations, ts FROM turn_throughput
                   WHERE turn_server_id = %s ORDER BY ts DESC LIMIT 1""",
                (srv.get("id"),),
            )
            turn_now.append({
                "id": srv.get("id"),
                "name": srv.get("name") or srv.get("id"),
                "rcvb": row["rcvb"] if row else None,
                "sentb": row["sentb"] if row else None,
                "allocations": row["allocations"] if row else None,
                "ts": iso(row["ts"]) if row else None,
            })
        live = []
        for s in sessions:
            host = s.get("telegraf_hostname") or s.get("hostname") or ""
            tel = self._tel_latest.get(host_key(host)) or {}
            live.append(self._session_public(s, tel))
        sess_by_host: dict[str, dict] = {}
        for s in sessions:
            hk = host_key(s.get("telegraf_hostname") or s.get("hostname"))
            if hk and hk not in sess_by_host:
                sess_by_host[hk] = s
        host_names = set(self._tel_latest.keys())
        for s in sessions:
            host_names.add(host_key(s.get("telegraf_hostname") or s.get("hostname")))
        maps = await self.fetchall("SELECT * FROM device_maps")
        map_by_host = {}
        for m in maps:
            for cand in [m.get("telegraf_hostname"), m.get("jump_hostname")]:
                hk = host_key(cand)
                if hk:
                    map_by_host[hk] = m
            for a in m.get("aliases") or []:
                hk = host_key(str(a))
                if hk:
                    map_by_host[hk] = m
        hosts = []
        for hk in sorted(n for n in host_names if n):
            tel = self._tel_latest.get(hk) or {}
            hostname = tel.get("hostname") or hk
            dmap = map_by_host.get(hk)
            sess = sess_by_host.get(hk)
            hosts.append(self._bay_public(hostname, tel, dmap, sess))
        return {
            "type": "snapshot",
            "hosts": hosts,
            "sessions": live,
            "devices": [self._device_public(d) for d in devices],
            "watchlist": enabled_watchlist(self.cfg),
            "unmatched": {
                "telemetry_hosts": (unmatched_tel or {}).get("n", 0),
                "unmapped_devices": (unmatched_maps or {}).get("n", 0),
            },
            "turn": turn_now,
        }

    def _row_to_tel(self, row: dict) -> dict:
        return {
            "hostname": row.get("hostname"),
            "ts": iso(row.get("ts")),
            "cpu_pct": row.get("cpu_pct"),
            "mem_pct": row.get("mem_pct"),
            "gpu": row.get("gpu_json") or [],
            "disk": row.get("disk_json") or [],
            "processes": row.get("processes_json") or [],
            "uptime_sec": row.get("uptime_sec"),
            "windows_sessions": row.get("windows_sessions") or [],
            "idle_sec": row.get("idle_sec"),
            "active": row.get("active"),
            "foreground_app": row.get("foreground_app") or "",
            "input_mouse": row.get("input_mouse"),
            "input_clicks": row.get("input_clicks"),
            "input_keys": row.get("input_keys"),
            "input_pulses": row.get("input_pulses"),
        }

    def _bay_public(self, hostname: str, tel: dict, dmap: Optional[dict], sess: Optional[dict]) -> dict:
        display = (dmap or {}).get("jump_display_name") or hostname
        jump = None
        if sess:
            jump = self._session_public(sess, tel)
        return {
            "hostname": hostname,
            "display_name": display,
            "telegraf_hostname": (dmap or {}).get("telegraf_hostname") or hostname,
            "windows_sessions": tel.get("windows_sessions") or [],
            "uptime_sec": tel.get("uptime_sec"),
            "idle_sec": tel.get("idle_sec"),
            "active": tel.get("active"),
            "foreground_app": tel.get("foreground_app") or "",
            "ts": tel.get("ts"),
            "telemetry": tel,
            "jump": jump,
            "online": bool(tel.get("ts")),
            "relay_policy": (dmap or {}).get("relay_policy"),
        }

    async def publish_host(self, hostname: str) -> None:
        if not hostname:
            return
        hk = host_key(hostname)
        tel = self._tel_latest.get(hk) or {}
        dmap = await self.fetchone(
            """SELECT * FROM device_maps
               WHERE lower(telegraf_hostname) = lower(%s)
                  OR lower(jump_hostname) = lower(%s)
                  OR EXISTS (
                    SELECT 1 FROM jsonb_array_elements_text(aliases) a
                    WHERE lower(a) = lower(%s)
                  )
               LIMIT 1""",
            (hostname, hostname, hostname),
        )
        sess = await self.fetchone(
            """SELECT s.*, m.telegraf_hostname, m.relay_policy, m.jump_display_name
               FROM sessions s
               LEFT JOIN device_maps m ON m.jump_device_id = s.device_id
               WHERE s.end_time IS NULL
                 AND (
                   lower(s.hostname) = lower(%s)
                   OR lower(m.telegraf_hostname) = lower(%s)
                   OR lower(split_part(s.hostname, '.', 1)) = lower(%s)
                 )
               ORDER BY s.start_time DESC LIMIT 1""",
            (hostname, hostname, host_key(hostname)),
        )
        bay = self._bay_public(tel.get("hostname") or hostname, tel, dmap, sess)
        await self.broadcast_if_changed(f"host:{hk}", {"type": "host_update", "host": bay})

    def _session_public(self, s: dict, tel: Optional[dict] = None) -> dict:
        start = s.get("start_time")
        end = s.get("end_time")
        duration = s.get("duration_sec")
        if start and not end:
            duration = int((utcnow() - start).total_seconds())
        return {
            "id": s.get("id"),
            "connection_id": s.get("connection_id"),
            "device_id": s.get("device_id"),
            "hostname": s.get("telegraf_hostname") or s.get("hostname"),
            "display_name": s.get("jump_display_name") or s.get("hostname"),
            "user_email": s.get("user_email"),
            "client_ip": s.get("client_ip"),
            "start_time": iso(start),
            "end_time": iso(end),
            "duration_sec": duration,
            "transport": s.get("transport"),
            "relay_policy": s.get("relay_policy"),
            "telemetry": tel or {},
        }

    def _device_public(self, d: dict) -> dict:
        host = d.get("telegraf_hostname") or d.get("hostname") or ""
        return {
            "id": d.get("id"),
            "hostname": d.get("hostname"),
            "display_name": d.get("jump_display_name") or d.get("display_name"),
            "telegraf_hostname": d.get("telegraf_hostname"),
            "os": d.get("os"),
            "online": bool(d.get("online")),
            "last_seen": iso(d.get("last_seen")),
            "relay_policy": d.get("relay_policy"),
            "telemetry": self._tel_latest.get(host_key(host)) or {},
        }

    async def ws_handler(self, websocket) -> None:
        self.ws_clients.add(websocket)
        log.info("WS client connected (%d total)", len(self.ws_clients))
        try:
            await websocket.send(json.dumps(await self.build_snapshot(), default=str))
            async for raw in websocket:
                if len(raw) > WS_MAX_MESSAGE_SIZE:
                    continue
                # live board is read-only over WS
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.ws_clients.discard(websocket)
            log.info("WS client disconnected (%d total)", len(self.ws_clients))

    # ---- Telegraf ingest ----

    def _client_allowed(self, ip: str) -> bool:
        try:
            addr = ipaddress.ip_address(ip.split("%")[0])
        except ValueError:
            return False
        cidrs = self.cfg.get("telegraf", {}).get("ingest_cidrs") or []
        if not cidrs:
            return True
        for c in cidrs:
            try:
                if addr in ipaddress.ip_network(c, strict=False):
                    return True
            except ValueError:
                continue
        return False

    def _token_ok(self, header: str) -> bool:
        expected = (self.cfg.get("telegraf") or {}).get("ingest_token") or ""
        if not expected:
            return False
        if not header.lower().startswith("bearer "):
            return False
        got = header[7:].strip()
        if len(got) != len(expected):
            return False
        return hashlib.sha256(got.encode()).digest() == hashlib.sha256(expected.encode()).digest()

    async def ingest_payload(self, body: bytes) -> int:
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return 400
        metrics = []
        if isinstance(data, dict) and isinstance(data.get("metrics"), list):
            metrics = data["metrics"]
        elif isinstance(data, dict) and "name" in data:
            metrics = [data]
        elif isinstance(data, list):
            metrics = data
        else:
            return 400

        event_metrics: list[dict] = []
        grouped: dict[tuple[str, int], list[dict]] = defaultdict(list)
        for m in metrics:
            if not isinstance(m, dict):
                continue
            tags = m.get("tags") or {}
            fields = m.get("fields") or {}
            host = tags.get("host") or tags.get("hostname") or tags.get("Computer") or ""
            if not host:
                continue
            ts = parse_ts(m.get("timestamp")) or utcnow()
            name = (m.get("name") or "").lower()
            if is_win_event_metric(name, fields):
                event_metrics.append(m)
                continue
            grouped[(host, int(ts.timestamp()))].append(m)

        await self.ingest_win_events(event_metrics)

        for (host, epoch), items in grouped.items():
            ts = datetime.fromtimestamp(epoch, tz=timezone.utc)
            raw = self._fold_metrics(host, items)
            if not any((
                raw.get("cpu_pct") is not None,
                raw.get("mem_pct") is not None,
                raw.get("uptime_sec") is not None,
                raw.get("idle_sec") is not None,
                raw.get("processes"),
                raw.get("disk"),
                raw.get("gpu"),
                raw.get("windows_sessions"),
                raw.get("foreground_app"),
                raw.get("input_mouse") is not None,
                raw.get("input_clicks") is not None,
                raw.get("input_keys") is not None,
                raw.get("input_pulses") is not None,
            )):
                continue
            hk = host_key(host)
            prev = self._tel_latest.get(hk)
            merged = merge_telemetry(prev, raw)
            compact = self._compact_tel(host, ts, merged)
            remembered = dict(compact)
            remembered["user_cleared"] = bool(merged.get("user_cleared"))
            self._tel_latest[hk] = remembered
            await self.exec(
                """INSERT INTO telemetry_samples
                   (hostname, ts, cpu_pct, mem_pct, mem_used_bytes, mem_total_bytes,
                    disk_json, gpu_json, processes_json, uptime_sec, windows_sessions,
                    idle_sec, active, foreground_app, input_mouse, input_clicks,
                    input_keys, input_pulses)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    host, ts, raw.get("cpu_pct"), raw.get("mem_pct"),
                    raw.get("mem_used_bytes"), raw.get("mem_total_bytes"),
                    Json(raw.get("disk") or []),
                    Json(raw.get("gpu") or []),
                    Json(raw.get("processes") or []),
                    raw.get("uptime_sec"),
                    Json(raw.get("windows_sessions") or []),
                    raw.get("idle_sec"),
                    raw.get("active"),
                    raw.get("foreground_app") or "",
                    raw.get("input_mouse"),
                    raw.get("input_clicks"),
                    raw.get("input_keys"),
                    raw.get("input_pulses"),
                ),
            )
            await self._save_latest(host, ts, merged)
            await self._save_minute(host, ts, raw, merged)
            await self.touch_work_interval(host, ts, merged)
            await self.exec(
                """UPDATE devices SET last_telemetry_at = %s, updated_at = now()
                   WHERE lower(hostname) = lower(%s)
                      OR id IN (
                        SELECT jump_device_id FROM device_maps
                        WHERE lower(telegraf_hostname) = lower(%s)
                      )""",
                (ts, host, host),
            )
            await self.ensure_host_map(host)
            prev_ts = parse_ts((prev or {}).get("ts"))
            aged = prev_ts is None or (ts - prev_ts).total_seconds() >= 60
            if prev and not aged and not self._tel_changed(prev, compact):
                continue
            await self.broadcast_if_changed(
                f"tel:{host_key(host)}",
                {"type": "telemetry_update", "hostname": host, "telemetry": compact},
            )
            await self.publish_host(host)
        return 204

    async def ingest_win_events(self, items: list[dict]) -> None:
        for m in items:
            tags = m.get("tags") or {}
            fields = m.get("fields") or {}
            host = str(tags.get("host") or tags.get("hostname") or tags.get("Computer") or "")
            if not host:
                continue
            event_id = _as_int(_field(tags, fields, "EventID", "event_id"))
            source = str(_field(tags, fields, "Source", "SourceName", "source") or "")
            message = str(_field(tags, fields, "Message", "message") or "")
            if not accept_security_event(event_id, message, fields):
                continue
            if len(message) > 4000:
                message = message[:3997] + "..."
            channel = str(_field(tags, fields, "Channel", "LogName", "channel") or "System")
            computer = str(_field(tags, fields, "Computer", "computer") or host)
            username = str(_field(tags, fields, "UserName", "User", "username") or "")
            record_id = _as_int(_field(tags, fields, "EventRecordID", "RecordID", "record_id"))
            kw_raw = _field(tags, fields, "Keywords", "keywords") or ""
            category = classify_win_event(event_id, source, message)
            if category in ("logon", "logoff", "lock", "unlock"):
                parsed_user = logon_account(message, category)
                if parsed_user:
                    username = parsed_user
            severity = win_event_severity(
                _field(tags, fields, "Level", "level"),
                str(_field(tags, fields, "LevelText", "level_text") or ""),
            )
            keywords = " ".join(x for x in (
                category,
                severity,
                str(event_id or ""),
                source,
                str(kw_raw),
            ) if x).strip()
            ts = parse_ts(m.get("timestamp")) or utcnow()
            event_ts = parse_ts(_field(tags, fields, "TimeCreated", "time_created")) or ts
            try:
                inserted = await self.fetchone(
                    """INSERT INTO host_events
                       (hostname, ts, category, severity, event_id, source, channel,
                        record_id, computer, username, keywords, message, payload_json)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (hostname, channel, record_id) WHERE record_id IS NOT NULL
                       DO NOTHING
                       RETURNING id""",
                    (
                        host, event_ts, category, severity, event_id, source, channel,
                        record_id, computer, username, keywords, message,
                        Json({"event_id": event_id, "source": source, "channel": channel}),
                    ),
                )
            except Exception:
                log.exception("host_event insert failed for %s event_id=%s", host, event_id)
                continue
            if not inserted:
                continue
            await self.ensure_host_map(host)
            await self.broadcast({
                "type": "host_event",
                "event": {
                    "hostname": host,
                    "ts": iso(event_ts),
                    "category": category,
                    "severity": severity,
                    "event_id": event_id,
                    "source": source,
                    "computer": computer,
                    "username": username,
                    "keywords": keywords,
                    "message": message[:400],
                },
            })
            if category == "logoff":
                await self.close_presence(host, event_ts, "logoff")
            elif category == "logon" and username:
                await self.note_logon(host, event_ts, username)

    async def ensure_host_map(self, host: str) -> None:
        existing = await self.fetchone(
            """SELECT id FROM device_maps
               WHERE lower(telegraf_hostname) = lower(%s)
                  OR EXISTS (
                    SELECT 1 FROM jsonb_array_elements_text(aliases) a
                    WHERE lower(a) = lower(%s)
                  )""",
            (host, host),
        )
        if existing:
            return
        await self.exec(
            """INSERT INTO device_maps
               (jump_device_id, jump_hostname, jump_display_name, telegraf_hostname, relay_policy)
               VALUES (NULL, %s, %s, %s, 'coturn_then_p2p')""",
            (host, host, host),
        )
        await self.add_event("host_seen", hostname=host, payload={"hostname": host})

    def _compact_tel(self, host: str, ts: datetime, merged: dict) -> dict:
        return {
            "hostname": host,
            "ts": iso(ts),
            "cpu_pct": merged.get("cpu_pct"),
            "mem_pct": merged.get("mem_pct"),
            "mem_used_bytes": merged.get("mem_used_bytes"),
            "mem_total_bytes": merged.get("mem_total_bytes"),
            "gpu": merged.get("gpu") or [],
            "disk": merged.get("disk") or [],
            "processes": merged.get("processes") or [],
            "uptime_sec": merged.get("uptime_sec"),
            "windows_sessions": merged.get("windows_sessions") or [],
            "idle_sec": merged.get("idle_sec"),
            "active": merged.get("active"),
            "foreground_app": merged.get("foreground_app") or "",
            "input_mouse": merged.get("input_mouse"),
            "input_clicks": merged.get("input_clicks"),
            "input_keys": merged.get("input_keys"),
            "input_pulses": merged.get("input_pulses"),
        }

    async def _save_latest(self, host: str, ts: datetime, merged: dict) -> None:
        await self.exec(
            """INSERT INTO telemetry_latest
               (hostname, ts, cpu_pct, mem_pct, mem_used_bytes, mem_total_bytes,
                disk_json, gpu_json, processes_json, uptime_sec, windows_sessions,
                idle_sec, active, foreground_app, input_mouse, input_clicks,
                input_keys, input_pulses)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (hostname) DO UPDATE SET
                 ts = EXCLUDED.ts,
                 cpu_pct = EXCLUDED.cpu_pct,
                 mem_pct = EXCLUDED.mem_pct,
                 mem_used_bytes = EXCLUDED.mem_used_bytes,
                 mem_total_bytes = EXCLUDED.mem_total_bytes,
                 disk_json = EXCLUDED.disk_json,
                 gpu_json = EXCLUDED.gpu_json,
                 processes_json = EXCLUDED.processes_json,
                 uptime_sec = EXCLUDED.uptime_sec,
                 windows_sessions = EXCLUDED.windows_sessions,
                 idle_sec = EXCLUDED.idle_sec,
                 active = EXCLUDED.active,
                 foreground_app = EXCLUDED.foreground_app,
                 input_mouse = EXCLUDED.input_mouse,
                 input_clicks = EXCLUDED.input_clicks,
                 input_keys = EXCLUDED.input_keys,
                 input_pulses = EXCLUDED.input_pulses""",
            (
                host, ts, merged.get("cpu_pct"), merged.get("mem_pct"),
                merged.get("mem_used_bytes"), merged.get("mem_total_bytes"),
                Json(merged.get("disk") or []),
                Json(merged.get("gpu") or []),
                Json(merged.get("processes") or []),
                merged.get("uptime_sec"),
                Json(merged.get("windows_sessions") or []),
                merged.get("idle_sec"),
                merged.get("active"),
                merged.get("foreground_app") or "",
                merged.get("input_mouse"),
                merged.get("input_clicks"),
                merged.get("input_keys"),
                merged.get("input_pulses"),
            ),
        )

    async def _save_minute(self, host: str, ts: datetime, raw: dict, merged: dict) -> None:
        bucket = ts.replace(second=0, microsecond=0)
        gpus = raw.get("gpu") or []
        gpu_pct = gpus[0].get("util_pct") if gpus and isinstance(gpus[0], dict) else None
        active = raw.get("active")
        active_pct = None if active is None else (100.0 if active else 0.0)
        procs = Json(watched_brief(raw.get("processes"))) if raw.get("saw_procs") else None
        await self.exec(
            """INSERT INTO telemetry_minutes
               (hostname, bucket, cpu_pct, mem_pct, gpu_pct, disk_free_pct, idle_sec,
                active_pct, username, foreground_app, input_mouse, input_clicks,
                input_keys, input_pulses, processes_json)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (hostname, bucket) DO UPDATE SET
                 cpu_pct = COALESCE(EXCLUDED.cpu_pct, telemetry_minutes.cpu_pct),
                 mem_pct = COALESCE(EXCLUDED.mem_pct, telemetry_minutes.mem_pct),
                 gpu_pct = COALESCE(EXCLUDED.gpu_pct, telemetry_minutes.gpu_pct),
                 disk_free_pct = COALESCE(EXCLUDED.disk_free_pct, telemetry_minutes.disk_free_pct),
                 idle_sec = COALESCE(EXCLUDED.idle_sec, telemetry_minutes.idle_sec),
                 active_pct = COALESCE(EXCLUDED.active_pct, telemetry_minutes.active_pct),
                 username = CASE WHEN EXCLUDED.username <> '' THEN EXCLUDED.username
                                 ELSE telemetry_minutes.username END,
                 foreground_app = CASE WHEN EXCLUDED.foreground_app <> '' THEN EXCLUDED.foreground_app
                                       ELSE telemetry_minutes.foreground_app END,
                 input_mouse = CASE
                   WHEN EXCLUDED.input_mouse IS NULL THEN telemetry_minutes.input_mouse
                   WHEN telemetry_minutes.input_mouse IS NULL THEN EXCLUDED.input_mouse
                   ELSE telemetry_minutes.input_mouse + EXCLUDED.input_mouse END,
                 input_clicks = CASE
                   WHEN EXCLUDED.input_clicks IS NULL THEN telemetry_minutes.input_clicks
                   WHEN telemetry_minutes.input_clicks IS NULL THEN EXCLUDED.input_clicks
                   ELSE telemetry_minutes.input_clicks + EXCLUDED.input_clicks END,
                 input_keys = CASE
                   WHEN EXCLUDED.input_keys IS NULL THEN telemetry_minutes.input_keys
                   WHEN telemetry_minutes.input_keys IS NULL THEN EXCLUDED.input_keys
                   ELSE telemetry_minutes.input_keys + EXCLUDED.input_keys END,
                 input_pulses = CASE
                   WHEN EXCLUDED.input_pulses IS NULL THEN telemetry_minutes.input_pulses
                   WHEN telemetry_minutes.input_pulses IS NULL THEN EXCLUDED.input_pulses
                   ELSE telemetry_minutes.input_pulses + EXCLUDED.input_pulses END,
                 processes_json = COALESCE(EXCLUDED.processes_json, telemetry_minutes.processes_json)""",
            (
                host, bucket, raw.get("cpu_pct"), raw.get("mem_pct"), gpu_pct,
                disk_free_pct(raw.get("disk")), raw.get("idle_sec"), active_pct,
                primary_user(merged.get("windows_sessions")),
                merged.get("foreground_app") or "",
                raw.get("input_mouse"), raw.get("input_clicks"),
                raw.get("input_keys"), raw.get("input_pulses"), procs,
            ),
        )

    async def _jump_email(self, host: str) -> str:
        row = await self.fetchone(
            """SELECT s.user_email
               FROM sessions s
               LEFT JOIN device_maps m ON m.jump_device_id = s.device_id
               WHERE s.end_time IS NULL
                 AND (
                   lower(s.hostname) = lower(%s)
                   OR lower(m.telegraf_hostname) = lower(%s)
                   OR lower(split_part(s.hostname, '.', 1)) = lower(%s)
                 )
               ORDER BY s.start_time DESC LIMIT 1""",
            (host, host, host_key(host)),
        )
        return str((row or {}).get("user_email") or "")

    async def _end_work(self, interval_id: int, ts: datetime, reason: str) -> None:
        await self.exec(
            """UPDATE work_intervals
               SET end_time = %s, end_reason = %s
               WHERE id = %s AND end_time IS NULL""",
            (ts, reason, interval_id),
        )

    async def touch_work_interval(self, host: str, ts: datetime, merged: dict) -> None:
        email = await self._jump_email(host)
        user = "" if merged.get("user_cleared") else primary_user(merged.get("windows_sessions"))
        kind = session_kind_for(merged.get("windows_sessions"), email)
        fg = merged.get("foreground_app") or ""
        active = merged.get("active") is True
        watched = any(isinstance(p, dict) and p.get("watch") for p in (merged.get("processes") or []))
        row = await self.fetchone(
            """SELECT * FROM work_intervals
               WHERE lower(hostname) = lower(%s) AND end_time IS NULL
               ORDER BY start_time DESC LIMIT 1""",
            (host,),
        )
        if row:
            last = row.get("last_seen")
            if isinstance(last, datetime) and last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            gap = (ts - last).total_seconds() if isinstance(last, datetime) else 0
            if gap > WORK_GAP_SEC:
                await self._end_work(row["id"], last or ts, "telemetry_gap")
                row = None
            elif user and (row.get("username") or "") and user.lower() != str(row.get("username")).lower():
                await self._end_work(row["id"], ts, "user_change")
                row = None
        if row is None:
            if not user and not active and not fg and not watched:
                return
            await self.exec(
                """INSERT INTO work_intervals
                   (hostname, username, session_kind, start_time, last_seen,
                    active_sec, foreground_app, jump_user_email)
                   VALUES (%s, %s, %s, %s, %s, 0, %s, %s)""",
                (host, user, kind, ts, ts, fg, email),
            )
            return
        last = row.get("last_seen")
        if isinstance(last, datetime) and last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        delta = 0
        if isinstance(last, datetime):
            delta = max(0, min(180, int((ts - last).total_seconds())))
        if email:
            kind = "jump"
        elif kind == "rdp":
            kind = "rdp"
        else:
            kind = row.get("session_kind") or "local"
        await self.exec(
            """UPDATE work_intervals
               SET last_seen = %s,
                   active_sec = active_sec + %s,
                   username = %s,
                   foreground_app = %s,
                   jump_user_email = CASE WHEN %s <> '' THEN %s ELSE jump_user_email END,
                   session_kind = %s
               WHERE id = %s AND end_time IS NULL""",
            (
                ts, delta if active else 0,
                user or row.get("username") or "",
                fg or row.get("foreground_app") or "",
                email, email, kind, row["id"],
            ),
        )

    async def note_logon(self, host: str, ts: datetime, username: str) -> None:
        hk = host_key(host)
        prev = dict(self._tel_latest.get(hk) or {})
        prev["windows_sessions"] = [{
            "username": username,
            "session_name": "console",
            "state": "Active",
        }]
        prev["user_cleared"] = False
        prev["hostname"] = prev.get("hostname") or host
        self._tel_latest[hk] = prev
        merged = merge_telemetry(prev, {"windows_sessions": prev["windows_sessions"]})
        merged["user_cleared"] = False
        self._tel_latest[hk] = dict(self._compact_tel(host, ts, merged))
        self._tel_latest[hk]["user_cleared"] = False
        await self._save_latest(host, ts, merged)
        await self.touch_work_interval(host, ts, merged)
        await self.publish_host(host)

    async def close_presence(self, host: str, ts: datetime, reason: str) -> None:
        rows = await self.fetchall(
            """SELECT id FROM work_intervals
               WHERE lower(hostname) = lower(%s) AND end_time IS NULL""",
            (host,),
        )
        for row in rows:
            await self._end_work(row["id"], ts, reason)
        hk = host_key(host)
        if hk not in self._tel_latest:
            return
        tel = dict(self._tel_latest[hk])
        tel["windows_sessions"] = []
        tel["user_cleared"] = True
        tel["active"] = False
        self._tel_latest[hk] = tel
        merged = dict(tel)
        await self._save_latest(host, ts, merged)
        await self.publish_host(host)

    def _tel_changed(self, a: dict, b: dict) -> bool:
        for k in ("cpu_pct", "mem_pct"):
            av, bv = a.get(k), b.get(k)
            if av is None or bv is None:
                if av != bv:
                    return True
                continue
            if abs(float(av) - float(bv)) > TEL_EPS:
                return True
        if (a.get("uptime_sec") or 0) // 60 != (b.get("uptime_sec") or 0) // 60:
            return True
        if a.get("active") != b.get("active"):
            return True
        if (a.get("idle_sec") or 0) // 15 != (b.get("idle_sec") or 0) // 15:
            return True
        if json.dumps(a.get("windows_sessions"), sort_keys=True) != json.dumps(b.get("windows_sessions"), sort_keys=True):
            return True
        if json.dumps(a.get("gpu"), sort_keys=True) != json.dumps(b.get("gpu"), sort_keys=True):
            return True
        if json.dumps(a.get("disk"), sort_keys=True) != json.dumps(b.get("disk"), sort_keys=True):
            return True
        if (a.get("foreground_app") or "") != (b.get("foreground_app") or ""):
            return True
        for key in ("input_mouse", "input_clicks", "input_keys", "input_pulses"):
            if (a.get(key) or 0) != (b.get(key) or 0):
                return True
        return json.dumps(a.get("processes"), sort_keys=True) != json.dumps(b.get("processes"), sort_keys=True)

    def _fold_metrics(self, host: str, items: list[dict]) -> dict:
        cpu = None
        mem_pct = None
        mem_used = None
        mem_total = None
        uptime = None
        idle_sec = None
        active = None
        foreground = ""
        input_mouse = None
        input_clicks = None
        input_keys = None
        input_pulses = None
        disks = []
        gpus = []
        procs = []
        sessions = []
        saw_procs = False
        watches = enabled_watchlist(self.cfg)
        idle_names = ("nre_idle", "user_idle", "idle")
        for m in items:
            name = (m.get("name") or "").lower()
            fields = m.get("fields") or {}
            tags = m.get("tags") or {}
            if name in ("cpu", "win_cpu"):
                if "usage_idle" in fields:
                    try:
                        cpu = round(100.0 - float(fields["usage_idle"]), 2)
                    except (TypeError, ValueError):
                        pass
                elif "Percent_Processor_Time" in fields:
                    cpu = _as_float(fields.get("Percent_Processor_Time"))
                elif "usage_user" in fields:
                    cpu = _as_float(fields.get("usage_user"))
            elif name in ("mem", "win_mem", "memory"):
                mem_pct = _as_float(fields.get("used_percent") or fields.get("Percent_Committed_Bytes_In_Use"))
                mem_used = _as_int(fields.get("used") or fields.get("used_bytes"))
                mem_total = _as_int(fields.get("total") or fields.get("total_bytes"))
            elif name == "disk":
                disks.append({
                    "volume": tags.get("path") or tags.get("device") or tags.get("fstype") or "?",
                    "free": _as_int(fields.get("free")),
                    "total": _as_int(fields.get("total")),
                    "used_pct": _as_float(fields.get("used_percent")),
                })
            elif name in ("nvidia_smi", "nvidia"):
                gpus.append({
                    "index": tags.get("index") or tags.get("name") or "0",
                    "util_pct": _as_float(fields.get("utilization_gpu") or fields.get("utilization_gpu_pct")),
                    "mem_used": _as_int(fields.get("memory_used") or fields.get("used_memory")),
                    "mem_total": _as_int(fields.get("memory_total") or fields.get("total_memory")),
                    "temp_c": _as_float(fields.get("temperature_gpu") or fields.get("temp")),
                })
            elif name in ("system",):
                uptime = _as_int(fields.get("uptime") or fields.get("uptime_ns"))
                if uptime and uptime > 10**12:
                    uptime = int(uptime / 1e9)
            elif name in idle_names or "idle_sec" in fields:
                idle_sec = _as_int(fields.get("idle_sec") or fields.get("idle") or fields.get("seconds"))
                if "active" in fields:
                    raw_a = fields.get("active")
                    if isinstance(raw_a, bool):
                        active = raw_a
                    elif raw_a is not None:
                        try:
                            active = float(raw_a) != 0
                        except (TypeError, ValueError):
                            active = str(raw_a).lower() in ("1", "true", "yes")
                fg = str(fields.get("foreground") or tags.get("foreground") or "").replace("\x00", "").strip()
                if fg and fg.lower() not in ("idle", "unknown"):
                    foreground = fg[:80]
                if "mouse" in fields:
                    input_mouse = _as_int(fields.get("mouse"))
                if "clicks" in fields:
                    input_clicks = _as_int(fields.get("clicks"))
                if "keys" in fields:
                    input_keys = _as_int(fields.get("keys"))
                if "pulses" in fields:
                    input_pulses = _as_int(fields.get("pulses"))
            elif name.startswith("procstat"):
                saw_procs = True
                cpu_v = _as_float(fields.get("cpu_usage") or fields.get("cpu_time"))
                if cpu_v is not None and cpu_v > 10000:
                    cpu_v = None
                procs.append({
                    "name": tags.get("exe") or tags.get("process_name") or tags.get("process_name")
                            or fields.get("process_name") or "?",
                    "cpu": cpu_v,
                    "rss": _as_int(fields.get("memory_rss") or fields.get("memory_rss_bytes")),
                    "pid": tags.get("pid") or fields.get("pid"),
                    "user": tags.get("user") or fields.get("user") or fields.get("username") or "",
                })
            else:
                sess = self._parse_windows_session(name, fields, tags)
                if sess:
                    sessions.append(sess)
        ranked = rank_processes(procs, watches)
        # de-dupe sessions by username+session_name
        uniq = {}
        for s in sessions:
            key = (s.get("username") or "", s.get("session_name") or "")
            uniq[key] = s
        threshold = int((self.cfg.get("telegraf") or {}).get("idle_active_seconds") or 120)
        threshold = max(5, threshold)
        if idle_sec is not None:
            active = idle_sec < threshold
        return {
            "cpu_pct": cpu,
            "mem_pct": mem_pct,
            "mem_used_bytes": mem_used,
            "mem_total_bytes": mem_total,
            "disk": disks,
            "gpu": gpus,
            "processes": ranked,
            "uptime_sec": uptime,
            "windows_sessions": list(uniq.values()),
            "idle_sec": idle_sec,
            "active": active,
            "foreground_app": foreground,
            "input_mouse": input_mouse,
            "input_clicks": input_clicks,
            "input_keys": input_keys,
            "input_pulses": input_pulses,
            "saw_procs": saw_procs,
        }

    def _parse_windows_session(self, name: str, fields: dict, tags: dict) -> Optional[dict]:
        user = (
            fields.get("UserName") or fields.get("username") or fields.get("user")
            or fields.get("USER") or tags.get("user") or tags.get("username") or ""
        )
        user = str(user).strip()
        if "\\" in user:
            user = user.split("\\", 1)[1]
        interesting = (
            "logon" in name or "quser" in name or "win_logon" in name
            or "win32_computersystem" in name or name in ("win_wmi", "wmi")
        )
        if not user and not interesting:
            return None
        if not user:
            return None
        if user.lower() in ("", "n/a", "none", "unknown"):
            return None
        return {
            "username": user,
            "session_name": str(fields.get("session_name") or fields.get("SESSIONNAME") or tags.get("session") or "console"),
            "state": str(fields.get("state") or fields.get("STATE") or "Active"),
            "logon_at": str(fields.get("logon_time") or fields.get("LogonTime") or ""),
        }

    async def ingest_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        ip = peer[0] if peer else ""
        if ip.startswith("::ffff:"):
            ip = ip[7:]
        try:
            header_bytes = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10)
        except Exception:
            writer.close()
            await writer.wait_closed()
            return
        try:
            head = header_bytes.decode("latin-1")
            lines = head.split("\r\n")
            req = lines[0].split()
            method = req[0] if req else ""
            path = req[1] if len(req) > 1 else ""
            headers = {}
            for line in lines[1:]:
                if ":" in line:
                    k, v = line.split(":", 1)
                    headers[k.strip().lower()] = v.strip()
            length = int(headers.get("content-length") or 0)
            body = b""
            if length:
                body = await asyncio.wait_for(reader.readexactly(length), timeout=15)
            if headers.get("content-encoding", "").lower() == "gzip":
                body = gzip.decompress(body)
            if method == "GET" and path in ("/health", "/"):
                await self._http_reply(writer, 200, b'{"ok":true}')
                return
            if method != "POST" or not path.startswith("/api/telemetry/ingest"):
                await self._http_reply(writer, 404, b'{"error":"not found"}')
                return
            if not self._client_allowed(ip):
                log.warning("ingest rejected (cidr) from %s", ip)
                await self._http_reply(writer, 403, b'{"error":"forbidden"}')
                return
            if not self._token_ok(headers.get("authorization", "")):
                log.warning("ingest rejected (auth) from %s", ip)
                await self.add_event("ingest_reject", payload={"ip": ip, "reason": "auth"})
                await self._http_reply(writer, 401, b'{"error":"unauthorized"}')
                return
            code = await self.ingest_payload(body)
            if code == 204:
                await self._http_reply(writer, 204, b"")
            else:
                await self._http_reply(writer, code, b'{"error":"bad payload"}')
        except Exception:
            log.exception("ingest handler failed")
            try:
                await self._http_reply(writer, 500, b'{"error":"internal"}')
            except Exception:
                writer.close()
                await writer.wait_closed()

    async def _http_reply(self, writer: asyncio.StreamWriter, code: int, body: bytes) -> None:
        reason = {200: "OK", 204: "No Content", 400: "Bad Request", 401: "Unauthorized",
                  403: "Forbidden", 404: "Not Found", 500: "Internal Server Error"}.get(code, "OK")
        hdr = f"HTTP/1.1 {code} {reason}\r\nContent-Length: {len(body)}\r\nConnection: close\r\n"
        if body:
            hdr += "Content-Type: application/json\r\n"
        hdr += "\r\n"
        writer.write(hdr.encode("latin-1") + body)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    # ---- Jump ----

    def _jump_get(self, path: str, query: Optional[dict] = None) -> dict:
        jump = self.cfg.get("jump") or {}
        base = (jump.get("base_url") or "https://api.jumpdesktop.com").rstrip("/")
        team = jump.get("team_id") or ""
        token = jump.get("api_token") or ""
        if not team or not token:
            raise RuntimeError("Jump team_id or api_token not set in Settings")
        url = f"{base}{path.format(teamID=team)}"
        if query:
            parts = []
            for k, v in query.items():
                if v is None:
                    continue
                if isinstance(v, bool):
                    v = "true" if v else "false"
                parts.append(f"{k}={v}")
            if parts:
                url += "?" + "&".join(parts)
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        })
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            return json.loads(resp.read().decode("utf-8"))

    async def jump_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.jump_once()
            except Exception as e:
                log.error("jump poll failed: %s", e)
                await self.add_event("poller_error", payload={"source": "jump", "error": str(e)})
            wait = int((self.cfg.get("bridge") or {}).get("jump_poll_seconds") or 45)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=wait)
            except asyncio.TimeoutError:
                pass

    async def jump_once(self) -> None:
        if not (self.cfg.get("jump") or {}).get("api_token"):
            return
        devices = await asyncio.to_thread(self._jump_get, "/v1/team/{teamID}/devices")
        raw_devs = devices.get("devices") or []
        if isinstance(raw_devs, dict):
            raw_devs = list(raw_devs.values())
        await self.upsert_devices(raw_devs)

        start = self._last_jump_start or (utcnow() - timedelta(hours=6))
        start = start - timedelta(seconds=120)
        offset = 0
        newest: Optional[datetime] = self._last_jump_start
        while True:
            payload = await asyncio.to_thread(
                self._jump_get,
                "/v1/team/{teamID}/history/devices",
                {
                    "desc": True,
                    "limit": 200,
                    "offset": offset,
                    "startTime": int(start.timestamp()),
                },
            )
            events = payload.get("deviceEvents") or payload.get("events") or []
            for ev in events:
                ts = parse_ts(ev.get("timestamp"))
                if ts and (newest is None or ts > newest):
                    newest = ts
                await self.ingest_jump_event(ev)
            if not payload.get("hasMoreEvents") or not events:
                break
            offset = int(payload.get("nextOffset") or (offset + len(events)))
            if offset > 20000:
                break
        if newest:
            self._last_jump_start = newest
        await self.close_stale_sessions()

    async def upsert_devices(self, devices: list) -> None:
        for d in devices:
            if not isinstance(d, dict):
                continue
            did = d.get("id") or ""
            if not did:
                continue
            osinfo = ((d.get("clientInfo") or {}).get("osInfo") or {})
            hostname = osinfo.get("hostname") or ""
            display = d.get("name") or hostname
            last = parse_ts(d.get("lastOnlineAt"))
            online = False
            if last:
                online = (utcnow() - last) < timedelta(minutes=10)
            os_label = f"{osinfo.get('platform', '')} {osinfo.get('version', '')}".strip()
            await self.exec(
                """INSERT INTO devices (id, hostname, display_name, os, public_ip, online, last_seen, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, now())
                   ON CONFLICT (id) DO UPDATE SET
                     hostname = EXCLUDED.hostname,
                     display_name = EXCLUDED.display_name,
                     os = EXCLUDED.os,
                     public_ip = EXCLUDED.public_ip,
                     online = EXCLUDED.online,
                     last_seen = EXCLUDED.last_seen,
                     updated_at = now()""",
                (did, hostname, display, os_label, osinfo.get("publicIP") or "", online, last),
            )
            existing = await self.fetchone(
                "SELECT id, telegraf_hostname FROM device_maps WHERE jump_device_id = %s",
                (did,),
            )
            if not existing:
                suggested = hostname or None
                await self.exec(
                    """INSERT INTO device_maps
                       (jump_device_id, jump_hostname, jump_display_name, telegraf_hostname, relay_policy)
                       VALUES (%s, %s, %s, %s, 'coturn_then_p2p')""",
                    (did, hostname, display, suggested),
                )
                if not hostname:
                    await self.add_event("device_unmapped", hostname=display, payload={"device_id": did})
            else:
                await self.exec(
                    """UPDATE device_maps
                       SET jump_hostname = %s, jump_display_name = %s, updated_at = now()
                       WHERE jump_device_id = %s""",
                    (hostname, display, did),
                )
            await self.broadcast_if_changed(f"dev:{did}", {
                "type": "device_update",
                "device": {
                    "id": did, "hostname": hostname, "display_name": display,
                    "online": online, "last_seen": iso(last),
                },
            })

    def _event_kind(self, ev: dict) -> tuple[str, dict]:
        for key in (
            "incomingConnectionEvent",
            "incomingConnectionRequest",
            "authSucceededEvent",
            "authFailedEvent",
            "connectionClosedEvent",
        ):
            if ev.get(key):
                return key, ev[key]
        return "", {}

    def _conn_from_body(self, body: dict) -> dict:
        assoc = body.get("associatedIncomingConnectionEvent") or body
        peer = assoc.get("peerInfo") or body.get("peerInfo") or {}
        direct = assoc.get("directConnectionInfo") or body.get("directConnectionInfo") or {}
        return {
            "connection_id": body.get("connectionID") or assoc.get("connectionID") or "",
            "peer_id": assoc.get("peerID") or body.get("peerID") or "",
            "email": peer.get("email") or "",
            "peer_ip": peer.get("ipAddress") or peer.get("ipaddress") or "",
            "direct_ip": direct.get("ipaddress") or direct.get("ipAddress") or "",
            "source_type": assoc.get("sourceType") or body.get("sourceType") or "",
            "tunnel_id": assoc.get("tunnelID") or body.get("tunnelID") or "",
        }

    async def ingest_jump_event(self, ev: dict) -> None:
        kind, body = self._event_kind(ev)
        if not kind:
            return
        ts = parse_ts(ev.get("timestamp")) or utcnow()
        device_id = ev.get("deviceID") or ""
        hostname = ev.get("computerHostName") or ""
        info = self._conn_from_body(body)
        cid = info["connection_id"]
        payload = {
            "kind": kind,
            "connection_id": cid,
            "device_id": device_id,
            "email": info["email"],
        }
        await self.add_event(f"jump_{kind}", hostname=hostname or None, payload=payload)

        if kind == "authFailedEvent":
            return
        if not cid:
            return

        if kind in ("incomingConnectionEvent", "authSucceededEvent"):
            existing = await self.fetchone(
                "SELECT * FROM sessions WHERE connection_id = %s", (cid,)
            )
            dmap = None
            if device_id:
                dmap = await self.fetchone(
                    "SELECT * FROM device_maps WHERE jump_device_id = %s", (device_id,)
                )
            host = (dmap or {}).get("telegraf_hostname") or hostname
            policy = (dmap or {}).get("relay_policy") or "coturn_then_p2p"
            transport = self._transport_from_policy(policy, info)
            if existing:
                await self.exec(
                    """UPDATE sessions SET
                         device_id = COALESCE(NULLIF(%s, ''), device_id),
                         hostname = COALESCE(NULLIF(%s, ''), hostname),
                         user_email = COALESCE(NULLIF(%s, ''), user_email),
                         user_peer_id = COALESCE(NULLIF(%s, ''), user_peer_id),
                         client_ip = COALESCE(NULLIF(%s, ''), client_ip),
                         transport = %s,
                         jump_source_type = %s,
                         jump_tunnel_id = %s
                       WHERE connection_id = %s""",
                    (
                        device_id, host, info["email"], info["peer_id"],
                        info["peer_ip"] or info["direct_ip"],
                        transport, info["source_type"], info["tunnel_id"], cid,
                    ),
                )
                sid = existing["id"]
            else:
                row = await self.fetchone(
                    """INSERT INTO sessions
                       (connection_id, device_id, hostname, user_email, user_peer_id, client_ip,
                        start_time, transport, transport_source, jump_source_type, jump_tunnel_id)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'policy', %s, %s)
                       RETURNING *""",
                    (
                        cid, device_id or None, host, info["email"], info["peer_id"],
                        info["peer_ip"] or info["direct_ip"], ts, transport,
                        info["source_type"], info["tunnel_id"],
                    ),
                )
                sid = row["id"]
                await self.add_event("session_start", hostname=host, session_id=sid, payload=payload)
                pub = self._session_public({**row, "relay_policy": policy, "jump_display_name": (dmap or {}).get("jump_display_name")})
                await self.broadcast({"type": "session_started", "session": pub})
            await self._store_session_ips(sid, ts, info)
            await self.publish_host(host or hostname)
        elif kind == "connectionClosedEvent":
            existing = await self.fetchone(
                "SELECT * FROM sessions WHERE connection_id = %s", (cid,)
            )
            if not existing:
                return
            if existing.get("end_time"):
                return
            duration = int((ts - existing["start_time"]).total_seconds()) if existing.get("start_time") else None
            await self.exec(
                """UPDATE sessions SET end_time = %s, duration_sec = %s, end_reason = 'closed'
                   WHERE id = %s""",
                (ts, duration, existing["id"]),
            )
            await self.add_event("session_end", hostname=existing.get("hostname"), session_id=existing["id"], payload=payload)
            closed = dict(existing)
            closed["end_time"] = ts
            closed["duration_sec"] = duration
            await self.broadcast({"type": "session_closed", "session": self._session_public(closed)})
            await self.publish_host(existing.get("hostname") or hostname)

    def _transport_from_policy(self, policy: str, info: dict) -> str:
        if policy == "relay_only":
            return "relayed"
        # coturn_then_p2p — metrics cannot prove relay; Jump direct IP ⇒ p2p fallback
        if info.get("direct_ip"):
            return "p2p"
        return "unknown"

    async def _store_session_ips(self, session_id: int, ts: datetime, info: dict) -> None:
        for source, raw in (("jump_peer", info.get("peer_ip")), ("jump_direct", info.get("direct_ip"))):
            ip = (raw or "").strip()
            if not ip:
                continue
            try:
                ipaddress.ip_address(ip.split("%")[0])
            except ValueError:
                continue
            await self.exec(
                """INSERT INTO session_ips (session_id, ip, source, first_seen, last_seen)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (session_id, ip, source) DO UPDATE SET last_seen = EXCLUDED.last_seen""",
                (session_id, ip, source, ts, ts),
            )

    async def close_stale_sessions(self) -> None:
        hours = int((self.cfg.get("bridge") or {}).get("stale_session_hours") or 18)
        rows = await self.fetchall(
            """SELECT * FROM sessions
               WHERE end_time IS NULL AND start_time < now() - (%s || ' hours')::interval""",
            (str(hours),),
        )
        for s in rows:
            await self.exec(
                """UPDATE sessions SET end_time = now(), end_reason = 'stale_timeout',
                   duration_sec = EXTRACT(EPOCH FROM (now() - start_time))::int
                   WHERE id = %s""",
                (s["id"],),
            )
            await self.add_event("session_end", hostname=s.get("hostname"), session_id=s["id"],
                                 payload={"reason": "stale_timeout"})
            await self.broadcast({"type": "session_closed", "session": self._session_public({**s, "end_reason": "stale_timeout"})})
            await self.publish_host(s.get("hostname") or "")

    # ---- coturn metrics ----

    def _scrape_metrics(self, url: str) -> str:
        req = urllib.request.Request(url, headers={"Accept": "text/plain"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read().decode("utf-8", errors="replace")

    def _parse_prom(self, text: str) -> dict:
        totals = {"rcvb": None, "sentb": None, "allocations": 0}
        alloc_re = re.compile(r'^turn_total_allocations(?:\{[^}]*\})?\s+([0-9.eE+-]+)')
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("turn_total_traffic_rcvb"):
                totals["rcvb"] = _prom_value(line)
            elif line.startswith("turn_total_traffic_sentb"):
                totals["sentb"] = _prom_value(line)
            else:
                m = alloc_re.match(line)
                if m:
                    try:
                        totals["allocations"] += int(float(m.group(1)))
                    except ValueError:
                        pass
        return totals

    async def turn_loop(self) -> None:
        while not self._stop.is_set():
            for srv in self.cfg.get("turn_servers") or []:
                if not srv.get("enabled", True):
                    continue
                url = (srv.get("metrics_url") or "").strip()
                sid = srv.get("id") or url
                if not url:
                    continue
                try:
                    text = await asyncio.to_thread(self._scrape_metrics, url)
                    vals = self._parse_prom(text)
                    await self.exec(
                        """INSERT INTO turn_throughput (turn_server_id, ts, rcvb, sentb, allocations)
                           VALUES (%s, now(), %s, %s, %s)""",
                        (sid, vals["rcvb"], vals["sentb"], vals["allocations"]),
                    )
                    await self.broadcast_if_changed(f"turn:{sid}", {
                        "type": "turn_update",
                        "server": {
                            "id": sid,
                            "name": srv.get("name") or sid,
                            "rcvb": vals["rcvb"],
                            "sentb": vals["sentb"],
                            "allocations": vals["allocations"],
                            "ts": iso(utcnow()),
                        },
                    })
                except Exception as e:
                    log.warning("turn scrape %s failed: %s", sid, e)
                    await self.add_event("poller_error", payload={"source": "turn", "server": sid, "error": str(e)})
            wait = int((self.cfg.get("bridge") or {}).get("turn_poll_seconds") or 15)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=wait)
            except asyncio.TimeoutError:
                pass

    # ---- maintenance ----

    async def prune_loop(self) -> None:
        while not self._stop.is_set():
            try:
                days = int((self.cfg.get("bridge") or {}).get("retention_days") or 90)
                await self.exec("DELETE FROM telemetry_samples WHERE ts < now() - (%s || ' days')::interval", (str(days),))
                await self.exec("DELETE FROM turn_throughput WHERE ts < now() - (%s || ' days')::interval", (str(days),))
                await self.exec("DELETE FROM events WHERE ts < now() - (%s || ' days')::interval", (str(days),))
                await self.exec("DELETE FROM host_events WHERE ts < now() - (%s || ' days')::interval", (str(days),))
                await self.exec("DELETE FROM telemetry_minutes WHERE bucket < now() - (%s || ' days')::interval", (str(days),))
                await self.exec(
                    """DELETE FROM work_intervals
                       WHERE COALESCE(end_time, last_seen) < now() - (%s || ' days')::interval""",
                    (str(days),),
                )
                await self.exec(
                    """UPDATE work_intervals
                       SET end_time = last_seen, end_reason = 'telemetry_gap'
                       WHERE end_time IS NULL
                         AND last_seen < now() - interval '20 minutes'"""
                )
                await self.exec(
                    """DELETE FROM sessions
                       WHERE COALESCE(end_time, start_time) < now() - (%s || ' days')::interval""",
                    (str(days),),
                )
                log.info("retention prune (%s days) complete", days)
            except Exception:
                log.exception("prune failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=3600)
            except asyncio.TimeoutError:
                pass

    async def config_watch(self) -> None:
        while not self._stop.is_set():
            try:
                if self.cfgstore.maybe_reload():
                    self.cfg = self.cfgstore.get()
            except Exception:
                log.exception("config reload failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=10)
            except asyncio.TimeoutError:
                pass

    async def run(self) -> None:
        await self.connect_db()
        br = self.cfg.get("bridge") or {}
        ingest_host = br.get("ingest_host") or "127.0.0.1"
        ingest_port = int(br.get("ingest_port") or 8766)
        ws_host = br.get("ws_host") or "0.0.0.0"
        ws_port = int(br.get("ws_port") or 8765)

        ingest_server = await asyncio.start_server(self.ingest_client, ingest_host, ingest_port)
        log.info("ingest listening on %s:%s", ingest_host, ingest_port)
        log.info("telegraf token fingerprint=%s", token_fingerprint((self.cfg.get("telegraf") or {}).get("ingest_token") or ""))

        async with websockets.serve(self.ws_handler, ws_host, ws_port, max_size=WS_MAX_MESSAGE_SIZE):
            log.info("websocket listening on %s:%s", ws_host, ws_port)
            tasks = [
                asyncio.create_task(self.jump_loop(), name="jump"),
                asyncio.create_task(self.turn_loop(), name="turn"),
                asyncio.create_task(self.prune_loop(), name="prune"),
                asyncio.create_task(self.config_watch(), name="config"),
                asyncio.create_task(ingest_server.serve_forever(), name="ingest"),
            ]
            await self._stop.wait()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        ingest_server.close()
        await ingest_server.wait_closed()
        if self.pool:
            await self.pool.close()

    def request_stop(self) -> None:
        self._stop.set()


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


def _prom_value(line: str) -> Optional[float]:
    parts = line.rsplit(None, 1)
    if len(parts) != 2:
        return None
    try:
        return float(parts[1])
    except ValueError:
        return None


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    app = App()

    def _stop(*_a: Any) -> None:
        log.info("shutdown requested")
        app.request_stop()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:
            signal.signal(sig, lambda *_: _stop())
    try:
        loop.run_until_complete(app.run())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
