<?php
/**
 * Session auth, roles, LDAP, user prefs.
 * Ported from xpmon-dashboard. WebSocket is not authenticated.
 */

declare(strict_types=1);

const AUTH_DATA_FILE = __DIR__ . '/../../data/auth.json';

const PERMISSIONS = [
    'dashboard',
    'history',
    'events',
    'mapping',
    'settings',
    'bridge_view',
    'bridge_control',
    'manage_users',
];

const PERMISSION_META = [
    'dashboard' => [
        'label' => 'Live dashboard',
        'description' => 'View the live remote-edit session board.',
    ],
    'history' => [
        'label' => 'History',
        'description' => 'View historical sessions, telemetry charts, and TURN throughput.',
    ],
    'events' => [
        'label' => 'Event log',
        'description' => 'Search connection events, unmatched rows, and Windows crash/reboot/update logs.',
    ],
    'mapping' => [
        'label' => 'Mapping',
        'description' => 'Map Jump devices to Telegraf hosts, set relay policy, look up IPs.',
    ],
    'settings' => [
        'label' => 'Settings',
        'description' => 'Edit app settings in data/config.json (Jump, PostgreSQL, Telegraf, TURN).',
    ],
    'bridge_view' => [
        'label' => 'Bridge — View Log',
        'description' => 'Open Bridge Admin and read nre-bridge service logs.',
    ],
    'bridge_control' => [
        'label' => 'Bridge — Start/Stop/Restart',
        'description' => 'Start, stop, and restart the nre-bridge systemd service.',
    ],
    'manage_users' => [
        'label' => 'Manage Users',
        'description' => 'Admin: users, LDAP groups, and session timeout.',
    ],
];

const DEFAULT_ROLES = [
    'admin' => [
        'label' => 'Administrator',
        'description' => 'Full access: live, history, events, mapping, settings, bridge, and users.',
        'permissions' => [
            'dashboard' => true,
            'history' => true,
            'events' => true,
            'mapping' => true,
            'settings' => true,
            'bridge_view' => true,
            'bridge_control' => true,
            'manage_users' => true,
        ],
    ],
    'operator' => [
        'label' => 'Operator',
        'description' => 'Live, history, events, and mapping. No settings, bridge control, or user admin.',
        'permissions' => [
            'dashboard' => true,
            'history' => true,
            'events' => true,
            'mapping' => true,
            'settings' => false,
            'bridge_view' => false,
            'bridge_control' => false,
            'manage_users' => false,
        ],
    ],
    'viewer' => [
        'label' => 'Viewer',
        'description' => 'Live dashboard only.',
        'permissions' => [
            'dashboard' => true,
            'history' => false,
            'events' => false,
            'mapping' => false,
            'settings' => false,
            'bridge_view' => false,
            'bridge_control' => false,
            'manage_users' => false,
        ],
    ],
    'bridge_monitor' => [
        'label' => 'Bridge Monitor',
        'description' => 'Live dashboard plus bridge log. Cannot start or stop the service.',
        'permissions' => [
            'dashboard' => true,
            'history' => false,
            'events' => false,
            'mapping' => false,
            'settings' => false,
            'bridge_view' => true,
            'bridge_control' => false,
            'manage_users' => false,
        ],
    ],
    'kiosk' => [
        'label' => 'Kiosk (wall display)',
        'description' => 'Wall-display live board. No idle timeout.',
        'permissions' => [
            'dashboard' => true,
            'history' => false,
            'events' => false,
            'mapping' => false,
            'settings' => false,
            'bridge_view' => false,
            'bridge_control' => false,
            'manage_users' => false,
        ],
    ],
];

const KIOSK_SESSION_LIFETIME = 31536000;

const DEFAULT_PREFS = [
    'theme' => 'dark',
];

const DEFAULT_GLOBAL = [
    'session_idle_minutes' => 120,
];

const DEFAULT_LDAP = [
    'enabled' => false,
    'host' => '',
    'port' => 636,
    'bind_template' => '{username}@example.com',
    'base_dn' => '',
    'ignore_cert' => true,
    'allowed_groups' => [],
];

function auth_data_dir(): string
{
    return dirname(AUTH_DATA_FILE);
}

function load_auth_data(): array
{
    ensure_auth_data();
    $raw = file_get_contents(AUTH_DATA_FILE);
    $data = json_decode($raw ?: '{}', true);
    if (!is_array($data)) {
        $data = [];
    }
    $data['ldap'] = array_merge(DEFAULT_LDAP, $data['ldap'] ?? []);
    $data['global'] = array_merge(DEFAULT_GLOBAL, $data['global'] ?? []);
    $data['users'] = $data['users'] ?? [];
    return $data;
}

