"""Build/verify a committed product candidate; never install or contact a service."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tempfile
from urllib.parse import urlsplit
import zipfile


FORMAT = 'salesbuddy-product-bundle-v1'
MAX_BYTES = 512 * 1024 * 1024
MAX_FILES = 20000
MANIFEST = 'PRODUCT_MANIFEST.json'
REQUIRED = frozenset({
    'backend/src/sales_backend/main.py', 'backend/src/sales_backend/config.py',
    'backend/pyproject.toml', 'backend/uv.lock', 'backend/openapi/openapi.yaml',
    'backend/src/sales_backend/contracts/visit_schema.py',
    'backend/src/sales_backend/services/agent_platform/result_contracts.py',
    'backend/src/sales_backend/domain/route_permissions.py',
    'database/baseline/V001__current_schema.sql', 'database/scripts/migrate.py',
    'frontend/miniprogram/config.js', 'frontend/project.config.json',
})
PRIVATE_PARTS = frozenset({'.git', '.ssh', 'node_modules', '__pycache__', '.local',
    'backups', 'credentials', 'project.private.config.json', '.DS_Store'})
PRIVATE_SUFFIXES = frozenset({'.key', '.pem', '.p12', '.pfx', '.dump', '.db', '.sqlite', '.sqlite3', '.pyc'})
GENERATED_PATHS = ['REVISION', 'frontend/VERSION.json', 'INSTALLATION_TARGET.json']
CONFIGURED_PATHS = ['frontend/miniprogram/config.js', 'frontend/project.config.json']
LEGACY_ADOPTION_TOOLS = frozenset({
    'backend/deploy/adopt_legacy_saas.py',
    'backend/deploy/legacy_adoption_state.py',
    'backend/deploy/legacy_adoption_backup.py',
    'backend/deploy/legacy_adoption_database.py',
    'backend/deploy/legacy_definer_dependencies.py',
    'backend/deploy/repair_legacy_definer_dependencies.py',
    'backend/deploy/inherit_service_environment.py',
    'backend/deploy/verify_service_identity.py',
    'backend/scripts/bootstrap_platform_operator.py',
})
ACCEPTANCE_TOOLS = frozenset({
    'backend/scripts/acceptance/feishu_release_probe.py',
    'backend/scripts/acceptance/FEISHU_RELEASE_PROBE.md',
})


class BundleError(ValueError):
    pass


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def safe_name(name):
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or str(path) != name or '\\' in name
            or '..' in path.parts or any(ord(c) < 32 for c in name)
            or any(p in PRIVATE_PARTS or p.startswith(('.env', 'id_rsa', 'id_ed25519')) for p in path.parts)
            or path.suffix.lower() in PRIVATE_SUFFIXES):
        raise BundleError('Unsafe or private artifact path')
    return name


def selected(name):
    path = PurePosixPath(name)
    return (name.startswith(('backend/src/', 'backend/openapi/', 'frontend/miniprogram/', 'tools/installation/'))
        or name in LEGACY_ADOPTION_TOOLS or name in ACCEPTANCE_TOOLS
        or name in {'backend/pyproject.toml', 'backend/uv.lock', 'frontend/project.config.json',
                    'database/scripts/migrate.py', 'docs/STANDARD_INSTALLATION.md',
                    'docs/DEMO_TENANT_TEMPLATE.md', 'docs/PRIVATE_CUSTOMIZATION_BASELINE.md', 'docs/TEMPORARY_SUPPORT_ACCESS.md',
                    'docs/INSTANCE_DISASTER_RECOVERY.md', 'docs/INSTANCE_MONITORING.md',
                    'docs/QUALITY_HARDENING_PLAN.md', 'docs/QUALITY_REVIEW_20260927.md',
                    'docs/QUALITY_VALIDATION_MATRIX.md', 'docs/CLOSED_COMPANY_EXPORT.md',
                    'docs/AUTHORIZATION_DELEGATION.md', 'docs/LOCAL_BACKUP_RECOVERY.md',
                    'docs/LOCAL_CI.md', 'docs/LOCAL_DEVELOPMENT.md', 'docs/PLATFORM_RUNTIME_OVERVIEW.md',
                    'docs/PLATFORM_TENANT_OPERATIONS.md', 'docs/SAAS_ACCEPTANCE_GUIDE.md',
                    'docs/BROWSER_ACCEPTANCE_FIXES_20260927.md', 'docs/SAAS_LOGIN_UX_20260928.md',
                    'docs/TASK_MEETING_IMPORT.md', 'docs/TASK_MEETING_IMPORT_ACCEPTANCE_20260928.md',
                    'docs/SAAS_COMPLETION_AUDIT.md', 'docs/SAAS_DESIGN.md', 'docs/SAAS_IMPLEMENTATION_PLAN.md',
                    'docs/SOURCE_RELOCATION_20260926.md', 'docs/TENANT_ACCOUNT_QUOTAS.md',
                    'docs/TENANT_CALL_QUOTAS.md', 'docs/TENANT_CONFIGURATION_STATUS.md',
                    'docs/TENANT_DATA_EXPORT.md', 'docs/TENANT_HTTP_LIMITS.md', 'docs/TENANT_STORAGE_QUOTAS.md'}
        or (path.suffix == '.sql' and (path.parent == PurePosixPath('database')
            or name.startswith(('database/baseline/', 'database/seeds/', 'database/migrations/')))))


def git(root, *args, **kwargs):
    try:
        return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True,
                              timeout=90, **kwargs).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise BundleError('Git source read failed; no release was published') from None


def committed_files(root, revision=None):
    checkout = git(root, 'rev-parse', 'HEAD').decode().strip()
    revision = revision or checkout
    if not re.fullmatch(r'[a-f0-9]{40}', revision):
        raise BundleError('Expected a full SHA-1 commit identity')
    if git(root, 'cat-file', '-t', revision).strip() != b'commit':
        raise BundleError('Source identity is not a commit')
    if git(root, 'status', '--porcelain', '--untracked-files=all').strip():
        raise BundleError('Commit all changes before packaging; no worktree files are substituted')
    entries = []
    for raw in git(root, 'ls-tree', '-r', '-z', revision).split(b'\0'):
        if not raw:
            continue
        info, name = raw.split(b'\t', 1)
        mode, kind, oid = info.split()
        name = name.decode()
        if selected(name):
            safe_name(name)
            if kind != b'blob' or mode not in (b'100644', b'100755'):
                raise BundleError('Product source must contain regular files only')
            entries.append((name, oid, int(mode, 8)))
    stream = io.BytesIO(git(root, 'cat-file', '--batch', input=b''.join(oid+b'\n' for _, oid, _ in entries)))
    files, modes = {}, {}
    for name, expected, mode in entries:
        oid, kind, size = stream.readline().strip().split()
        content = stream.read(int(size))
        actual = hashlib.sha1(b'blob '+str(len(content)).encode()+b'\0'+content).hexdigest().encode()
        if oid != expected or actual != expected or kind != b'blob' or stream.read(1) != b'\n':
            raise BundleError('Git object is incomplete or does not match its commit')
        files[name], modes[name] = content, mode
    if (stream.read() or git(root, 'rev-parse', 'HEAD').decode().strip() != checkout
            or git(root, 'status', '--porcelain', '--untracked-files=all').strip()):
        raise BundleError('Source changed while building the artifact')
    return revision, files, modes


def api_origin(value, profile):
    url = urlsplit(value)
    try:
        port = url.port
    except ValueError:
        raise BundleError('Invalid target API URL') from None
    if (value != value.strip() or any(ord(c) < 33 for c in value) or '\\' in value
            or url.username or url.password or url.query or url.fragment or not url.hostname
            or url.path != '/api/v1' or (port is not None and not 1 <= port <= 65535)):
        raise BundleError('API URL must have a host and end in /api/v1 without credentials, query or fragment')
    if profile == 'local':
        if url.scheme != 'http' or url.hostname not in {'127.0.0.1', 'localhost', '::1'}:
            raise BundleError('Local candidate must target a loopback HTTP API')
    elif profile == 'private':
        if url.scheme != 'https' or url.hostname in {'localhost', '127.0.0.1', '::1'}:
            raise BundleError('Private candidate requires an explicit HTTPS API')
    else:
        raise BundleError('Unknown candidate profile')
    return value


def migration_manifest(files):
    entries = []
    for name, content in sorted(files.items()):
        path = PurePosixPath(name)
        if name == 'database/baseline/V001__current_schema.sql':
            key = 'BASELINE_V1'
        elif name.startswith('database/seeds/') and re.fullmatch(r'S\d+__.+\.sql', path.name):
            key = path.name.split('__')[0]
        elif (path.parent == PurePosixPath('database') or name.startswith('database/migrations/')) and re.fullmatch(r'V\d+__.+\.sql', path.name):
            key = path.name.split('__')[0]
        else:
            continue
        entries.append({'key': key, 'path': name, 'sha256': sha256(content)})
    keys = [row['key'] for row in entries]
    if len(keys) != len(set(keys)) or 'BASELINE_V1' not in keys or not any(k.startswith('V') for k in keys):
        raise BundleError('Migration manifest is incomplete or contains duplicates')
    return sorted(entries, key=lambda r: (0 if r['key']=='BASELINE_V1' else 1 if r['key'].startswith('S') else 2,
                                         0 if r['key']=='BASELINE_V1' else int(r['key'][1:])))


def prepare(files, modes, *, revision, api_url, appid, profile, product_name):
    if not REQUIRED <= files.keys():
        raise BundleError('Required product files are missing')
    if not re.fullmatch(r'wx[a-f0-9]{16}', appid):
        raise BundleError('An explicit WeChat AppID is required')
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}', product_name):
        raise BundleError('Invalid product candidate label')
    api_origin(api_url, profile)
    for name in files:
        safe_name(name)
    source_hashes = {name: sha256(content) for name, content in files.items()}
    config = files['frontend/miniprogram/config.js'].decode()
    config, count = re.subn(r'(API_BASE_URL\s*:\s*)["\'][^"\']*["\']',
                           lambda match: match[1] + json.dumps(api_url), config)
    if count != 1:
        raise BundleError('Frontend API configuration shape changed; review the adapter')
    files = dict(files)
    files['frontend/miniprogram/config.js'] = config.encode()
    project = json.loads(files['frontend/project.config.json'])
    project['appid'], project['projectname'] = appid, 'Raccoon-SalesBuddy-'+product_name
    project.setdefault('setting', {})['urlCheck'] = profile != 'local'
    files['frontend/project.config.json'] = json_bytes(project)
    migrations = migration_manifest(files)
    head = migrations[-1]['key']
    files['REVISION'] = (revision+'\n').encode()
    files['frontend/VERSION.json'] = json_bytes({
        'artifact_kind': 'product_candidate', 'source_revision': revision,
        'api_base_url': api_url, 'expected_schema': head, 'appid': appid,
        'uploaded': False, 'production_deployed': False,
    })
    files['INSTALLATION_TARGET.json'] = json_bytes({
        'profile': profile, 'api_base_url': api_url, 'appid': appid,
        'auth_mode': 'password', 'external_integrations': 'unconfigured',
        'copy_existing_company_data': False, 'copy_existing_credentials': False,
    })
    manifest = {'format': FORMAT, 'artifact_kind': 'candidate', 'product': 'Raccoon SalesBuddy',
        'label': product_name, 'revision': revision, 'profile': profile,
        'api_base_url': api_url, 'appid': appid, 'expected_schema': head,
        'migrations': migrations, 'source_hashes': source_hashes,
        'file_hashes': {name: sha256(content) for name, content in sorted(files.items())},
        'dependency_locks': {'backend/uv.lock': sha256(files['backend/uv.lock'])},
        'api_contracts': {'backend/openapi/openapi.yaml': sha256(files['backend/openapi/openapi.yaml'])},
        'runtime_contracts': {name: sha256(content) for name, content in sorted(files.items())
             if name.startswith(('backend/src/sales_backend/contracts/', 'backend/src/sales_backend/services/agent_platform/'))},
        'generated_paths': GENERATED_PATHS,
        'configured_paths': CONFIGURED_PATHS,
        'production_deployed': False, 'mini_program_uploaded': False,
        'installation_verified': False, 'created_by': 'tools/installation/release_bundle.py'}
    files[MANIFEST] = json_bytes(manifest)
    return files, {name: modes.get(name, 0o100644) for name in files}, manifest


def write_archive(files, modes, output):
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise BundleError('Output already exists')
    output.parent.mkdir(parents=True, exist_ok=True)
    if len(files) > MAX_FILES or sum(len(content) for content in files.values()) > MAX_BYTES:
        raise BundleError('Expanded artifact exceeds limits')
    # Finish and fsync a private temporary file, then publish with an exclusive
    # hard link. Readers never see a partial archive; concurrent writers cannot
    # replace an already-published artifact.
    descriptor, temporary = tempfile.mkstemp(dir=output.parent, prefix='.salesbuddy-bundle-')
    try:
        with os.fdopen(descriptor, 'w+b') as temp, zipfile.ZipFile(temp, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, content in sorted(files.items()):
                safe_name(name)
                entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                entry.create_system = 3
                entry.external_attr = modes[name] << 16
                entry.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(entry, content)
        with open(temporary, 'rb') as stream:
            payload = stream.read(MAX_BYTES+1)
            os.fsync(stream.fileno())
        if len(payload) > MAX_BYTES:
            raise BundleError('Artifact exceeds the supported size')
        read_verified(temporary, sha256(payload))
        os.link(temporary, output)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return sha256(payload)


def read_verified(archive_path, expected_sha256):
    if not re.fullmatch(r'[a-f0-9]{64}', expected_sha256):
        raise BundleError('Supply the reviewed artifact SHA-256')
    with Path(archive_path).open('rb') as source:
        raw = source.read(MAX_BYTES+1)
    if len(raw) > MAX_BYTES or sha256(raw) != expected_sha256:
        raise BundleError('Artifact digest or size differs from the reviewed artifact')
    files = {}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_FILES or sum(e.file_size for e in entries) > MAX_BYTES:
            raise BundleError('Expanded artifact exceeds limits')
        for entry in entries:
            name = safe_name(entry.filename)
            mode = entry.external_attr >> 16
            if name in files or entry.is_dir() or stat.S_IFMT(mode) not in {0, stat.S_IFREG}:
                raise BundleError('Duplicate or non-regular artifact member')
            files[name] = archive.read(entry)
    try:
        manifest = json.loads(files.pop(MANIFEST))
    except (KeyError, ValueError):
        raise BundleError('Missing or invalid product manifest') from None
    if not isinstance(manifest, dict):
        raise BundleError('Malformed product manifest')
    try:
        validate_manifest(files, manifest)
    except (KeyError, TypeError, AttributeError, ValueError) as error:
        if isinstance(error, BundleError):
            raise
        raise BundleError('Malformed product manifest') from None
    return files, manifest


def validate_manifest(files, manifest):
    if (manifest.get('format') != FORMAT or manifest.get('artifact_kind') != 'candidate'
            or manifest.get('product') != 'Raccoon SalesBuddy'
            or any(manifest.get(key) is not False for key in ('installation_verified', 'production_deployed', 'mini_program_uploaded'))
            or not re.fullmatch(r'[a-f0-9]{40}', manifest.get('revision', ''))
            or not REQUIRED <= files.keys()
            or manifest.get('file_hashes') != {name: sha256(content) for name, content in sorted(files.items())}
            or manifest.get('migrations') != migration_manifest(files)
            or manifest.get('expected_schema') != manifest['migrations'][-1]['key']
            or files.get('REVISION') != (manifest['revision']+'\n').encode()):
        raise BundleError('Product identity, file set, or migration manifest differs')
    api_origin(manifest.get('api_base_url', ''), manifest.get('profile'))
    version = json.loads(files['frontend/VERSION.json'])
    project = json.loads(files['frontend/project.config.json'])
    target = json.loads(files['INSTALLATION_TARGET.json'])
    api_values = re.findall(r'API_BASE_URL\s*:\s*["\']([^"\']+)["\']', files['frontend/miniprogram/config.js'].decode())
    if (version.get('source_revision') != manifest['revision'] or version.get('expected_schema') != manifest['expected_schema']
            or version.get('api_base_url') != manifest['api_base_url'] or project.get('appid') != manifest.get('appid')
            or version.get('appid') != manifest['appid'] or api_values != [manifest['api_base_url']]
            or target.get('api_base_url') != manifest['api_base_url'] or target.get('appid') != manifest['appid']
            or target.get('profile') != manifest['profile'] or target.get('auth_mode') != 'password'
            or target.get('external_integrations') != 'unconfigured'
            or any(target.get(key) is not False for key in ('copy_existing_company_data', 'copy_existing_credentials'))
            or any(version.get(key) is not False for key in ('uploaded', 'production_deployed'))
            or project['setting']['urlCheck'] != (manifest['profile'] != 'local')):
        raise BundleError('Frontend and backend package identities differ')
    source = manifest['source_hashes']
    if (manifest.get('generated_paths') != GENERATED_PATHS or manifest.get('configured_paths') != CONFIGURED_PATHS
            or set(source) != files.keys() - set(GENERATED_PATHS)
            or any(not re.fullmatch(r'[a-f0-9]{64}', digest) for digest in source.values())
            or any(source[name] != sha256(content) for name, content in files.items()
                   if name not in GENERATED_PATHS + CONFIGURED_PATHS)
            or manifest['dependency_locks'] != {'backend/uv.lock': sha256(files['backend/uv.lock'])}
            or manifest['api_contracts'] != {'backend/openapi/openapi.yaml': sha256(files['backend/openapi/openapi.yaml'])}
            or manifest['runtime_contracts'] != {name: sha256(content) for name, content in sorted(files.items())
                 if name.startswith(('backend/src/sales_backend/contracts/', 'backend/src/sales_backend/services/agent_platform/'))}):
        raise BundleError('Source provenance or dependency/contract fingerprints differ')


def upgrade_plan(previous, incoming):
    """Package comparison only; actual database ledger and backup are installer gates."""
    prior = {row['key']: row['sha256'] for row in previous['migrations']}
    future = {row['key']: row['sha256'] for row in incoming['migrations']}
    if any(future.get(key) != value for key, value in prior.items()):
        raise BundleError('Previously packaged migration removed or changed; no automatic upgrade')
    old_head = int(previous['expected_schema'][1:])
    pending = [row for row in incoming['migrations'] if row['key'] not in prior]
    if any(not row['key'].startswith('V') or int(row['key'][1:]) <= old_head for row in pending):
        raise BundleError('Upgrade includes historical or unreviewed seed changes')
    if previous['api_base_url'] != incoming['api_base_url'] or previous['appid'] != incoming['appid']:
        raise BundleError('Target API or AppID changed; treat this as an explicit environment migration')
    return {'from_revision': previous['revision'], 'to_revision': incoming['revision'],
        'pending_migrations': [row['key'] for row in pending],
        'api_changed': previous['api_contracts'] != incoming['api_contracts'],
        'dependencies_changed': previous['dependency_locks'] != incoming['dependency_locks'],
        'automatic_database_rollback': False, 'ready_to_install': False,
        'required_next_checks': ['installed revision and complete database ledger', 'backup and recovery readiness',
             'restricted runtime roles', 'compatibility with existing clients', 'process and HTTP version verification']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    build = sub.add_parser('build')
    build.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[2])
    build.add_argument('--output', type=Path, required=True)
    build.add_argument('--revision', help='Explicit full historical commit; omitted packages HEAD without switching branches')
    build.add_argument('--api-url', required=True)
    build.add_argument('--appid', required=True)
    build.add_argument('--profile', choices=('local', 'private'), required=True)
    build.add_argument('--label', required=True)
    for name in ('verify', 'compare'):
        command = sub.add_parser(name)
        command.add_argument('--archive', type=Path, required=True)
        command.add_argument('--sha256', required=True)
        if name == 'compare':
            command.add_argument('--previous', type=Path, required=True)
            command.add_argument('--previous-sha256', required=True)
    args = parser.parse_args()
    if args.command == 'build':
        if args.repo.resolve() in args.output.resolve().parents:
            raise BundleError('Write product bundles outside the source repository')
        revision, files, modes = committed_files(args.repo, args.revision)
        files, modes, manifest = prepare(files, modes, revision=revision, api_url=args.api_url,
            appid=args.appid, profile=args.profile, product_name=args.label)
        digest = write_archive(files, modes, args.output)
        _, checked = read_verified(args.output, digest)
        assert checked == manifest
        result = {'archive': str(args.output), 'sha256': digest, 'revision': revision,
                  'expected_schema': manifest['expected_schema'], 'files': len(files), 'candidate_only': True}
    else:
        _, manifest = read_verified(args.archive, args.sha256)
        if args.command == 'compare':
            _, previous = read_verified(args.previous, args.previous_sha256)
            result = upgrade_plan(previous, manifest)
        else:
            result = {'verified': True, 'revision': manifest['revision'], 'expected_schema': manifest['expected_schema'],
                'files': len(manifest['file_hashes']), 'profile': manifest['profile'], 'installation_verified': False}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (BundleError, OSError, ValueError, zipfile.BadZipFile) as error:
        # Do not print archive content, credentials, subprocess command or tracebacks.
        raise SystemExit(str(error) if isinstance(error, BundleError) else 'Product bundle operation failed') from None
