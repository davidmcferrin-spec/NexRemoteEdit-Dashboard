"""Windows and Jump event processing, plus live broadcasts."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
from datetime import datetime
from typing import Any, Optional

import psycopg
from psycopg.types.json import Json
import websockets
import websockets.exceptions

from nre_common import (
    _as_int,
    _field,
    accept_security_event,
    classify_win_event,
    iso,
    logon_account,
    parse_ts,
    utcnow,
    win_event_severity,
)

log = logging.getLogger("nre_bridge")


class EventsMixin:
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
            except (websockets.exceptions.WebSocketException, OSError):
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
            except psycopg.Error:
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
