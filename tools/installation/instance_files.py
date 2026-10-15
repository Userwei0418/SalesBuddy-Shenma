"""Private, portable instance state; no production path or credentials defaults."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import uuid

from release_bundle import read_verified, json_bytes, MANIFEST


class InstanceError(ValueError):
    pass


def clean_env():
    return {k: os.environ[k] for k in ('PATH', 'HOME', 'LANG', 'LC_ALL', 'TMPDIR') if k in os.environ}


def root_path(value, *, existing=True):
    path = Path(value).absolute()
    if path.resolve() != path or path == Path('/'):
        raise InstanceError('Instance root must be a literal, non-symlink directory')
    if existing:
        info = path.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise InstanceError('Instance root must be private and owned by the current OS user')
    return path


def read_private(path, *, max_bytes=1048576):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > max_bytes:
            raise InstanceError('Private instance file has invalid ownership, mode or size')
        return json.loads(stream.read())


def atomic_json(path, value, *, replace=True):
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(prefix='.instance-write-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(json_bytes(value)); stream.flush(); os.fsync(stream.fileno())
        if replace:
            if path.is_symlink():
                raise InstanceError('Refusing to replace a symlink')
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def locked(root):
    root = root_path(root)
    descriptor = os.open(root/'instance.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try: fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise InstanceError('Another instance operation is running') from None
        yield root
    finally:
        os.close(descriptor)


def instance(root):
    root = root_path(root)
    data = read_private(root/'INSTANCE.json')
    if data.get('format') not in {'salesbuddy-instance-v1', 'salesbuddy-instance-v2'} or data.get('root') != str(root):
        raise InstanceError('Instance metadata belongs to another location or format')
    try:
        uuid.UUID(data['instance_id'])
        for key in ('artifact_sha256', 'revision'):
            if not re.fullmatch(r'[0-9a-f]{'+str(40 if key == 'revision' else 64)+'}', data[key]):
                raise ValueError()
        if set(data['roles']) != {'api', 'worker', 'platform', 'feishu'}:
            raise ValueError()
        names = [*data['roles'].values(), data['database']['name'], data['database']['administrator']]
        if any(not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', name) for name in names):
            raise ValueError()
        if not 1 <= data['api_port'] <= 65535 or not 1 <= data['database']['port'] <= 65535:
            raise ValueError()
        if type(data['trust_loopback_proxy']) is not bool or (data['profile'] == 'private') != data['trust_loopback_proxy']:
            raise ValueError()
        if set(data['private_hashes']) != {'bootstrap.json', 'api.json', 'worker.json', 'feishu.json',
                                          'provider-keys.json', 'platform-mfa-keys.json'}:
            raise ValueError()
        if any(not re.fullmatch(r'[0-9a-f]{64}', value) for value in data['private_hashes'].values()):
            raise ValueError()
        if data['format'] == 'salesbuddy-instance-v2':
            if not re.fullmatch(r'upgrades/[0-9a-f]{32}/runtime', data.get('runtime_directory', '')):
                raise ValueError()
        elif 'runtime_directory' in data:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise InstanceError('Invalid instance metadata') from None
    for name in ('private', 'release', 'cache', 'run', 'logs', 'materials'):
        root_path(root/name)
    return data


def runtime_path(root, data=None):
    data = instance(root) if data is None else data
    path = Path(root)/data.get('runtime_directory', 'runtime')
    if path.exists() or path.is_symlink():
        # uv's existing v1 virtualenv is 0755 inside the private 0700 instance.
        # It contains dependencies, not credentials; do not break a valid old
        # installation by requiring the newer staging directory's 0700 mode.
        info = path.stat()
        if (path.resolve() != path or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid() or info.st_mode & 0o022):
            raise InstanceError('Runtime directory is redirected or writable by another user')
    return path


def no_pending_upgrade(root):
    if (Path(root)/'run/recovery.json').exists():
        raise InstanceError('An instance recovery is pending; resume it before starting or setting up services')
    if (Path(root)/'run/upgrade.json').exists():
        raise InstanceError('An instance upgrade is pending; resume or abort it before starting or setting up services')


def source_check(root, *, recovery=False):
    root = root_path(root)
    data = instance(root)
    files, manifest = read_verified(root/'artifact.zip', data['artifact_sha256'])
    if (manifest['revision'] != data['revision'] or manifest['api_base_url'] != data['api_base_url']
            or manifest['expected_schema'] != data['expected_schema'] or manifest['profile'] != data['profile']):
        raise InstanceError('Installed artifact does not match instance metadata')
    release = root/'release'
    actual = {}
    for path in release.rglob('*'):
        if path.is_symlink():
            raise InstanceError('Installed source contains a symlink')
        if path.is_file(): actual[str(path.relative_to(release))] = path.read_bytes()
    expected = {**files, MANIFEST: json_bytes(manifest)}
    if recovery:
        # Deliberate extra file also blocks historical installers whose
        # source_check predates recovery journals. It is removed only after
        # restored data, credentials and completion receipts are verified.
        expected['RECOVERY_PENDING'] = json_bytes({'instance_id':data['instance_id']})
    if actual != expected:
        raise InstanceError('Installed source differs from the verified artifact')
    for name, digest in data['private_hashes'].items():
        path = root/'private'/name
        payload = read_private(path)
        if hashlib.sha256(json_bytes(payload)).hexdigest() != digest:
            raise InstanceError('Runtime configuration changed without a reviewed update')
    return data, manifest


def metadata_digest(data):
    return hashlib.sha256(json_bytes(data)).hexdigest()
