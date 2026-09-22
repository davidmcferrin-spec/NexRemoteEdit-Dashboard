<?php
require_once __DIR__ . '/includes/auth.php';
require_permission('manage_users');
$user = session_user_payload_full();
$nre_active = 'admin';
?>
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Admin — NexEditorStats</title>
  <?php require __DIR__ . '/includes/theme_head.php'; ?>
  <link rel="stylesheet" href="assets/style.css">
</head>
<body>
<?php require __DIR__ . '/includes/nav.php'; ?>
<main class="admin-page">
  <h1 class="page-title">Users &amp; LDAP</h1>
  <p class="hint">Local + LDAP (same pattern as xpmon). App secrets (Jump, Postgres, Telegraf) live under <a href="settings.php">Settings</a>, not here.</p>

  <section class="admin-section">
    <h2>Users</h2>
    <div class="admin-toolbar">
      <button class="btn btn-sm" id="btnAddUser">+ Add User</button>
    </div>
    <div class="admin-table-wrap">
      <table class="admin-table" id="usersTable">
        <thead>
          <tr><th>Username</th><th>Type</th><th>Roles</th><th>Overrides</th><th>Enabled</th><th></th></tr>
        </thead>
        <tbody id="usersBody"></tbody>
      </table>
    </div>
  </section>

  <section class="admin-section">
    <h2>LDAP Settings</h2>
    <form id="ldapForm" class="admin-form">
      <label class="checkbox-label">
        <input type="checkbox" id="ldapEnabled">
        <span>Enable LDAP authentication</span>
      </label>
      <label>LDAP Host <input type="text" id="ldapHost" placeholder="ldaps://ad.example.com"></label>
      <label>Port <input type="number" id="ldapPort" value="636"></label>
      <label>Bind template <span class="hint-inline">{username}@nexstar.tv</span>
        <input type="text" id="ldapBindTemplate" placeholder="{username}@nexstar.tv">
      </label>
      <label>Base DN <span class="hint-inline">optional</span>
        <input type="text" id="ldapBaseDn" placeholder="DC=nexstar,DC=tv">
      </label>
      <label class="checkbox-label">
        <input type="checkbox" id="ldapIgnoreCert" checked>
        <span>Ignore SSL certificate errors</span>
      </label>
      <button type="submit" class="btn btn-sm">Save LDAP Settings</button>
    </form>
  </section>

  <section class="admin-section">
    <h2>LDAP Groups</h2>
    <p class="hint">AD groups that can sign in without a pre-created account. Match by CN or full DN.</p>
    <div class="admin-inline-form">
      <input type="text" id="newGroupName" placeholder="Group name">
      <select id="newGroupRoles" multiple size="3"></select>
      <button class="btn btn-sm" id="btnAddGroup">Add Group</button>
    </div>
    <ul class="admin-list" id="ldapGroupsList"></ul>
  </section>

  <section class="admin-section">
    <h2>Session Settings</h2>
    <form id="sessionForm" class="admin-form">
      <label>Session idle timeout (minutes)
        <input type="number" id="sessionIdleMinutes" min="5" max="1440" value="120">
      </label>
      <button type="submit" class="btn btn-sm">Save Session Settings</button>
    </form>
  </section>
</main>

<div class="modal-overlay" id="modalUser" hidden>
  <div class="modal modal-lg">
    <div class="modal-header">
      <h2 id="userModalTitle">Edit User</h2>
      <button class="modal-close" data-modal="modalUser">✕</button>
    </div>
    <div class="modal-body">
      <input type="hidden" id="userId">
      <label>Username <input type="text" id="userUsername"></label>
      <label>Account type
        <select id="userType">
          <option value="local">Local</option>
          <option value="ldap">LDAP</option>
        </select>
      </label>
      <label id="userPasswordRow">Password <span class="hint-inline">leave blank to keep</span>
        <input type="password" id="userPassword" autocomplete="new-password">
      </label>
      <label class="checkbox-label">
        <input type="checkbox" id="userEnabled" checked>
        <span>Account enabled</span>
      </label>
      <div class="edit-section-title">Roles</div>
      <div id="userRolesCheckboxes" class="checkbox-grid"></div>
      <div class="edit-section-title">Permission overrides</div>
      <div class="perm-override-header">
        <span>Permission</span><span>Override</span><span>Effective</span>
      </div>
      <div id="userPermOverrides" class="perm-override-grid"></div>
    </div>
    <div class="modal-footer">
      <button class="btn btn-danger btn-sm" id="btnDeleteUser" style="margin-right:auto">Delete User</button>
      <button class="btn" id="btnSaveUser">Save</button>
      <button class="btn btn-secondary" data-modal="modalUser">Cancel</button>
    </div>
  </div>
</div>

<div class="modal-overlay" id="modalLdapGroup" hidden>
  <div class="modal">
    <div class="modal-header">
      <h2>Edit LDAP Group</h2>
      <button class="modal-close" data-modal="modalLdapGroup">✕</button>
    </div>
    <div class="modal-body">
      <input type="hidden" id="ldapGroupName">
      <label>AD group name <input type="text" id="ldapGroupNameDisplay" readonly></label>
      <div class="edit-section-title">Roles</div>
      <div id="ldapGroupRolesCheckboxes" class="checkbox-grid"></div>
    </div>
    <div class="modal-footer">
      <button class="btn" id="btnSaveLdapGroup">Save</button>
      <button class="btn btn-secondary" data-modal="modalLdapGroup">Cancel</button>
    </div>
  </div>
</div>

<div class="toast-container" id="toastContainer"></div>
<script src="assets/theme.js"></script>
<script src="assets/admin.js"></script>
</body>
</html>
