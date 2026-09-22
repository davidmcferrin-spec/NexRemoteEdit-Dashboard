<?php
require_once __DIR__ . '/includes/auth.php';

if (is_logged_in()) {
    header('Location: index.php');
    exit;
}

$error = '';
$next = $_POST['next'] ?? $_GET['next'] ?? 'index.php';
if (!is_string($next) || !preg_match('#^[a-zA-Z0-9_./?=&%-]+$#', $next) || str_starts_with($next, '//')) {
    $next = 'index.php';
}

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $username = trim($_POST['username'] ?? '');
    $password = $_POST['password'] ?? '';
    $result = authenticate($username, $password);
    if ($result['ok']) {
        header('Location: ' . $next);
        exit;
    }
    $error = $result['error'] ?? 'Login failed';
}

if ($error === '' && ($_GET['reason'] ?? '') === 'timeout') {
    $error = 'Session expired after ' . session_idle_minutes() . ' minutes of inactivity.';
}
?>
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Login — NexEditorStats</title>
  <?php require __DIR__ . '/includes/theme_head.php'; ?>
  <link rel="stylesheet" href="assets/style.css">
</head>
<body class="auth-page">
  <div class="auth-card">
    <div class="auth-logo">Nex<span class="accent">EditorStats</span></div>
    <h1>Sign in</h1>
    <p class="auth-subtitle">Bay telemetry and remote sessions</p>
    <?php if ($error): ?>
      <div class="auth-error"><?= htmlspecialchars($error) ?></div>
    <?php endif; ?>
    <form method="post" class="auth-form">
      <input type="hidden" name="next" value="<?= htmlspecialchars($next) ?>">
      <label>Username
        <input type="text" name="username" autocomplete="username" required autofocus>
      </label>
      <label>Password
        <input type="password" name="password" autocomplete="current-password" required>
      </label>
      <button type="submit" class="btn auth-submit">Sign in</button>
    </form>
  </div>
</body>
</html>
