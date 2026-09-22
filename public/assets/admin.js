'use strict';

let adminData = null;

async function apiPost(action, payload = {}) {
  const r = await fetch('api/admin.php', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, ...payload }),
  });
  return r.json();
}

async function loadAdmin() {
  const r = await fetch('api/admin.php');
  adminData = await r.json();
  if (!adminData.ok) { toast('error', adminData.error || 'Load failed'); return; }
  renderUsers();
  renderLdap();
  renderGroups();
  document.getElementById('sessionIdleMinutes').value = adminData.global.session_idle_minutes ?? 120;
  populateRoleSelects();
}

function renderUsers() {
  const tbody = document.getElementById('usersBody');
  tbody.innerHTML = adminData.users.map(u => {
    const overrideCount = Object.keys(u.permission_overrides || {}).length;
    return `<tr>
      <td>${esc(u.username)}</td>
      <td>${esc(u.type || 'local')}</td>
      <td>${(u.roles || []).map(esc).join(', ')}</td>
      <td>${overrideCount ? overrideCount : '—'}</td>
      <td>${u.enabled ? 'Yes' : 'No'}</td>
      <td><button class="btn btn-sm btn-secondary" data-edit-user="${esc(u.id)}">Edit</button></td>
    </tr>`;
  }).join('');
  tbody.querySelectorAll('[data-edit-user]').forEach(btn => {
    btn.addEventListener('click', () => openUserModal(btn.dataset.editUser));
  });
}

function renderLdap() {
  const l = adminData.ldap;
  document.getElementById('ldapEnabled').checked = !!l.enabled;
  document.getElementById('ldapHost').value = l.host || '';
  document.getElementById('ldapPort').value = l.port || 636;
  document.getElementById('ldapBindTemplate').value = l.bind_template || '';
  document.getElementById('ldapBaseDn').value = l.base_dn || '';
  document.getElementById('ldapIgnoreCert').checked = l.ignore_cert !== false;
}

function renderGroups() {
  const list = document.getElementById('ldapGroupsList');
  const groups = adminData.ldap.allowed_groups || [];
  if (!groups.length) {
    list.innerHTML = '<li class="hint">No LDAP groups configured.</li>';
    return;
  }
  list.innerHTML = groups.map(g => {
    const name = typeof g === 'string' ? g : g.name;
    const roles = typeof g === 'string' ? ['viewer'] : (g.roles || []);
    return `<li class="admin-list-item">
      <span><strong>${esc(name)}</strong> → ${roles.map(esc).join(', ')}</span>
      <span class="admin-list-actions">
        <button class="btn btn-sm btn-secondary" data-edit-group="${esc(name)}">Edit</button>
        <button class="btn btn-sm btn-danger" data-rm-group="${esc(name)}">Remove</button>
      </span>
    </li>`;
  }).join('');
  list.querySelectorAll('[data-edit-group]').forEach(btn => {
    btn.addEventListener('click', () => openLdapGroupModal(btn.dataset.editGroup));
  });
  list.querySelectorAll('[data-rm-group]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const d = await apiPost('remove_ldap_group', { name: btn.dataset.rmGroup });
      if (d.ok) { toast('success', 'Group removed'); loadAdmin(); }
      else toast('error', d.error);
    });
  });
}

function populateRoleSelects() {
  const sel = document.getElementById('newGroupRoles');
  sel.innerHTML = Object.entries(adminData.roles).map(([id, r]) =>
    `<option value="${esc(id)}" title="${esc(r.description || '')}">${esc(r.label)}</option>`
  ).join('');
}

function rolePermissions(roleIds) {
  const merged = {};
  for (const perm of adminData.permissions) merged[perm] = false;
  for (const rid of roleIds) {
    const role = adminData.roles[rid];
    if (!role) continue;
    for (const [perm, val] of Object.entries(role.permissions || {})) {
      if (val) merged[perm] = true;
    }
  }
  return merged;
}

