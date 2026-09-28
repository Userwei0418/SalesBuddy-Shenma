"""Offline regression tests: synthetic keys only; no Docker, API or database calls."""
import copy
import base64
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest

spec = importlib.util.spec_from_file_location('agent_rotation', Path(__file__).with_name('rotate-agent-model-key.py'))
rotation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rotation)

OLD = 'synthetic-old-value-1234'
NEW = 'synthetic-new-value-5678'
PROVIDER = 'langgenius/openai_api_compatible/openai_api_compatible'


def encrypt(tenant, value):
    return 'fake-encrypted:' + value


def decrypt(tenant, value):
    assert value.startswith('fake-encrypted:')
    return value.removeprefix('fake-encrypted:')


def record(model='senseaudio-s2', endpoint='https://api.senseaudio.cn/v1', kind='model', model_type='llm'):
    return dict(kind=kind, id=str(uuid4()), tenant_id='fixed-tenant', provider_name=PROVIDER,
                model_name=model, model_type=model_type,
                encrypted_config=json.dumps({'api_key': encrypt('', OLD), 'endpoint_url': endpoint,
                                             'max_tokens_to_sample': 16384, 'mode': 'chat'}))


def test_only_matching_supplier_records_selected():
    llm = record()
    asr = record('senseaudio-asr-lite', model_type='speech2text')
    tts = record('senseaudio-tts', 'http://senseaudio_speech:8099/v1', model_type='tts')
    other = record('unrelated', 'https://api.example.invalid/v1')
    assert len(rotation.snapshot([llm, asr, tts, other])) == 3
    bad = record('senseaudio-s2', 'https://api.example.invalid/v1')
    with pytest.raises(rotation.RotationError, match='unsupported_senseaudio_endpoint'):
        rotation.snapshot([llm, bad])


@pytest.mark.parametrize('endpoint', ['https://api.senseaudio.cn.evil.invalid/v1',
    'https://api.senseaudio.cn/v1?secret=x', 'https://user:pass@api.senseaudio.cn/v1',
    'http://api.senseaudio.cn/v1', 'http://senseaudio_speech:1234/v1'])
def test_reject_unsupported_supplier_endpoints(endpoint):
    with pytest.raises(rotation.RotationError, match='unsupported_senseaudio_endpoint'):
        rotation.snapshot([record(endpoint=endpoint)])


def test_fail_closed_if_known_models_use_another_provider_or_secret_field():
    row = record()
    row['provider_name'] = 'unreviewed/plugin/provider'
    with pytest.raises(rotation.RotationError, match='unsupported_senseaudio_provider'):
        rotation.snapshot([row])
    row = record()
    data = json.loads(row['encrypted_config'])
    data['other_key'] = data.pop('api_key')
    row['encrypted_config'] = json.dumps(data)
    with pytest.raises(rotation.RotationError, match='unsupported_key_field'):
        rotation.snapshot([row])


def test_apply_is_idempotent_and_preserves_every_other_configuration():
    saved = rotation.snapshot([record(), record(model_type='tts', endpoint='http://senseaudio_speech:8099/v1')])
    current = rotation.next_records(saved, saved, 'apply', NEW, None, encrypt, decrypt)
    assert rotation.same_shape(current, saved)
    assert saved != current
    assert rotation.next_records(current, saved, 'apply', NEW, None, encrypt, decrypt) == current
    restored = rotation.next_records(current, saved, 'rollback', None, rotation.key_hash(NEW), encrypt, decrypt)
    assert restored == saved
    assert rotation.next_records(restored, saved, 'rollback', None, rotation.key_hash(NEW), encrypt, decrypt) == saved


def test_rollback_refuses_a_later_rotation_or_configuration_change():
    saved = rotation.snapshot([record()])
    current = rotation.next_records(saved, saved, 'apply', NEW, None, encrypt, decrypt)
    with pytest.raises(rotation.RotationError, match='credentials_changed'):
        rotation.next_records(current, saved, 'rollback', None, 'other-fingerprint', encrypt, decrypt)
    current[0]['model_name'] = 'another-model'
    with pytest.raises(rotation.RotationError, match='configuration_changed'):
        rotation.next_records(current, saved, 'rollback', None, rotation.key_hash(NEW), encrypt, decrypt)


def test_status_redacts_all_keys_and_rejects_multiple_workspaces():
    row = record()
    status = rotation.public_status([row], decrypt)
    text = json.dumps(status)
    assert OLD not in text and 'fake-encrypted' not in text
    assert status['items'][0]['key_tail'] == '1234'
    assert status['keys_equal'] is True
    second = record()
    second['tenant_id'] = 'another-tenant'
    with pytest.raises(rotation.RotationError, match='ambiguous_workspace'):
        rotation.snapshot([row, second])


