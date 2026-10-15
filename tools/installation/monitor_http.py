"""Bounded, proxy-free health reads from a verified loopback listener only."""
from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

from instance_files import InstanceError

MAX_RESPONSE = 65536


def listener_owned(port, pid):
    """Prove the IPv4 listener belongs to the recorded API, not a reused port."""
    if sys.platform == 'darwin':
        result = subprocess.run(['/usr/sbin/lsof', '-nP', '-iTCP:'+str(port),
            '-sTCP:LISTEN', '-Fpn'], capture_output=True, text=True, timeout=5)
        if result.returncode not in (0, 1):
            raise InstanceError('Listener identity could not be checked')
        owners, addresses = set(), set()
        for line in result.stdout.splitlines():
            if line.startswith('p'): owners.add(int(line[1:]))
            if line.startswith('n'): addresses.add(line[1:])
        return owners == {pid} and addresses == {'127.0.0.1:'+str(port)}
    if sys.platform.startswith('linux'):
        matches = set()
        for name in ('tcp', 'tcp6'):
            for line in (Path('/proc/net')/name).read_text().splitlines()[1:]:
                parts = line.split()
                address, number = parts[1].split(':')
                if int(number, 16) == port and parts[3] == '0A':
                    if name != 'tcp' or address != '0100007F': return False
                    matches.add(parts[9])
        owned = set()
        descriptors = Path('/proc')/str(pid)/'fd'
        if not descriptors.exists(): return False
        for descriptor in descriptors.iterdir():
            try: target = os.readlink(descriptor)
            except FileNotFoundError: continue
            if target.startswith('socket:['): owned.add(target[8:-1])
        return bool(matches) and matches <= owned
    raise InstanceError('Listener identity is unsupported on this operating system')


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value: raise ValueError('Duplicate JSON key')
        value[key] = item
    return value


def _invalid_number(_):
    raise ValueError('Invalid JSON number')


def health_json(port, endpoint, *, timeout=3.0):
    if endpoint not in ('live', 'version', 'ready'):
        raise ValueError('Unknown health endpoint')
    # HTTPConnection never consults proxy environment variables and never follows
    # Location. read1 makes at most one underlying read, allowing a total deadline
    # even when a peer trickles bytes rather than hitting a socket idle timeout.
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=timeout)
    deadline = time.monotonic()+timeout
    timer = None
    try:
        connection.connect()
        transport = connection.sock
        def expire():
            try: transport.shutdown(socket.SHUT_RDWR)
            except OSError: pass
        timer = threading.Timer(max(0, deadline-time.monotonic()), expire)
        timer.daemon = True; timer.start()
        connection.request('GET', '/api/v1/health/'+endpoint, headers={'Connection':'close'})
        response = connection.getresponse()
        if response.status not in (200, 503): raise InstanceError('Unexpected health HTTP status')
        if response.getheader('Content-Encoding', 'identity').lower() != 'identity':
            raise InstanceError('Compressed health response refused')
        length = response.getheader('Content-Length')
        if length is not None and (not length.isdigit() or int(length) > MAX_RESPONSE):
            raise InstanceError('Health response exceeds the size limit')
        body = bytearray()
        while True:
            if response.fp is None: break
            remaining = deadline-time.monotonic()
            if remaining <= 0: raise TimeoutError('Health deadline expired')
            # HTTPResponse may own the socket after Connection: close.
            response.fp.raw._sock.settimeout(remaining)
            part = response.read1(min(8192, MAX_RESPONSE+1-len(body)))
            if not part: break
            body.extend(part)
            if len(body) > MAX_RESPONSE: raise InstanceError('Health response exceeds the size limit')
        if length is not None and len(body) != int(length): raise InstanceError('Incomplete health response')
        value = json.loads(body, object_pairs_hook=_object, parse_constant=_invalid_number)
        if not isinstance(value, dict): raise InstanceError('Health response must be an object')
        return response.status, value
    except (socket.timeout, http.client.HTTPException, UnicodeError, json.JSONDecodeError,
            RecursionError, AttributeError, ValueError) as exc:
        raise InstanceError('Health response unavailable or invalid') from exc
    finally:
        if timer: timer.cancel()
        connection.close()
