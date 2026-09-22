<?php
require_once __DIR__ . '/../includes/auth.php';
require_once __DIR__ . '/../includes/db.php';

require_login();
if (!has_permission('history')) {
    json_response(['ok' => false, 'error' => 'Access denied'], 403);
}

try {
    $pdo = nre_pdo();
} catch (Throwable $e) {
    json_response(['ok' => false, 'error' => 'Database unavailable'], 503);
}

$from = $_GET['from'] ?? '';
$to = $_GET['to'] ?? '';
$host = trim((string)($_GET['host'] ?? ''));
if ($from === '') {
    $from = gmdate('c', time() - 7 * 86400);
}
if ($to === '') {
    $to = gmdate('c');
}

$kind = $_GET['kind'] ?? 'sessions';

if ($kind === 'hosts') {
    $rows = $pdo->query(
        "SELECT DISTINCT host FROM (
            SELECT COALESCE(NULLIF(telegraf_hostname, ''), jump_hostname) AS host FROM device_maps
            UNION
            SELECT hostname FROM telemetry_latest
            UNION
            SELECT hostname FROM work_intervals
         ) h
         WHERE COALESCE(host, '') <> ''
         ORDER BY 1"
    )->fetchAll();
    json_response(['ok' => true, 'hosts' => array_column($rows, 'host')]);
}

if ($kind === 'sessions') {
    $sql = "SELECT s.id, s.connection_id, s.hostname, s.user_email, s.client_ip,
                   s.start_time, s.end_time, s.duration_sec, s.transport,
                   m.jump_display_name, m.telegraf_hostname
            FROM sessions s
            LEFT JOIN device_maps m ON m.jump_device_id = s.device_id
            WHERE s.start_time >= :from::timestamptz AND s.start_time <= :to::timestamptz";
    $params = ['from' => $from, 'to' => $to];
    if ($host !== '') {
        $sql .= " AND (s.hostname = :host OR m.telegraf_hostname = :host OR m.jump_hostname = :host)";
        $params['host'] = $host;
    }
    $sql .= " ORDER BY s.start_time DESC LIMIT 500";
    $stmt = $pdo->prepare($sql);
    $stmt->execute($params);
    json_response(['ok' => true, 'sessions' => $stmt->fetchAll()]);
}

if ($kind === 'work') {
    $sql = "SELECT w.id, w.hostname, w.username, w.session_kind, w.start_time, w.end_time,
                   w.last_seen, w.active_sec, w.foreground_app, w.jump_user_email, w.end_reason,
                   COALESCE(NULLIF(m.jump_display_name, ''), w.hostname) AS display_name
            FROM work_intervals w
            LEFT JOIN LATERAL (
                SELECT jump_display_name FROM device_maps
                WHERE lower(telegraf_hostname) = lower(w.hostname)
                   OR lower(jump_hostname) = lower(w.hostname)
                LIMIT 1
            ) m ON true
            WHERE w.start_time <= :to::timestamptz
              AND COALESCE(w.end_time, now()) >= :from::timestamptz";
    $params = ['from' => $from, 'to' => $to];
    if ($host !== '') {
        $sql .= " AND (w.hostname = :host OR lower(w.hostname) = lower(:host2))";
        $params['host'] = $host;
        $params['host2'] = $host;
    }
    $sql .= " ORDER BY w.start_time DESC LIMIT 500";
    $stmt = $pdo->prepare($sql);
    $stmt->execute($params);
    json_response(['ok' => true, 'intervals' => $stmt->fetchAll()]);
}

