#!/usr/bin/env python3
"""Root-only, stdin JSON maintenance bridge for the fixed Shenma Agent instance.

Protocol: {"action":"status"} or {"action":"prepare|apply|rollback",
"rotation_id":"UUID", "api_key":"only on apply"}. Never pass a key in argv.
Encrypted rollback snapshots stay under /var/lib/shenma-provision with mode 0600.
This updates credentials, not model names, endpoints, Agent settings or app keys.
No supplier requests are made; the caller verifies the new key before rotation.
"""
from __future__ import annotations

import base64
import ast
import contextlib
import fcntl
import hashlib
import importlib.util
import json
import logging
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
from urllib.parse import urlsplit
from uuid import UUID, uuid4
from typing import Union

ROOT = Path('/opt/raccoon-agent')
STATE = Path('/var/lib/shenma-provision/model-key-rotations')
ORIGIN = 'https://ops-salesbuddy.shenzhoukuntai.com:18899'
PROVIDERS = {'langgenius/openai_api_compatible/openai_api_compatible',
             'openai_api_compatible', 'senseaudio'}
MODEL_TYPES = {'llm', 'text-generation', 'speech2text', 'speech-to-text', 'tts', 'text-to-speech'}
MAX_INPUT = 16384
CONTAINER_LAUNCHER = (
    "import base64,json,os,sys;ns={'__name__':'rotation_module'};"
    "exec(compile(base64.b64decode(sys.stdin.buffer.readline()),'<rotation>','exec'),ns);"
    "ns['container_main'](json.load(sys.stdin));"
    # All operation/Session context managers have exited before this point. This
    # is a disposable docker-exec process, not the serving API process. Avoid
    # waiting for unrelated telemetry shutdown hooks or background import threads.
    "sys.stdout.flush();sys.stderr.flush();os._exit(0)"
)


class RotationError(Exception):
    """Codes are static and safe to print; never include a caught exception."""


def key_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def request_from_stdin(stream):
    raw = stream.read(MAX_INPUT + 1)
    if len(raw) > MAX_INPUT:
        raise RotationError('input_too_large')
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError):
        raise RotationError('invalid_json') from None
    if not isinstance(data, dict) or set(data) - {'action', 'rotation_id', 'api_key'}:
        raise RotationError('invalid_request')
    action = data.get('action')
    if action not in {'status', 'prepare', 'apply', 'rollback'}:
        raise RotationError('invalid_action')
    if action != 'status':
        try:
            data['rotation_id'] = str(UUID(data['rotation_id']))
        except (ValueError, TypeError, KeyError, AttributeError):
            raise RotationError('invalid_rotation_id') from None
    if action == 'apply':
        key = data.get('api_key')
        if not isinstance(key, str) or not 12 <= len(key) <= 4096 or any(not 33 <= ord(c) <= 126 for c in key):
            raise RotationError('invalid_api_key')
    elif 'api_key' in data:
        raise RotationError('unexpected_api_key')
    return data


def selection(record):
    """Match explicit supplier endpoints; never infer credentials from a label alone."""
    if record.get('model_type') and record['model_type'] not in MODEL_TYPES:
        return None
    supported_provider = record['provider_name'].lower() in PROVIDERS
    named = 'senseaudio' in record.get('model_name', '').lower() or record['provider_name'].lower() == 'senseaudio'
    try:
        config = json.loads(record['encrypted_config'])
    except (TypeError, ValueError):
        if not supported_provider and not named:
            return None
        raise RotationError('unsupported_credential_format') from None
    if not isinstance(config, dict):
        raise RotationError('unsupported_credential_format')
    endpoints = [config[k] for k in ('endpoint_url', 'api_base', 'base_url', 'openai_api_base')
                 if isinstance(config.get(k), str) and config[k]]
    if len(set(endpoints)) != 1:
        if named:
            raise RotationError('unsupported_senseaudio_endpoint')
        return None
    endpoint = endpoints[0]
    try:
        url = urlsplit(endpoint)
        valid = not (url.username or url.password or url.query or url.fragment) and (
            (url.scheme == 'https' and url.hostname == 'api.senseaudio.cn' and url.port in (None, 443)) or
            (url.scheme == 'http' and url.hostname in {'senseaudio_speech', 'senseaudio-speech'}
             and url.port == 8099))
    except ValueError:
        valid = False
    if not valid:
        if named:
            raise RotationError('unsupported_senseaudio_endpoint')
        return None
    if not supported_provider:
        raise RotationError('unsupported_senseaudio_provider')
    if not isinstance(config.get('api_key'), str) or not config['api_key']:
        raise RotationError('unsupported_key_field')
    return config, endpoint


