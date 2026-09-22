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

function nre_iso($v): ?string
{
    if ($v === null || $v === '') {
        return null;
    }
    try {
        $dt = new DateTimeImmutable((string)$v);
    } catch (Throwable $e) {
        return null;
    }
    return $dt->setTimezone(new DateTimeZone('UTC'))->format('Y-m-d\TH:i:s\Z');
}

function nre_num($v): ?float
{
    if ($v === null || $v === '' || !is_numeric($v)) {
        return null;
    }
    return round((float)$v, 2);
}

function nre_hostkey(string $host): string
{
    $host = strtolower(trim($host));
    $dot = strpos($host, '.');
    return $dot === false ? $host : substr($host, 0, $dot);
}

function nre_resolve_host(PDO $pdo, string $host): string
{
    $key = nre_hostkey($host);
    $tries = [
        ["SELECT hostname AS host FROM telemetry_latest WHERE hostname = :h LIMIT 1", $host],
        ["SELECT hostname AS host FROM telemetry_minutes WHERE hostname = :h LIMIT 1", $host],
        ["SELECT hostname AS host FROM telemetry_latest WHERE lower(hostname) = lower(:h) LIMIT 1", $host],
        ["SELECT telegraf_hostname AS host FROM device_maps WHERE lower(telegraf_hostname) = lower(:h) AND COALESCE(telegraf_hostname, '') <> '' LIMIT 1", $host],
        ["SELECT hostname AS host FROM telemetry_latest WHERE lower(split_part(hostname, '.', 1)) = lower(:h) LIMIT 1", $key],
    ];
    foreach ($tries as [$sql, $param]) {
        if ($param === '') {
            continue;
        }
        $stmt = $pdo->prepare($sql);
        $stmt->execute(['h' => $param]);
        $found = trim((string)($stmt->fetch()['host'] ?? ''));
        if ($found !== '') {
            return $found;
        }
    }
    return $host;
}

function nre_best_map(array $maps, string $host, string $resolved): ?array
{
    $want = [];
    foreach ([$host, $resolved, nre_hostkey($host), nre_hostkey($resolved)] as $name) {
        $name = strtolower(trim($name));
        if ($name !== '') {
            $want[$name] = true;
        }
    }
    $best = null;
    $bestScore = 99;
    foreach ($maps as $m) {
        $tel = strtolower(trim((string)($m['telegraf_hostname'] ?? '')));
        $jump = strtolower(trim((string)($m['jump_hostname'] ?? '')));
        $score = 99;
        if ($tel !== '' && $tel === strtolower($resolved)) {
            $score = 0;
        } elseif ($tel !== '' && $tel === strtolower($host)) {
            $score = 1;
        } elseif ($jump !== '' && ($jump === strtolower($resolved) || $jump === strtolower($host))) {
            $score = 2;
        } elseif ($tel !== '' && isset($want[nre_hostkey($tel)])) {
            $score = 3;
        } elseif ($jump !== '' && isset($want[nre_hostkey($jump)])) {
            $score = 4;
        }
        if ($score < $bestScore) {
            $bestScore = $score;
            $best = $m;
        }
    }
    return $best;
}

function nre_bucket_epoch($ts, int $bucket): ?int
{
    $iso = nre_iso($ts);
    if ($iso === null) {
        return null;
    }
    $t = strtotime($iso);
    if ($t === false || $bucket < 1) {
        return null;
    }
    return intdiv($t, $bucket) * $bucket;
}

function nre_pinned($v): bool
{
    return $v === true || $v === 1 || $v === '1' || $v === 't' || $v === 'true';
}

