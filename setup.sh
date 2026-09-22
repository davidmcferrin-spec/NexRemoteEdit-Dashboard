#!/usr/bin/env bash
###############################################################################
# setup.sh — NexEditorStats installer for Debian / Ubuntu LTS
#
# Idempotent. Run from the git checkout as root:
#
#   sudo ./setup.sh              full install (apt, Postgres, Apache, bridge)
#   sudo ./setup.sh update       rsync code + restart nre-bridge (keeps data/)
#   sudo ./setup.sh --check      sanity checks only
#   sudo ./setup.sh status       systemctl snapshot
#
# Layout:
#   /opt/nre-web                 code + venv
#   /opt/nre-web/data/config.json  Settings UI (no .env)
#   /opt/nre-web/data/auth.json    users / LDAP
#   PostgreSQL role/db           nre / nre (localhost)
#
# Env overrides: NRE_PREFIX NRE_SERVER_NAME NRE_INGEST_PORT NRE_WS_PORT
###############################################################################
if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${NRE_PREFIX:-/opt/nre-web}"
SERVER_NAME="${NRE_SERVER_NAME:-}"
INGEST_PORT="${NRE_INGEST_PORT:-8766}"
WS_PORT="${NRE_WS_PORT:-8765}"

GREEN=$'\033[32m'; RED=$'\033[31m'; YELLOW=$'\033[33m'; RESET=$'\033[0m'
ok()   { echo "${GREEN}[ OK ]${RESET} $*"; }
warn() { echo "${YELLOW}[WARN]${RESET} $*"; WARNINGS+=("$*"); }
fail() { echo "${RED}[FAIL]${RESET} $*" >&2; exit 1; }
step() { echo; echo "=== $* ==="; }
WARNINGS=()

COMMAND="install"

usage() {
  cat <<EOF
Install and maintain NexEditorStats on Debian or Ubuntu LTS
(Apache + PHP + PostgreSQL + nre-bridge).

Usage:
  sudo $0                 First-time / re-run install
  sudo $0 update          Refresh code from this checkout, restart bridge
  sudo $0 --check         Sanity checks only (deploy test)
  sudo $0 status          systemctl snapshot
  $0 --help

Env overrides:
  NRE_PREFIX        code dest          (default /opt/nre-web)
  NRE_SERVER_NAME   Apache ServerName  (default: hostname -f)
  NRE_INGEST_PORT   Telegraf ingest    (default 8766)
  NRE_WS_PORT       WebSocket          (default 8765)

Secrets stay in ${PREFIX}/data/config.json (Settings UI). No .env file.
After install: sign in admin/admin, set Jump + Telegraf in Settings,
then install bay agents from the Telegraf page.
EOF
}

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    fail "run as root: sudo $0 ${COMMAND}"
  fi
}

detect_os() {
  local id=""
  if [[ -f /etc/os-release ]]; then
    # shellcheck disable=SC1091
    id="$(. /etc/os-release && echo "${ID}")"
  fi
  case "${id}" in
    debian|ubuntu) ok "OS: ${id} ($(. /etc/os-release && echo "${PRETTY_NAME}"))" ;;
    *)
      warn "expected Debian or Ubuntu LTS; found '${id:-unknown}'. Continuing anyway."
      ;;
  esac
}

default_server_name() {
  if [[ -n "${SERVER_NAME}" ]]; then
    printf '%s' "${SERVER_NAME}"
    return
  fi
  local host
  host="$(hostname -f 2>/dev/null || true)"
  if [[ -z "${host}" || "${host}" == "(none)" ]]; then
    host="$(hostname 2>/dev/null || echo nre.local)"
  fi
  printf '%s' "${host}"
}

json_get() {
  python3 - "$1" "$2" <<'PY'
import json, sys
path, dotted = sys.argv[1], sys.argv[2]
try:
    data = json.loads(open(path, encoding="utf-8").read())
except Exception:
    sys.exit(0)
cur = data
for part in dotted.split("."):
    if not isinstance(cur, dict) or part not in cur:
        sys.exit(0)
    cur = cur[part]
if cur is None:
    sys.exit(0)
print(cur if not isinstance(cur, bool) else ("true" if cur else "false"))
PY
}