def snapshot(records):
    selected = [r for r in records if selection(r) is not None]
    if not selected:
        raise RotationError('no_senseaudio_credentials')
    if len({r['tenant_id'] for r in selected}) != 1:
        raise RotationError('ambiguous_workspace')
    return sorted(selected, key=lambda r: (r['kind'], r['id']))


def public_status(records, decrypt):
    items = []
    hashes = set()
    tails = set()
    for r in records:
        config, endpoint = selection(r)
        plain = decrypt(r['tenant_id'], config['api_key'])
        hashes.add(key_hash(plain))
        tail = plain[-4:] if len(plain) >= 12 else '<short-key>'
        tails.add(tail)
        items.append({'provider': r['provider_name'], 'model': r.get('model_name'),
                      'model_type': r.get('model_type'), 'endpoint': endpoint, 'key_tail': tail})
    return {'count': len(records), 'keys_equal': len(hashes) == 1,
            'key_tails_equal': len(tails) == 1, 'items': items}


def same_shape(left, right):
    def without_key(row):
        copy = dict(row)
        config = json.loads(copy['encrypted_config'])
        config.pop('api_key', None)
        copy['encrypted_config'] = config
        return copy
    return [without_key(r) for r in left] == [without_key(r) for r in right]


def next_records(current, saved, action, new_key, expected_hash, encrypt, decrypt):
    """Pure transition policy, shared by the container implementation and tests."""
    if not same_shape(current, saved):
        raise RotationError('configuration_changed')
    if action == 'rollback' and current == saved:
        return saved
    current_hashes = {key_hash(decrypt(r['tenant_id'], json.loads(r['encrypted_config'])['api_key'])) for r in current}
    if action == 'apply':
        if current != saved and current_hashes != {key_hash(new_key)}:
            raise RotationError('credentials_changed')
        if current_hashes == {key_hash(new_key)}:
            return current
        result = []
        for record in current:
            updated = dict(record)
            config = json.loads(record['encrypted_config'])
            config['api_key'] = encrypt(record['tenant_id'], new_key)
            updated['encrypted_config'] = json.dumps(config, sort_keys=True)
            result.append(updated)
        return result
    if current_hashes != {expected_hash}:
        raise RotationError('credentials_changed')
    return saved


def crypto_functions(code_root=Path('/app/api')):
    """Load only the installed provider's two wire-format functions, not Flask/configs.

    The reviewed local key provider stores base64(HYBRID: + RSA-OAEP(AES key)
    + EAX nonce + tag + ciphertext), and also accepts historical RSA-only values.
    Loading the installed functions avoids duplicating or changing that algorithm.
    """
    from Crypto.Cipher import AES
    from Crypto.PublicKey import RSA
    from Crypto.Random import get_random_bytes

    cipher_path = code_root / 'libs/gmpy2_pkcs10aep_cipher.py'
    spec = importlib.util.spec_from_file_location('rotation_installed_oaep', cipher_path)
    cipher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cipher)
    source = ast.parse((code_root / 'libs/rsa.py').read_text())
    names = {'encrypt', 'decrypt_token_with_decoding'}
    functions = [node for node in source.body if isinstance(node, ast.FunctionDef) and node.name in names]
    prefixes = [node for node in source.body if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == 'prefix_hybrid' for target in node.targets)]
    if {node.name for node in functions} != names or len(prefixes) != 1 or ast.literal_eval(prefixes[0].value) != b'HYBRID:':
        raise RotationError('unsupported_encryption_format')
    namespace = {'AES': AES, 'RSA': RSA, 'get_random_bytes': get_random_bytes,
                 'gmpy2_pkcs10aep_cipher': cipher, 'Union': Union}
    exec(compile(ast.Module(body=prefixes + functions, type_ignores=[]), str(code_root / 'libs/rsa.py'), 'exec'), namespace)
    return namespace['encrypt'], namespace['decrypt_token_with_decoding'], RSA, cipher


