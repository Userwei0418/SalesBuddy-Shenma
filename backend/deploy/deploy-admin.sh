#!/usr/bin/env bash
# Approved admin release only. Credentials/configuration are inherited by systemd,
# never opened here. This script owns the shared lock through final verification.
set -Eeuo pipefail
test "$#" -eq 4 || { echo 'Usage: deploy-admin.sh COMMIT ARCHIVE MANIFEST EVIDENCE'; exit 64; }
COMMIT="$1"; ARCHIVE="$2"; MANIFEST="$3"; EVIDENCE="$4"
[[ "$COMMIT" =~ ^[a-f0-9]{40}$ ]] || exit 64
APP_ROOT=/www/wwwroot/salegent
EXPECTED_OLD="$APP_ROOT/releases/agent-report-20260912-7b2c6b9"
NEW="$APP_ROOT/releases/admin-20260912-${COMMIT:0:7}"
SCRIPT_ROOT="$(cd "$(dirname "$0")" && pwd)"
exec 9>>"$APP_ROOT/.deploy.lock"
flock -n 9 || { echo 'Deployment lock busy; no change'; exit 75; }
test "$(readlink -f "$APP_ROOT/current")" = "$EXPECTED_OLD"
test ! -e "$NEW" && test ! -e "$EVIDENCE"
install -d -m 755 "$EVIDENCE"
STOPPED=0; MIGRATING=0; MIGRATED=0; SWITCHED=0

health() {
  local expected="$1"
  for attempt in $(seq 1 15); do
    if systemctl is-active --quiet sales-api.service && systemctl is-active --quiet sales-worker.service &&
       curl --fail --silent --connect-timeout 3 --max-time 5 https://www.ericepc.com/api/v1/health/live >/dev/null &&
       curl --fail --silent --connect-timeout 3 --max-time 5 https://www.ericepc.com/api/v1/health/ready >/dev/null; then
      for service in sales-api.service sales-worker.service; do
        local pid
        pid=$(systemctl show "$service" -p MainPID --value)
        test "$(readlink -f "/proc/$pid/cwd")" = "$expected" || return 1
      done
      echo 'API_WORKER_LIVE_READY_AND_RELEASE_OK'; return 0
    fi
    sleep 2
  done
  return 1
}
failure() {
  result=$?; trap - ERR INT TERM
  test "$result" -ne 0 || result=1
  if [ "$MIGRATING" -eq 1 ] && [ "$MIGRATED" -eq 0 ]; then
    echo 'MIGRATION_INCOMPLETE: services remain stopped; inspect ledger and repair forward'
  elif [ "$SWITCHED" -eq 1 ]; then
    systemctl stop sales-api.service sales-worker.service || true
    ln -sfn "$NEW" "$APP_ROOT/failed-admin-${COMMIT:0:7}"
    ln -sfn "$EXPECTED_OLD" "$APP_ROOT/current.next"
    mv -Tf "$APP_ROOT/current.next" "$APP_ROOT/current"
    systemctl start sales-api.service sales-worker.service
    health "$EXPECTED_OLD" || echo 'ROLLBACK_HEALTH_FAILED'
    echo 'DEFAULT_POLICY_COMPATIBLE_APPLICATION_ROLLBACK_EXECUTED'
  elif [ "$STOPPED" -eq 1 ]; then
    systemctl start sales-api.service sales-worker.service
    health "$EXPECTED_OLD" || echo 'OLD_SERVICE_RESTART_FAILED'
  fi
  exit "$result"
}
trap failure ERR INT TERM
python3 - "$ARCHIVE" "$MANIFEST" "$COMMIT" "$NEW" <<'PY'
import hashlib,json,sys,tarfile
from pathlib import Path
archive,manifest,commit,target=sys.argv[1:]
meta=json.loads(Path(manifest).read_text())
assert meta['commit']==commit
assert hashlib.sha256(Path(archive).read_bytes()).hexdigest()==meta['sha256']
root=Path(target);root.mkdir(mode=0o755)
with tarfile.open(archive) as source:
    for member in source.getmembers():
        assert member.isdir() or member.isfile()
        assert not member.name.startswith('/') and '..' not in Path(member.name).parts
    source.extractall(root,filter='data')
for name,digest in meta['file_hashes'].items():
    assert hashlib.sha256((root/name).read_bytes()).hexdigest()==digest
