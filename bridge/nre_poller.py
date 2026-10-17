"""Jump Desktop poller and coturn metrics scrape."""

from __future__ import annotations

import asyncio
import http.client
import json
import logging
import re
import ssl
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from typing import Optional

import psycopg

from nre_common import iso, parse_ts, utcnow

log = logging.getLogger("nre_bridge")


def _prom_value(line: str) -> Optional[float]:
    parts = line.rsplit(None, 1)
    if len(parts) != 2:
        return None
    try:
        return float(parts[1])
    except ValueError:
        return None


class PollerMixin:
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
            except (
                urllib.error.URLError,
                http.client.HTTPException,
                OSError,
                psycopg.Error,
                RuntimeError,
                ValueError,
                OverflowError,
                TypeError,
                KeyError,
                AttributeError,
            ) as e:
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
                except (
                    urllib.error.URLError,
                    http.client.HTTPException,
                    OSError,
                    psycopg.Error,
                    ValueError,
                ) as e:
                    log.warning("turn scrape %s failed: %s", sid, e)
                    await self.add_event("poller_error", payload={"source": "turn", "server": sid, "error": str(e)})
            wait = int((self.cfg.get("bridge") or {}).get("turn_poll_seconds") or 15)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=wait)
            except asyncio.TimeoutError:
                pass