def lightweight_settings(environ):
    def value(name, default=''):
        return environ.get(name, default)

    def enabled(name):
        return value(name).lower() in {'true', '1', 'yes'}

    if value('DB_TYPE', 'postgresql') != 'postgresql' or value('KEY_PROVIDER_TYPE', 'local') != 'local':
        raise RotationError('unsupported_maintenance_configuration')
    if enabled('REDIS_USE_SENTINEL') or enabled('REDIS_USE_CLUSTERS'):
        raise RotationError('unsupported_maintenance_configuration')
    storage = value('STORAGE_TYPE', 'opendal')
    if storage == 'local':
        storage_root = value('STORAGE_LOCAL_PATH', 'storage')
    elif storage == 'opendal' and value('OPENDAL_SCHEME', 'fs') == 'fs':
        storage_root = value('OPENDAL_FS_ROOT', 'storage')
    else:
        raise RotationError('unsupported_maintenance_configuration')
    root = Path(storage_root)
    if not root.is_absolute():
        root = Path('/app/api') / root
    database = dict(host=value('DB_HOST', 'localhost'), port=int(value('DB_PORT', '5432')),
                    user=value('DB_USERNAME', 'postgres'), password=value('DB_PASSWORD'),
                    dbname=value('DB_DATABASE', 'dify'), connect_timeout=10)
    if value('DB_EXTRAS'):
        # This fixed standalone deployment needs no proxy/client-certificate options.
        raise RotationError('unsupported_maintenance_configuration')
    redis_options = dict(host=value('REDIS_HOST', 'localhost'), port=int(value('REDIS_PORT', '6379')),
                         username=value('REDIS_USERNAME') or None, password=value('REDIS_PASSWORD') or None,
                         db=int(value('REDIS_DB', '0')), socket_connect_timeout=10, socket_timeout=10)
    if enabled('REDIS_USE_SSL'):
        redis_options['ssl'] = True
        redis_options['ssl_cert_reqs'] = value('REDIS_SSL_CERT_REQS', 'required').lower().removeprefix('cert_')
        for name, option in [('REDIS_SSL_CA_CERTS', 'ssl_ca_certs'), ('REDIS_SSL_CERTFILE', 'ssl_certfile'),
                             ('REDIS_SSL_KEYFILE', 'ssl_keyfile')]:
            if value(name):
                redis_options[option] = value(name)
    return database, redis_options, root, value('REDIS_KEY_PREFIX').strip()


CACHE_SOURCES = ('provider_models', 'preferred_model_providers', 'provider_model_settings',
                 'provider_model_credentials', 'provider_credentials', 'provider_load_balancing_configs')


