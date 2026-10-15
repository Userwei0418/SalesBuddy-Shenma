#!/usr/bin/env bash
# Run on sales-demo only after explicit visit Agent publication/business approval.
# Arguments are an immutable source commit, archive, hash manifest, approved scope,
# and a new evidence directory. No credentials are read or printed by this script.
set -Eeuo pipefail
test "$#" -eq 5 || { echo 'Usage: deploy-visit-agent.sh COMMIT ARCHIVE MANIFEST APPROVED_SCOPE EVIDENCE'; exit 64; }
COMMIT="$1"
ARCHIVE="$2"
MANIFEST="$3"
SCOPE="$4"
EVIDENCE="$5"
[[ "$COMMIT" =~ ^[a-f0-9]{40}$ ]] || exit 64
APP_ROOT=/www/wwwroot/salegent
EXPECTED_OLD="$APP_ROOT/releases/agent-risk-20260912-47956c7-r2"
NEW="$APP_ROOT/releases/agent-visit-20260912-${COMMIT:0:7}"
DROPIN=/etc/systemd/system/sales-worker.service.d/agent-fde-pilot.conf
VERIFY_DIR=/var/tmp/salegent-chatbi-verify-c699680
exec 9>>"$APP_ROOT/.deploy.lock"
flock -n 9 || { echo 'Deployment lock busy; no change'; exit 75; }
test "$(readlink -f "$APP_ROOT/current")" = "$EXPECTED_OLD"
test ! -e "$NEW"
test ! -e "$EVIDENCE"
test "$(stat -c '%U:%a' /etc/sales-fde-visit-entry.env)" = 'root:600'
test -f "$VERIFY_DIR/runtime-constraints.txt"

# Validate authorization and existing rollout before any release/config changes.
python3 - "$SCOPE" "$DROPIN" <<'PY'
import json,sys
from pathlib import Path
scope=json.loads(Path(sys.argv[1]).read_text())
assert scope['approval_status']=='approved'
assert scope['preflight_verified'] is True
assert scope['account']=='XS001'
assert scope['actor_id']=='02000000-0000-0000-0000-000000000004'
assert scope['customer_id']=='1b127c42-c436-470f-a6dc-876de6586d53'
assert scope['customer_name']=='【演示】字段验收-422815'
caps={name:{'enabled':True,'rollout':'production'} for name in
      ('battle_map_review','opportunity_draft','personal_risks')}
assert json.loads(Path('/etc/sales-fde-pilot.json').read_text())=={
    '00000000-0000-0000-0000-000000000001':{'capabilities':caps}}
assert Path(sys.argv[2]).read_text()==('[Service]\nEnvironmentFile=/etc/sales-fde-pilot.env\n'
    'EnvironmentFile=/etc/sales-fde-battle-map.env\nEnvironmentFile=/etc/sales-fde-opportunity.env\n'
    'EnvironmentFile=/etc/sales-fde-personal-risks.env\n')
print('APPROVED_SCOPE_AND_EXISTING_THREE_ROUTES_VERIFIED',flush=True)
PY

install -d -m 700 -o postgres -g postgres "$EVIDENCE"
# These two files contain rollout flags and file references only, never secrets.
cp /etc/sales-fde-pilot.json "$EVIDENCE/prior-rollout.json"
cp "$DROPIN" "$EVIDENCE/prior-worker-dropin.conf"
SWITCHED=0
CONFIG_CHANGED=0

health() {
    for attempt in $(seq 1 15); do
        if systemctl is-active --quiet sales-api.service && systemctl is-active --quiet sales-worker.service &&
           curl --fail --silent --connect-timeout 3 --max-time 5 https://www.ericepc.com/api/v1/health/ready >/dev/null; then
            echo 'API_WORKER_READY_OK'; return 0
        fi
        sleep 2
    done
    return 1
}

restore_rollout() {
    python3 - "$EVIDENCE/prior-rollout.json" <<'PY'
import grp,os,sys
from pathlib import Path
target=Path('/etc/sales-fde-pilot.json')
fd=os.open(str(target)+'.visit-rollback',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o640)
with os.fdopen(fd,'wb') as stream:
    stream.write(Path(sys.argv[1]).read_bytes());stream.flush();os.fsync(stream.fileno())
    os.fchown(stream.fileno(),0,grp.getgrnam('sales-backend').gr_gid)
os.replace(str(target)+'.visit-rollback',target)
PY
}

failure() {
    result=$?
    trap - ERR INT TERM
    test "$result" -ne 0 || result=1
    if [ "$CONFIG_CHANGED" -eq 1 ]; then
        restore_rollout || echo 'ROLLOUT_RESTORE_FAILED'
        cp "$EVIDENCE/prior-worker-dropin.conf" "$DROPIN"
    fi
    if [ "$SWITCHED" -eq 1 ]; then
        ln -sfn "$NEW" "$APP_ROOT/failed-agent-visit-${COMMIT:0:7}"
        ln -sfn "$EXPECTED_OLD" "$APP_ROOT/current.next"
        mv -Tf "$APP_ROOT/current.next" "$APP_ROOT/current"
        systemctl daemon-reload
        systemctl restart sales-api.service sales-worker.service
        health || echo 'ROLLBACK_HEALTH_FAILED'
        echo "ROLLED_BACK_TO=$EXPECTED_OLD"
    fi
    exit "$result"
}
trap failure ERR INT TERM