function ensure_auth_dir(): void
{
    $dir = auth_data_dir();
    if (!is_dir($dir)) {
        mkdir($dir, 0750, true);
    }
}

function write_auth_data(array $data): bool
{
    ensure_auth_dir();
    $data['ldap'] = array_merge(DEFAULT_LDAP, $data['ldap'] ?? []);
    $data['global'] = array_merge(DEFAULT_GLOBAL, $data['global'] ?? []);
    $data['users'] = $data['users'] ?? [];
    $json = json_encode($data, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES);
    if ($json === false) {
        return false;
    }
    $tmp = AUTH_DATA_FILE . '.tmp';
    if (file_put_contents($tmp, $json, LOCK_EX) === false) {
        return false;
    }
    return rename($tmp, AUTH_DATA_FILE);
}

function save_auth_data(array $data): bool
{
    return write_auth_data($data);
}

function ensure_auth_data(): void
{
    ensure_auth_dir();
    if (!file_exists(AUTH_DATA_FILE)) {
        seed_auth_data();
    }
}

function seed_auth_data(): void
{
    $data = [
        'ldap' => DEFAULT_LDAP,
        'global' => DEFAULT_GLOBAL,
        'users' => [
            [
                'id' => uuid4(),
                'username' => 'admin',
                'type' => 'local',
                'password_hash' => password_hash('admin', PASSWORD_DEFAULT),
                'roles' => ['admin'],
                'permission_overrides' => [],
                'prefs' => DEFAULT_PREFS,
                'enabled' => true,
                'must_change_password' => true,
            ],
        ],
    ];
    write_auth_data($data);
}

function uuid4(): string
{
    $data = random_bytes(16);
    $data[6] = chr((ord($data[6]) & 0x0f) | 0x40);
    $data[8] = chr((ord($data[8]) & 0x3f) | 0x80);
    return vsprintf('%s%s-%s-%s-%s-%s%s%s', str_split(bin2hex($data), 4));
}

function is_logged_in(): bool
{
    return !empty($_SESSION['user_id']);
}

function current_user_record(): ?array
{
    if (!is_logged_in()) {
        return null;
    }
    $data = load_auth_data();
    foreach ($data['users'] as $user) {
        if (($user['id'] ?? '') === $_SESSION['user_id']) {
            return $user;
        }
    }
    return null;
}

function effective_permissions(array $user): array
{
    $merged = [];
    foreach (PERMISSIONS as $perm) {
        $merged[$perm] = false;
    }
    foreach ($user['roles'] ?? [] as $roleId) {
        $role = DEFAULT_ROLES[$roleId] ?? null;
        if (!$role) {
            continue;
        }
        foreach ($role['permissions'] as $perm => $val) {
            if ($val) {
                $merged[$perm] = true;
            }
        }
    }
    foreach ($user['permission_overrides'] ?? [] as $perm => $val) {
        if (in_array($perm, PERMISSIONS, true)) {
            $merged[$perm] = (bool)$val;
        }
    }
    return $merged;
}

function effective_prefs(array $user, array $global): array
{
    return array_merge(DEFAULT_PREFS, $user['prefs'] ?? []);
}

function has_permission(string $perm): bool
{
    $payload = session_user_payload_full();
    if (!$payload) {
        return false;
    }
    return !empty($payload['permissions'][$perm]);
}

function session_idle_minutes(): int
{
    $data = load_auth_data();
    return max(5, min(1440, (int)($data['global']['session_idle_minutes'] ?? 120)));
}

function user_has_kiosk_role(array $user): bool
{
    return in_array('kiosk', $user['roles'] ?? [], true);
}

function is_kiosk_session(): bool
{
    if (!is_logged_in()) {
        return false;
    }
    if (!empty($_SESSION['kiosk'])) {
        return true;
    }
    $user = current_user_record_with_ldap();
    return $user !== null && user_has_kiosk_role($user);
}

function init_session(): void
{
    if (session_status() !== PHP_SESSION_NONE) {
        return;
    }
    $gcLifetime = max(session_idle_minutes() * 60, KIOSK_SESSION_LIFETIME);
    ini_set('session.gc_maxlifetime', (string)$gcLifetime);
    $secure = !empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off';
    session_set_cookie_params([
        'lifetime' => 0,
        'path' => '/',
        'secure' => $secure,
        'httponly' => true,
        'samesite' => 'Lax',
    ]);
    session_start();
}