def verify_cache_contract(code_root=Path('/app/api')):
    """Verify literal cache contracts in the actual installed source, without imports."""
    manager = ast.parse((code_root / 'core/provider_manager.py').read_text())
    literals = {}
    for node in manager.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in {
                    '_PROVIDER_CONFIGURATION_CACHE_VERSION_KEY', '_PROVIDER_CONFIGURATION_CACHE_VERSION_TTL_SECONDS'}:
                    literals[target.id] = ast.literal_eval(node.value)
    source_class = next((n for n in manager.body if isinstance(n, ast.ClassDef)
                         and n.name == 'ProviderConfigurationCacheSource'), None)
    source_values = tuple(ast.literal_eval(n.value) for n in source_class.body if isinstance(n, ast.Assign)) if source_class else ()
    cache_module = ast.parse((code_root / 'core/helper/model_provider_cache.py').read_text())
    cache_class = next((n for n in cache_module.body if isinstance(n, ast.ClassDef) and n.name == 'ProviderCredentialsCache'), None)
    constructor = next((n for n in cache_class.body if isinstance(n, ast.FunctionDef) and n.name == '__init__'), None) if cache_class else None
    assignment = next((n for n in ast.walk(constructor) if isinstance(n, ast.Assign)
                       and any(isinstance(t, ast.Attribute) and t.attr == 'cache_key' for t in n.targets)), None) if constructor else None
    template = ast.parse("f'{cache_type}_credentials:tenant_id:{tenant_id}:id:{identity_id}'", mode='eval').body
    type_class = next((n for n in cache_module.body if isinstance(n, ast.ClassDef)
                       and n.name == 'ProviderCredentialsCacheType'), None)
    type_values = tuple(ast.literal_eval(n.value) for n in type_class.body if isinstance(n, ast.Assign)) if type_class else ()
    if (literals.get('_PROVIDER_CONFIGURATION_CACHE_VERSION_KEY') != 'provider_configurations:tenant:{tenant_id}:source:{source}:version'
        or literals.get('_PROVIDER_CONFIGURATION_CACHE_VERSION_TTL_SECONDS') != 360
        or source_values != CACHE_SOURCES
        or type_values != ('provider', 'provider_model', 'load_balancing_provider_model')
        or assignment is None or ast.dump(assignment.value) != ast.dump(template)):
        raise RotationError('unsupported_cache_format')


def cache_invalidation(redis_client, tenant, identities, prefix):
    def physical(name):
        return prefix + ':' + name if prefix else name
    pipe = redis_client.pipeline(transaction=True)
    for kind, identity in identities:
        pipe.delete(physical(f'{kind}_credentials:tenant_id:{tenant}:id:{identity}'))
    # Same names and 360s version TTL as core/provider_manager.py; no import of
    # that module, which transitively constructs the whole Pydantic app config.
    for source in CACHE_SOURCES:
        name = physical(f'provider_configurations:tenant:{tenant}:source:{source}:version')
        pipe.incr(name)
        pipe.expire(name, 360)
    pipe.execute()