(root/'REVISION').write_text(commit+'\n')
print('IMMUTABLE_SOURCE_VERIFIED',len(meta['file_hashes']),flush=True)
PY
cmp "$0" "$NEW/backend/deploy/deploy-admin.sh"
cmp "$SCRIPT_ROOT/verify_admin_database.py" "$NEW/backend/deploy/verify_admin_database.py"
# Match versions by installed distribution metadata; never emit pip credentials/URLs.
"$EXPECTED_OLD/.venv/bin/python" - "$EVIDENCE/runtime-constraints.txt" <<'PY'
from importlib.metadata import distributions
from pathlib import Path
import sys
Path(sys.argv[1]).write_text(''.join(sorted(
    f"{d.metadata['Name']}=={d.version}\n" for d in distributions()
    if d.metadata['Name'].lower() not in ('sales-assistant-backend','pip','setuptools')
)))
PY
python3 -m venv "$NEW/.venv"
"$NEW/.venv/bin/python" -m pip install --quiet --constraint "$EVIDENCE/runtime-constraints.txt" -e "$NEW/backend"
"$NEW/.venv/bin/python" -m pip check
"$NEW/.venv/bin/python" "$NEW/backend/scripts/verify_release_runtime.py" --release "$NEW"
"$NEW/.venv/bin/python" "$NEW/database/scripts/migrate.py" --plan > "$EVIDENCE/migration-plan.json"
sudo -u postgres "$NEW/.venv/bin/python" - "$EVIDENCE/migration-plan.json" <<'PY'
import asyncio,json,sys,asyncpg
async def main():
 c=await asyncpg.connect(host='/tmp',database='sales_saas',user='postgres')
 try:
  rows=await c.fetch('SELECT migration_key,checksum_sha256 FROM ops.migration_checksum')
  prior={r['migration_key']:r['checksum_sha256'] for r in rows}
  plan=json.load(open(sys.argv[1]))
  assert all(m['sha256']==prior[m['key']] for m in plan if m['key'] in prior)
  assert [m['key'] for m in plan if m['key'] not in prior]==['V055','V056','V057','V058','V059','V060','V061']
  assert await c.fetchval('SELECT max(version) FROM ops.schema_migration')=='V054'
  assert await c.fetchval("SELECT count(*) FROM ops.job WHERE status='running'")==0
  for account in ('OPSADMIN','XS001'):
   assert await c.fetchrow("SELECT * FROM security.resolve_account_actor('demo-sales-workspace',$1,NULL)",account)
  print('PRIOR_CHECKSUMS_AND_IDLE_JOBS_VERIFIED',flush=True)
 finally: await c.close()
asyncio.run(main())
PY
STOPPED=1
systemctl stop sales-api.service
systemctl stop sales-worker.service
test "$(sudo -u postgres psql -X -h /tmp -d sales_saas -At -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM ops.job WHERE status='running'")" = 0
BACKUP_DIR="/var/backups/sales-management/admin-20260912-${COMMIT:0:7}"
test ! -e "$BACKUP_DIR"
install -d -m 700 "$BACKUP_DIR"
sudo -u postgres pg_dump -h /tmp -Fc sales_saas > "$BACKUP_DIR/pre-migration.dump.partial"
test -s "$BACKUP_DIR/pre-migration.dump.partial"
pg_restore --list "$BACKUP_DIR/pre-migration.dump.partial" >/dev/null
sync -f "$BACKUP_DIR/pre-migration.dump.partial"
mv "$BACKUP_DIR/pre-migration.dump.partial" "$BACKUP_DIR/pre-migration.dump"
sync -f "$BACKUP_DIR"
echo "VERIFIED_BACKUP_BYTES=$(stat -c %s "$BACKUP_DIR/pre-migration.dump")"
MIGRATING=1
sudo -u postgres env DATABASE_URL='postgresql:///sales_saas?host=/tmp' "$NEW/.venv/bin/python" \
 "$NEW/database/scripts/migrate.py" > "$EVIDENCE/migration-first.json"
sudo -u postgres env DATABASE_URL='postgresql:///sales_saas?host=/tmp' "$NEW/.venv/bin/python" \
 "$NEW/database/scripts/migrate.py" > "$EVIDENCE/migration-second.json"
python3 - "$EVIDENCE" <<'PY'
import json,sys
from pathlib import Path
p=Path(sys.argv[1])
first=json.loads((p/'migration-first.json').read_text())
assert [r['key'] for r in first if r['status']=='applied']==['V055','V056','V057','V058','V059','V060','V061']
assert all(r['status']=='unchanged' for r in json.loads((p/'migration-second.json').read_text()))
print('V055_V061_APPLIED_SECOND_RUN_UNCHANGED',flush=True)
PY
MIGRATED=1
sudo -u postgres env PYTHONDONTWRITEBYTECODE=1 "$NEW/.venv/bin/python" \
 "$SCRIPT_ROOT/verify_admin_database.py" > "$EVIDENCE/database-verification.json"
ln -sfn "$EXPECTED_OLD" "$APP_ROOT/previous.next"
mv -Tf "$APP_ROOT/previous.next" "$APP_ROOT/previous"
ln -sfn "$NEW" "$APP_ROOT/current.next"
SWITCHED=1
mv -Tf "$APP_ROOT/current.next" "$APP_ROOT/current"
systemctl start sales-api.service sales-worker.service
health "$NEW"
echo "ADMIN_RELEASE_READY=$NEW"
trap - ERR INT TERM