function refresh_session_cookie(int $lifetime): void
{
    $p = session_get_cookie_params();
    setcookie(session_name(), session_id(), [
        'expires' => $lifetime > 0 ? time() + $lifetime : 0,
        'path' => $p['path'] ?: '/',
        'domain' => $p['domain'] ?? '',
        'secure' => $p['secure'] ?? false,
        'httponly' => $p['httponly'] ?? true,
        'samesite' => $p['samesite'] ?? 'Lax',
    ]);
}

function check_session_idle(string $redirect = 'login.php'): void
{
    if (is_kiosk_session()) {
        return;
    }
    $idleSec = session_idle_minutes() * 60;
    $last = (int)($_SESSION['last_activity'] ?? $_SESSION['login_at'] ?? 0);
    if ($last > 0 && (time() - $last) > $idleSec) {
        logout_user();
        header('Location: ' . $redirect . '?reason=timeout');
        exit;
    }
}

function touch_session(): void
{
    $_SESSION['last_activity'] = time();
}

function require_login(string $redirect = 'login.php'): void
{
    if (!is_logged_in()) {
        $next = $_SERVER['REQUEST_URI'] ?? 'index.php';
        header('Location: ' . $redirect . '?next=' . urlencode($next));
        exit;
    }
    $user = current_user_record_with_ldap();
    if (!$user || empty($user['enabled'])) {
        logout_user();
        header('Location: ' . $redirect);
        exit;
    }
    check_session_idle($redirect);
    touch_session();
}

function require_permission(string $perm): void
{
    require_login();
    if (!has_permission($perm)) {
        http_response_code(403);
        die('Access denied.');
    }
}

function login_user(array $user): void
{
    $kiosk = user_has_kiosk_role($user);
    session_regenerate_id(true);
    $_SESSION['user_id'] = $user['id'];
    $_SESSION['username'] = $user['username'];
    $_SESSION['kiosk'] = $kiosk;
    $_SESSION['login_at'] = time();
    $_SESSION['last_activity'] = time();
    refresh_session_cookie($kiosk ? KIOSK_SESSION_LIFETIME : 0);
}

function logout_user(): void
{
    $_SESSION = [];
    if (ini_get('session.use_cookies')) {
        $p = session_get_cookie_params();
        setcookie(session_name(), '', time() - 42000, $p['path'], $p['domain'], $p['secure'], $p['httponly']);
    }
    session_destroy();
}

function ldap_build_uri(array $ldap): ?string
{
    $host = trim($ldap['host'] ?? '');
    if ($host === '') {
        return null;
    }
    $port = (int)($ldap['port'] ?? 636);
    if (!str_contains($host, '://')) {
        $host = 'ldaps://' . ltrim($host, '/');
    }
    if (!preg_match('#:\d+$#', $host)) {
        $host .= ':' . $port;
    }
    return $host;
}

function ldap_apply_tls_options(bool $ignoreCert): void
{
    if (!$ignoreCert) {
        return;
    }
    @ldap_set_option(null, LDAP_OPT_X_TLS_REQUIRE_CERT, LDAP_OPT_X_TLS_NEVER);
}

function ldap_connect_config(array $ldap): mixed
{
    if (!extension_loaded('ldap')) {
        return null;
    }
    $uri = ldap_build_uri($ldap);
    if ($uri === null) {
        return null;
    }
    ldap_apply_tls_options(!empty($ldap['ignore_cert']));
    $conn = @ldap_connect($uri);
    if (!$conn) {
        return null;
    }
    ldap_set_option($conn, LDAP_OPT_PROTOCOL_VERSION, 3);
    ldap_set_option($conn, LDAP_OPT_REFERRALS, 0);
    if (!empty($ldap['ignore_cert'])) {
        @ldap_set_option($conn, LDAP_OPT_X_TLS_REQUIRE_CERT, LDAP_OPT_X_TLS_NEVER);
    }
    return $conn;
}

function ldap_make_bind_dn(string $username, array $ldap): string
{
    $template = $ldap['bind_template'] ?? '{username}';
    return str_replace('{username}', $username, $template);
}

function ldap_infer_base_dn(array $ldap): string
{
    if (!empty($ldap['base_dn'])) {
        return trim($ldap['base_dn']);
    }
    $template = $ldap['bind_template'] ?? '';
    if (preg_match('/@([^{}\s]+)/', $template, $m)) {
        $parts = explode('.', strtolower(trim($m[1])));
        $parts = array_filter($parts, fn($p) => $p !== '');
        if ($parts) {
            return implode(',', array_map(fn($p) => 'DC=' . $p, $parts));
        }
    }
    return '';
}