def container_operation(request):
    # Deliberately no app_factory/configs/models/ProviderManager imports: their
    # configuration model construction can take minutes in the deployed image.
    import psycopg2
    from psycopg2.extras import RealDictCursor
    import redis

    database, redis_options, storage_root, prefix = lightweight_settings(os.environ)
    verify_cache_contract()
    encrypt_raw, decrypt_raw, RSA, cipher = crypto_functions()
    connection = psycopg2.connect(**database)
    redis_client = redis.Redis(**redis_options)
    tables = {'provider': 'provider_credentials', 'model': 'provider_model_credentials',
              'load_balancing': 'load_balancing_model_configs'}
    try:
        with connection, connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SET LOCAL lock_timeout = '10s'")
            cursor.execute("SET LOCAL statement_timeout = '30s'")
            cursor.execute('SELECT pg_advisory_xact_lock(724628194025)')
            rows = []
            for kind, table in tables.items():
                columns = 'id::text,tenant_id::text,provider_name,encrypted_config'
                if kind != 'provider':
                    columns += ',model_name,model_type'
                cursor.execute(f'SELECT {columns} FROM {table} WHERE encrypted_config IS NOT NULL ORDER BY id FOR UPDATE')
                rows.extend(dict(row, kind=kind) for row in cursor.fetchall())
            current = snapshot(rows)
            tenant = str(UUID(current[0]['tenant_id']))
            cursor.execute('SELECT encrypt_public_key FROM tenants WHERE id=%s', (tenant,))
            tenant_record = cursor.fetchone()
            if not tenant_record or not tenant_record['encrypt_public_key']:
                raise RotationError('tenant_key_unavailable')
            private = RSA.import_key((storage_root / 'privkeys' / tenant / 'private.pem').read_bytes())
            public = RSA.import_key(tenant_record['encrypt_public_key'])
            if private.publickey().export_key(format='DER') != public.publickey().export_key(format='DER'):
                raise RotationError('tenant_key_mismatch')
            decoder = cipher.new(private)

            def encrypt_token(_, plain):
                return base64.b64encode(encrypt_raw(plain, public.export_key())).decode()

            def decrypt_token(_, encoded):
                return decrypt_raw(base64.b64decode(encoded, validate=True), private, decoder)

            action = request['action']
            if action in {'status', 'prepare'}:
                result = public_status(current, decrypt_token)
                if action == 'prepare':
                    result['snapshot'] = current
                return result
            proposed = next_records(current, request['snapshot'], action, request.get('api_key'),
                                    request.get('expected_key_hash'), encrypt_token, decrypt_token)
            redis_client.ping()
            for row in proposed:
                cursor.execute(f'UPDATE {tables[row["kind"]]} SET encrypted_config=%s,updated_at=CURRENT_TIMESTAMP WHERE id=%s AND tenant_id=%s',
                               (row['encrypted_config'], row['id'], tenant))
                if cursor.rowcount != 1:
                    raise RotationError('configuration_changed')
            names = sorted({r['provider_name'] for r in current})
            identities = []
            for table, cache_type in [('providers', 'provider'), ('provider_models', 'provider_model'),
                                       ('load_balancing_model_configs', 'load_balancing_provider_model')]:
                cursor.execute(f'SELECT id::text FROM {table} WHERE tenant_id=%s AND provider_name=ANY(%s)', (tenant, names))
                identities.extend((cache_type, row['id']) for row in cursor.fetchall())
        # PostgreSQL commit has completed. A cache error leaves durable intent for
        # retry or rollback; success is never returned before invalidation succeeds.
        cache_invalidation(redis_client, tenant, identities, prefix)
        return public_status(proposed, decrypt_token)
    finally:
        connection.close()
        redis_client.close()


def container_main(request):
    # The launcher captures both streams, but suppress third-party import/SQL/plugin logs
    # as well. Never print exception repr, traceback, SQL parameters or credential JSON.
    logging.disable(logging.CRITICAL)
    try:
        with open(os.devnull, 'w') as quiet, contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
            result = container_operation(request)
        output = {'ok': True, 'result': result}
    except RotationError as exc:
        output = {'ok': False, 'error': str(exc)}
    except Exception:
        output = {'ok': False, 'error': 'container_operation_failed'}
    print(json.dumps(output, separators=(',', ':')))


def invoke_container(request):
    source = base64.b64encode(Path(__file__).read_bytes()) + b'\n'
    process = subprocess.run(['docker', 'compose', '-f', str(ROOT / 'runtime/compose.json'),
        'exec', '-T', '-w', '/app/api', 'api', '/app/api/.venv/bin/python', '-c', CONTAINER_LAUNCHER],
        input=source + json.dumps(request).encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=180, check=False, cwd=ROOT / 'runtime')
    if process.returncode:
        raise RotationError('container_exec_failed')
    try:
        envelope = json.loads(process.stdout)
    except ValueError:
        raise RotationError('invalid_container_response') from None
    if not envelope.get('ok'):
        # These are fixed error codes from this script, never raw dependency messages.
        code = envelope.get('error')
        if code not in {'container_operation_failed', 'configuration_changed', 'credentials_changed',
                        'unsupported_credential_format', 'unsupported_senseaudio_endpoint',
                        'unsupported_senseaudio_provider', 'unsupported_key_field',
                        'no_senseaudio_credentials', 'ambiguous_workspace',
                        'unsupported_encryption_format', 'unsupported_maintenance_configuration',
                        'unsupported_cache_format', 'tenant_key_unavailable', 'tenant_key_mismatch'}:
            code = 'container_operation_failed'
        raise RotationError(code)
    return envelope['result']