if ($kind === 'telemetry') {
    if ($host === '') {
        json_response(['ok' => false, 'error' => 'host required for telemetry'], 400);
    }
    $fromTs = strtotime($from) ?: (time() - 86400);
    $toTs = strtotime($to) ?: time();
    $span = max(1, $toTs - $fromTs);
    $bucket = $span <= 6 * 3600 ? 60 : ($span <= 7 * 86400 ? 300 : 3600);
    $params = ['host' => $host, 'from' => $from, 'to' => $to, 'bucket' => $bucket, 'bucket2' => $bucket];
    $minuteCount = $pdo->prepare(
        "SELECT count(*)::int AS n FROM telemetry_minutes
         WHERE hostname = :host AND bucket >= :from::timestamptz AND bucket <= :to::timestamptz"
    );
    $minuteCount->execute(['host' => $host, 'from' => $from, 'to' => $to]);
    $hasMinutes = (int)($minuteCount->fetch()['n'] ?? 0) > 0;
    if ($hasMinutes) {
        $sql = "SELECT to_timestamp(floor(extract(epoch from bucket) / :bucket) * :bucket2) AS ts,
                       avg(cpu_pct) AS cpu_pct, avg(mem_pct) AS mem_pct, avg(gpu_pct) AS gpu_pct,
                       avg(disk_free_pct) AS disk_free_pct, avg(idle_sec) AS idle_sec,
                       avg(active_pct) AS active_pct,
                       sum(COALESCE(input_mouse, 0) + COALESCE(input_clicks, 0)
                           + COALESCE(input_keys, 0) + COALESCE(input_pulses, 0)) AS input_n,
                       (array_agg(username ORDER BY bucket DESC) FILTER (WHERE username <> ''))[1] AS username,
                       (array_agg(foreground_app ORDER BY bucket DESC) FILTER (WHERE foreground_app <> ''))[1] AS foreground_app
                FROM telemetry_minutes
                WHERE hostname = :host AND bucket >= :from::timestamptz AND bucket <= :to::timestamptz
                GROUP BY 1 ORDER BY 1";
    } else {
        $sql = "SELECT to_timestamp(floor(extract(epoch from ts) / :bucket) * :bucket2) AS ts,
                       avg(cpu_pct) AS cpu_pct, avg(mem_pct) AS mem_pct,
                       avg( (SELECT avg((g->>'util_pct')::float) FROM jsonb_array_elements(COALESCE(gpu_json, '[]'::jsonb)) g) ) AS gpu_pct,
                       avg( (SELECT min(100.0 - (d->>'used_pct')::float)
                             FROM jsonb_array_elements(COALESCE(disk_json, '[]'::jsonb)) d
                             WHERE d->>'used_pct' IS NOT NULL) ) AS disk_free_pct,
                       avg(idle_sec) AS idle_sec,
                       avg(CASE WHEN active THEN 100.0 WHEN active IS FALSE THEN 0 END) AS active_pct,
                       NULL::float AS input_n,
                       NULL::text AS username,
                       NULL::text AS foreground_app
                FROM telemetry_samples
                WHERE hostname = :host AND ts >= :from::timestamptz AND ts <= :to::timestamptz
                GROUP BY 1 ORDER BY 1";
    }
    $stmt = $pdo->prepare($sql);
    $stmt->execute($params);
    $points = $stmt->fetchAll();
    $dwell = [];
    $apps = [];
    if ($hasMinutes) {
        $d = $pdo->prepare(
            "SELECT foreground_app, count(*)::int AS minutes
             FROM telemetry_minutes
             WHERE hostname = :host AND bucket >= :from::timestamptz AND bucket <= :to::timestamptz
               AND foreground_app <> ''
             GROUP BY 1 ORDER BY 2 DESC LIMIT 12"
        );
        $d->execute(['host' => $host, 'from' => $from, 'to' => $to]);
        $dwell = $d->fetchAll();
        $a = $pdo->prepare(
            "SELECT p->>'name' AS name,
                    avg(NULLIF(p->>'cpu', '')::float) AS cpu,
                    max(NULLIF(p->>'rss', '')::bigint) AS rss
             FROM telemetry_minutes t
             CROSS JOIN LATERAL jsonb_array_elements(COALESCE(t.processes_json, '[]'::jsonb)) p
             WHERE t.hostname = :host AND t.bucket >= :from::timestamptz AND t.bucket <= :to::timestamptz
             GROUP BY 1
             ORDER BY cpu DESC NULLS LAST
             LIMIT 20"
        );
        $a->execute(['host' => $host, 'from' => $from, 'to' => $to]);
        $apps = $a->fetchAll();
    }
    json_response(['ok' => true, 'points' => $points, 'dwell' => $dwell, 'apps' => $apps]);
}

if ($kind === 'turn') {
    $stmt = $pdo->prepare(
        "SELECT turn_server_id, date_trunc('hour', ts) AS ts,
                max(rcvb) AS rcvb, max(sentb) AS sentb, avg(allocations) AS allocations
         FROM turn_throughput
         WHERE ts >= :from::timestamptz AND ts <= :to::timestamptz
         GROUP BY 1, 2
         ORDER BY 2"
    );
    $stmt->execute(['from' => $from, 'to' => $to]);
    json_response(['ok' => true, 'points' => $stmt->fetchAll()]);
}

json_response(['ok' => false, 'error' => 'Unknown kind'], 400);
