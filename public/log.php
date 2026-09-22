<?php
require_once __DIR__ . '/includes/auth.php';

header('Content-Type: application/json');
header('Cache-Control: no-store');

$action = $_GET['action'] ?? 'log';
$lines = max(20, min(500, (int)($_GET['lines'] ?? 100)));

if ($action === 'log' || $action === 'status') {
    require_login();
    if (!has_permission('bridge_view')) {
        http_response_code(403);
        echo json_encode(['ok' => false, 'error' => 'Access denied']);
        exit;
    }
} elseif (in_array($action, ['start', 'stop', 'restart'], true)) {
    require_login();
    if (!has_permission('bridge_control')) {
        http_response_code(403);
        echo json_encode(['ok' => false, 'error' => 'Access denied']);
        exit;
    }
} else {
    http_response_code(400);
    echo json_encode(['ok' => false, 'error' => 'Invalid action']);
    exit;
}

$allowedLines = [50, 100, 200, 500];
if (!in_array($lines, $allowedLines, true)) {
    $lines = 100;
}

$sudo = '/usr/bin/sudo -n';

if ($action === 'log') {
    $cmd = $sudo . ' /usr/bin/journalctl -u nre-bridge -n ' . $lines
         . ' --no-pager --output=short-iso 2>&1';
    $output = [];
    exec($cmd, $output, $rc);
    echo json_encode(['ok' => $rc === 0, 'lines' => $output]);
} elseif ($action === 'status') {
    $output = [];
    exec($sudo . ' /usr/bin/systemctl is-active nre-bridge 2>&1', $output, $rc);
    $state = trim($output[0] ?? 'unknown');
    echo json_encode(['ok' => $rc === 0 || $state === 'inactive', 'active' => $state === 'active', 'state' => $state]);
} else {
    $cmd = $sudo . ' /usr/bin/systemctl ' . $action . ' nre-bridge 2>&1';
    $output = [];
    exec($cmd, $output, $rc);
    echo json_encode([
        'ok' => $rc === 0,
        'action' => $action,
        'output' => implode("\n", $output),
    ]);
}