json_set() {
  python3 - "$1" "$2" "$3" <<'PY'
import json, pathlib, sys
path, dotted, value = sys.argv[1], sys.argv[2], sys.argv[3]
p = pathlib.Path(path)
data = {}
if p.exists():
    data = json.loads(p.read_text(encoding="utf-8") or "{}")
    if not isinstance(data, dict):
        data = {}
cur = data
parts = dotted.split(".")
for part in parts[:-1]:
    nxt = cur.get(part)
    if not isinstance(nxt, dict):
        nxt = {}
        cur[part] = nxt
    cur = nxt
cur[parts[-1]] = value
tmp = p.with_suffix(p.suffix + ".tmp")
tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
tmp.replace(p)
PY
}

ensure_config_file() {
  local dest="${PREFIX}/data/config.json"
  mkdir -p "${PREFIX}/data"
  if [[ ! -f "${dest}" ]]; then
    if [[ -f "${PREFIX}/data/config.example.json" ]]; then
      cp "${PREFIX}/data/config.example.json" "${dest}"
    elif [[ -f "${ROOT}/data/config.example.json" ]]; then
      cp "${ROOT}/data/config.example.json" "${dest}"
    else
      echo '{}' > "${dest}"
    fi
    ok "created ${dest} from example"
  else
    ok "keeping existing ${dest}"
  fi
  chmod 640 "${dest}"
  chown www-data:www-data "${dest}" "${PREFIX}/data"
  chmod 750 "${PREFIX}/data"
}

install_packages() {
  step "APT packages"
  export DEBIAN_FRONTEND=noninteractive
  if ! command -v apt-get >/dev/null 2>&1; then
    fail "apt-get not found — this installer targets Debian/Ubuntu"
  fi
  apt-get update -qq
  apt-get install -y \
    apache2 \
    libapache2-mod-php \
    php \
    php-pgsql \
    php-ldap \
    php-mbstring \
    php-xml \
    postgresql \
    postgresql-contrib \
    postgresql-client \
    python3 \
    python3-venv \
    python3-pip \
    rsync \
    curl \
    ca-certificates \
    ufw
  ok "apache2 php php-pgsql php-ldap postgresql python3-venv rsync"
}

sync_code() {
  step "Code → ${PREFIX}"
  mkdir -p "${PREFIX}"
  if [[ "${ROOT}" == "${PREFIX}" ]]; then
    ok "running from ${PREFIX} — skip rsync"
    return
  fi
  rsync -a --delete \
    --exclude '.git' \
    --exclude 'venv' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude 'data/config.json' \
    --exclude 'data/auth.json' \
    --exclude 'data/*.tmp' \
    "${ROOT}/" "${PREFIX}/"
  ok "rsync ${ROOT} → ${PREFIX}"
}

fix_perms() {
  mkdir -p "${PREFIX}/data"
  chown -R www-data:www-data "${PREFIX}"
  chmod 750 "${PREFIX}/data"
  if [[ -f "${PREFIX}/data/config.json" ]]; then
    chmod 640 "${PREFIX}/data/config.json"
  fi
  if [[ -f "${PREFIX}/data/auth.json" ]]; then
    chmod 640 "${PREFIX}/data/auth.json"
  fi
  chmod 755 "${PREFIX}/setup.sh" 2>/dev/null || true
}

install_venv() {
  step "Python venv"
  if [[ ! -x "${PREFIX}/venv/bin/python" ]]; then
    python3 -m venv "${PREFIX}/venv"
    ok "created ${PREFIX}/venv"
  else
    ok "venv already exists"
  fi
  "${PREFIX}/venv/bin/pip" install -q --upgrade pip
  "${PREFIX}/venv/bin/pip" install -q -r "${PREFIX}/bridge/requirements.txt"
  "${PREFIX}/venv/bin/python" -c "import websockets, psycopg" \
    || fail "venv missing websockets or psycopg"
  ok "websockets + psycopg installed"
  chown -R www-data:www-data "${PREFIX}/venv"
}

