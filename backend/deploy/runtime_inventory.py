"""Read-only inventory: actual service cwd/revision versus current and rollback.

Prints no command lines, credentials, environment values or database contents.
Run with the release interpreter on the managed Linux host after every release.
"""
import json
from pathlib import Path
import re
import subprocess

ROOT = Path('/www/wwwroot/salegent')


def revision(path):
    file = path / 'REVISION'
    value = file.read_text().strip() if file.is_file() else ''
    return value if re.fullmatch('[a-f0-9]{40}', value) else None


def inventory(root=ROOT):
    current = (root / 'current').resolve(strict=True)
    previous = (root / 'previous').resolve(strict=True) if (root / 'previous').exists() else None
    services = []
    for name in ('sales-api.service', 'sales-worker.service', 'sales-feishu-worker.service'):
        values = subprocess.check_output(['systemctl', 'show', name, '-p', 'MainPID', '-p', 'ActiveState', '-p', 'LoadState'], text=True)
        props = dict(line.split('=', 1) for line in values.splitlines() if '=' in line)
        if props.get('LoadState') == 'not-found' and name == 'sales-feishu-worker.service':
            continue
        pid = int(props.get('MainPID', 0))
        cwd = Path(f'/proc/{pid}/cwd').resolve() if pid else None
        services.append({'service':name, 'state':props.get('ActiveState'), 'pid':pid,
                         'release':cwd.name if cwd else None, 'revision':revision(cwd) if cwd else None,
                         'matches_current':props.get('ActiveState') == 'active' and cwd == current})
    releases = [{'release':p.name, 'revision':revision(p),
                 'purpose':'current' if p == current else 'rollback' if p == previous else 'archive'}
                for p in sorted((root / 'releases').iterdir()) if p.is_dir()]
    return {'current':current.name, 'revision':revision(current), 'previous':previous.name if previous else None,
            'consistent':bool(revision(current)) and all(s['matches_current'] for s in services),
            'services':services, 'releases':releases}


if __name__ == '__main__':
    result = inventory()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['consistent'] else 1)