function effectivePermissionsForUser(roles, overrides) {
  const merged = rolePermissions(roles);
  for (const [perm, val] of Object.entries(overrides || {})) {
    if (adminData.permissions.includes(perm)) merged[perm] = !!val;
  }
  return merged;
}

function renderPermOverrides(user) {
  const box = document.getElementById('userPermOverrides');
  const roles = [...document.querySelectorAll('input[name=userRole]:checked')].map(c => c.value);
  const overrides = user.permission_overrides || {};
  const effective = effectivePermissionsForUser(roles, overrides);
  box.innerHTML = adminData.permissions.map(perm => {
    const val = overrides[perm];
    const mode = val === undefined ? 'inherit' : (val ? 'grant' : 'deny');
    const meta = (adminData.permission_meta && adminData.permission_meta[perm]) || {};
    return `<div class="perm-override-row">
      <span class="perm-override-name" title="${esc(meta.description || '')}">${esc(meta.label || perm)}</span>
      <select class="perm-override-select" data-perm="${esc(perm)}">
        <option value="inherit" ${mode === 'inherit' ? 'selected' : ''}>Role default</option>
        <option value="grant" ${mode === 'grant' ? 'selected' : ''}>Grant</option>
        <option value="deny" ${mode === 'deny' ? 'selected' : ''}>Deny</option>
      </select>
      <span class="perm-override-effective ${effective[perm] ? 'perm-yes' : 'perm-no'}">${effective[perm] ? 'Yes' : 'No'}</span>
    </div>`;
  }).join('');
  box.querySelectorAll('.perm-override-select').forEach(sel => {
    sel.addEventListener('change', () => {
      renderPermOverrides({ permission_overrides: collectPermOverridesFromForm() });
    });
  });
}

function collectPermOverridesFromForm() {
  const overrides = {};
  document.querySelectorAll('.perm-override-select').forEach(sel => {
    if (sel.value === 'grant') overrides[sel.dataset.perm] = true;
    else if (sel.value === 'deny') overrides[sel.dataset.perm] = false;
  });
  return overrides;
}

function roleCheckboxHtml(id, role, checked, inputName = 'userRole') {
  return `<label class="checkbox-label role-option" title="${esc(role.description || '')}">
    <input type="checkbox" name="${esc(inputName)}" value="${esc(id)}" ${checked ? 'checked' : ''}>
    <span>${esc(role.label)}</span>
  </label>`;
}

function findLdapGroup(name) {
  return (adminData.ldap.allowed_groups || []).find(g => {
    const gn = typeof g === 'string' ? g : g.name;
    return gn && gn.toLowerCase() === name.toLowerCase();
  });
}

function openLdapGroupModal(name) {
  const entry = findLdapGroup(name);
  if (!entry) return;
  const groupName = typeof entry === 'string' ? entry : entry.name;
  const roles = typeof entry === 'string' ? ['viewer'] : (entry.roles || ['viewer']);
  document.getElementById('ldapGroupName').value = groupName;
  document.getElementById('ldapGroupNameDisplay').value = groupName;
  document.getElementById('ldapGroupRolesCheckboxes').innerHTML =
    Object.entries(adminData.roles).map(([id, r]) => roleCheckboxHtml(id, r, roles.includes(id), 'ldapGroupRole')).join('');
  document.getElementById('modalLdapGroup').removeAttribute('hidden');
}

function openUserModal(userId) {
  const isNew = !userId;
  document.getElementById('userModalTitle').textContent = isNew ? 'Add User' : 'Edit User';
  document.getElementById('userId').value = userId || '';
  document.getElementById('btnDeleteUser').style.display = isNew ? 'none' : '';
  const user = isNew
    ? { username: '', type: 'local', roles: ['viewer'], enabled: true, permission_overrides: {} }
    : adminData.users.find(u => u.id === userId);
  document.getElementById('userUsername').value = user.username || '';
  document.getElementById('userType').value = user.type || 'local';
  document.getElementById('userPassword').value = '';
  document.getElementById('userEnabled').checked = user.enabled !== false;
  togglePasswordRow();
  const rolesBox = document.getElementById('userRolesCheckboxes');
  rolesBox.innerHTML = Object.entries(adminData.roles).map(([id, r]) =>
    roleCheckboxHtml(id, r, (user.roles || []).includes(id))).join('');
  rolesBox.querySelectorAll('input[name=userRole]').forEach(cb => {
    cb.addEventListener('change', () => {
      renderPermOverrides({ permission_overrides: collectPermOverridesFromForm() });
    });
  });
  renderPermOverrides(user);
  document.getElementById('modalUser').removeAttribute('hidden');
}