python3 - "$ARCHIVE" "$MANIFEST" "$COMMIT" "$NEW" <<'PY'
import hashlib,json,sys,tarfile
from pathlib import Path
archive,manifest,commit,destination=sys.argv[1:]
meta=json.loads(Path(manifest).read_text())
assert meta['commit']==commit
assert hashlib.sha256(Path(archive).read_bytes()).hexdigest()==meta['archive_sha256']
root=Path(destination);root.mkdir(mode=0o755)
with tarfile.open(archive) as source:source.extractall(root,filter='data')
assert all((root/name).is_file() and hashlib.sha256((root/name).read_bytes()).hexdigest()==digest
           for name,digest in meta['hashes'].items())
(root/'REVISION').write_text(commit+'\n')
print('IMMUTABLE_SOURCE_FILES_VERIFIED='+str(len(meta['hashes'])),flush=True)
PY
"$EXPECTED_OLD/.venv/bin/python" -m venv "$NEW/.venv"
"$NEW/.venv/bin/python" -m pip install --quiet --constraint "$VERIFY_DIR/runtime-constraints.txt" -e "$NEW/backend"
"$NEW/.venv/bin/python" -m compileall -q "$NEW/backend/src"
"$NEW/.venv/bin/python" "$NEW/backend/scripts/verify_release_runtime.py" --release "$NEW"
BACKUP_DIR="/var/backups/sales-management/agent-visit-${COMMIT:0:7}"
test ! -e "$BACKUP_DIR"
install -d -m 700 "$BACKUP_DIR"
sudo -u postgres pg_dump -h /tmp -Fc sales_saas > "$BACKUP_DIR/pre-switch.dump.partial"
test -s "$BACKUP_DIR/pre-switch.dump.partial"
pg_restore --list "$BACKUP_DIR/pre-switch.dump.partial" >/dev/null
sync -f "$BACKUP_DIR/pre-switch.dump.partial"
mv "$BACKUP_DIR/pre-switch.dump.partial" "$BACKUP_DIR/pre-switch.dump"
echo "BACKUP_BYTES=$(stat -c %s "$BACKUP_DIR/pre-switch.dump")"
BUSY=$(sudo -u postgres psql -X -h /tmp -d sales_saas -At -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM ops.job WHERE status='running'")
test "$BUSY" = 0

pilot() {
    python3 - "$1" <<'PY'
import grp,json,os,sys
from datetime import datetime,timezone,timedelta
from pathlib import Path
mode=sys.argv[1];path=Path('/etc/sales-fde-pilot.json');config=json.loads(path.read_text())
caps=config['00000000-0000-0000-0000-000000000001']['capabilities']
for name in ('battle_map_review','opportunity_draft','personal_risks'):
    assert caps[name]=={'enabled':True,'rollout':'production'}
caps['visit_entry']={'enabled':mode!='off','rollout':'production' if mode=='production' else 'pilot'}
if mode not in ('off','production'):
    caps['visit_entry'].update(user_ids=['02000000-0000-0000-0000-000000000004'],
        expires_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),
        block_platform_requests=mode=='blocked')
fd=os.open(str(path)+'.visit-next',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o640)
with os.fdopen(fd,'w') as stream:
    json.dump(config,stream);stream.flush();os.fsync(stream.fileno())
    os.fchown(stream.fileno(),0,grp.getgrnam('sales-backend').gr_gid)
os.replace(str(path)+'.visit-next',path)
print('VISIT_ENTRY_MODE='+mode,flush=True)
PY
}

CONFIG_CHANGED=1
pilot off
printf '%s\n' 'EnvironmentFile=/etc/sales-fde-visit-entry.env' >> "$DROPIN"
ln -sfn "$EXPECTED_OLD" "$APP_ROOT/previous.next"
mv -Tf "$APP_ROOT/previous.next" "$APP_ROOT/previous"
ln -sfn "$NEW" "$APP_ROOT/current.next"
# Set before the atomic switch so a failure immediately after it also rolls back.
SWITCHED=1
mv -Tf "$APP_ROOT/current.next" "$APP_ROOT/current"
systemctl daemon-reload
systemctl restart sales-api.service sales-worker.service
health
scenario() {
    pilot "$1"
    sudo -u postgres env PYTHONDONTWRITEBYTECODE=1 "$NEW/.venv/bin/python" \
        "$NEW/backend/scripts/acceptance/visit_entry_business_live.py" \
        --scenario "$1" --case "$2" --scope-file "$SCOPE" --output "$EVIDENCE/$1-$2.json"
}
scenario normal regular
scenario normal first
scenario normal review
scenario normal poor
scenario off regular
scenario blocked review
scenario production regular
python3 - "$EVIDENCE" <<'PY'
import json,sys
from pathlib import Path
root=Path(sys.argv[1]);cases=('normal-regular','normal-first','normal-review','normal-poor',
                             'off-regular','blocked-review','production-regular')
reports=[json.loads((root/(case+'.json')).read_text()) for case in cases]
assert all(item.get('passed') and all(check['passed'] for check in item['checks']) for item in reports)
print(json.dumps({'business_cases_passed':len(reports),
                  'platform_answers_accepted':sum(bool(item.get('platform_accepted')) for item in reports),
                  'manual_archival_performed':False}),flush=True)
PY
health
echo "VISIT_ENTRY_PRODUCTION_ENABLED=$NEW"
trap - ERR INT TERM
