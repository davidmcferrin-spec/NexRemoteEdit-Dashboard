'use strict';

async function refreshStatus() {
  const d = await (await fetch('log.php?action=status')).json();
  const el = document.getElementById('svcStatus');
  el.className = 'service-status ' + (d.active ? 'active' : 'inactive');
  el.querySelector('.service-label').textContent = d.state || (d.active ? 'active' : 'inactive');
}

async function refreshLog() {
  if (document.getElementById('logPause').checked) return;
  const n = document.getElementById('logLines').value;
  const d = await (await fetch('log.php?action=log&lines=' + n)).json();
  const view = document.getElementById('logView');
  view.textContent = (d.lines && d.lines.length)
    ? d.lines.join('\n')
    : (d.ok === false ? 'Could not read journal (sudo -n / sudoers).' : '');
  view.scrollTop = view.scrollHeight;
}

document.querySelectorAll('[data-svc]').forEach(btn => {
  btn.addEventListener('click', async () => {
    if (!confirm(btn.dataset.svc + ' nre-bridge?')) return;
    const d = await (await fetch('log.php?action=' + btn.dataset.svc)).json();
    toast(d.ok ? 'success' : 'error', d.ok ? btn.dataset.svc + ' issued' : (d.output || 'failed'));
    refreshStatus();
  });
});

refreshStatus();
refreshLog();
setInterval(refreshStatus, 4000);
setInterval(refreshLog, 2000);
