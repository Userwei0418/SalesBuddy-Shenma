#!/usr/bin/env python3
"""Package committed customer source and deployment instructions, without runtime data."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile


ROOT = Path(__file__).resolve().parents[1]


def git(*args: str) -> bytes:
    return subprocess.check_output(['git', '-C', str(ROOT), *args])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.expanduser().resolve()
    if output == ROOT or ROOT in output.parents:
        parser.error('Output must be outside the Git repository')
    if git('status', '--porcelain', '--untracked-files=all').strip():
        parser.error('Commit and review the complete source tree before packaging')
    remote = git('remote', 'get-url', 'origin').decode().strip().removesuffix('.git')
    if remote not in ('https://github.com/Userwei0418/SalesBuddy-Shenma',
                      'git@github.com:Userwei0418/SalesBuddy-Shenma'):
        parser.error('This packager only accepts the isolated Shenma repository')
    subprocess.run(['python3', str(ROOT / 'scripts/check_isolation.py'), '--verify-remote'], check=True, cwd=ROOT)
    revision = git('rev-parse', 'HEAD').decode().strip()
    timestamp = int(git('show', '-s', '--format=%ct', 'HEAD'))
    name = 'SalesBuddy-Shenma-source-' + revision[:12]
    target = output / (name + '.tar.gz')
    if target.exists() or target.with_suffix(target.suffix + '.sha256').exists():
        parser.error('Release artifact already exists; refusing to overwrite it')
    files: dict[str, tuple[bytes, int]] = {}
    archive = git('archive', '--format=tar', 'HEAD')
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        for item in source:
            path = PurePosixPath(item.name)
            if item.isdir():
                continue
            forbidden = (
                not item.isfile() or path.is_absolute() or '..' in path.parts
                or any(part in {'.git', '.venv', 'node_modules', '__pycache__', '.private'} for part in path.parts)
                or path.name == 'project.private.config.json'
                or (path.name.startswith('.env') and path.name != '.env.example')
                or path.suffix.lower() in {'.dump', '.backup', '.sqlite', '.sqlite3', '.db', '.key', '.pem', '.p12', '.pfx'}
                or '/runtime/volumes/' in '/' + item.name
                or '/runtime/tls/' in '/' + item.name
                or re.search(r'[\x00-\x1f]', item.name)
            )
            if forbidden:
                parser.error('Forbidden source artifact: ' + item.name)
            content = source.extractfile(item).read()
            if re.search(rb'^-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----\s*$', content, re.M):
                parser.error('Private key found in ' + item.name)
            files[item.name] = (content, item.mode & 0o777)
    project = json.loads(files['frontend/project.config.json'][0])
    migrations = [int(match.group(1)) for path in files
                  if (match := re.fullmatch(r'database/(?:migrations/)?V(\d+)__[^/]+\.sql', path))]
    if not migrations:
        parser.error('Database migration ledger source is missing')
    files['REVISION'] = ((revision + '\n').encode(), 0o644)
    metadata = {
        'artifact_kind': 'customer_source', 'customer': 'shenzhoukuntai',
        'source_revision': revision,
        'source_commit_time': datetime.fromtimestamp(timestamp, timezone.utc).isoformat(),
        'database_head': f'V{max(migrations):03d}', 'contains_business_data': False,
        'contains_runtime_credentials': False,
        'customer_appid': 'pending' if project['appid'].startswith('REPLACE_') else project['appid'],
        'deployment_status': 'See docs/DEPLOYMENT.md; source packaging is not runtime acceptance.',
        'agent_platform_installer': 'https://github.com/Userwei0418/SalesBuddy-Shenma/releases/tag/agent-platform-20260923',
        'agent_platform_web_source': 'Not supplied; compiled Web bundle is in the private installer release.',
    }
    files['DELIVERY.json'] = ((json.dumps(metadata, ensure_ascii=False, indent=2) + '\n').encode(), 0o644)
    checksums = ''.join(hashlib.sha256(content).hexdigest() + '  ' + path + '\n'
                        for path, (content, _) in sorted(files.items()))
    files['SHA256SUMS'] = (checksums.encode(), 0o644)
    output.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with target.open('xb') as raw:
            created = True
            target.chmod(0o600)
            with gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=timestamp) as compressed:
                with tarfile.open(fileobj=compressed, mode='w', format=tarfile.PAX_FORMAT) as dest:
                    for path, (content, mode) in sorted(files.items()):
                        info = tarfile.TarInfo(name + '/' + path)
                        info.size, info.mode, info.mtime = len(content), mode, timestamp
                        dest.addfile(info, io.BytesIO(content))
        with tarfile.open(target) as verified:
            members = verified.getmembers()
            assert len(members) == len(files)
            for member in members:
                path = str(PurePosixPath(member.name).relative_to(name))
                assert verified.extractfile(member).read() == files[path][0]
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        checksum_file = target.with_suffix(target.suffix + '.sha256')
        with checksum_file.open('x') as checksum:
            checksum.write(digest + '  ' + target.name + '\n')
        checksum_file.chmod(0o600)
    except BaseException:
        if created:
            target.unlink(missing_ok=True)
        raise
    print(json.dumps({'artifact': str(target), 'revision': revision, 'sha256': digest,
                      'files': len(files), 'bytes': target.stat().st_size,
                      'customer_appid': metadata['customer_appid']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
