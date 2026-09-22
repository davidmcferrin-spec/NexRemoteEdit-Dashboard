<?php
declare(strict_types=1);

$theme_pref = null;
if (isset($user) && is_array($user) && !empty($user['prefs']['theme'])) {
    $candidate = $user['prefs']['theme'];
    if (in_array($candidate, ['dark', 'light'], true)) {
        $theme_pref = $candidate;
    }
}
?>
<script>
(function() {
  var serverTheme = <?= json_encode($theme_pref) ?>;
  var theme = serverTheme || localStorage.getItem('nre-theme') || 'dark';
  if (theme !== 'dark' && theme !== 'light') theme = 'dark';
  document.documentElement.setAttribute('data-theme', theme);
  try { localStorage.setItem('nre-theme', theme); } catch (e) {}
})();
</script>