install_postgres() {
  step "PostgreSQL (localhost, role nre)"
  command -v psql >/dev/null 2>&1 || fail "psql not found"
  systemctl enable postgresql >/dev/null 2>&1 || true
  systemctl start postgresql || fail "postgresql failed to start"
  local i
  for i in 1 2 3 4 5 6 7 8 9 10; do
    if sudo -u postgres psql -c "SELECT 1" >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
  sudo -u postgres psql -c "SELECT 1" >/dev/null 2>&1 \
    || fail "postgresql is not accepting connections"

  ensure_config_file
  local pw
  pw="$(json_get "${PREFIX}/data/config.json" postgres.password || true)"
  if [[ -z "${pw}" ]]; then
    pw="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
    json_set "${PREFIX}/data/config.json" postgres.password "${pw}"
    json_set "${PREFIX}/data/config.json" postgres.user nre
    json_set "${PREFIX}/data/config.json" postgres.database nre
    json_set "${PREFIX}/data/config.json" postgres.host 127.0.0.1
    chown www-data:www-data "${PREFIX}/data/config.json"
    chmod 640 "${PREFIX}/data/config.json"
    ok "wrote generated Postgres password into data/config.json"
  else
    ok "using Postgres password already in data/config.json"
  fi

  local pw_sql
  pw_sql="${pw//\'/\'\'}"
  sudo -u postgres psql -v ON_ERROR_STOP=1 -c \
    "DO \$\$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'nre') THEN CREATE ROLE nre LOGIN PASSWORD '${pw_sql}'; ELSE ALTER ROLE nre LOGIN PASSWORD '${pw_sql}'; END IF; END \$\$;"
  if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname = 'nre'" | grep -q 1; then
    sudo -u postgres psql -v ON_ERROR_STOP=1 -c "CREATE DATABASE nre OWNER nre"
    ok "created database nre"
  else
    ok "database nre exists"
  fi

  local hba
  hba="$(ls /etc/postgresql/*/main/pg_hba.conf 2>/dev/null | sort | tail -1 || true)"
  if [[ -n "${hba}" ]]; then
    if ! grep -qE '^host[[:space:]]+(all|nre)[[:space:]]+(all|nre)[[:space:]]+127\.0\.0\.1/32' "${hba}"; then
      printf '%s\n' "host nre nre 127.0.0.1/32 scram-sha-256" >> "${hba}"
      printf '%s\n' "host nre nre ::1/128 scram-sha-256" >> "${hba}"
      systemctl reload postgresql || systemctl restart postgresql || true
      ok "added localhost scram lines to ${hba}"
    else
      ok "pg_hba localhost TCP for nre"
    fi
  fi

  PGPASSWORD="${pw}" psql -h 127.0.0.1 -U nre -d nre -v ON_ERROR_STOP=1 \
    -c "SELECT 1" >/dev/null \
    || fail "nre cannot connect to database nre on 127.0.0.1"
  ok "nre → database nre (TCP 127.0.0.1)"
}

apply_schema() {
  step "Postgres schema"
  local pw
  pw="$(json_get "${PREFIX}/data/config.json" postgres.password || true)"
  [[ -n "${pw}" ]] || fail "postgres.password missing in data/config.json"
  [[ -f "${PREFIX}/schema.sql" ]] || fail "missing ${PREFIX}/schema.sql"
  PGPASSWORD="${pw}" psql -h 127.0.0.1 -U nre -d nre -v ON_ERROR_STOP=1 \
    -f "${PREFIX}/schema.sql" >/dev/null \
    || fail "schema.sql failed"
  ok "schema applied"
}

install_unit() {
  step "systemd nre-bridge"
  [[ -f "${PREFIX}/bridge/nre-bridge.service" ]] || fail "missing nre-bridge.service"
  sed \
    -e "s|/opt/nre-web|${PREFIX}|g" \
    "${PREFIX}/bridge/nre-bridge.service" \
    > /etc/systemd/system/nre-bridge.service
  chmod 644 /etc/systemd/system/nre-bridge.service
  systemctl daemon-reload
  systemctl enable nre-bridge >/dev/null
  ok "installed nre-bridge.service"
}

