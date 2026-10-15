"""Private, durable state for a single explicitly reviewed legacy adoption.

This is not the standard installer marker. No database identity is inferred from
its existence; every resume also checks PostgreSQL and the frozen source files.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile


class AdoptionError(ValueError):
    """An operator-safe refusal; do not include database rows or secrets."""


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(part)
    return h.hexdigest()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str,
                                    separators=(',', ':')).encode()).hexdigest()


def fsync_dir(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def literal_path(value):
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts or path == Path('/'):
        raise AdoptionError('An explicit absolute non-root path is required')
    if path.parent.resolve() != path.parent:
        raise AdoptionError('Redirected parent directory refused')
    return path


def private_directory(value, *, create=False):
    path = literal_path(value)
    if create:
        path.mkdir(mode=0o700)
        fsync_dir(path.parent)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise AdoptionError('Adoption directory must be private and owned by the maintenance user')
    return path


def read_private(path):
    path = literal_path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_size > 64 * 1024 * 1024):
            raise AdoptionError('Invalid private adoption file')
        return json.load(stream)


def atomic_bytes(path, content, *, replace=True):
    path = literal_path(path)
    fd, temporary = tempfile.mkstemp(prefix='.adoption-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            if path.is_symlink():
                raise AdoptionError('Refusing to replace a redirected file')
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
        fsync_dir(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def atomic_json(path, value, *, replace=True):
    atomic_bytes(path, (json.dumps(value, sort_keys=True, indent=2, default=str) + '\n').encode(), replace=replace)


@contextmanager
def locked(directory):
    directory = private_directory(directory)
    fd = os.open(directory/'adoption.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if os.fstat(fd).st_uid != os.getuid() or os.fstat(fd).st_mode & 0o077:
            raise AdoptionError('Invalid adoption lock ownership or permissions')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise AdoptionError('Another adoption operation is running') from None
        yield directory
    finally:
        os.close(fd)


def checkpoint(directory, journal, phase, *, fault=None):
    journal['phase'] = phase
    atomic_json(directory/'ADOPTION.json', journal)
    # Fault injection is an explicit library callback, never an environment flag
    # that could accidentally interrupt a production deployment.
    if fault:
        fault(phase)