def private_read(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
            raise RotationError('unsafe_state_file')
        with os.fdopen(fd, 'r') as stream:
            fd = -1
            return json.load(stream)
    finally:
        if fd != -1:
            os.close(fd)


def private_write(path, data):
    # A killed writer must not block retries with a stale fixed .tmp filename.
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


def host_operation(request, invoke=invoke_container, state_dir=STATE):
    action = request['action']
    if action == 'status':
        return {'status': 'ok', **invoke(request)}
    path = state_dir / (request['rotation_id'] + '.json')
    if action == 'prepare':
        if path.exists():
            saved = private_read(path)
            return {'status': saved['state'], 'rotation_id': request['rotation_id'], 'count': len(saved['snapshot'])}
        result = invoke(request)
        saved = {'version': 1, 'state': 'prepared', 'snapshot': result.pop('snapshot')}
        private_write(path, saved)
        return {'status': 'prepared', 'rotation_id': request['rotation_id'], **result}
    if not path.exists():
        if action == 'rollback':
            # prepare can fail before producing a snapshot; no write occurred.
            return {'status': 'rolled_back', 'rotation_id': request['rotation_id'], 'count': 0}
        raise RotationError('prepare_required')
    saved = private_read(path)
    if saved['state'] == 'rolled_back':
        if action == 'rollback':
            return {'status': 'rolled_back', 'rotation_id': request['rotation_id'], 'count': len(saved['snapshot'])}
        raise RotationError('rotation_already_rolled_back')
    if action == 'apply':
        fingerprint = key_hash(request['api_key'])
        if saved.get('expected_key_hash') not in (None, fingerprint):
            raise RotationError('rotation_key_changed')
        # Persist intent before DB mutation; retry/rollback works even after lost stdout.
        saved['expected_key_hash'] = fingerprint
        private_write(path, saved)
    elif not saved.get('expected_key_hash'):
        saved['state'] = 'rolled_back'
        private_write(path, saved)
        return {'status': 'rolled_back', 'rotation_id': request['rotation_id'], 'count': len(saved['snapshot'])}
    result = invoke({**request, 'snapshot': saved['snapshot'], 'expected_key_hash': saved['expected_key_hash']})
    saved['state'] = 'applied' if action == 'apply' else 'rolled_back'
    private_write(path, saved)
    return {'status': saved['state'], 'rotation_id': request['rotation_id'], **result}


def main():
    try:
        os.umask(0o077)
        if len(sys.argv) != 1 or os.geteuid() != 0 or socket.gethostname() != 'opsbuddy':
            raise RotationError('fixed_host_root_required')
        addresses = subprocess.run(['hostname', '-I'], capture_output=True, text=True, check=True).stdout.split()
        if '172.22.9.233' not in addresses:
            raise RotationError('wrong_host_address')
        if json.loads((ROOT / 'instance.json').read_text()).get('public_url') != ORIGIN:
            raise RotationError('wrong_instance')
        request = request_from_stdin(sys.stdin.buffer)
        STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = STATE.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
            raise RotationError('unsafe_state_directory')
        fd = os.open(STATE / 'rotation.lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            result = host_operation(request)
        print(json.dumps(result, ensure_ascii=False))
    except RotationError as exc:
        print(json.dumps({'status': 'error', 'error': str(exc)}))
        return 1
    except Exception:
        print(json.dumps({'status': 'error', 'error': 'rotation_failed'}))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
