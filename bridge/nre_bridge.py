#!/usr/bin/env python3
"""
nre_bridge.py — NexEditorStats asyncio service.

Telegraf HTTP ingest + Jump Desktop poller + coturn /metrics scrapes
+ PostgreSQL persistence + diff-based WebSocket broadcast.

Settings live in data/config.json (Admin → Settings). No .env.
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
from datetime import datetime
from typing import Any, Optional

import psycopg
from psycopg.rows import dict_row
import websockets
import websockets.exceptions

from nre_common import (
    LIVE_KEEP_HOURS,
    SCHEMA_PATH,
    WS_MAX_MESSAGE_SIZE,
    ConfigStore,
    dsn_from_cfg,
    enabled_watchlist,
    host_key,
    iso,
    token_fingerprint,
    utcnow,
)
# test_presence.py imports these from the entry module.
from nre_common import (
    accept_security_event,
    classify_win_event,
    disk_free_pct,
    logon_account,
    merge_telemetry,
    primary_user,
    session_kind_for,
)
from nre_events import EventsMixin
from nre_ingest import IngestMixin
from nre_poller import PollerMixin

log = logging.getLogger("nre_bridge")


class App(IngestMixin, PollerMixin, EventsMixin):
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
            except (psycopg.Error, ValueError, TypeError):
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
            except (
                OSError,
                json.JSONDecodeError,
                UnicodeDecodeError,
                TypeError,
                KeyError,
                AttributeError,
            ):
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