install_apache() {
  step "Apache site"
  local name tmpl dest
  name="$(default_server_name)"
  tmpl="${PREFIX}/deploy/apache-nre.conf"
  [[ -f "${tmpl}" ]] || tmpl="${ROOT}/deploy/apache-nre.conf"
  [[ -f "${tmpl}" ]] || fail "missing deploy/apache-nre.conf"
  dest=/etc/apache2/sites-available/nre.conf

  a2enmod proxy proxy_http rewrite >/dev/null
  ok "enabled proxy proxy_http rewrite"

  sed \
    -e "s|@@SERVER_NAME@@|${name}|g" \
    -e "s|@@PREFIX@@|${PREFIX}|g" \
    -e "s|@@INGEST_PORT@@|${INGEST_PORT}|g" \
    "${tmpl}" > "${dest}"
  chmod 644 "${dest}"
  a2ensite nre >/dev/null
  if [[ -e /etc/apache2/sites-enabled/000-default.conf ]]; then
    a2dissite 000-default >/dev/null
    ok "disabled Ubuntu default site (000-default)"
  fi
  if apache2ctl configtest >/dev/null 2>&1; then
    systemctl enable apache2 >/dev/null 2>&1 || true
    if systemctl is-active --quiet apache2; then
      systemctl reload apache2
      ok "apache2 reloaded (http://${name}/)"
    else
      systemctl enable --now apache2
      ok "apache2 started (http://${name}/)"
    fi
  else
    apache2ctl configtest || true
    fail "apache2ctl configtest failed"
  fi
}

install_sudoers() {
  step "Sudoers (Bridge page)"
  local src dest
  src="${PREFIX}/deploy/sudoers-nre"
  [[ -f "${src}" ]] || src="${ROOT}/deploy/sudoers-nre"
  [[ -f "${src}" ]] || fail "missing deploy/sudoers-nre"
  dest=/etc/sudoers.d/nre-bridge
  cp "${src}" "${dest}"
  chmod 440 "${dest}"
  if visudo -cf "${dest}" >/dev/null 2>&1; then
    ok "installed ${dest}"
  else
    rm -f "${dest}"
    fail "sudoers validation failed"
  fi
}

ssh_listen_ports() {
  if command -v sshd >/dev/null 2>&1; then
    sshd -T 2>/dev/null | awk '/^port / {print $2}'
  fi
}

install_firewall() {
  step "Host firewall (ufw)"
  if ! command -v ufw >/dev/null 2>&1; then
    warn "ufw not installed — skip firewall"
    return
  fi
  ufw allow OpenSSH >/dev/null 2>&1 || true
  ufw allow 22/tcp comment 'nre-ssh' >/dev/null 2>&1 || true
  local port
  while read -r port; do
    [[ -z "${port}" ]] && continue
    ufw allow "${port}/tcp" comment 'nre-ssh' >/dev/null 2>&1 || true
  done < <(ssh_listen_ports)
  ufw allow 80/tcp comment 'nre-web' >/dev/null 2>&1 || true
  ufw allow 443/tcp comment 'nre-web' >/dev/null 2>&1 || true
  ufw allow "${WS_PORT}/tcp" comment 'nre-websocket' >/dev/null 2>&1 || true
  if ufw status | grep -q 'Status: active'; then
    ok "ufw already active (80/443/${WS_PORT} allowed)"
  else
    warn "ufw installed but not enabled — enable when ready: ufw --force enable"
  fi
}

start_bridge() {
  step "Start nre-bridge"
  systemctl restart nre-bridge
  local i
  for i in 1 2 3 4 5 6 7 8 9 10; do
    if systemctl is-active --quiet nre-bridge; then
      ok "nre-bridge active"
      return
    fi
    sleep 1
  done
  journalctl -u nre-bridge -n 30 --no-pager || true
  fail "nre-bridge failed to start"
}

port_listen() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    ss -lnt | grep -qE ":${port}\\b"
  else
    python3 -c "import socket; s=socket.socket(); s.settimeout(1); s.connect(('127.0.0.1', int('${port}'))); s.close()" 2>/dev/null
  fi
}

http_code() {
  curl -sS -o /dev/null -w '%{http_code}' --max-time 5 "$@" || echo 000
}