function nre_pick_processes(array $rows): array
{
    $byName = [];
    foreach ($rows as $r) {
        $name = trim((string)($r['name'] ?? ''));
        if ($name === '') {
            continue;
        }
        if (!isset($byName[$name])) {
            $byName[$name] = [
                'name' => $name,
                'pinned' => false,
                'peak_cpu' => 0.0,
                'peak_rss' => 0.0,
                'points' => [],
            ];
        }
        $cpu = nre_num($r['cpu'] ?? null);
        $rss = nre_num($r['rss'] ?? null);
        if (nre_pinned($r['pinned'] ?? false)) {
            $byName[$name]['pinned'] = true;
        }
        if ($cpu !== null && $cpu > $byName[$name]['peak_cpu']) {
            $byName[$name]['peak_cpu'] = $cpu;
        }
        if ($rss !== null && $rss > $byName[$name]['peak_rss']) {
            $byName[$name]['peak_rss'] = $rss;
        }
        $epoch = nre_bucket_epoch($r['ts'] ?? null, 1);
        $byName[$name]['points'][] = [
            'ts' => $epoch ? gmdate('Y-m-d\TH:i:s\Z', $epoch) : nre_iso($r['ts'] ?? null),
            'cpu' => $cpu,
            'rss' => $rss,
        ];
    }
    $pinned = [];
    $rest = [];
    foreach ($byName as $item) {
        if ($item['pinned']) {
            $pinned[] = $item;
        } else {
            $rest[] = $item;
        }
    }
    $byPeak = static function (array $a, array $b): int {
        return $b['peak_cpu'] <=> $a['peak_cpu'];
    };
    usort($pinned, $byPeak);
    usort($rest, $byPeak);
    $chosen = [];
    foreach (array_merge($pinned, $rest) as $item) {
        if (count($chosen) >= 5) {
            break;
        }
        $chosen[] = [
            'name' => $item['name'],
            'pinned' => $item['pinned'],
            'peak_cpu' => round($item['peak_cpu'], 2),
            'peak_rss' => (int)round($item['peak_rss']),
            'points' => $item['points'],
        ];
    }
    return $chosen;
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

if ($kind === 'machine') {
    if ($host === '') {
        json_response(['ok' => false, 'error' => 'host required'], 400);
    }
    $fromTs = strtotime($from) ?: (time() - 8 * 3600);
    $toTs = strtotime($to) ?: time();
    if ($toTs < $fromTs) {
        [$fromTs, $toTs] = [$toTs, $fromTs];
    }
    $span = max(1, $toTs - $fromTs);
    $bucket = $span <= 26 * 3600 ? 60 : ($span <= 8 * 86400 ? 300 : 3600);
    $resolved = nre_resolve_host($pdo, $host);
    $maps = $pdo->query(
        "SELECT id, jump_device_id, jump_display_name, telegraf_hostname, jump_hostname, relay_policy, notes FROM device_maps"
    )->fetchAll();
    $map = nre_best_map($maps, $host, $resolved);
    $display = trim((string)($map['jump_display_name'] ?? ''));
    if ($display === '') {
        $display = $resolved;
    }
    $aliases = [];
    foreach ([
        $resolved,
        $host,
        (string)($map['telegraf_hostname'] ?? ''),
        (string)($map['jump_hostname'] ?? ''),
    ] as $name) {
        $name = trim($name);
        if ($name !== '') {
            $aliases[strtolower($name)] = $name;
        }
    }

    $params = [
        'host' => $resolved,
        'from' => $from,
        'to' => $to,
        'bucket' => $bucket,
        'bucket2' => $bucket,
    ];
    $minuteCount = $pdo->prepare(
        "SELECT count(*)::int AS n FROM telemetry_minutes
         WHERE hostname = :host AND bucket >= :from::timestamptz AND bucket <= :to::timestamptz"
    );
    $minuteCount->execute(['host' => $resolved, 'from' => $from, 'to' => $to]);
    $hasMinutes = (int)($minuteCount->fetch()['n'] ?? 0) > 0;
    if ($hasMinutes) {
        $sql = "SELECT to_timestamp(floor(extract(epoch from bucket) / :bucket) * :bucket2) AS ts,
                       avg(cpu_pct) AS cpu_pct, avg(mem_pct) AS mem_pct, avg(gpu_pct) AS gpu_pct,
                       avg(disk_free_pct) AS disk_free_pct,
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
                       avg( (SELECT avg((g->>'util_pct')::float)
                             FROM jsonb_array_elements(CASE WHEN jsonb_typeof(gpu_json) = 'array' THEN gpu_json ELSE '[]'::jsonb END) g) ) AS gpu_pct,
                       avg( (SELECT min(100.0 - (d->>'used_pct')::float)
                             FROM jsonb_array_elements(CASE WHEN jsonb_typeof(disk_json) = 'array' THEN disk_json ELSE '[]'::jsonb END) d
                             WHERE d->>'used_pct' IS NOT NULL) ) AS disk_free_pct,
                       sum(COALESCE(input_mouse, 0) + COALESCE(input_clicks, 0)
                           + COALESCE(input_keys, 0) + COALESCE(input_pulses, 0)) AS input_n,
                       NULL::text AS username,
                       (array_agg(foreground_app ORDER BY ts DESC) FILTER (WHERE COALESCE(foreground_app, '') <> ''))[1] AS foreground_app
                FROM telemetry_samples
                WHERE hostname = :host AND ts >= :from::timestamptz AND ts <= :to::timestamptz
                GROUP BY 1 ORDER BY 1";
    }
    $stmt = $pdo->prepare($sql);
    $stmt->execute($params);
    $rawPoints = $stmt->fetchAll();

    $numLit = "'" . '^[0-9]+(\\.[0-9]+)?$' . "'";
    $gpuStmt = $pdo->prepare(
        "SELECT to_timestamp(floor(extract(epoch from ts) / :bucket) * :bucket2) AS ts,
                avg(
                  CASE
                    WHEN (gpu_json->0->>'mem_total') ~ $numLit
                     AND (gpu_json->0->>'mem_used') ~ $numLit
                     AND (gpu_json->0->>'mem_total')::float > 0
                    THEN 100.0 * (gpu_json->0->>'mem_used')::float / (gpu_json->0->>'mem_total')::float
                  END
                ) AS gpu_mem_pct
         FROM telemetry_samples
         WHERE hostname = :host AND ts >= :from::timestamptz AND ts <= :to::timestamptz
           AND jsonb_typeof(gpu_json) = 'array' AND jsonb_array_length(gpu_json) > 0
         GROUP BY 1"
    );
    $gpuStmt->execute($params);
    $gpuMem = [];
    foreach ($gpuStmt->fetchAll() as $g) {
        $epoch = nre_bucket_epoch($g['ts'] ?? null, $bucket);
        $val = nre_num($g['gpu_mem_pct'] ?? null);
        if ($epoch !== null && $val !== null) {
            $gpuMem[$epoch] = $val;
        }
    }
    $points = [];
    foreach ($rawPoints as $r) {
        $epoch = nre_bucket_epoch($r['ts'] ?? null, $bucket);
        if ($epoch === null) {
            continue;
        }
        $disk = nre_num($r['disk_free_pct'] ?? null);
        $points[] = [
            'ts' => gmdate('Y-m-d\TH:i:s\Z', $epoch),
            'cpu_pct' => nre_num($r['cpu_pct'] ?? null),
            'mem_pct' => nre_num($r['mem_pct'] ?? null),
            'gpu_pct' => nre_num($r['gpu_pct'] ?? null),
            'gpu_mem_pct' => $gpuMem[$epoch] ?? null,
            'disk_free_pct' => $disk,
            'disk_hot' => $disk !== null && $disk <= 10.0,
            'input_n' => (int)round((float)($r['input_n'] ?? 0)),
            'username' => (string)($r['username'] ?? ''),
            'foreground_app' => (string)($r['foreground_app'] ?? ''),
        ];
    }

    $numRe = '^[0-9]+(\\.[0-9]+)?$';
    $procSql = static function (string $timeCol) use ($numRe): string {
        return "SELECT to_timestamp(floor(extract(epoch from t.$timeCol) / :bucket) * :bucket2) AS ts,
                       COALESCE(NULLIF(p->>'name', ''), '') AS name,
                       bool_or(jsonb_typeof(p->'watch') = 'array' AND jsonb_array_length(p->'watch') > 0) AS pinned,
                       avg(CASE WHEN (p->>'cpu') ~ '$numRe' THEN (p->>'cpu')::float END) AS cpu,
                       avg(CASE WHEN (p->>'rss') ~ '^[0-9]+$' THEN (p->>'rss')::float END) AS rss
                FROM " . ($timeCol === 'bucket' ? 'telemetry_minutes' : 'telemetry_samples') . " t
                CROSS JOIN LATERAL jsonb_array_elements(
                  CASE WHEN jsonb_typeof(t.processes_json) = 'array' THEN t.processes_json ELSE '[]'::jsonb END
                ) p
                WHERE t.hostname = :host
                  AND t.$timeCol >= :from::timestamptz AND t.$timeCol <= :to::timestamptz
                  AND COALESCE(p->>'name', '') <> ''
                GROUP BY 1, 2
                ORDER BY 2, 1";
    };
    $processSource = 'none';
    $procRows = [];
    if ($span <= 8 * 86400) {
        $ps = $pdo->prepare($procSql('ts'));
        $ps->execute($params);
        $procRows = $ps->fetchAll();
        if ($procRows) {
            $processSource = 'samples';
        }
    }
    if (!$procRows && $hasMinutes) {
        $ps = $pdo->prepare($procSql('bucket'));
        $ps->execute($params);
        $procRows = $ps->fetchAll();
        if ($procRows) {
            $processSource = 'minutes';
        }
    }
    $processes = nre_pick_processes($procRows);

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
        $d->execute(['host' => $resolved, 'from' => $from, 'to' => $to]);
        $dwell = $d->fetchAll();
        $a = $pdo->prepare(
            "SELECT p->>'name' AS name,
                    avg(NULLIF(p->>'cpu', '')::float) AS cpu,
                    max(NULLIF(p->>'rss', '')::bigint) AS rss
             FROM telemetry_minutes t
             CROSS JOIN LATERAL jsonb_array_elements(
               CASE WHEN jsonb_typeof(t.processes_json) = 'array' THEN t.processes_json ELSE '[]'::jsonb END
             ) p
             WHERE t.hostname = :host AND t.bucket >= :from::timestamptz AND t.bucket <= :to::timestamptz
             GROUP BY 1
             ORDER BY cpu DESC NULLS LAST
             LIMIT 20"
        );
        $a->execute(['host' => $resolved, 'from' => $from, 'to' => $to]);
        $apps = $a->fetchAll();
    }

    $hostSql = [];
    $hostParams = ['wfrom' => $from, 'wto' => $to];
    $i = 0;
    foreach ($aliases as $name) {
        $hostParams['wh' . $i] = $name;
        $hostSql[] = "lower(w.hostname) = lower(:wh$i)";
        $i++;
        $hostParams['wk' . $i] = nre_hostkey($name);
        $hostSql[] = "lower(split_part(w.hostname, '.', 1)) = lower(:wk$i)";
        $i++;
    }
    $workStmt = $pdo->prepare(
        "SELECT w.hostname, w.username, w.session_kind, w.start_time, w.end_time,
                w.active_sec, w.foreground_app, w.jump_user_email, w.end_reason
         FROM work_intervals w
         WHERE w.start_time <= :wto::timestamptz
           AND COALESCE(w.end_time, now()) >= :wfrom::timestamptz
           AND (" . implode(' OR ', $hostSql) . ")
         ORDER BY w.start_time DESC
         LIMIT 200"
    );
    $workStmt->execute($hostParams);
    $work = [];
    foreach ($workStmt->fetchAll() as $w) {
        $work[] = [
            'hostname' => $w['hostname'],
            'display_name' => $display,
            'username' => $w['username'],
            'session_kind' => $w['session_kind'],
            'start_time' => nre_iso($w['start_time']),
            'end_time' => nre_iso($w['end_time']),
            'active_sec' => (int)($w['active_sec'] ?? 0),
            'foreground_app' => $w['foreground_app'],
            'jump_user_email' => $w['jump_user_email'],
            'end_reason' => $w['end_reason'],
        ];
    }

    $sessSql = [];
    $sessParams = ['sfrom' => $from, 'sto' => $to];
    $i = 0;
    foreach ($aliases as $name) {
        $sessParams['sh' . $i] = $name;
        $sessSql[] = "lower(s.hostname) = lower(:sh$i)";
        $i++;
        $sessParams['sk' . $i] = nre_hostkey($name);
        $sessSql[] = "lower(split_part(s.hostname, '.', 1)) = lower(:sk$i)";
        $i++;
        $sessParams['st' . $i] = $name;
        $sessSql[] = "lower(COALESCE(m.telegraf_hostname, '')) = lower(:st$i)";
        $i++;
        $sessParams['sj' . $i] = $name;
        $sessSql[] = "lower(COALESCE(m.jump_hostname, '')) = lower(:sj$i)";
        $i++;
    }
    $sessStmt = $pdo->prepare(
        "SELECT s.hostname, s.user_email, s.client_ip, s.start_time, s.end_time,
                s.duration_sec, s.transport, s.end_reason, s.bytes_sent, s.bytes_recv,
                m.jump_display_name, m.telegraf_hostname
         FROM sessions s
         LEFT JOIN device_maps m ON m.jump_device_id = s.device_id
         WHERE s.start_time <= :sto::timestamptz
           AND COALESCE(s.end_time, now()) >= :sfrom::timestamptz
           AND (" . implode(' OR ', $sessSql) . ")
         ORDER BY s.start_time DESC
         LIMIT 200"
    );
    $sessStmt->execute($sessParams);
    $sessions = [];
    foreach ($sessStmt->fetchAll() as $s) {
        $startIso = nre_iso($s['start_time']);
        $endIso = nre_iso($s['end_time']);
        $dur = $s['duration_sec'];
        if ($dur === null && $startIso) {
            $dur = max(0, ($endIso ? strtotime($endIso) : time()) - strtotime($startIso));
        }
        $sessions[] = [
            'hostname' => $s['hostname'],
            'telegraf_hostname' => $s['telegraf_hostname'],
            'jump_display_name' => $s['jump_display_name'],
            'user_email' => $s['user_email'],
            'client_ip' => $s['client_ip'],
            'start_time' => $startIso,
            'end_time' => $endIso,
            'duration_sec' => $dur === null ? null : (int)$dur,
            'transport' => $s['transport'],
            'end_reason' => $s['end_reason'],
            'bytes_sent' => $s['bytes_sent'] === null ? null : (int)$s['bytes_sent'],
            'bytes_recv' => $s['bytes_recv'] === null ? null : (int)$s['bytes_recv'],
        ];
    }

    $evSql = [];
    $evParams = ['efrom' => $from, 'eto' => $to];
    $i = 0;
    foreach ($aliases as $name) {
        $evParams['eh' . $i] = $name;
        $evSql[] = "lower(e.hostname) = lower(:eh$i)";
        $i++;
        $evParams['ek' . $i] = nre_hostkey($name);
        $evSql[] = "lower(split_part(e.hostname, '.', 1)) = lower(:ek$i)";
        $i++;
        $evParams['ec' . $i] = $name;
        $evSql[] = "lower(e.computer) = lower(:ec$i)";
        $i++;
        $evParams['eck' . $i] = nre_hostkey($name);
        $evSql[] = "lower(split_part(e.computer, '.', 1)) = lower(:eck$i)";
        $i++;
    }
    $evMatch = implode(' OR ', $evSql);
    $evStmt = $pdo->prepare(
        "SELECT e.id, e.ts, e.category, e.severity, e.event_id, e.source, e.username, e.message
         FROM host_events e
         WHERE e.ts >= :efrom::timestamptz AND e.ts <= :eto::timestamptz
           AND ($evMatch)
         ORDER BY e.ts DESC
         LIMIT 400"
    );
    $evStmt->execute($evParams);
    $events = [];
    foreach ($evStmt->fetchAll() as $e) {
        $events[] = [
            'id' => (int)$e['id'],
            'ts' => nre_iso($e['ts']),
            'category' => $e['category'],
            'severity' => $e['severity'],
            'event_id' => $e['event_id'] === null ? null : (int)$e['event_id'],
            'source' => $e['source'],
            'username' => $e['username'],
            'message' => $e['message'],
        ];
    }
    $lockStmt = $pdo->prepare(
        "SELECT e.category
         FROM host_events e
         WHERE e.ts < :efrom::timestamptz
           AND e.category IN ('lock', 'unlock')
           AND ($evMatch)
         ORDER BY e.ts DESC
         LIMIT 1"
    );
    $lockParams = $evParams;
    unset($lockParams['eto']);
    $lockStmt->execute($lockParams);
    $prior = $lockStmt->fetch();
    $lockedAtStart = $prior && ($prior['category'] ?? '') === 'lock';

    $turnServers = [];
    if ($map) {
        $link = $pdo->prepare("SELECT turn_server_id FROM device_map_turn_servers WHERE device_map_id = :id");
        $link->execute(['id' => (int)$map['id']]);
        $cfg = nre_load_config();
        $byId = [];
        foreach ($cfg['turn_servers'] ?? [] as $srv) {
            if (is_array($srv) && ($srv['id'] ?? '') !== '') {
                $byId[(string)$srv['id']] = $srv;
            }
        }
        foreach ($link->fetchAll() as $row) {
            $id = (string)$row['turn_server_id'];
            $srv = $byId[$id] ?? null;
            $turnServers[] = [
                'id' => $id,
                'name' => $srv['name'] ?? $id,
                'enabled' => $srv ? !empty($srv['enabled']) : true,
            ];
        }
    }

    json_response([
        'ok' => true,
        'host' => $resolved,
        'display_name' => $display,
        'bucket_sec' => $bucket,
        'stale_sec' => 180,
        'points' => $points,
        'processes' => $processes,
        'process_source' => $processSource,
        'work' => $work,
        'sessions' => $sessions,
        'events' => $events,
        'events_truncated' => count($events) >= 400,
        'locked_at_start' => $lockedAtStart,
        'dwell' => $dwell,
        'apps' => $apps,
        'context' => [
            'mapped' => $map !== null,
            'on_prem_only' => $map !== null && ($map['jump_device_id'] ?? '') === '',
            'relay_policy' => $map['relay_policy'] ?? '',
            'notes' => $map['notes'] ?? '',
            'turn_servers' => $turnServers,
        ],
    ]);
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
