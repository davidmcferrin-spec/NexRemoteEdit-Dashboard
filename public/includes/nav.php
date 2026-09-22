<?php
declare(strict_types=1);

/** @var array $user */
/** @var string $nre_active */

$nre_active = $nre_active ?? '';
$isKiosk = !empty($user['is_kiosk']);
$canHistory = !empty($user['permissions']['history']);
$canEvents = !empty($user['permissions']['events']);
$canMapping = !empty($user['permissions']['mapping']);
$canSettings = !empty($user['permissions']['settings']);
$canBridge = !empty($user['permissions']['bridge_view']);
$canAdmin = !empty($user['permissions']['manage_users']);

function nre_nav_btn(string $href, string $label, string $active, string $key): string
{
    $cls = $active === $key ? 'btn btn-sm' : 'btn btn-sm btn-secondary';
    return '<a href="' . htmlspecialchars($href) . '" class="' . $cls . '">' . htmlspecialchars($label) . '</a>';
}
?>
<header class="topbar">
  <div class="topbar-left">
    <a href="index.php" class="topbar-logo" style="text-decoration:none">Nex<span class="accent">EditorStats</span></a>
    <?php if (!$isKiosk): ?>
    <nav class="topbar-nav">
      <?= nre_nav_btn('index.php', 'Live', $nre_active, 'live') ?>
      <?php if ($canHistory): ?><?= nre_nav_btn('history.php', 'History', $nre_active, 'history') ?><?php endif; ?>
      <?php if ($canEvents): ?><?= nre_nav_btn('events.php', 'Events', $nre_active, 'events') ?><?php endif; ?>
      <?php if ($canEvents): ?><?= nre_nav_btn('windows.php', 'Windows', $nre_active, 'windows') ?><?php endif; ?>
      <?php if ($canMapping): ?><?= nre_nav_btn('mapping.php', 'Mapping', $nre_active, 'mapping') ?><?php endif; ?>
      <?= nre_nav_btn('telegraf.php', 'Telegraf', $nre_active, 'telegraf') ?>
      <?php if ($canSettings): ?><?= nre_nav_btn('settings.php', 'Settings', $nre_active, 'settings') ?><?php endif; ?>
      <?php if ($canBridge): ?><?= nre_nav_btn('bridge.php', 'Bridge', $nre_active, 'bridge') ?><?php endif; ?>
      <?php if ($canAdmin): ?><?= nre_nav_btn('admin.php', 'Admin', $nre_active, 'admin') ?><?php endif; ?>
    </nav>
    <?php endif; ?>
  </div>
  <div class="topbar-right">
    <?php if ($nre_active === 'live'): ?>
    <span class="ws-status" id="wsStatus" title="WebSocket bridge">
      <span class="ws-dot"></span>
      <span class="ws-label">Connecting…</span>
    </span>
    <?php endif; ?>
    <?php if (!$isKiosk): ?>
    <span class="topbar-user" title="<?= htmlspecialchars($user['username']) ?>"><?= htmlspecialchars($user['username']) ?></span>
    <button class="btn btn-sm btn-secondary" id="btnTheme" title="Toggle light/dark theme">🌙</button>
    <a href="logout.php" class="btn btn-sm btn-secondary">Logout</a>
    <?php else: ?>
    <span class="kiosk-badge">Kiosk</span>
    <?php endif; ?>
  </div>
</header>
