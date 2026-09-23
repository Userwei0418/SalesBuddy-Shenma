#!/usr/bin/env python3
"""Install the verified original bundle using verified local Docker archives."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
from urllib.parse import urlsplit


def run(*args, cwd=None):
    subprocess.run(args, cwd=cwd, check=True)


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bundle', type=Path, required=True)
    p.add_argument('--images', type=Path, required=True)
    p.add_argument('--install-dir', type=Path, default=Path('/opt/raccoon-agent'))
    p.add_argument('--public-url', default='https://ops-salesbuddy.shenzhoukuntai.com')
    p.add_argument('--resume', action='store_true')
    a = p.parse_args()
    if os.geteuid() != 0 or socket.gethostname() != 'opsbuddy':
        p.error('Run as root on the customer opsbuddy host only')
    a.bundle = a.bundle.resolve()
    a.images = a.images.resolve()
    a.install_dir = a.install_dir.absolute()
    run('sha256sum', '-c', '--quiet', 'SHA256SUMS', cwd=a.bundle)
    expected = set(json.loads((a.bundle / 'BASE-IMAGES.json').read_text()).values())
    entries = json.loads((a.images / 'transfer-manifest.json').read_text())
    if len(entries) != len(expected) or {e['source'] for e in entries} != expected:
        p.error('Image manifest must exactly match all pinned official base images')
    mapping = {}
    for e in entries:
        name = e['compressed_file']
        if Path(name).name != name or not name.endswith('.tar.gz'):
            p.error('Invalid image archive path')
        if not re.fullmatch(r'shenma-base/[a-z0-9-]+:[0-9a-f]{16}', e['local_tag']):
            p.error('Invalid local image tag')
        image_file = a.images / name
        if digest(image_file) != e['compressed_sha256']:
            p.error('Archive SHA256 mismatch: ' + name)
        mapping[e['source']] = e['local_tag']
    # Verify every archive before modifying Docker or creating the instance.
    run('docker', 'info', '--format', '{{.ServerVersion}}')
    for e in entries:
        run('docker', 'load', '-i', str(a.images / e['compressed_file']))
        run('docker', 'image', 'inspect', '--format', '{{.Architecture}} {{.Os}} {{.Id}}', e['local_tag'])
    args = ['python3', str(a.bundle / 'configure.py'), '--install-dir', str(a.install_dir),
            '--public-url', a.public_url]
    if a.resume:
        args.append('--resume')
    run(*args)
    runtime = a.install_dir / 'runtime'
    compose_file = runtime / 'compose.json'
    compose = json.loads(compose_file.read_text())
    for service in compose['services'].values():
        if service.get('image') in mapping:
            service['image'] = mapping[service['image']]
            service['pull_policy'] = 'never'
        elif 'image' in service and 'build' not in service:
            if service['image'] not in mapping.values():
                p.error('Unexpected image in compose; refusing registry fallback')
    # Verified customer NAT: public 18899 reaches opsbuddy:18899.
    # Keep internal 443 for the canonical URL and expose only HTTPS on this extra port.
    ports = compose['services']['nginx'].setdefault('ports', [])
    reserved_https_port = '${BIND_ADDRESS:-0.0.0.0}:18899:443'
    if (urlsplit(a.public_url).port or 443) != 18899 and reserved_https_port not in ports:
        ports.append(reserved_https_port)
    compose_file.write_text(json.dumps(compose, indent=2) + '\n')
    for name in ('api', 'web'):
        dockerfile = runtime / 'build' / name / 'Dockerfile'
        text = dockerfile.read_text()
        for source, local in mapping.items():
            text = text.replace('FROM ' + source, 'FROM ' + local)
        if not text.startswith('FROM shenma-base/'):
            p.error('Unexpected base image in ' + str(dockerfile))
        dockerfile.write_text(text)
    receipt = {'installer_script_sha256': digest(Path(__file__).resolve()),
               'original_base_images': json.loads((a.bundle / 'BASE-IMAGES.json').read_text()),
               'verified_local_archives': entries,
               'modified_files': {str(f.relative_to(a.install_dir)): digest(f) for f in
                                  (compose_file, runtime / 'build/api/Dockerfile', runtime / 'build/web/Dockerfile')}}
    (a.install_dir / 'offline-provenance.json').write_text(json.dumps(receipt, indent=2) + '\n')
    command = ('docker', 'compose', '-f', str(compose_file))
    run(*command, 'config', '--quiet', cwd=runtime)
    run(*command, 'build', '--pull=false', 'api', 'web', cwd=runtime)
    run(*command, 'up', '-d', '--no-build', '--pull', 'never', cwd=runtime)
    run('python3', str(a.install_dir / 'healthcheck.py'), '--wait', '300')
    print('Local HTTPS readiness passed. Public routing and trusted TLS still need separate validation.')


if __name__ == '__main__':
    main()
