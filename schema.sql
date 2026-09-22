-- NexEditorStats — PostgreSQL schema
-- Applied by the bridge on startup (idempotent).

CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    hostname TEXT NOT NULL DEFAULT '',
    display_name TEXT NOT NULL DEFAULT '',
    os TEXT NOT NULL DEFAULT '',
    public_ip TEXT NOT NULL DEFAULT '',
    online BOOLEAN NOT NULL DEFAULT false,
    last_seen TIMESTAMPTZ,
    last_telemetry_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_devices_hostname ON devices (lower(hostname));

CREATE TABLE IF NOT EXISTS device_maps (
    id SERIAL PRIMARY KEY,
    jump_device_id TEXT UNIQUE,
    jump_hostname TEXT NOT NULL DEFAULT '',
    jump_display_name TEXT NOT NULL DEFAULT '',
    telegraf_hostname TEXT,
    aliases JSONB NOT NULL DEFAULT '[]',
    relay_policy TEXT NOT NULL DEFAULT 'coturn_then_p2p'
        CHECK (relay_policy IN ('coturn_then_p2p', 'relay_only')),
    notes TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_device_maps_telegraf ON device_maps (lower(telegraf_hostname));

CREATE TABLE IF NOT EXISTS device_map_turn_servers (
    device_map_id INTEGER NOT NULL REFERENCES device_maps(id) ON DELETE CASCADE,
    turn_server_id TEXT NOT NULL,
    PRIMARY KEY (device_map_id, turn_server_id)
);

CREATE TABLE IF NOT EXISTS sessions (
    id BIGSERIAL PRIMARY KEY,
    connection_id TEXT UNIQUE NOT NULL,
    device_id TEXT,
    hostname TEXT NOT NULL DEFAULT '',
    user_email TEXT NOT NULL DEFAULT '',
    user_peer_id TEXT NOT NULL DEFAULT '',
    client_ip TEXT NOT NULL DEFAULT '',
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ,
    duration_sec INTEGER,
    transport TEXT NOT NULL DEFAULT 'unknown'
        CHECK (transport IN ('relayed', 'p2p', 'unknown')),
    transport_source TEXT NOT NULL DEFAULT 'policy',
    jump_source_type TEXT NOT NULL DEFAULT '',
    jump_tunnel_id TEXT NOT NULL DEFAULT '',
    end_reason TEXT,
    bytes_sent BIGINT,
    bytes_recv BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sessions_host_time ON sessions (hostname, start_time DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_active ON sessions (start_time) WHERE end_time IS NULL;
CREATE INDEX IF NOT EXISTS idx_sessions_device ON sessions (device_id, start_time DESC);

CREATE TABLE IF NOT EXISTS session_ips (
    session_id BIGINT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    ip INET NOT NULL,
    source TEXT NOT NULL,
    first_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (session_id, ip, source)
);
CREATE INDEX IF NOT EXISTS idx_session_ips_ip_time ON session_ips (ip, first_seen, last_seen);

CREATE TABLE IF NOT EXISTS telemetry_samples (
    id BIGSERIAL PRIMARY KEY,
    hostname TEXT NOT NULL,
    ts TIMESTAMPTZ NOT NULL,
    cpu_pct REAL,
    mem_pct REAL,
    mem_used_bytes BIGINT,
    mem_total_bytes BIGINT,
    disk_json JSONB,
    gpu_json JSONB,
    processes_json JSONB,
    uptime_sec BIGINT,
    windows_sessions JSONB,
    idle_sec INTEGER,
    active BOOLEAN
);
CREATE INDEX IF NOT EXISTS idx_tel_host_ts ON telemetry_samples (hostname, ts DESC);

ALTER TABLE telemetry_samples ADD COLUMN IF NOT EXISTS uptime_sec BIGINT;
ALTER TABLE telemetry_samples ADD COLUMN IF NOT EXISTS windows_sessions JSONB;
ALTER TABLE telemetry_samples ADD COLUMN IF NOT EXISTS idle_sec INTEGER;
ALTER TABLE telemetry_samples ADD COLUMN IF NOT EXISTS active BOOLEAN;
ALTER TABLE telemetry_samples ADD COLUMN IF NOT EXISTS foreground_app TEXT;
ALTER TABLE telemetry_samples ADD COLUMN IF NOT EXISTS input_mouse INTEGER;
ALTER TABLE telemetry_samples ADD COLUMN IF NOT EXISTS input_clicks INTEGER;
ALTER TABLE telemetry_samples ADD COLUMN IF NOT EXISTS input_keys INTEGER;
ALTER TABLE telemetry_samples ADD COLUMN IF NOT EXISTS input_pulses INTEGER;

-- Last non-empty reading per host. Live reads this so a partial Telegraf
-- post (idle only, or processes only) cannot blank CPU, memory, disk, or user.
CREATE TABLE IF NOT EXISTS telemetry_latest (
    hostname TEXT PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL,
    cpu_pct REAL,
    mem_pct REAL,
    mem_used_bytes BIGINT,
    mem_total_bytes BIGINT,
    disk_json JSONB,
    gpu_json JSONB,
    processes_json JSONB,
    uptime_sec BIGINT,
    windows_sessions JSONB,
    idle_sec INTEGER,
    active BOOLEAN,
    foreground_app TEXT NOT NULL DEFAULT '',
    input_mouse INTEGER,
    input_clicks INTEGER,
    input_keys INTEGER,
    input_pulses INTEGER
);

-- One row per host per minute. Gauges keep the last real sample in the minute.
-- Input counts sum. Used by History so charts survive sparse posts.
CREATE TABLE IF NOT EXISTS telemetry_minutes (
    hostname TEXT NOT NULL,
    bucket TIMESTAMPTZ NOT NULL,
    cpu_pct REAL,
    mem_pct REAL,
    gpu_pct REAL,
    disk_free_pct REAL,
    idle_sec INTEGER,
    active_pct REAL,
    username TEXT NOT NULL DEFAULT '',
    foreground_app TEXT NOT NULL DEFAULT '',
    input_mouse INTEGER,
    input_clicks INTEGER,
    input_keys INTEGER,
    input_pulses INTEGER,
    processes_json JSONB,
    PRIMARY KEY (hostname, bucket)
);

-- Who was at the workstation. Independent of Jump.
CREATE TABLE IF NOT EXISTS work_intervals (
    id BIGSERIAL PRIMARY KEY,
    hostname TEXT NOT NULL,
    username TEXT NOT NULL DEFAULT '',
    session_kind TEXT NOT NULL DEFAULT 'local'
        CHECK (session_kind IN ('local', 'rdp', 'jump')),
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ,
    last_seen TIMESTAMPTZ NOT NULL,
    active_sec INTEGER NOT NULL DEFAULT 0,
    foreground_app TEXT NOT NULL DEFAULT '',
    jump_user_email TEXT NOT NULL DEFAULT '',
    end_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_work_host_time ON work_intervals (hostname, start_time DESC);
CREATE INDEX IF NOT EXISTS idx_work_open ON work_intervals (hostname) WHERE end_time IS NULL;

CREATE TABLE IF NOT EXISTS turn_allocations (
    id BIGSERIAL PRIMARY KEY,
    turn_server_id TEXT NOT NULL,
    coturn_session_id TEXT,
    client_ip INET,
    client_port INTEGER,
    allocated_at TIMESTAMPTZ,
    deallocated_at TIMESTAMPTZ,
    bytes_sent BIGINT,
    bytes_recv BIGINT,
    realm TEXT,
    username TEXT,
    session_id BIGINT REFERENCES sessions(id) ON DELETE SET NULL,
    match_status TEXT NOT NULL DEFAULT 'unmatched'
        CHECK (match_status IN ('unmatched', 'matched', 'ambiguous')),
    UNIQUE (turn_server_id, coturn_session_id)
);
CREATE INDEX IF NOT EXISTS idx_turn_ip_time ON turn_allocations (client_ip, allocated_at);

CREATE TABLE IF NOT EXISTS turn_throughput (
    id BIGSERIAL PRIMARY KEY,
    turn_server_id TEXT NOT NULL,
    ts TIMESTAMPTZ NOT NULL,
    rcvb BIGINT,
    sentb BIGINT,
    allocations INTEGER
);
CREATE INDEX IF NOT EXISTS idx_turn_tp_server_ts ON turn_throughput (turn_server_id, ts DESC);

CREATE TABLE IF NOT EXISTS events (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind TEXT NOT NULL,
    hostname TEXT,
    session_id BIGINT,
    payload_json JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events (ts DESC);
CREATE INDEX IF NOT EXISTS idx_events_kind_ts ON events (kind, ts DESC);

-- Windows Event Log (crash / reboot / shutdown / update). 90-day retention.
CREATE TABLE IF NOT EXISTS host_events (
    id BIGSERIAL PRIMARY KEY,
    hostname TEXT NOT NULL,
    ts TIMESTAMPTZ NOT NULL,
    category TEXT NOT NULL DEFAULT 'other'
        CHECK (category IN (
            'crash', 'unexpected', 'reboot', 'shutdown', 'update',
            'logon', 'logoff', 'lock', 'unlock', 'other'
        )),
    severity TEXT NOT NULL DEFAULT 'info'
        CHECK (severity IN ('critical', 'error', 'warning', 'info', 'verbose')),
    event_id INTEGER,
    source TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL DEFAULT '',
    record_id BIGINT,
    computer TEXT NOT NULL DEFAULT '',
    username TEXT NOT NULL DEFAULT '',
    keywords TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL DEFAULT '',
    payload_json JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_host_events_host_ts ON host_events (hostname, ts DESC);
CREATE INDEX IF NOT EXISTS idx_host_events_cat_ts ON host_events (category, ts DESC);
CREATE INDEX IF NOT EXISTS idx_host_events_sev_ts ON host_events (severity, ts DESC);
CREATE INDEX IF NOT EXISTS idx_host_events_q ON host_events (ts DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_host_events_dedupe
    ON host_events (hostname, channel, record_id)
    WHERE record_id IS NOT NULL;

-- Presence categories added after the original check constraint shipped.
DO $$
DECLARE r record;
BEGIN
  IF EXISTS (
    SELECT 1
    FROM pg_constraint con
    JOIN pg_class rel ON rel.oid = con.conrelid
    WHERE rel.relname = 'host_events' AND con.contype = 'c'
      AND pg_get_constraintdef(con.oid) ILIKE '%category%'
      AND pg_get_constraintdef(con.oid) ILIKE '%logon%'
  ) THEN
    RETURN;
  END IF;
  FOR r IN
    SELECT con.conname
    FROM pg_constraint con
    JOIN pg_class rel ON rel.oid = con.conrelid
    WHERE rel.relname = 'host_events' AND con.contype = 'c'
      AND pg_get_constraintdef(con.oid) ILIKE '%category%'
  LOOP
    EXECUTE format('ALTER TABLE host_events DROP CONSTRAINT %I', r.conname);
  END LOOP;
  ALTER TABLE host_events ADD CONSTRAINT host_events_category_check
    CHECK (category IN (
      'crash', 'unexpected', 'reboot', 'shutdown', 'update',
      'logon', 'logoff', 'lock', 'unlock', 'other'
    ));
END $$;

CREATE TABLE IF NOT EXISTS schema_migrations (
    id TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
