'use strict';

function nreApplyTheme(theme) {
  if (theme !== 'dark' && theme !== 'light') theme = 'dark';
  document.documentElement.setAttribute('data-theme', theme);
  try { localStorage.setItem('nre-theme', theme); } catch (e) { /* private mode */ }
  nreUpdateThemeButton(theme);
}

function nreUpdateThemeButton(theme) {
  const btn = document.getElementById('btnTheme');
  if (!btn) return;
  btn.textContent = theme === 'dark' ? '☀' : '🌙';
  btn.title = theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme';
}

async function nreSaveThemePreference(theme) {
  try { localStorage.setItem('nre-theme', theme); } catch (e) { /* private mode */ }
  try {
    await fetch('api/profile.php', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'save_prefs', theme }),
    });
  } catch (e) { /* localStorage fallback */ }
}

function toast(type, msg) {
  const c = document.getElementById('toastContainer');
  if (!c) return;
  const el = document.createElement('div');
  el.className = 'toast ' + type;
  el.textContent = msg;
  c.appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

(function nreInitThemeToggle() {
  const current = document.documentElement.getAttribute('data-theme') || 'dark';
  nreUpdateThemeButton(current);
  const btn = document.getElementById('btnTheme');
  if (!btn) return;
  btn.addEventListener('click', () => {
    const now = document.documentElement.getAttribute('data-theme') || 'dark';
    const next = now === 'dark' ? 'light' : 'dark';
    nreApplyTheme(next);
    nreSaveThemePreference(next);
  });
})();
