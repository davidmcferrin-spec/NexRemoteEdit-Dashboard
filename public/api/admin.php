<?php
require_once __DIR__ . '/../includes/auth.php';

require_login();
if (!has_permission('manage_users')) {
    json_response(['ok' => false, 'error' => 'Access denied'], 403);
}

$method = $_SERVER['REQUEST_METHOD'];

if ($method === 'GET') {
    $data = load_auth_data();
    $users = array_map(function ($u) {
        unset($u['password_hash']);
        return $u;
    }, $data['users']);
    json_response([
        'ok' => true,
        'roles' => DEFAULT_ROLES,
        'permissions' => PERMISSIONS,
        'permission_meta' => PERMISSION_META,
        'ldap' => $data['ldap'],
        'global' => $data['global'],
        'users' => $users,
    ]);
}

if ($method !== 'POST') {
    json_response(['ok' => false, 'error' => 'Method not allowed'], 405);
}

$body = json_decode(file_get_contents('php://input') ?: '{}', true);
if (!is_array($body)) {
    json_response(['ok' => false, 'error' => 'Invalid JSON'], 400);
}

$action = $body['action'] ?? '';
$data = load_auth_data();

switch ($action) {
    case 'save_ldap':
        $ldap = $data['ldap'];
        $ldap['enabled'] = !empty($body['enabled']);
        $ldap['host'] = trim($body['host'] ?? $ldap['host']);
        $ldap['port'] = (int)($body['port'] ?? $ldap['port']);
        $ldap['bind_template'] = trim($body['bind_template'] ?? $ldap['bind_template']);
        $ldap['base_dn'] = trim($body['base_dn'] ?? $ldap['base_dn'] ?? '');
        $ldap['ignore_cert'] = !empty($body['ignore_cert']);
        $data['ldap'] = $ldap;
        save_auth_data($data);
        json_response(['ok' => true, 'ldap' => $data['ldap']]);

    case 'save_global':
        $g = $data['global'];
        if (array_key_exists('session_idle_minutes', $body)) {
            $g['session_idle_minutes'] = max(5, min(1440, (int)$body['session_idle_minutes']));
        }
        $data['global'] = $g;
        save_auth_data($data);
        json_response(['ok' => true, 'global' => $data['global']]);

    case 'add_ldap_group':
        $name = trim($body['name'] ?? '');
        $roles = $body['roles'] ?? ['viewer'];
        if ($name === '') {
            json_response(['ok' => false, 'error' => 'Group name required'], 400);
        }
        $roles = array_values(array_filter($roles, fn($r) => isset(DEFAULT_ROLES[$r])));
        if (empty($roles)) {
            $roles = ['viewer'];
        }
        foreach ($data['ldap']['allowed_groups'] as $g) {
            $gn = is_array($g) ? ($g['name'] ?? '') : $g;
            if (strcasecmp($gn, $name) === 0) {
                json_response(['ok' => false, 'error' => 'Group already exists'], 400);
            }
        }
        $data['ldap']['allowed_groups'][] = ['name' => $name, 'roles' => $roles];
        save_auth_data($data);
        json_response(['ok' => true, 'allowed_groups' => $data['ldap']['allowed_groups']]);

    case 'update_ldap_group':
        $name = trim($body['name'] ?? '');
        $roles = $body['roles'] ?? ['viewer'];
        if ($name === '') {
            json_response(['ok' => false, 'error' => 'Group name required'], 400);
        }
        $roles = array_values(array_filter($roles, fn($r) => isset(DEFAULT_ROLES[$r])));
        if (empty($roles)) {
            $roles = ['viewer'];
        }
        $found = false;
        foreach ($data['ldap']['allowed_groups'] as $i => $g) {
            $gn = is_array($g) ? ($g['name'] ?? '') : $g;
            if (strcasecmp($gn, $name) !== 0) {
                continue;
            }
            $storedName = is_array($g) ? ($g['name'] ?? $name) : $g;
            $data['ldap']['allowed_groups'][$i] = ['name' => $storedName, 'roles' => $roles];
            $found = true;
            break;
        }
        if (!$found) {
            json_response(['ok' => false, 'error' => 'Group not found'], 404);
        }
        save_auth_data($data);
        json_response(['ok' => true, 'allowed_groups' => $data['ldap']['allowed_groups']]);

    case 'remove_ldap_group':
        $name = trim($body['name'] ?? '');
        $data['ldap']['allowed_groups'] = array_values(array_filter(
            $data['ldap']['allowed_groups'],
            function ($g) use ($name) {
                $gn = is_array($g) ? ($g['name'] ?? '') : $g;
                return strcasecmp($gn, $name) !== 0;
            }
        ));
        save_auth_data($data);
        json_response(['ok' => true, 'allowed_groups' => $data['ldap']['allowed_groups']]);

    case 'save_user':
        $id = $body['id'] ?? '';
        $username = trim($body['username'] ?? '');
        $type = ($body['type'] ?? 'local') === 'ldap' ? 'ldap' : 'local';
        $roles = array_values(array_filter($body['roles'] ?? [], fn($r) => isset(DEFAULT_ROLES[$r])));
        $enabled = !empty($body['enabled']);
        $overrides = [];
        if (!empty($body['permission_overrides']) && is_array($body['permission_overrides'])) {
            foreach ($body['permission_overrides'] as $k => $v) {
                if (in_array($k, PERMISSIONS, true)) {
                    $overrides[$k] = (bool)$v;
                }
            }
        }
        if ($username === '') {
            json_response(['ok' => false, 'error' => 'Username required'], 400);
        }
        if (empty($roles)) {
            $roles = ['viewer'];
        }
        if ($id) {
            $found = false;
            foreach ($data['users'] as $i => $u) {
                if (($u['id'] ?? '') !== $id) {
                    continue;
                }
                foreach ($data['users'] as $other) {
                    if (($other['id'] ?? '') !== $id && strcasecmp($other['username'] ?? '', $username) === 0) {
                        json_response(['ok' => false, 'error' => 'Username already taken'], 400);
                    }
                }
                $data['users'][$i]['username'] = $username;
                $data['users'][$i]['type'] = $type;
                $data['users'][$i]['roles'] = $roles;
                $data['users'][$i]['enabled'] = $enabled;
                if (array_key_exists('permission_overrides', $body)) {
                    $data['users'][$i]['permission_overrides'] = $overrides;
                }
                if ($type === 'local' && !empty($body['password'])) {
                    if (strlen($body['password']) < 6) {
                        json_response(['ok' => false, 'error' => 'Password must be at least 6 characters'], 400);
                    }
                    $data['users'][$i]['password_hash'] = password_hash($body['password'], PASSWORD_DEFAULT);
                }
                if ($type === 'ldap') {
                    unset($data['users'][$i]['password_hash']);
                }
                $found = true;
                break;
            }
            if (!$found) {
                json_response(['ok' => false, 'error' => 'User not found'], 404);
            }
        } else {
            foreach ($data['users'] as $u) {
                if (strcasecmp($u['username'] ?? '', $username) === 0) {
                    json_response(['ok' => false, 'error' => 'Username already taken'], 400);
                }
            }
            $newUser = [
                'id' => uuid4(),
                'username' => $username,
                'type' => $type,
                'roles' => $roles,
                'permission_overrides' => $overrides,
                'prefs' => DEFAULT_PREFS,
                'enabled' => $enabled,
                'must_change_password' => false,
            ];
            if ($type === 'local') {
                $pass = $body['password'] ?? '';
                if (strlen($pass) < 6) {
                    json_response(['ok' => false, 'error' => 'Password must be at least 6 characters'], 400);
                }
                $newUser['password_hash'] = password_hash($pass, PASSWORD_DEFAULT);
            }
            $data['users'][] = $newUser;
        }
        save_auth_data($data);
        json_response(['ok' => true]);

    case 'delete_user':
        $id = $body['id'] ?? '';
        if ($id === $_SESSION['user_id']) {
            json_response(['ok' => false, 'error' => 'Cannot delete your own account'], 400);
        }
        $before = count($data['users']);
        $data['users'] = array_values(array_filter($data['users'], fn($u) => ($u['id'] ?? '') !== $id));
        if (count($data['users']) === $before) {
            json_response(['ok' => false, 'error' => 'User not found'], 404);
        }
        if (count($data['users']) === 0) {
            json_response(['ok' => false, 'error' => 'Cannot delete last user'], 400);
        }
        save_auth_data($data);
        json_response(['ok' => true]);

    default:
        json_response(['ok' => false, 'error' => 'Unknown action'], 400);
}
