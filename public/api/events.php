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

$q = trim((string)($_GET['q'] ?? ''));
$kind = trim((string)($_GET['kind'] ?? ''));
$unmatchedOnly = $kind === 'unmatched';

$sql = "SELECT id, ts, kind, hostname, session_id, payload_json FROM events WHERE 1=1";
$params = [];
if ($unmatchedOnly) {
    $sql .= " AND kind IN ('telemetry_no_device','device_unmapped','turn_unmatched','turn_ambiguous','ingest_reject')";
} elseif ($kind !== '') {
    $sql .= " AND kind = :kind";
    $params['kind'] = $kind;
}
if ($q !== '') {
    $sql .= " AND (hostname ILIKE :q OR kind ILIKE :q OR payload_json::text ILIKE :q)";
    $params['q'] = '%' . $q . '%';
}
$sql .= " ORDER BY ts DESC LIMIT 400";
$stmt = $pdo->prepare($sql);
$stmt->execute($params);
json_response(['ok' => true, 'events' => $stmt->fetchAll()]);
