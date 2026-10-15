#!/usr/bin/env bash
# Resume the already-migrated, immutable 0f27dac release after correcting the
# verification account. No schema/config/data changes are performed here.
set -Eeuo pipefail
APP_ROOT=/www/wwwroot/salegent
OLD="$APP_ROOT/releases/agent-report-20260912-7b2c6b9"
NEW="$APP_ROOT/releases/admin-20260912-0f27dac"
STAGE=/var/tmp/salegent-admin-release-0f27dac
exec 9>>"$APP_ROOT/.deploy.lock"
flock -n 9 || exit 75
test "$(readlink -f "$APP_ROOT/current")" = "$OLD"
test "$(cat "$NEW/REVISION")" = 0f27dacae4900492d60fc5470e8ac6cd24fd8219
sudo -u postgres env PYTHONDONTWRITEBYTECODE=1 "$NEW/.venv/bin/python" \
 "$STAGE/verify_admin_database_v2.py" > "$STAGE/evidence/database-verification-v2.json"
test "$(sudo -u postgres psql -X -h /tmp -d sales_saas -At -v ON_ERROR_STOP=1 -c 'SELECT max(version) FROM ops.schema_migration')" = V061
test "$(sudo -u postgres psql -X -h /tmp -d sales_saas -At -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM ops.job WHERE status='running'")" = 0
STOPPED=0
SWITCHED=0
health() {
  local target="$1"
  for attempt in $(seq 1 15); do
    if systemctl is-active --quiet sales-api.service && systemctl is-active --quiet sales-worker.service &&
      curl --fail --silent --connect-timeout 3 --max-time 5 https://www.ericepc.com/api/v1/health/live >/dev/null &&
      curl --fail --silent --connect-timeout 3 --max-time 5 https://www.ericepc.com/api/v1/health/ready >/dev/null; then
      for service in sales-api.service sales-worker.service; do
        local pid
        pid=$(systemctl show "$service" -p MainPID --value)
        test "$(readlink -f "/proc/$pid/cwd")" = "$target" || return 1
      done
      echo 'API_WORKER_LIVE_READY_AND_RELEASE_VERIFIED'; return 0
    fi
    sleep 2
  done
  return 1
}
failure() {
  result=$?; trap - ERR INT TERM
  test "$result" -ne 0 || result=1
  if [ "$STOPPED" -eq 1 ]; then
    if [ "$SWITCHED" -eq 1 ]; then
      systemctl stop sales-api.service sales-worker.service || true
      ln -sfn "$NEW" "$APP_ROOT/failed-admin-0f27dac"
      ln -sfn "$OLD" "$APP_ROOT/current.next"
      mv -Tf "$APP_ROOT/current.next" "$APP_ROOT/current"
    fi
    systemctl start sales-api.service sales-worker.service
    health "$OLD" || echo 'OLD_SERVICE_HEALTH_FAILED'
  fi
  exit "$result"
}
trap failure ERR INT TERM
STOPPED=1
systemctl stop sales-api.service sales-worker.service
test "$(sudo -u postgres psql -X -h /tmp -d sales_saas -At -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM ops.job WHERE status='running'")" = 0
ln -sfn "$OLD" "$APP_ROOT/previous.next"
mv -Tf "$APP_ROOT/previous.next" "$APP_ROOT/previous"
ln -sfn "$NEW" "$APP_ROOT/current.next"
SWITCHED=1
mv -Tf "$APP_ROOT/current.next" "$APP_ROOT/current"
systemctl start sales-api.service sales-worker.service
health "$NEW"
echo "ADMIN_RELEASE_CURRENT=$NEW"
trap - ERR INT TERM
