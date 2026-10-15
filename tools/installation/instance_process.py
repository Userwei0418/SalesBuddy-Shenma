"""Owned child-process identity and locks; never signal a reused/unowned PID."""
import fcntl
import os
from pathlib import Path
import re
import signal
import subprocess
import time

from instance_files import InstanceError, atomic_json, read_private


class ProcessIdentityChanged(InstanceError):
    """The observed PID cannot be treated as the recorded service."""


def process_identity(pid):
    if not isinstance(pid, int) or pid <= 1:
        return None
    # One process-table observation: separate ps calls can combine a live
    # command with an exiting process (or a reused PID) during shutdown.
    result = subprocess.run(['ps', '-p', str(pid), '-o', 'lstart=', '-o', 'stat=', '-o', 'command='],
                            capture_output=True, text=True, timeout=5)
    if result.returncode or not result.stdout.strip():
        return None
    match = re.fullmatch(r'\s*(\S+\s+\S+\s+\d+\s+\d{2}:\d{2}:\d{2}\s+\d{4})\s+(\S+)\s+(.+?)\s*', result.stdout)
    if not match:
        raise InstanceError('Cannot parse process identity; refusing to signal or adopt this PID')
    started, status, command = match.groups()
    if status.startswith('Z'):
        return None
    return {'pid': pid, 'started': started, 'command': command}


def state(root, service, identity):
    path = Path(root)/'run'/f'{service}.json'
    if not path.exists():
        return None
    record = read_private(path)
    if record.get('instance_id') != identity or record.get('service') != service:
        raise InstanceError('Process record belongs to another instance')
    live = process_identity(record.get('pid'))
    if not live:
        return None
    if any(live[key] != record.get(key) for key in live):
        # Some platforms briefly expose an exiting command before marking it
        # as a zombie. Only disappearance is safe; a persistent mismatch fails.
        if process_identity(record.get('pid')) is None:
            return None
        raise ProcessIdentityChanged('Process identity changed; refusing to signal or adopt this PID')
    return record


def service_lock(root, service):
    descriptor = os.open(Path(root)/'run'/f'{service}.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(descriptor)
        raise InstanceError('Instance service is already running or starting') from None
    return descriptor


def register(root, service, identity):
    descriptor = service_lock(root, service)
    record = process_identity(os.getpid())
    if not record:
        os.close(descriptor)
        raise InstanceError('Cannot establish service process identity')
    atomic_json(Path(root)/'run'/f'{service}.json', {**record, 'instance_id': identity, 'service': service})
    return descriptor


def stop(root, service, identity):
    record = state(root, service, identity)
    if not record:
        descriptor = service_lock(root, service)
        os.close(descriptor)
        return
    # Re-check immediately before the signal. No process-group or name-wide kills.
    live = process_identity(record['pid'])
    if live is None:
        descriptor = service_lock(root, service)
        os.close(descriptor)
        return
    if live != {key: record[key] for key in ('pid', 'started', 'command')}:
        raise ProcessIdentityChanged('Service process identity changed during stop')
    try:
        os.kill(record['pid'], signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic()+20
    while time.monotonic() < deadline:
        try:
            if not state(root, service, identity):
                return
        except ProcessIdentityChanged:
            # After signalling our verified service, ps can briefly show its
            # exiting command before Z/disappearance. Wait without signalling
            # or adopting that observation; persistent mismatch still fails.
            pass
        time.sleep(.1)
    raise InstanceError('Service is still stopping; retained its record for a later check')
