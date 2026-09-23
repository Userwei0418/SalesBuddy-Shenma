#!/usr/bin/python3
"""Certbot deploy hook for the two isolated customer hosts; never print key data."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import tempfile
import time

LE_ROOT = Path('/etc/letsencrypt')
STATE_ROOT = Path('/var/lib/shenma-tls')
TARGETS = {
    'salesbuddy': ('salesbuddy.shenzhoukuntai.com', '/etc/shenma-sales/tls'),
    'opsbuddy': ('ops-salesbuddy.shenzhoukuntai.com', '/opt/raccoon-agent/runtime/tls'),
}


def run(*args, cwd=None):
    subprocess.run(args, cwd=cwd, check=True)


def atomic_copy(source, destination, mode):
    fd, name = tempfile.mkstemp(prefix='.certbot-', dir=destination.parent)
    try:
        with os.fdopen(fd, 'wb') as out, source.open('rb') as inp:
            shutil.copyfileobj(inp, out)
            out.flush()
            os.fsync(out.fileno())
            os.fchmod(out.fileno(), mode)
        os.replace(name, destination)
    finally:
        Path(name).unlink(missing_ok=True)


def main():
    if os.geteuid() != 0:
        raise SystemExit('Run as root')
    host = socket.gethostname()
    if host not in TARGETS:
        raise SystemExit('Unexpected customer host')
    domain, destination = TARGETS[host]
    lineage = LE_ROOT / 'live' / domain
    if os.environ.get('RENEWED_LINEAGE') != str(lineage):
        return  # This hook must not affect unrelated certificate lineages.
    cert, key = lineage / 'fullchain.pem', lineage / 'privkey.pem'
    ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(cert, key)
    run('openssl', 'x509', '-in', str(cert), '-noout', '-checkhost', domain)
    run('openssl', 'x509', '-in', str(cert), '-noout', '-checkend', '86400')
    target = Path(destination)
    if not target.is_dir():
        raise SystemExit('HTTPS instance is not installed; deploy certificate after installation')
    files = [(cert, target / 'server.crt', 0o644), (key, target / 'server.key', 0o600)]
    runtime = Path('/opt/raccoon-agent/runtime')
    compose = ['docker', 'compose', '-f', str(runtime / 'compose.json')]
    # Backups remain root-only and are removed after success or rollback.
    with tempfile.TemporaryDirectory(prefix='shenma-tls-', dir=LE_ROOT) as scratch:
        saved = []
        for _, dst, _ in files:
            if not dst.is_file():
                raise SystemExit('Existing HTTPS material missing; refusing partial installation')
            backup = Path(scratch) / dst.name
            shutil.copy2(dst, backup)
            saved.append((backup, dst, dst.stat().st_mode & 0o777))
        try:
            for src, dst, mode in files:
                atomic_copy(src, dst, mode)
            if host == 'salesbuddy':
                run('nginx', '-t')
                run('systemctl', 'reload', 'nginx')
            else:
                # The installer mounts the TLS directory, so atomic replacement is visible.
                run(*compose, 'exec', '-T', 'nginx', 'nginx', '-t', cwd=runtime)
                run(*compose, 'exec', '-T', 'nginx', 'nginx', '-s', 'reload', cwd=runtime)
        except Exception:
            for src, dst, mode in saved:
                atomic_copy(src, dst, mode)
            raise
    receipt = STATE_ROOT
    receipt.mkdir(mode=0o700, exist_ok=True)
    (receipt / 'last-deploy.json').write_text(json.dumps({
        'domain': domain, 'deployed_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'fullchain_sha256': hashlib.sha256(cert.read_bytes()).hexdigest(),
        'service_reloaded': True,
    }, indent=2) + '\n')
    print('Certificate deployed and HTTPS reloaded for ' + domain)


if __name__ == '__main__':
    main()
