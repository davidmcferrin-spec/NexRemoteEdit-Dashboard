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
        "SELECT DISTINCT COALESCE(NULLIF(telegraf_hostname,''), jump_hostname) AS host
         FROM device_maps
         WHERE COALESCE(telegraf_hostname, jump_hostname, '') <> ''
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

if ($kind === 'telemetry') {
    if ($host === '') {
        json_response(['ok' => false, 'error' => 'host required for telemetry'], 400);
    }
    $fromTs = strtotime($from) ?: (time() - 86400);
    $toTs = strtotime($to) ?: time();
    $span = max(1, $toTs - $fromTs);
    $bucket = $span <= 6 * 3600 ? 60 : ($span <= 7 * 86400 ? 300 : 3600);
    $sql = "SELECT to_timestamp(floor(extract(epoch from ts) / :bucket) * :bucket2) AS ts,
                   avg(cpu_pct) AS cpu_pct, avg(mem_pct) AS mem_pct,
                   avg( (SELECT avg((g->>'util_pct')::float) FROM jsonb_array_elements(COALESCE(gpu_json, '[]'::jsonb)) g) ) AS gpu_pct,
                   avg(idle_sec) AS idle_sec,
                   avg(CASE WHEN active THEN 100.0 WHEN active IS FALSE THEN 0 END) AS active_pct
            FROM telemetry_samples
            WHERE hostname = :host AND ts >= :from::timestamptz AND ts <= :to::timestamptz
            GROUP BY 1 ORDER BY 1";
    $stmt = $pdo->prepare($sql);
    $stmt->execute(['host' => $host, 'from' => $from, 'to' => $to, 'bucket' => $bucket, 'bucket2' => $bucket]);
    json_response(['ok' => true, 'points' => $stmt->fetchAll()]);
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