def test_stdin_protocol_rejects_arbitrary_targets_and_keys_on_other_actions():
    good = {'action': 'apply', 'rotation_id': str(uuid4()), 'api_key': NEW}
    assert rotation.request_from_stdin(io.BytesIO(json.dumps(good).encode())) == good
    for bad in [dict(good, target='elsewhere'), {'action': 'status', 'api_key': NEW},
                {'action': 'apply', 'rotation_id': '../file', 'api_key': NEW}]:
        with pytest.raises(rotation.RotationError):
            rotation.request_from_stdin(io.BytesIO(json.dumps(bad).encode()))


def test_host_lost_response_retries_and_rolls_back_without_storing_plaintext(monkeypatch, tmp_path):
    # Root-only file validation is independently tested below; fake persistence here
    # makes this test portable to developer machines without root.
    storage = {}
    current = rotation.snapshot([record()])
    failed_once = [False]

    def write(path, data):
        storage[path] = copy.deepcopy(data)
        path.touch()

    monkeypatch.setattr(rotation, 'private_write', write)
    monkeypatch.setattr(rotation, 'private_read', lambda p: copy.deepcopy(storage[p]))

    def invoke(request):
        nonlocal current
        if request['action'] == 'prepare':
            return dict(rotation.public_status(current, decrypt), snapshot=copy.deepcopy(current))
        current = rotation.next_records(current, request['snapshot'], request['action'], request.get('api_key'),
                                        request.get('expected_key_hash'), encrypt, decrypt)
        if request['action'] == 'apply' and not failed_once[0]:
            failed_once[0] = True
            raise rotation.RotationError('simulated_lost_response')
        return rotation.public_status(current, decrypt)

    identity = {'rotation_id': str(uuid4())}
    assert rotation.host_operation(dict(identity, action='prepare'), invoke, tmp_path)['status'] == 'prepared'
    with pytest.raises(rotation.RotationError, match='simulated_lost_response'):
        rotation.host_operation(dict(identity, action='apply', api_key=NEW), invoke, tmp_path)
    assert NEW not in json.dumps(list(storage.values()))
    assert rotation.host_operation(dict(identity, action='apply', api_key=NEW), invoke, tmp_path)['status'] == 'applied'
    assert rotation.host_operation(dict(identity, action='rollback'), invoke, tmp_path)['status'] == 'rolled_back'
    assert rotation.host_operation(dict(identity, action='rollback'), invoke, tmp_path)['status'] == 'rolled_back'


def test_missing_prepare_snapshot_rollback_is_idempotent_and_does_not_invoke_container(tmp_path):
    def forbidden(_):
        raise AssertionError('no container call is needed without a prepared snapshot')

    identity = {'rotation_id': str(uuid4())}
    expected = {'status': 'rolled_back', **identity, 'count': 0}
    assert rotation.host_operation(dict(identity, action='rollback'), forbidden, tmp_path) == expected
    assert rotation.host_operation(dict(identity, action='rollback'), forbidden, tmp_path) == expected
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(rotation.RotationError, match='prepare_required'):
        rotation.host_operation(dict(identity, action='apply', api_key=NEW), forbidden, tmp_path)


def test_subprocess_only_stdin_contains_key(monkeypatch):
    def run(command, **kwargs):
        assert NEW not in repr(command)
        assert NEW.encode() in kwargs['input']
        assert command[:2] == ['docker', 'compose']
        assert 'exec' in command and 'api' in command and '-T' in command
        return subprocess.CompletedProcess(command, 0, b'{"ok":true,"result":{"count":2}}', b'')
    monkeypatch.setattr(rotation.subprocess, 'run', run)
    assert rotation.invoke_container({'action': 'apply', 'api_key': NEW}) == {'count': 2}


def test_launcher_flushes_result_and_exits_without_waiting_for_background_threads():
    source = '''
import atexit,json,threading
def container_main(request):
    threading.Thread(target=lambda: threading.Event().wait(60), daemon=False).start()
    atexit.register(lambda: threading.Event().wait(60))
    print(json.dumps({'ok': True, 'result': {'count': 2}}))
'''
    process = subprocess.run([sys.executable, '-c', rotation.CONTAINER_LAUNCHER],
        input=base64.b64encode(source.encode()) + b'\n{}', capture_output=True, timeout=5)
    assert process.returncode == 0
    assert json.loads(process.stdout) == {'ok': True, 'result': {'count': 2}}
    assert process.stderr == b''


def test_container_exception_never_discloses_key(monkeypatch, capsys):
    def fail(_):
        print(NEW)
        raise ValueError(NEW)
    monkeypatch.setattr(rotation, 'container_operation', fail)
    rotation.container_main({'action': 'status'})
    captured = capsys.readouterr()
    assert NEW not in captured.out + captured.err
    assert json.loads(captured.out) == {'ok': False, 'error': 'container_operation_failed'}