function ldap_whoami_dn(mixed $conn): ?string
{
    if (!function_exists('ldap_exop_whoami')) {
        return null;
    }
    $whoami = @ldap_exop_whoami($conn);
    if (!is_string($whoami) || $whoami === '') {
        return null;
    }
    if (str_starts_with($whoami, 'dn:')) {
        return substr($whoami, 3);
    }
    return null;
}

function ldap_read_member_of_at_dn(mixed $conn, string $dn): array
{
    if ($dn === '') {
        return [];
    }
    $result = @ldap_read($conn, $dn, '(objectClass=*)', ['memberOf'], 0, 1);
    if (!$result) {
        return [];
    }
    $entries = ldap_get_entries($conn, $result);
    if ($entries['count'] < 1) {
        return [];
    }
    $memberOf = $entries[0]['memberof'] ?? [];
    unset($memberOf['count']);
    return array_values($memberOf);
}

function ldap_fetch_member_of(mixed $conn, string $loginUsername, string $bindDn, array $ldap): array
{
    $dn = ldap_whoami_dn($conn);
    if ($dn) {
        $groups = ldap_read_member_of_at_dn($conn, $dn);
        if ($groups) {
            return ['ok' => true, 'member_of' => $groups];
        }
    }
    $groups = ldap_read_member_of_at_dn($conn, $bindDn);
    if ($groups) {
        return ['ok' => true, 'member_of' => $groups];
    }
    $base = ldap_infer_base_dn($ldap);
    if ($base === '') {
        return [
            'ok' => false,
            'error' => 'LDAP base DN not configured — set base_dn or use a bind_template with @domain.com',
        ];
    }
    $safe = ldap_escape($loginUsername, '', LDAP_ESCAPE_FILTER);
    $result = @ldap_search($conn, $base, "(sAMAccountName=$safe)", ['memberOf'], 0, 1);
    if (!$result) {
        $err = @ldap_error($conn) ?: '';
        return ['ok' => false, 'error' => 'LDAP group lookup failed' . ($err !== '' ? ' (' . $err . ')' : '')];
    }
    $entries = ldap_get_entries($conn, $result);
    if (!is_array($entries) || ($entries['count'] ?? 0) < 1) {
        return ['ok' => false, 'error' => 'LDAP user not found for group lookup'];
    }
    $memberOf = $entries[0]['memberof'] ?? [];
    unset($memberOf['count']);
    return ['ok' => true, 'member_of' => array_values($memberOf)];
}

function ldap_bind_user(array $ldap, string $username, string $password): array
{
    if ($password === '') {
        return ['ok' => false, 'error' => 'Password required'];
    }
    $conn = ldap_connect_config($ldap);
    if (!$conn) {
        return ['ok' => false, 'error' => 'LDAP unavailable — check host, port, and php-ldap extension'];
    }
    $bindDn = ldap_make_bind_dn($username, $ldap);
    if (!@ldap_bind($conn, $bindDn, $password)) {
        $err = @ldap_error($conn) ?: '';
        ldap_unbind($conn);
        if (stripos($err, "Can't contact") !== false || stripos($err, 'connect') !== false) {
            return ['ok' => false, 'error' => 'LDAP server unreachable (' . $err . ')'];
        }
        return ['ok' => false, 'error' => 'Invalid username or password'];
    }
    $memberResult = ldap_fetch_member_of($conn, $username, $bindDn, $ldap);
    ldap_unbind($conn);
    if (empty($memberResult['ok'])) {
        return ['ok' => false, 'error' => $memberResult['error'] ?? 'LDAP group lookup failed'];
    }
    return ['ok' => true, 'bind_dn' => $bindDn, 'member_of' => $memberResult['member_of']];
}

function ldap_group_matches(string $memberOfDn, string $groupName): bool
{
    $groupName = strtolower(trim($groupName));
    if ($groupName === '') {
        return false;
    }
    $dn = strtolower($memberOfDn);
    if ($dn === $groupName) {
        return true;
    }
    if (preg_match('/^cn=([^,]+)/', $dn, $m)) {
        return strtolower($m[1]) === $groupName;
    }
    return str_contains($dn, $groupName);
}

