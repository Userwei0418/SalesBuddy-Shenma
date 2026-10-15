#!/usr/bin/env bash
# Existing managed Salegent environment only. No credential or systemd changes.
# Run as root after release approval. This does not create an initial database.
set -Eeuo pipefail
umask 077
if [ "$#" -lt 9 ]; then
  echo 'Usage: deploy-forward-release.sh COMMIT ARCHIVE MANIFEST EXPECTED_RELEASE EXPECTED_COMMIT FROM_SCHEMA TO_SCHEMA RELEASE_ID EVIDENCE [--credential-workspace ID --credential-administrator ACCOUNT]'
  exit 64
fi
COMMIT="$1"; ARCHIVE="$2"; MANIFEST="$3"; EXPECTED_RELEASE="$4"; EXPECTED_COMMIT="$5"
FROM_SCHEMA="$6"; TO_SCHEMA="$7"; RELEASE_ID="$8"; EVIDENCE="$9"
shift 9
CREDENTIAL_WORKSPACE=''; CREDENTIAL_ADMINISTRATOR=''
while [ "$#" -gt 0 ]; do
  case "$1" in
    --credential-workspace) test "$#" -ge 2 && test -z "$CREDENTIAL_WORKSPACE" || exit 64; CREDENTIAL_WORKSPACE="$2"; shift 2 ;;
    --credential-administrator) test "$#" -ge 2 && test -z "$CREDENTIAL_ADMINISTRATOR" || exit 64; CREDENTIAL_ADMINISTRATOR="$2"; shift 2 ;;
    *) echo 'Unsupported deployment argument'; exit 64 ;;
  esac