cmd_check() {
  step "Sanity checks"
  local failed=0
  soft_fail() { warn "$1"; failed=1; }

  [[ -d "${PREFIX}/public" ]] && ok "code at ${PREFIX}" || soft_fail "missing ${PREFIX}/public"
  [[ -x "${PREFIX}/venv/bin/python" ]] && ok "venv present" || soft_fail "venv missing — sudo $0"
  if [[ -x "${PREFIX}/venv/bin/python" ]]; then
    if "${PREFIX}/venv/bin/python" -c "import websockets, psycopg" 2>/dev/null; then
      ok "venv imports websockets + psycopg"
    else
      soft_fail "venv cannot import websockets/psycopg"
    fi
  fi

  if command -v php >/dev/null 2>&1; then
    local f lint_ok=1
    for f in "${PREFIX}"/public/*.php "${PREFIX}"/public/api/*.php "${PREFIX}"/public/includes/*.php; do
      [[ -f "${f}" ]] || continue
      if ! php -l "${f}" >/dev/null 2>&1; then
        soft_fail "php -l failed: ${f}"
        lint_ok=0
      fi
    done
    [[ "${lint_ok}" -eq 1 ]] && ok "php -l on public PHP"
  else
    soft_fail "php not installed"
  fi

  if [[ -f "${PREFIX}/data/config.json" ]]; then
    ok "data/config.json exists"
    local pw
    pw="$(json_get "${PREFIX}/data/config.json" postgres.password || true)"
    if [[ -n "${pw}" ]]; then
      if PGPASSWORD="${pw}" psql -h 127.0.0.1 -U nre -d nre -tAc \
        "SELECT to_regclass('public.telemetry_samples')" 2>/dev/null | grep -q telemetry_samples; then
        ok "Postgres reachable; telemetry_samples present"
      else
        soft_fail "cannot query telemetry_samples as nre"
      fi
    else
      soft_fail "postgres.password empty in data/config.json"
    fi
  else
    soft_fail "data/config.json missing"
  fi

  if systemctl is-active --quiet postgresql 2>/dev/null; then
    ok "postgresql active"
  else
    soft_fail "postgresql not active"
  fi
  if systemctl is-active --quiet apache2 2>/dev/null; then
    ok "apache2 active"
  else
    soft_fail "apache2 not active"
  fi
  if systemctl is-active --quiet nre-bridge 2>/dev/null; then
    ok "nre-bridge active"
  else
    soft_fail "nre-bridge not active"
  fi

  if [[ -f /etc/apache2/sites-enabled/nre.conf ]] || [[ -L /etc/apache2/sites-enabled/nre.conf ]]; then
    if grep -q 'ProxyPass' /etc/apache2/sites-available/nre.conf \
      && grep -q '/api/telemetry/ingest' /etc/apache2/sites-available/nre.conf; then
      ok "Apache ingest ProxyPass present"
    else
      soft_fail "nre.conf missing ingest ProxyPass"
    fi
  else
    soft_fail "site nre not enabled"
  fi
  if [[ -f /etc/sudoers.d/nre-bridge ]]; then
    ok "sudoers.d/nre-bridge installed"
  else
    soft_fail "sudoers.d/nre-bridge missing"
  fi

  local login_code
  login_code="$(http_code "http://127.0.0.1/login.php")"
  if [[ "${login_code}" == "200" ]]; then
    ok "login.php HTTP ${login_code}"
  else
    soft_fail "login.php HTTP ${login_code} (expected 200)"
  fi

  if port_listen "${WS_PORT}"; then
    ok "WebSocket port ${WS_PORT} listening"
  else
    soft_fail "nothing listening on :${WS_PORT}"
  fi
  if port_listen "${INGEST_PORT}"; then
    ok "ingest port ${INGEST_PORT} listening"
  else
    soft_fail "nothing listening on :${INGEST_PORT}"
  fi

  local health unauth
  health="$(http_code "http://127.0.0.1:${INGEST_PORT}/health")"
  if [[ "${health}" == "200" ]]; then
    ok "ingest /health HTTP 200"
  else
    soft_fail "ingest /health HTTP ${health} (expected 200)"
  fi
  unauth="$(http_code -X POST "http://127.0.0.1:${INGEST_PORT}/api/telemetry/ingest" \
    -H 'Content-Type: application/json' -d '{}')"
  if [[ "${unauth}" == "401" ]]; then
    ok "ingest without Bearer → 401"
  else
    soft_fail "ingest without Bearer HTTP ${unauth} (expected 401)"
  fi

  local token
  token="$(json_get "${PREFIX}/data/config.json" telegraf.ingest_token || true)"
  if [[ -n "${token}" ]]; then
    local authc proxyc
    authc="$(http_code -X POST "http://127.0.0.1:${INGEST_PORT}/api/telemetry/ingest" \
      -H "Authorization: Bearer ${token}" \
      -H 'Content-Type: application/json' \
      -d '{"metrics":[{"name":"nre_setup_probe","tags":{"host":"_setup_check"},"fields":{"ok":1}}]}')"
    if [[ "${authc}" == "204" ]]; then
      ok "ingest with Bearer → 204"
    else
      soft_fail "ingest with Bearer HTTP ${authc} (expected 204)"
    fi
    proxyc="$(http_code -X POST "http://127.0.0.1/api/telemetry/ingest" \
      -H "Authorization: Bearer ${token}" \
      -H 'Content-Type: application/json' \
      -d '{"metrics":[{"name":"nre_setup_probe","tags":{"host":"_setup_check"},"fields":{"ok":1}}]}')"
    if [[ "${proxyc}" == "204" ]]; then
      ok "Apache ingest proxy → 204"
    else
      soft_fail "Apache ingest proxy HTTP ${proxyc} (expected 204)"
    fi
  else
    warn "no ingest token yet — generate one in Settings, then re-run --check"
  fi

  echo
  if (( ${#WARNINGS[@]} > 0 )); then
    echo "Warnings / failures:"
    local w
    for w in "${WARNINGS[@]}"; do
      echo "  - ${w}"
    done
    return 1
  fi
  ok "all checks passed"
}

cmd_status() {
  systemctl status --no-pager --lines=8 nre-bridge apache2 postgresql || true
}

print_summary() {
  local name
  name="$(default_server_name)"
  cat <<EOF

NexEditorStats setup complete.

  Code:     ${PREFIX}
  Settings: ${PREFIX}/data/config.json   (no .env)
  Auth:     ${PREFIX}/data/auth.json     (created on first login)
  DB:       Postgres nre @ 127.0.0.1
  Site:     http://${name}/
  Ingest:   http://${name}/api/telemetry/ingest
  Bridge:   nre-bridge  (WS :${WS_PORT}, ingest :${INGEST_PORT})

Next:
  1. Open http://${name}/  and sign in admin / admin (change password)
  2. Settings — Jump team ID + read-only token; generate Telegraf ingest token;
     add coturn /metrics URLs
  3. Telegraf page — install the agent + idle helper on each bay
  4. Mapping — link hostnames if Windows name ≠ Jump name

Maintenance:
  sudo $0 update
  sudo $0 --check
  sudo $0 status
  journalctl -u nre-bridge -f

WebSocket :${WS_PORT} is unauthenticated — keep it on trusted subnets (ufw
already has a rule; enable ufw when ready).
EOF
}

cmd_install() {
  require_root
  echo "NexEditorStats setup"
  echo "Source: ${ROOT}"
  detect_os
  install_packages
  sync_code
  fix_perms
  ensure_config_file
  install_venv
  install_postgres
  apply_schema
  install_unit
  install_apache
  install_sudoers
  install_firewall
  start_bridge
  cmd_check || true
  print_summary
}

cmd_update() {
  require_root
  echo "NexEditorStats update"
  echo "Source: ${ROOT}"
  detect_os
  install_packages
  sync_code
  fix_perms
  ensure_config_file
  install_venv
  install_postgres
  apply_schema
  install_unit
  if [[ -d /etc/apache2/sites-available ]]; then
    install_apache
  fi
  install_sudoers
  start_bridge
  cmd_check || true
  echo
  ok "update complete"
}

while (( $# > 0 )); do
  case "$1" in
    install|update|status)
      COMMAND="$1"
      ;;
    --check|check)
      COMMAND="check"
      ;;
    --update)
      COMMAND="update"
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

case "${COMMAND}" in
  install) cmd_install ;;
  update)  cmd_update ;;
  check)   require_root; cmd_check ;;
  status)  cmd_status ;;
  *)
    echo "Unknown command: ${COMMAND}" >&2
    usage >&2
    exit 2
    ;;
esac
