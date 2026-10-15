"""Copy a stopped, marked local environment outside cloud-synchronized Documents.

The old environment is retained. No arbitrary path, DSN, production data reset,
or forced PostgreSQL shutdown is accepted.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import uuid

from local_paths import ensure_private_directory


def files(folder):
    result = {}
    for path in folder.rglob('*'):
        if path.is_symlink():
            raise RuntimeError('Local data contains a symlink; relocation requires explicit review')
        if path.is_file():
            digest = hashlib.sha256()
            try:
                with path.open('rb') as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        digest.update(chunk)
            except OSError as exc:
                raise RuntimeError('Local file is not readable; original retained: ' + str(path)) from exc
            result[str(path.relative_to(folder))] = digest.hexdigest()
    return result


def relocate(dev):
    legacy, target = dev.ROOT / '.local', dev.LOCAL
    source = legacy / 'settings.json'
    if not source.is_file() or legacy.is_symlink() or source.is_symlink():
        raise RuntimeError('No marked legacy environment to relocate')
    cfg = json.loads(source.read_text())
    if cfg.get('kind') != 'salesbuddy-local-v1' or cfg.get('root') != str(dev.ROOT) or (
        cfg.get('db_port'), cfg.get('api_port')) != (dev.DB_PORT, dev.API_PORT):
        raise RuntimeError('Legacy environment identity mismatch; nothing moved')
    if target.exists():
        raise RuntimeError('Destination exists; keep both environments for review instead of overwriting')
    data = legacy / 'postgres'
    if not (data / 'PG_VERSION').is_file() or data.is_symlink():
        raise RuntimeError('Legacy database identity is missing')
    version = (data / 'PG_VERSION').read_text().strip()
    if version != '16':
        raise RuntimeError('Relocation is supported only for the existing PostgreSQL 16 environment')
    pidfile = data / 'postmaster.pid'
    if pidfile.exists():
        lines = pidfile.read_text().splitlines()
        if len(lines) < 4 or Path(lines[1]).resolve() != data.resolve() or int(lines[3]) != dev.DB_PORT:
            raise RuntimeError('Database process marker belongs to another environment')
    dev.stop_apps(legacy)
    command = [str(dev.pg_bin() / 'pg_ctl'), '-D', str(data)]
    running = subprocess.run([*command, 'status'], env=dev.clean_env(), capture_output=True)
    if running.returncode == 0:
        dev.run([*command, '-m', 'fast', '-t', '30', '-w', 'stop'], env=dev.clean_env())
    elif running.returncode != 3:
        raise RuntimeError('Cannot determine legacy database state; nothing copied')
    if pidfile.exists():
        raise RuntimeError('Database is not cleanly stopped; no file copy attempted')
    control = subprocess.run([str(dev.pg_bin() / 'pg_controldata'), str(data)],
                             env={**dev.clean_env(), 'LC_ALL': 'C'}, capture_output=True, text=True, check=True)
    if not any(line.startswith('Database cluster state:') and line.split(':',1)[1].strip() == 'shut down'
               for line in control.stdout.splitlines()):
        raise RuntimeError('Database shutdown was not clean; keep original data for recovery')
    ensure_private_directory(target.parent)
    stage = target.with_name(target.name + '.relocating-' + uuid.uuid4().hex)
    ensure_private_directory(stage)
    # Regenerate code, interpreters and logs; preserve settings, keys, originals,
    # backups and any unknown user-created local files rather than dropping them.
    generated = {'builds', 'venv', 'frontend', 'dependencies.json', 'build.json',
                 'api.pid.json', 'worker.pid.json', 'api.log', 'worker.log', 'postgres.log',
                 'verification.json', 'LOCATION.md', 'MOVED.json'}
    hashes = {}
    try:
        for entry in legacy.iterdir():
            if entry.name in generated:
                continue
            if entry.is_symlink():
                raise RuntimeError('Legacy local entry is a symlink; original retained')
            dest = stage / entry.name
            if entry.is_dir():
                before = files(entry)
                shutil.copytree(entry, dest, symlinks=False)
                if files(dest) != before or files(entry) != before:
                    raise RuntimeError('Copied local data changed or failed checksum validation')
                hashes[entry.name] = before
            elif entry.is_file():
                content = entry.read_bytes()
                dest.write_bytes(content)
                shutil.copystat(entry, dest)
                if dest.read_bytes() != content or entry.read_bytes() != content:
                    raise RuntimeError('Copied local file differs')
                hashes[entry.name] = hashlib.sha256(content).hexdigest()
        cfg['state_root'] = str(target)
        dev.write_json(stage / 'settings.json', cfg, private=True)
        dev.write_json(stage / 'relocation.json', {'kind':'salesbuddy-local-relocation-v1',
            'source':str(legacy), 'target':str(target), 'source_retained':True, 'source_sha256':hashes}, private=True)
        # Refuse a destination introduced during the copy; rename must never merge.
        if target.exists():
            raise RuntimeError('Destination appeared during relocation; both copies retained')
        stage.rename(target)
        dev.write_json(legacy / 'MOVED.json', {'state_root':str(target), 'source_retained':True})
    except BaseException:
        print('Relocation incomplete; original retained, partial copy: ' + str(stage))
        raise
    print('Local data preserved and verified at: ' + str(target))
    print('Old .local retained. Run dev.py up to rebuild and verify before acceptance.')
