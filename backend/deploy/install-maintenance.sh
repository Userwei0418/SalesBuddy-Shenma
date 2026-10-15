#!/usr/bin/env bash
# Install versioned operational files only; never start/restart application services.
set -euo pipefail
umask 027
SOURCE=$(cd -- "$(dirname -- "$0")" && pwd)
DESTDIR=''
CHECK_ONLY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) CHECK_ONLY=1; shift ;;
    --destdir)
      [[ $# -ge 2 && "$2" == /* && "$2" != / ]] || { echo '--destdir needs a non-root absolute staging directory' >&2; exit 2; }
      DESTDIR=${2%/}; shift 2 ;;
    *) echo 'Usage: install-maintenance.sh [--check] [--destdir /absolute/staging-directory]' >&2; exit 2 ;;
  esac
done
command -v python3 >/dev/null
command -v install >/dev/null
if [[ -n "$DESTDIR" ]]; then
  DESTDIR=$(python3 - "$DESTDIR" <<'PY'
from pathlib import Path
import sys
root = Path(sys.argv[1]).resolve()
if root == Path('/'):
    raise SystemExit('Refusing the live root as an offline staging directory')
print(root)
PY
)
fi
for file in sales-db-backup sales-release-retention server-retention sales-db-backup.service sales-db-backup.timer server-retention.service server-retention.timer server-retention.conf.example; do
  [[ -f "$SOURCE/$file" ]] || { echo "Missing installer source: $file" >&2; exit 1; }
done

assert_services_idle() {
  local unit state
  for unit in server-retention.service sales-db-backup.service ob-blog-sync.service; do
    state=$(systemctl show "$unit" --property=ActiveState --value) || { echo "Cannot determine activity: $unit" >&2; exit 1; }
    case "$state" in
      inactive|failed) ;;
      *) echo "Refusing install: $unit is $state; wait for it to finish" >&2; exit 1 ;;
    esac
  done
}

if [[ -z "$DESTDIR" ]]; then
  command -v systemctl >/dev/null
  command -v flock >/dev/null
  assert_services_idle
fi

# Read only the non-secret policy, as data; reject invalid syntax before copying.
LOCK_PATHS=$(python3 - "$DESTDIR/etc/server-retention.conf" <<'PY'
from pathlib import Path
import re, shlex, sys
p = Path(sys.argv[1])
values = {}
if p.is_symlink():
    raise SystemExit('Refusing a symlink policy file')
if p.exists():
    for number, line in enumerate(p.read_text().splitlines(), 1):
        tokens = shlex.split(line, comments=True)
        if not tokens:
            continue
        if len(tokens) != 1 or not re.match(r'^[A-Z][A-Z0-9_]*=', tokens[0]):
            raise SystemExit(f'Invalid policy syntax at line {number}')
        key, value = tokens[0].split('=', 1)
        values[key] = value
paths = [values.get('DB_BACKUP_LOCK_FILE', '/run/lock/sales-db-backup.lock'),
         values.get('APP_ROOT', '/www/wwwroot/salegent') + '/.deploy.lock']
if values.get('BLOG_ENABLED', '0') == '1':
    paths.append(values.get('BLOG_STATE_DIR', '/var/lib/ob-blog-sync') + '/sync.lock')
for value in paths:
    if not value.startswith('/') or '\n' in value:
        raise SystemExit('Invalid lock path')
    print(value)
PY
)
if [[ "$CHECK_ONLY" == 1 ]]; then
  echo 'Preflight OK; no files, timers or services changed'
  exit 0
fi