function roles_from_ldap_groups(array $memberOf, array $allowedGroups): array
{
    $roles = [];
    foreach ($allowedGroups as $entry) {
        if (is_string($entry)) {
            $entry = ['name' => $entry, 'roles' => ['viewer']];
        }
        $name = $entry['name'] ?? '';
        $groupRoles = $entry['roles'] ?? ['viewer'];
        foreach ($memberOf as $dn) {
            if (ldap_group_matches($dn, $name)) {
                foreach ($groupRoles as $r) {
                    $roles[$r] = true;
                }
            }
        }
    }
    return array_keys($roles);
}

function find_user_by_username(string $username): ?array
{
    $data = load_auth_data();
    $username = strtolower(trim($username));
    foreach ($data['users'] as $user) {
        if (strtolower($user['username'] ?? '') === $username) {
            return $user;
        }
    }
    return null;
}

function authenticate(string $username, string $password): array
{
    $data = load_auth_data();
    $user = find_user_by_username($username);

    if ($user && empty($user['enabled'])) {
        return ['ok' => false, 'error' => 'Account disabled'];
    }

    if ($user && ($user['type'] ?? 'local') === 'local') {
        if (!password_verify($password, $user['password_hash'] ?? '')) {
            return ['ok' => false, 'error' => 'Invalid credentials'];
        }
        login_user($user);
        return ['ok' => true, 'user' => $user];
    }

    $ldap = $data['ldap'];
    if (empty($ldap['enabled'])) {
        if ($user && ($user['type'] ?? '') === 'ldap') {
            return ['ok' => false, 'error' => 'LDAP is not enabled'];
        }
        return ['ok' => false, 'error' => 'Invalid credentials'];
    }

    $ldapResult = ldap_bind_user($ldap, $username, $password);
    if (!$ldapResult['ok']) {
        return ['ok' => false, 'error' => $ldapResult['error']];
    }

    if ($user) {
        if (($user['type'] ?? '') !== 'ldap') {
            return ['ok' => false, 'error' => 'Invalid credentials'];
        }
        login_user($user);
        return ['ok' => true, 'user' => $user];
    }

    $groupRoles = roles_from_ldap_groups($ldapResult['member_of'], $ldap['allowed_groups'] ?? []);
    if (empty($groupRoles)) {
        return ['ok' => false, 'error' => 'Not authorized — no matching LDAP group'];
    }

    $ephemeral = [
        'id' => 'ldap:' . strtolower($username),
        'username' => $username,
        'type' => 'ldap',
        'roles' => $groupRoles,
        'permission_overrides' => [],
        'prefs' => DEFAULT_PREFS,
        'enabled' => true,
        'must_change_password' => false,
    ];
    login_user($ephemeral);
    $_SESSION['ldap_ephemeral'] = true;
    $_SESSION['ldap_roles'] = $groupRoles;
    return ['ok' => true, 'user' => $ephemeral, 'ephemeral' => true];
}

function current_user_record_with_ldap(): ?array
{
    if (!is_logged_in()) {
        return null;
    }
    if (!empty($_SESSION['ldap_ephemeral'])) {
        return [
            'id' => $_SESSION['user_id'],
            'username' => $_SESSION['username'] ?? '',
            'type' => 'ldap',
            'roles' => $_SESSION['ldap_roles'] ?? ['viewer'],
            'permission_overrides' => [],
            'prefs' => DEFAULT_PREFS,
            'enabled' => true,
            'must_change_password' => false,
        ];
    }
    return current_user_record();
}

function session_user_payload_full(): ?array
{
    $user = current_user_record_with_ldap();
    if (!$user) {
        return null;
    }
    $data = load_auth_data();
    if (!empty($_SESSION['ldap_ephemeral'])) {
        $stored = find_user_by_username($user['username']);
        if ($stored && ($stored['type'] ?? '') === 'ldap') {
            $user = $stored;
            unset($_SESSION['ldap_ephemeral'], $_SESSION['ldap_roles']);
        }
    }
    $perms = effective_permissions($user);
    $prefs = effective_prefs($user, $data['global']);
    return [
        'id' => $user['id'],
        'username' => $user['username'],
        'type' => $user['type'] ?? 'local',
        'roles' => $user['roles'] ?? [],
        'permissions' => $perms,
        'prefs' => $prefs,
        'forced_prefs' => [],
        'must_change_password' => !empty($user['must_change_password']),
        'is_kiosk' => is_kiosk_session(),
    ];
}

init_session();

function json_response(array $payload, int $code = 200): void
{
    http_response_code($code);
    header('Content-Type: application/json');
    header('Cache-Control: no-store');
    echo json_encode($payload);
    exit;
}