done
if [ -n "$CREDENTIAL_WORKSPACE$CREDENTIAL_ADMINISTRATOR" ]; then
  [[ "$CREDENTIAL_WORKSPACE" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || exit 64
  [[ "$CREDENTIAL_ADMINISTRATOR" =~ ^[A-Za-z0-9][A-Za-z0-9.@_-]{0,127}$ ]] || exit 64
fi
[[ "$COMMIT" =~ ^[a-f0-9]{40}$ && "$EXPECTED_COMMIT" =~ ^[a-f0-9]{40}$ ]] || exit 64
[[ "$EXPECTED_RELEASE" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]+$ && "$RELEASE_ID" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]+$ ]] || exit 64
[[ "$FROM_SCHEMA" =~ ^V[0-9]{3,}$ && "$TO_SCHEMA" =~ ^V[0-9]{3,}$ && "$EVIDENCE" == /* ]] || exit 64
test "$(id -u)" -eq 0
APP_ROOT=/www/wwwroot/salegent
if [ -e "$APP_ROOT/bundles/CURRENT.json" ]; then
  echo 'COORDINATED_BUNDLE_REQUIRED: use release_bundle_control.py; schema changes need a reviewed maintenance/recovery plan'
  exit 64
fi
OLD="$APP_ROOT/releases/$EXPECTED_RELEASE"; NEW="$APP_ROOT/releases/$RELEASE_ID"
HELPER="$(cd "$(dirname "$0")" && pwd)/release_upgrade.py"
BACKUP_DIR="/var/backups/sales-management/$RELEASE_ID"
exec 9>>"$APP_ROOT/.deploy.lock"
flock -n 9 || { echo 'Deployment lock busy; no change'; exit 75; }
test "$(readlink -f "$APP_ROOT/current")" = "$OLD"
test "$(cat "$OLD/REVISION")" = "$EXPECTED_COMMIT"
test ! -e "$NEW" && test ! -e "$EVIDENCE" && test ! -e "$BACKUP_DIR"
install -d -m 700 "$EVIDENCE"
STOPPED=0; SCHEMA_TOUCHED=0; SWITCHED=0
SERVICES=(sales-api.service sales-worker.service)
if [ "$(systemctl show sales-feishu-worker.service -p LoadState --value)" = loaded ]; then
  SERVICES+=(sales-feishu-worker.service)
fi

health() {
  local expected="$1" pid good endpoint
  for attempt in $(seq 1 15); do
    good=1
    for service in "${SERVICES[@]}"; do
      systemctl is-active --quiet "$service" || good=0
      pid=$(systemctl show "$service" -p MainPID --value)
      test "$(readlink -f "/proc/$pid/cwd")" = "$expected" || good=0
    done
    for endpoint in live ready; do
      if ! curl --fail --silent --connect-timeout 3 --max-time 5 "https://www.ericepc.com/api/v1/health/$endpoint" |
        python3 -c 'import json,sys; assert json.load(sys.stdin)["status"] == "ok"' >/dev/null 2>&1; then good=0; fi
    done
    if [ "$good" -eq 1 ]; then echo 'API_WORKER_CWD_LIVE_READY_VERIFIED'; return 0; fi
    sleep 2
  done
  return 1
}
switch_current() {
  ln -sfn "$1" "$APP_ROOT/current.next"
  mv -Tf "$APP_ROOT/current.next" "$APP_ROOT/current"
  sync -f "$APP_ROOT"
}
failure() {
  local result=$?
  trap - ERR INT TERM
  set +e
  test "$result" -ne 0 || result=1
  if [ "$SCHEMA_TOUCHED" -eq 1 ]; then
    systemctl stop "${SERVICES[@]}"
    echo 'FORWARD_REPAIR_REQUIRED: migration began; services stopped, no automatic old-code rollback'
  elif [ "$STOPPED" -eq 1 ]; then
    systemctl stop "${SERVICES[@]}"
    if [ "$SWITCHED" -eq 1 ]; then switch_current "$OLD"; fi
    systemctl start "${SERVICES[@]}"
    health "$OLD" || echo 'OLD_SERVICE_RECOVERY_NEEDS_ATTENTION'
  fi
  exit "$result"
}
trap failure ERR INT TERM
test ! -e "$APP_ROOT/current.next" && test ! -L "$APP_ROOT/current.next"
test ! -e "$APP_ROOT/previous.next" && test ! -L "$APP_ROOT/previous.next"
health "$OLD"
# The working directory alone cannot prove imported code. Reject pinned
# interpreters/import overrides before migration or service interruption.
for service in "${SERVICES[@]}"; do
  entry=$(systemctl show "$service" -p ExecStart --value)
  [[ "$entry" == *"path=$APP_ROOT/current/.venv/bin/python ;"* ]] || {
    echo "SERVICE_ENTRY_NOT_CURRENT=$service; repair its interpreter before deployment"; exit 64;
  }
  "$OLD/.venv/bin/python" "$OLD/backend/deploy/inherit_service_environment.py" \
    --service "$service" --unit "salegent-import-guard-${COMMIT:0:7}-${service%.service}" \
    -- "$OLD/.venv/bin/python" -c 'import os; assert os.environ.get("PYTHONPATH", "") in ("", "/www/wwwroot/salegent/current/backend/src"), "Service import path is pinned to another release"'
done
for service in sales-api sales-worker; do
  systemctl show "$service" -p EnvironmentFiles --value > "$EVIDENCE/$service.environment-files.before"
done
python3 "$HELPER" unpack --archive "$ARCHIVE" --manifest "$MANIFEST" --commit "$COMMIT" --target "$NEW" > "$EVIDENCE/source-verification.json"
cmp "$0" "$NEW/backend/deploy/deploy-forward-release.sh"
cmp "$HELPER" "$NEW/backend/deploy/release_upgrade.py"
HELPER="$NEW/backend/deploy/release_upgrade.py"
CREDENTIAL_CHECK="$NEW/backend/scripts/runtime_config_credentials.py"
if [ -f "$CREDENTIAL_CHECK" ] && { [ -z "$CREDENTIAL_WORKSPACE" ] || [ -z "$CREDENTIAL_ADMINISTRATOR" ]; }; then
  echo 'Credential-capable release requires --credential-workspace and --credential-administrator'
  exit 64
fi
"$OLD/.venv/bin/python" "$NEW/backend/deploy/runtime_dependency_constraints.py" build \
  --lock "$NEW/backend/uv.lock" --constraints "$EVIDENCE/runtime-constraints.txt"
(umask 022; python3 -m venv "$NEW/.venv")
(umask 022; "$NEW/.venv/bin/python" -m pip install --quiet --constraint "$EVIDENCE/runtime-constraints.txt" -e "$NEW/backend") > "$EVIDENCE/install.log" 2>&1
"$NEW/.venv/bin/python" -m pip check
"$NEW/.venv/bin/python" "$NEW/backend/deploy/runtime_dependency_constraints.py" verify \
  --lock "$NEW/backend/uv.lock" --constraints "$EVIDENCE/runtime-constraints.txt" > "$EVIDENCE/dependency-verification.json"
"$NEW/.venv/bin/python" "$NEW/backend/scripts/verify_release_runtime.py" --release "$NEW"
# Validate both effective service environments before any downtime, rather than
# discovering disabled/ambiguous authentication after the symlink has switched.
for service in sales-api sales-worker; do
  "$NEW/.venv/bin/python" "$NEW/backend/deploy/inherit_service_environment.py" \
    --service "$service.service" --unit "salegent-auth-check-${COMMIT:0:7}-$service" \
    -- "$NEW/.venv/bin/python" "$NEW/backend/deploy/verify_auth_runtime.py" \
    > "$EVIDENCE/$service.auth-runtime.json"
done
cmp "$EVIDENCE/sales-api.auth-runtime.json" "$EVIDENCE/sales-worker.auth-runtime.json"
verify_agent_runtime() {
  local phase="$1" service
  for service in sales-api sales-worker; do
    "$NEW/.venv/bin/python" "$NEW/backend/deploy/inherit_service_environment.py" \
      --service "$service.service" --unit "salegent-agent-check-${COMMIT:0:7}-$service-$phase" \
      -- "$NEW/.venv/bin/python" "$NEW/backend/deploy/verify_agent_runtime.py" \
      > "$EVIDENCE/$service.agent-runtime.$phase.json"
  done
  cmp "$EVIDENCE/sales-api.agent-runtime.$phase.json" "$EVIDENCE/sales-worker.agent-runtime.$phase.json"
}
# Reject incomplete or different effective metadata before any downtime. The
# Environment, ordered files, PassEnvironment and final UnsetEnvironment are
# inherited independently for each service; unsupported metadata fails closed.
verify_agent_runtime before
verify_credential_runtime() {
  local phase="$1" action="$2" service
  local -a arguments=("$action")
  test -f "$CREDENTIAL_CHECK" || return 0
  if [ "$action" != keyring-check ]; then
    arguments+=(--workspace "$CREDENTIAL_WORKSPACE" --administrator "$CREDENTIAL_ADMINISTRATOR")
  fi
  for service in sales-api sales-worker; do
    "$NEW/.venv/bin/python" "$NEW/backend/deploy/inherit_service_environment.py" \
      --service "$service.service" --unit "salegent-credential-${COMMIT:0:7}-$service-$phase" \
      --as-service-user --max-runtime 120 \
      -- "$NEW/.venv/bin/python" "$CREDENTIAL_CHECK" "${arguments[@]}" \
      > "$EVIDENCE/$service.credential-runtime.$phase.json"
  done
  # Key ID, DB revision and actual identity must agree for this shared deployment.
  cmp "$EVIDENCE/sales-api.credential-runtime.$phase.json" "$EVIDENCE/sales-worker.credential-runtime.$phase.json"
}
# No database reads: V080 columns need not exist yet, but the actual service
# user must already be able to use the provisioned private keyring.
verify_credential_runtime before keyring-check
"$NEW/.venv/bin/python" "$NEW/database/scripts/migrate.py" --plan > "$EVIDENCE/migration-plan.json"
verify_db() {
  sudo -u postgres "$NEW/.venv/bin/python" "$HELPER" verify-db --source "$1" --target "$2" < "$EVIDENCE/migration-plan.json"
}
verify_db "$FROM_SCHEMA" "$TO_SCHEMA" > "$EVIDENCE/database-before.json"
# Stop the producer first, then drain/stop the Worker. A race-started job blocks
# the second guard; it is not silently marked failed or reclaimed by deployment.
STOPPED=1
systemctl stop sales-api.service
systemctl stop "${SERVICES[@]:1}"
verify_db "$FROM_SCHEMA" "$TO_SCHEMA" > "$EVIDENCE/database-stopped.json"
cmp "$EVIDENCE/database-before.json" "$EVIDENCE/database-stopped.json"
install -d -m 700 "$BACKUP_DIR"
sudo -u postgres pg_dump -h /tmp -Fc sales_saas > "$BACKUP_DIR/pre-migration.dump.partial"
test -s "$BACKUP_DIR/pre-migration.dump.partial"
pg_restore --list "$BACKUP_DIR/pre-migration.dump.partial" >/dev/null
sync -f "$BACKUP_DIR/pre-migration.dump.partial"
mv "$BACKUP_DIR/pre-migration.dump.partial" "$BACKUP_DIR/pre-migration.dump"
sync -f "$BACKUP_DIR"
echo "VERIFIED_BACKUP_BYTES=$(stat -c %s "$BACKUP_DIR/pre-migration.dump")"
if python3 - "$EVIDENCE/database-before.json" <<'PY'
import json,sys
raise SystemExit(0 if json.load(open(sys.argv[1]))['pending'] else 1)
PY
then
  # Conservative even if the first file rolls back: never guess compatibility.
  SCHEMA_TOUCHED=1
  sudo -u postgres env DATABASE_URL='postgresql:///sales_saas?host=/tmp' "$NEW/.venv/bin/python" "$NEW/database/scripts/migrate.py" > "$EVIDENCE/migration-first.json"
  sudo -u postgres env DATABASE_URL='postgresql:///sales_saas?host=/tmp' "$NEW/.venv/bin/python" "$NEW/database/scripts/migrate.py" > "$EVIDENCE/migration-second.json"
  python3 - "$EVIDENCE" <<'PY'
import json,sys
from pathlib import Path
p=Path(sys.argv[1]); pending=json.loads((p/'database-before.json').read_text())['pending']
first=json.loads((p/'migration-first.json').read_text()); second=json.loads((p/'migration-second.json').read_text())
assert [row['key'] for row in first if row['status']=='applied']==pending
assert all(row['status'] in ('applied','unchanged') for row in first)
assert all(row['status']=='unchanged' for row in second)
PY
fi
verify_db "$TO_SCHEMA" "$TO_SCHEMA" > "$EVIDENCE/database-after.json"
verify_credential_runtime migrated check
for service in sales-api sales-worker; do
  systemctl show "$service" -p EnvironmentFiles --value > "$EVIDENCE/$service.environment-files.after"
  cmp "$EVIDENCE/$service.environment-files.before" "$EVIDENCE/$service.environment-files.after"
done
test "$(readlink -f "$APP_ROOT/current")" = "$OLD"
test "$(cat "$OLD/REVISION")" = "$EXPECTED_COMMIT"
ln -s "$OLD" "$APP_ROOT/previous.next"
mv -Tf "$APP_ROOT/previous.next" "$APP_ROOT/previous"
SWITCHED=1
switch_current "$NEW"
systemctl start "${SERVICES[@]}"
health "$NEW"
curl --fail --silent --connect-timeout 3 --max-time 10 https://www.ericepc.com/api/v1/health/version > "$EVIDENCE/runtime-version.json"
python3 - "$EVIDENCE/runtime-version.json" "$COMMIT" "$TO_SCHEMA" <<'PYVERSION'
import json,sys
value=json.load(open(sys.argv[1]))
assert value['mode']=='release' and value['revision']==sys.argv[2] and value['expected_schema']==sys.argv[3]
PYVERSION
"$NEW/.venv/bin/python" "$NEW/backend/deploy/runtime_inventory.py" > "$EVIDENCE/runtime-inventory.json"
install -m 644 "$EVIDENCE/runtime-inventory.json" "$APP_ROOT/RELEASES.json"
verify_agent_runtime after
verify_credential_runtime after check
for service in sales-api sales-worker; do
  cmp "$EVIDENCE/$service.agent-runtime.before.json" "$EVIDENCE/$service.agent-runtime.after.json"
  if [ -f "$CREDENTIAL_CHECK" ]; then
    cmp "$EVIDENCE/$service.credential-runtime.migrated.json" "$EVIDENCE/$service.credential-runtime.after.json"
  fi
done
echo "FORWARD_RELEASE_READY=$NEW SCHEMA=$TO_SCHEMA"
trap - ERR INT TERM