install_owned() {
  if [[ -z "$DESTDIR" ]]; then
    install -o root -g root "$@"
  else
    install "$@"
  fi
}
if [[ -z "$DESTDIR" ]]; then
  [[ $EUID -eq 0 ]] || { echo 'Run installation as root (or use --destdir for offline staging)' >&2; exit 1; }
  # Hold the same locks used by manual exports, releases and blog sync. Never
  # interrupt an active operation, including one started outside systemd.
  while IFS= read -r path; do
    [[ -d "$(dirname -- "$path")" ]] || continue
    [[ ! -L "$path" ]] || { echo "Refusing lock symlink: $path" >&2; exit 1; }
    exec {lock_fd}>"$path"
    flock -n "$lock_fd" || { echo "Refusing install: maintenance lock busy: $path" >&2; exit 1; }
  done <<< "$LOCK_PATHS"
  # Stop only idle scheduling timers; no running service is stopped.
  for unit in server-retention.timer sales-db-backup.timer; do
    if systemctl is-active --quiet "$unit"; then systemctl stop "$unit"; fi
  done
  assert_services_idle
fi

TARGETS=(
  /usr/local/sbin/sales-db-backup
  /usr/local/sbin/sales-release-retention
  /usr/local/sbin/server-retention
  /etc/systemd/system/sales-db-backup.service
  /etc/systemd/system/sales-db-backup.timer
  /etc/systemd/system/server-retention.service
  /etc/systemd/system/server-retention.timer
  /etc/server-retention.conf
)
for path in "${TARGETS[@]}"; do
  [[ ! -L "$DESTDIR$path" && ! -d "$DESTDIR$path" ]] || { echo "Refusing non-regular target: $path" >&2; exit 1; }
done
SNAPSHOT="$DESTDIR/var/backups/server-retention/install-salegent-$(date +%Y%m%d_%H%M%S)-$$"
install -d -m 0700 "$SNAPSHOT"
for path in "${TARGETS[@]}"; do
  if [[ -f "$DESTDIR$path" ]]; then
    install -d -m 0700 "$(dirname -- "$SNAPSHOT$path")"
    cp -a -- "$DESTDIR$path" "$SNAPSHOT$path"
  fi
done
install -d -m 0755 "$DESTDIR/usr/local/sbin" "$DESTDIR/etc/systemd/system"
for name in sales-db-backup sales-release-retention server-retention; do
  install_owned -m 0755 "$SOURCE/$name" "$DESTDIR/usr/local/sbin/$name"
done
for name in sales-db-backup.service sales-db-backup.timer server-retention.service server-retention.timer; do
  install_owned -m 0644 "$SOURCE/$name" "$DESTDIR/etc/systemd/system/$name"
done
python3 - "$SOURCE/server-retention.conf.example" "$DESTDIR/etc/server-retention.conf" <<'PY'
from pathlib import Path
import os, re, shlex, sys, tempfile
template, target = map(Path, sys.argv[1:])
original = target.read_text() if target.exists() else ''
existing = set()
for line in original.splitlines():
    parts = shlex.split(line, comments=True)
    if parts:
        existing.add(parts[0].split('=', 1)[0])
missing = []
for line in template.read_text().splitlines():
    match = re.match(r'^([A-Z][A-Z0-9_]*)=', line)
    if match and match[1] not in existing:
        missing.append(line)
if not target.exists():
    result = template.read_text()
elif missing:
    result = original.rstrip('\n') + '\n# Added by Salegent maintenance installer\n' + '\n'.join(missing) + '\n'
else:
    result = original
fd, temporary = tempfile.mkstemp(prefix='.server-retention-', dir=target.parent)
try:
    with os.fdopen(fd, 'w') as output:
        output.write(result)
        output.flush()
        os.fsync(output.fileno())
        os.fchmod(output.fileno(), 0o644)
    os.replace(temporary, target)
finally:
    Path(temporary).unlink(missing_ok=True)
PY
if [[ -z "$DESTDIR" ]]; then
  chown root:root /etc/server-retention.conf
  systemctl daemon-reload
  systemctl enable server-retention.timer sales-db-backup.timer
  echo 'Installed and enabled; timers remain stopped. Review dry-run, run maintenance and backup, then start the timers.'
else
  echo 'Offline files staged; no host service or timer commands executed.'
fi
echo "Exact previous files backed up at: $SNAPSHOT"