function togglePasswordRow() {
  document.getElementById('userPasswordRow').style.display =
    document.getElementById('userType').value === 'local' ? '' : 'none';
}

function esc(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');
}

document.getElementById('userType').addEventListener('change', togglePasswordRow);
document.getElementById('btnAddUser').addEventListener('click', () => openUserModal(null));
document.getElementById('btnSaveUser').addEventListener('click', async () => {
  const roles = [...document.querySelectorAll('input[name=userRole]:checked')].map(c => c.value);
  const d = await apiPost('save_user', {
    id: document.getElementById('userId').value || undefined,
    username: document.getElementById('userUsername').value.trim(),
    type: document.getElementById('userType').value,
    password: document.getElementById('userPassword').value,
    roles,
    enabled: document.getElementById('userEnabled').checked,
    permission_overrides: collectPermOverridesFromForm(),
  });
  if (d.ok) {
    toast('success', 'User saved');
    document.getElementById('modalUser').setAttribute('hidden', '');
    loadAdmin();
  } else toast('error', d.error);
});
document.getElementById('btnDeleteUser').addEventListener('click', async () => {
  const id = document.getElementById('userId').value;
  if (!id || !confirm('Delete this user?')) return;
  const d = await apiPost('delete_user', { id });
  if (d.ok) {
    toast('success', 'User deleted');
    document.getElementById('modalUser').setAttribute('hidden', '');
    loadAdmin();
  } else toast('error', d.error);
});
document.getElementById('ldapForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const d = await apiPost('save_ldap', {
    enabled: document.getElementById('ldapEnabled').checked,
    host: document.getElementById('ldapHost').value.trim(),
    port: parseInt(document.getElementById('ldapPort').value, 10) || 636,
    bind_template: document.getElementById('ldapBindTemplate').value.trim(),
    base_dn: document.getElementById('ldapBaseDn').value.trim(),
    ignore_cert: document.getElementById('ldapIgnoreCert').checked,
  });
  if (d.ok) toast('success', 'LDAP settings saved');
  else toast('error', d.error);
});
document.getElementById('btnAddGroup').addEventListener('click', async () => {
  const name = document.getElementById('newGroupName').value.trim();
  const roles = [...document.getElementById('newGroupRoles').selectedOptions].map(o => o.value);
  const d = await apiPost('add_ldap_group', { name, roles });
  if (d.ok) { document.getElementById('newGroupName').value = ''; toast('success', 'Group added'); loadAdmin(); }
  else toast('error', d.error);
});
document.getElementById('btnSaveLdapGroup').addEventListener('click', async () => {
  const name = document.getElementById('ldapGroupName').value;
  const roles = [...document.querySelectorAll('input[name=ldapGroupRole]:checked')].map(c => c.value);
  if (!roles.length) { toast('error', 'Select at least one role'); return; }
  const d = await apiPost('update_ldap_group', { name, roles });
  if (d.ok) {
    toast('success', 'Group roles updated');
    document.getElementById('modalLdapGroup').setAttribute('hidden', '');
    loadAdmin();
  } else toast('error', d.error);
});
document.getElementById('sessionForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const d = await apiPost('save_global', {
    session_idle_minutes: parseInt(document.getElementById('sessionIdleMinutes').value, 10) || 120,
  });
  if (d.ok) toast('success', 'Session settings saved');
  else toast('error', d.error);
});
document.querySelectorAll('[data-modal]').forEach(btn => {
  btn.addEventListener('click', () => document.getElementById(btn.dataset.modal).setAttribute('hidden', ''));
});

loadAdmin();
