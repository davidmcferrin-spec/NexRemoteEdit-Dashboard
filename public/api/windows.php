<?php
require_once __DIR__ . '/../includes/auth.php';
require_once __DIR__ . '/../includes/db.php';

require_login();
if (!has_permission('events')) {
    json_response(['ok' => false, 'error' => 'Access denied'], 403);
}

try {
    $pdo = nre_pdo();
} catch (Throwable $e) {
    json_response(['ok' => false, 'error' => 'Database unavailable'], 503);
}

$kind = $_GET['kind'] ?? 'events';

if ($kind === 'hosts') {
    $rows = $pdo->query(
        "SELECT DISTINCT e.hostname,
                COALESCE(NULLIF(m.jump_display_name, ''), e.hostname) AS display_name
         FROM host_events e
         LEFT JOIN device_maps m ON lower(m.telegraf_hostname) = lower(e.hostname)
         ORDER BY 2"
    )->fetchAll();
    json_response(['ok' => true, 'hosts' => $rows]);
}

$q = trim((string)($_GET['q'] ?? ''));
$severity = trim((string)($_GET['severity'] ?? ''));
$host = trim((string)($_GET['host'] ?? ''));
$category = trim((string)($_GET['category'] ?? ''));

$sql = "SELECT e.id, e.ts, e.hostname, e.category, e.severity, e.event_id, e.source,
               e.channel, e.computer, e.username, e.keywords, e.message,
               COALESCE(NULLIF(m.jump_display_name, ''), e.hostname) AS display_name
        FROM host_events e
        LEFT JOIN device_maps m ON lower(m.telegraf_hostname) = lower(e.hostname)
        WHERE 1=1";
$params = [];
if ($q !== '') {
    $like = '%' . $q . '%';
    $sql .= " AND (e.message ILIKE :q1 OR e.keywords ILIKE :q2 OR e.source ILIKE :q3
              OR e.username ILIKE :q4 OR e.computer ILIKE :q5 OR e.hostname ILIKE :q6
              OR CAST(e.event_id AS TEXT) ILIKE :q7)";
    $params['q1'] = $like;
    $params['q2'] = $like;
    $params['q3'] = $like;
    $params['q4'] = $like;
    $params['q5'] = $like;
    $params['q6'] = $like;
    $params['q7'] = $like;
}
if ($severity !== '') {
    $sql .= " AND e.severity = :severity";
    $params['severity'] = $severity;
}
if ($host !== '') {
    $sql .= " AND (e.hostname = :host OR e.computer = :host2 OR m.telegraf_hostname = :host3)";
    $params['host'] = $host;
    $params['host2'] = $host;
    $params['host3'] = $host;
}
if ($category !== '') {
    $sql .= " AND e.category = :category";
    $params['category'] = $category;
}
$sql .= " ORDER BY e.ts DESC LIMIT 500";
$stmt = $pdo->prepare($sql);
$stmt->execute($params);
json_response(['ok' => true, 'events' => $stmt->fetchAll()]);