def test_private_write_mode_and_symlink_rejection(tmp_path):
    path = tmp_path / 'snapshot.json'
    stale = tmp_path / 'snapshot.tmp'
    stale.write_text('interrupted previous process')
    rotation.private_write(path, {'encrypted': 'synthetic'})
    assert path.stat().st_mode & 0o777 == 0o600
    link = tmp_path / 'link.json'
    link.symlink_to(path)
    with pytest.raises(OSError):
        rotation.private_read(link)


def test_lightweight_settings_use_only_supported_storage_and_database_modes():
    database, redis_options, storage, prefix = rotation.lightweight_settings({
        'DB_HOST': 'db_postgres', 'DB_PASSWORD': 'synthetic-password',
        'REDIS_HOST': 'redis', 'REDIS_KEY_PREFIX': ' namespace ',
        'STORAGE_TYPE': 'opendal', 'OPENDAL_SCHEME': 'fs', 'OPENDAL_FS_ROOT': 'storage'})
    assert database['host'] == 'db_postgres' and database['connect_timeout'] == 10
    assert redis_options['host'] == 'redis' and redis_options['socket_timeout'] == 10
    assert str(storage) == '/app/api/storage' and prefix == 'namespace'
    for unsupported in [{'KEY_PROVIDER_TYPE': 'azure-keyvault'}, {'STORAGE_TYPE': 's3'},
                        {'REDIS_USE_SENTINEL': 'true'}, {'DB_TYPE': 'mysql'},
                        {'OPENDAL_SCHEME': 's3'}, {'DB_EXTRAS': 'unknown=1'}]:
        with pytest.raises(rotation.RotationError, match='unsupported_maintenance_configuration'):
            rotation.lightweight_settings(unsupported)


def test_cache_invalidation_matches_installed_names_and_applies_namespace():
    operations = []
    class Pipeline:
        def __getattr__(self, name):
            return lambda *args: operations.append((name, *args))
    class Cache:
        def pipeline(self, transaction):
            assert transaction is True
            return Pipeline()
    rotation.cache_invalidation(Cache(), 'tenant-id', [('provider_model', 'model-id')], 'scope')
    assert operations[0] == ('delete', 'scope:provider_model_credentials:tenant_id:tenant-id:id:model-id')
    assert ('incr', 'scope:provider_configurations:tenant:tenant-id:source:provider_credentials:version') in operations
    assert sum(action[0] == 'expire' and action[-1] == 360 for action in operations) == 6
    assert operations[-1] == ('execute',)


def test_container_path_does_not_import_full_application_config():
    import ast
    source = ast.parse(Path(rotation.__file__).read_text())
    for function in source.body:
        if isinstance(function, ast.FunctionDef) and function.name in {'container_operation', 'crypto_functions'}:
            imports = [node.module or '' for node in ast.walk(function) if isinstance(node, ast.ImportFrom)]
            assert all(not name.startswith(('app_factory', 'configs', 'core', 'extensions', 'models')) for name in imports)


def test_cache_contract_is_verified_against_installed_source_before_mutation(tmp_path):
    code_root = Path(rotation.__file__).parents[1] / 'agent-platform/runtime/build/api/code'
    rotation.verify_cache_contract(code_root)
    (tmp_path / 'core/helper').mkdir(parents=True)
    source = (code_root / 'core/provider_manager.py').read_text()
    (tmp_path / 'core/provider_manager.py').write_text(source.replace('_PROVIDER_CONFIGURATION_CACHE_VERSION_TTL_SECONDS = 360',
                                                                    '_PROVIDER_CONFIGURATION_CACHE_VERSION_TTL_SECONDS = 999'))
    (tmp_path / 'core/helper/model_provider_cache.py').write_text((code_root / 'core/helper/model_provider_cache.py').read_text())
    with pytest.raises(rotation.RotationError, match='unsupported_cache_format'):
        rotation.verify_cache_contract(tmp_path)


def test_installed_crypto_wire_format_round_trip_and_legacy_oaep():
    # Optional on general developer envs; deployment image contains both packages.
    # The dedicated verification run supplies them from a disposable /tmp target.
    pytest.importorskip('Crypto')
    pytest.importorskip('gmpy2')
    code_root = Path(rotation.__file__).parents[1] / 'agent-platform/runtime/build/api/code'
    encrypt_raw, decrypt_raw, RSA, cipher = rotation.crypto_functions(code_root)
    private = RSA.generate(2048)
    encrypted = encrypt_raw(NEW, private.publickey().export_key())
    assert encrypted.startswith(b'HYBRID:')
    assert decrypt_raw(encrypted, private, cipher.new(private)) == NEW
    legacy = cipher.new(private.publickey()).encrypt(OLD.encode())
    assert decrypt_raw(legacy, private, cipher.new(private)) == OLD
    with pytest.raises(ValueError):
        decrypt_raw(encrypted[:-1] + bytes([encrypted[-1] ^ 1]), private, cipher.new(private))
