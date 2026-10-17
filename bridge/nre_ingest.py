"""Telegraf ingest: HTTP, metric folding, and telemetry persistence."""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import ipaddress
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Optional

from psycopg.types.json import Json

from nre_common import (
    TEL_EPS,
    WORK_GAP_SEC,
    _as_float,
    _as_int,
    disk_free_pct,
    enabled_watchlist,
    host_key,
    is_win_event_metric,
    iso,
    merge_telemetry,
    parse_ts,
    primary_user,
    rank_processes,
    session_kind_for,
    utcnow,
    watched_brief,
)

log = logging.getLogger("nre_bridge")


class IngestMixin:
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
        except (OSError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
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
            # Any failure on this connection is a 500, or a drop if that write fails.
            log.exception("ingest handler failed")
            await self._reply_or_close(writer, 500, b'{"error":"internal"}')

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

    async def _reply_or_close(self, writer: asyncio.StreamWriter, code: int, body: bytes) -> None:
        try:
            await self._http_reply(writer, code, body)
        except (OSError, RuntimeError, AttributeError):
            writer.close()
            await writer.wait_closed()
