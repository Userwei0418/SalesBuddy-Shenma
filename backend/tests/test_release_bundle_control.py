"""Failure injection exercises real filesystem pointers and durable journals."""
from contextlib import nullcontext
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

DEPLOY = Path(__file__).parents[1] / 'deploy'
sys.path.insert(0, str(DEPLOY))
spec = importlib.util.spec_from_file_location('release_bundle_control', DEPLOY/'release_bundle_control.py')
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


class FixtureHost(control.Host):
    def __init__(self, root):
        super().__init__(root/'app', root/'static')
        self.db = {'name':'sales_saas','schema':'V202','ledger_sha256':'a'*64}
        self.config = {'service_metadata_sha256':'b'*64,'agent_metadata':{'bindings_sha256':'c'*64}}
        self.events = []
        self.fail_health = False
        self.fail_backup = False
        self.drift_database = False
        for base in (self.root, self.web):
            for name in ('old','new'):
                p=base/'releases'/name
                (p/'backend/src').mkdir(parents=True)
                (p/'backend/src/app.py').write_text(name)
                (p/'web').mkdir()
                (p/'web/index.html').write_text(name)
                (p/'REVISION').write_text(('a' if name=='old' else 'b')*40)
            (base/'current').symlink_to(base/'releases/old')
            (base/'previous').symlink_to(base/'releases/old')
        control.atomic_json(self.state/'ACTIVE_MINI_CLIENTS.json',{'versions':['1.1.1']})
        evidence={'status':'passed','tested_mini_versions':['1.1.1','1.1.2'],
                  'tested_source_sha256':self.artifacts('new','new')['backend']['sha256']}
        control.make_bundle(self,'new','new','new','1.1.2',['1.1.1','1.1.2'],evidence)

    def lock(self): return nullcontext()
    def database(self): return deepcopy(self.db)
    def configuration(self): return deepcopy(self.config)
    def assert_source_database(self, backend): pass
    def idle(self): pass
    def stop(self): self.events.append('stop')
    def start(self):
        self.events.append('start')
        if self.drift_database and control.pointer(self.root)=='new': self.db['schema']='V203'
    def backup(self, transaction_id):
        self.events.append('backup')
        if self.fail_backup: raise control.Refused('injected backup failure')
        return {'path':'synthetic-only','bytes':100}
    def health(self, backend):
        self.events.append('health-'+backend)
        if self.fail_health and backend=='new': raise control.Refused('injected health failure')
    def record_current(self,bundle):
        control.atomic_json(self.state/'CURRENT.json',{'bundle_id':bundle['bundle_id']})
    def record_inventory(self): self.events.append('inventory')


@pytest.fixture
def host(tmp_path): return FixtureHost(tmp_path)


def transaction(host): return json.loads((host.state/'TRANSACTION.json').read_text())


def test_success_switches_backend_and_web_without_replacing_database(host):
    before=deepcopy(host.db)
    result=control.switch(host,'new','old','old')
    assert result['phase']=='complete' and result['backup']['bytes']>0
    assert control.pointer(host.root)==control.pointer(host.web)=='new'
    assert control.pointer(host.root,'previous')==control.pointer(host.web,'previous')=='old'
    assert host.db==before
    assert host.events.index('backup')<host.events.index('health-new')
    assert json.loads((host.state/'CURRENT.json').read_text())['bundle_id']=='new'


@pytest.mark.parametrize('problem',['schema','ledger','config','client','code','web','expected_current'])
def test_mismatches_refuse_before_downtime(host,problem):
    if problem=='schema': host.db['schema']='V203'
    if problem=='ledger': host.db['ledger_sha256']='x'*64
    if problem=='config': host.config['service_metadata_sha256']='x'*64
    if problem=='client': control.atomic_json(host.state/'ACTIVE_MINI_CLIENTS.json',{'versions':['1.1.3']})
    if problem=='code': (host.root/'releases/new/backend/src/app.py').write_text('changed')
    if problem=='web': (host.web/'releases/new/web/index.html').write_text('changed')
    with pytest.raises(control.Refused):
        control.switch(host,'new','new' if problem=='expected_current' else 'old','old')
    assert 'stop' not in host.events
    assert control.pointer(host.root)==control.pointer(host.web)=='old'


@pytest.mark.parametrize('failure',['health','backup'])
def test_failed_release_recovers_both_pointers_and_records_rollback(host,failure):
    host.fail_health=failure=='health';host.fail_backup=failure=='backup'
    with pytest.raises(control.Refused): control.switch(host,'new','old','old')
    assert control.pointer(host.root)==control.pointer(host.web)=='old'
    assert transaction(host)['phase']=='rolled_back'
    assert host.events[-2:]==['health-old','inventory']
    assert not (host.state/'CURRENT.json').exists()


def test_database_drift_blocks_automatic_code_recovery_and_next_release(host):
    host.drift_database=True
    with pytest.raises(control.Refused,match='requires attention'): control.switch(host,'new','old','old')
    assert transaction(host)['phase']=='recovery_required'
    assert control.pointer(host.root)=='new'
    with pytest.raises(control.Refused,match='Unfinished'): control.switch(host,'new','new','new')


def test_interrupted_between_web_and_backend_switch_recovers_whole_pair(host):
    journal={'id':'interrupted','before':host.live(),'previous_bundle':None,
             'active_mini_versions':host.clients(),'phase':'switching'}
    path=host.state/'TRANSACTION.json';control.atomic_json(path,journal)
    control.switch_pointer(host.web,'current','new')
    control.recover(host,journal,path)
    assert control.pointer(host.root)==control.pointer(host.web)=='old'
    assert transaction(host)['phase']=='rolled_back'


def test_client_publication_during_interrupted_switch_prevents_unsafe_old_backend(host):
    journal={'id':'interrupted','before':host.live(),'previous_bundle':None,
             'active_mini_versions':['1.1.1'],'phase':'switching'}
    path=host.state/'TRANSACTION.json';control.atomic_json(path,journal)
    control.atomic_json(host.state/'ACTIVE_MINI_CLIENTS.json',{'versions':['1.1.1','1.1.2']})
    with pytest.raises(control.Refused,match='requires attention'): control.recover(host,journal,path)
    assert transaction(host)['phase']=='recovery_required'
    assert 'stop' not in host.events


def test_release_ids_and_symlinks_cannot_escape_managed_directories(host):
    with pytest.raises(control.Refused): control.release_path(host.root,'../elsewhere')
    (host.root/'releases/link').symlink_to(host.root/'releases/old')
    with pytest.raises(control.Refused): control.release_path(host.root,'link')
    (host.web/'releases/new/web/link').symlink_to(host.web/'releases/old/web/index.html')
    with pytest.raises(control.Refused): control.plan(host,'new')


def test_legacy_root_migrations_are_part_of_the_immutable_source_digest(host):
    root=host.root/'releases/new'
    before=control.tree_digest(root,control.SOURCE_PATHS)
    (root/'database').mkdir()
    (root/'database/V035__legacy.sql').write_text('SELECT 1;')
    assert control.tree_digest(root,control.SOURCE_PATHS)!=before


def test_registration_requires_tests_for_exact_source_and_supported_clients(host):
    evidence={'status':'passed','tested_source_sha256':'wrong','tested_mini_versions':['1.1.1']}
    with pytest.raises(control.Refused,match='Validation'): control.make_bundle(host,'bad','new','new','1.1.2',['1.1.1'],evidence)
    assert not (host.state/'bad.json').exists()
    evidence['tested_source_sha256']=host.artifacts('new','new')['backend']['sha256']
    with pytest.raises(control.Refused,match='compatibility'): control.make_bundle(host,'bad','new','new','1.1.2',['1.1.2'],evidence)
    with pytest.raises(control.Refused,match='already registered'):
        control.make_bundle(host,'new','new','new','1.1.2',['1.1.1'],evidence)


MINI_BUILD = '1.2.1+mini.56f89d6479d39698d733bc5b47220f68fbef2b51'
ACTIVE_VERSIONS = ['1.1.1', '1.1.2', '1.2.0', '1.2.1']


def run_cli(monkeypatch, host, *arguments):
    """Real parser/command path; only host interactions stay in tmp_path."""
    monkeypatch.setattr(control, 'Host', lambda: host)
    monkeypatch.setattr(control.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(control.signal, 'signal', lambda *args: None)
    monkeypatch.setattr(sys, 'argv', ['release_bundle_control.py', *arguments])
    control.main()


def bundle_evidence(host, backend, clients):
    return {'status': 'passed', 'tested_source_sha256': host.artifacts(backend, 'new')['backend']['sha256'],
            'tested_mini_versions': clients}


def test_cli_build_requirement_blocks_old_same_version_bundle_before_switch(host, monkeypatch, tmp_path):
    # Both bundles claim the same numeric versions and exact DB/configuration.
    control.make_bundle(host, 'old013', 'old', 'old', '1.2.1', ACTIVE_VERSIONS,
                        bundle_evidence(host, 'old', ACTIVE_VERSIONS))
    evidence_path = tmp_path/'compatible-evidence.json'
    clients = [*ACTIVE_VERSIONS, MINI_BUILD]
    evidence_path.write_text(json.dumps(bundle_evidence(host, 'new', clients)))
    run_cli(monkeypatch, host, 'register', '--id', 'compatible', '--backend', 'new', '--web', 'new',
            '--product', '1.2.1', '--clients', *clients, '--evidence', str(evidence_path))
    # Simulate the already-compatible current artifact before client distribution.
    control.switch_pointer(host.root, 'current', 'new')
    control.switch_pointer(host.web, 'current', 'new')
    control.atomic_json(host.state/'CURRENT.json', {'bundle_id': 'compatible'})
    run_cli(monkeypatch, host, 'require-clients', '--versions', *clients)
    assert host.clients() == sorted(clients)
    for command in ('plan', 'switch'):
        extra = ('--expected-backend', 'new', '--expected-web', 'new') if command == 'switch' else ()
        with pytest.raises(control.Refused, match='CLIENT_MISMATCH'):
            run_cli(monkeypatch, host, command, '--target', 'old013', *extra)
    assert 'stop' not in host.events and not (host.state/'TRANSACTION.json').exists()
    assert control.pointer(host.root) == control.pointer(host.web) == 'new'
    run_cli(monkeypatch, host, 'plan', '--target', 'compatible')
    run_cli(monkeypatch, host, 'switch', '--target', 'compatible',
            '--expected-backend', 'new', '--expected-web', 'new')
    assert transaction(host)['phase'] == 'complete'
    assert transaction(host)['active_mini_versions'] == sorted(clients)
    assert host.db == {'name':'sales_saas','schema':'V202','ledger_sha256':'a'*64}
    # Requirements may only grow, including exact build identities.
    with pytest.raises(control.Refused, match='Cannot silently remove'):
        run_cli(monkeypatch, host, 'require-clients', '--versions', *ACTIVE_VERSIONS)
    assert host.clients() == sorted(clients)
    other = '1.2.1+mini.' + 'a'*40
    with pytest.raises(control.Refused, match='CLIENT_MISMATCH'):
        run_cli(monkeypatch, host, 'require-clients', '--versions', *clients, other)
    assert host.clients() == sorted(clients)


@pytest.mark.parametrize('clients', [ACTIVE_VERSIONS, [*ACTIVE_VERSIONS, MINI_BUILD],
    [*ACTIVE_VERSIONS, '1.2.2+mini.' + 'a'*40], ['3.14.159+mini.' + '0'*40]])
def test_cli_init_accepts_numeric_and_build_ids_without_changing_display_version(host, monkeypatch, clients):
    (host.state/'ACTIVE_MINI_CLIENTS.json').unlink()  # Only this test's temporary host.
    run_cli(monkeypatch, host, 'init-clients', '--versions', *clients)
    assert host.clients() == sorted(clients)
    assert not host.events


@pytest.mark.parametrize('invalid', [
    '1.2', '1.2.2.3+mini.' + 'a'*40, '1.2+mini.' + 'a'*40, '1.2.1+mini.' + 'A'*40,
    '1.2.1+mini.' + 'a'*39, '1.2.1+mini.' + 'a'*41,
    '1.2.2+mini.' + 'A'*40, '1.2.2+mini.' + 'a'*39, '1.2.2+mini.' + 'a'*41,
    '1.2.1+web.' + 'a'*40, '1.2.1+mini.' + 'a'*40 + '/suffix', '１.２.１',
])
@pytest.mark.parametrize('command', ['init-clients', 'require-clients', 'register'])
def test_cli_invalid_client_identity_refuses_before_host_or_file_access(monkeypatch, command, invalid):
    def forbidden_host():
        raise AssertionError('Invalid identity must be rejected before host access')
    monkeypatch.setattr(control, 'Host', forbidden_host)
    args = [command, '--versions', '1.2.1', invalid] if command != 'register' else [
        command, '--id', 'unused', '--backend', 'unused', '--web', 'unused', '--product', '1.2.1',
        '--clients', invalid, '--evidence', '/never-read-invalid-client-evidence.json',
    ]
    monkeypatch.setattr(sys, 'argv', ['release_bundle_control.py', *args])
    with pytest.raises(control.Refused, match='Invalid mini version or build identity'):
        control.main()


@pytest.mark.parametrize('tested', [ACTIVE_VERSIONS, [*ACTIVE_VERSIONS, '1.2.1+mini.' + 'a'*40]])
def test_cli_register_requires_evidence_for_exact_build_identity(host, monkeypatch, tmp_path, tested):
    evidence_path = tmp_path/'numeric-only-evidence.json'
    evidence_path.write_text(json.dumps(bundle_evidence(host, 'new', tested)))
    with pytest.raises(control.Refused, match='compatibility evidence'):
        run_cli(monkeypatch, host, 'register', '--id', 'missing-build-proof', '--backend', 'new', '--web', 'new',
                '--product', '1.2.1', '--clients', *ACTIVE_VERSIONS, MINI_BUILD, '--evidence', str(evidence_path))
    assert not (host.state/'missing-build-proof.json').exists()
    assert not (host.root/'bundle-missing-build-proof').exists()
    assert not (host.web/'bundle-missing-build-proof').exists()
    assert host.clients() == ['1.1.1'] and not host.events


NEXT_MINI_BUILD = '1.2.1+mini.' + 'a'*40  # Synthetic successor, never a production identity.


@pytest.fixture
def adoption_host(host):
    control.atomic_json(host.state/'ACTIVE_MINI_CLIENTS.json', {'versions': ACTIVE_VERSIONS})
    for name, clients in [('live-bundle', [*ACTIVE_VERSIONS, MINI_BUILD]),
                          ('entry-bundle', [*ACTIVE_VERSIONS, NEXT_MINI_BUILD])]:
        control.make_bundle(host, name, 'new', 'new', '1.2.1', clients,
                            bundle_evidence(host, 'new', clients))
    control.switch_pointer(host.root, 'current', 'new')
    control.switch_pointer(host.web, 'current', 'new')
    control.atomic_json(host.state/'CURRENT.json', {'bundle_id': 'live-bundle'})
    control.atomic_json(host.state/'TRANSACTION.json', {'id': 'physical-switch', 'target': 'live-bundle',
                                                      'phase': 'complete', 'backup': {'bytes': 100}})
    control.atomic_json(host.root/'RELEASES.json', {'existing_inventory': True})
    (host.root/'.deploy.lock').touch()
    host.lock = control.Host.lock.__get__(host, control.Host)
    def forbidden(*args, **kwargs):
        raise AssertionError('Adoption must not stop/start/backup, refresh inventory or execute host commands')
    for method in ('stop', 'start', 'backup', 'record_current', 'record_inventory', 'run'):
        setattr(host, method, forbidden)
    host.events.clear()
    return host


def adoption_args(host):
    sha = lambda name: hashlib.sha256((host.state/(name+'.json')).read_bytes()).hexdigest()
    return ['adopt-compatibility', '--target', 'entry-bundle', '--expected-current', 'live-bundle',
            '--target-sha256', sha('entry-bundle'), '--current-sha256', sha('live-bundle')]


def adoption_snapshot(host):
    result = {}
    for root in (host.root, host.web):
        for path in root.rglob('*'):
            if path.is_symlink():
                info = path.lstat()
                result[str(path)] = ('link', str(path.readlink()), info.st_ino, info.st_mtime_ns)
            elif path.is_file():
                result[str(path)] = ('file', path.read_bytes())
    return result


@pytest.mark.parametrize('terminal_phase', ['complete', 'rolled_back'])
def test_adoption_cli_changes_only_current_and_own_receipt(adoption_host, monkeypatch, capsys, terminal_phase):
    host = adoption_host
    physical = transaction(host); physical['phase'] = terminal_phase
    control.atomic_json(host.state/'TRANSACTION.json', physical)
    before = adoption_snapshot(host)
    run_cli(monkeypatch, host, *adoption_args(host))
    result = json.loads(capsys.readouterr().out)
    assert result['phase'] == 'complete' and result['target'] == 'entry-bundle'
    assert result['active_mini_versions'] == ACTIVE_VERSIONS
    assert host.clients() == ACTIVE_VERSIONS
    after = adoption_snapshot(host)
    changed = {path for path in before.keys() | after.keys() if before.get(path) != after.get(path)}
    assert changed == {str(host.state/'CURRENT.json'), str(host.state/'compatibility/entry-bundle.json')}
    assert host.events == ['health-new']
    assert result == json.loads((host.state/'compatibility/entry-bundle.json').read_bytes())
    # Original require-clients remains independently necessary.
    run_cli(monkeypatch, host, 'require-clients', '--versions', *ACTIVE_VERSIONS, NEXT_MINI_BUILD)
    assert host.clients() == sorted([*ACTIVE_VERSIONS, NEXT_MINI_BUILD])


@pytest.mark.parametrize('problem', [
    'backend_bytes', 'web_bytes', 'ledger', 'schema', 'config',
    'current_id', 'current_bundle_artifact', 'current_bundle_config',
    'target_backend', 'target_web', 'missing_active_client',
    'evidence_status', 'evidence_source', 'evidence_exact_client',
    'unfinished_transaction', 'missing_transaction', 'unfinished_adoption',
    'current_metadata_symlink',
])
def test_adoption_refuses_drift_and_invalid_evidence_before_metadata_writes(adoption_host, monkeypatch, problem):
    host = adoption_host
    target = host.load('entry-bundle'); current = host.load('live-bundle')
    if problem == 'backend_bytes': (host.root/'releases/new/backend/src/app.py').write_text('changed')
    if problem == 'web_bytes': (host.web/'releases/new/web/index.html').write_text('changed')
    if problem == 'ledger': host.db['ledger_sha256'] = 'd'*64
    if problem == 'schema': host.db['schema'] = 'V999'
    if problem == 'config': host.config['service_metadata_sha256'] = 'd'*64
    if problem == 'current_id': control.atomic_json(host.state/'CURRENT.json', {'bundle_id': 'new'})
    if problem == 'current_bundle_artifact': current['backend'] = host.artifacts('old', 'new')['backend']
    if problem == 'current_bundle_config': current['configuration']['service_metadata_sha256'] = 'd'*64
    if problem == 'target_backend': target['backend'] = host.artifacts('old', 'new')['backend']
    if problem == 'target_web': target['web'] = host.artifacts('new', 'old')['web']
    if problem == 'missing_active_client': target['supported_mini_versions'].remove('1.1.1')
    if problem == 'evidence_status': target['validation']['status'] = 'pending'
    if problem == 'evidence_source': target['validation']['tested_source_sha256'] = 'd'*64
    if problem == 'evidence_exact_client': target['validation']['tested_mini_versions'] = ACTIVE_VERSIONS
    if problem == 'unfinished_transaction': control.atomic_json(host.state/'TRANSACTION.json', {'phase': 'switching'})
    if problem == 'missing_transaction': (host.state/'TRANSACTION.json').unlink()
    if problem == 'unfinished_adoption': control.atomic_json(host.state/'compatibility/previous.json', {'phase': 'prepared'})
    if problem == 'current_metadata_symlink':
        (host.state/'CURRENT.json').rename(host.state/'saved-current.json')
        (host.state/'CURRENT.json').symlink_to('saved-current.json')
    control.atomic_json(host.state/'live-bundle.json', current)
    control.atomic_json(host.state/'entry-bundle.json', target)
    # Re-pin changed metadata; semantic guards must still reject it.
    args = adoption_args(host); before = adoption_snapshot(host)
    with pytest.raises(control.Refused): run_cli(monkeypatch, host, *args)
    assert adoption_snapshot(host) == before
    assert not (host.state/'compatibility/entry-bundle.json').exists()


@pytest.mark.parametrize('which', ['target', 'current'])
def test_adoption_pins_both_registered_bundle_files(adoption_host, monkeypatch, which):
    host = adoption_host; args = adoption_args(host)
    args[args.index('--'+which+'-sha256')+1] = '0'*64
    before = adoption_snapshot(host)
    with pytest.raises(control.Refused, match='reviewed SHA256'): run_cli(monkeypatch, host, *args)
    assert adoption_snapshot(host) == before


@pytest.mark.parametrize('drift', ['source', 'config', 'clients', 'pointer', 'transaction', 'target_evidence'])
def test_adoption_detects_drift_during_final_readonly_health_check(adoption_host, monkeypatch, drift):
    host = adoption_host; args = adoption_args(host)
    current_before = (host.state/'CURRENT.json').read_bytes()
    def health(backend):
        if drift == 'source': (host.root/'releases/new/backend/src/app.py').write_text('late source drift')
        if drift == 'config': host.config['service_metadata_sha256'] = 'e'*64
        if drift == 'clients': control.atomic_json(host.state/'ACTIVE_MINI_CLIENTS.json', {'versions': ['1.1.1']})
        if drift == 'pointer': control.switch_pointer(host.web, 'previous', 'new')
        if drift == 'transaction': control.atomic_json(host.state/'TRANSACTION.json', {'phase': 'prepared'})
        if drift == 'target_evidence':
            target = host.load('entry-bundle'); target['validation']['status'] = 'pending'
            control.atomic_json(host.state/'entry-bundle.json', target)
    host.health = health
    with pytest.raises(control.Refused): run_cli(monkeypatch, host, *args)
    assert (host.state/'CURRENT.json').read_bytes() == current_before
    assert not (host.state/'compatibility/entry-bundle.json').exists()


def test_adoption_obeys_original_exclusive_lock(adoption_host, monkeypatch):
    host = adoption_host; before = adoption_snapshot(host)
    with host.lock():
        with pytest.raises(control.Refused, match='Another deployment'):
            run_cli(monkeypatch, host, *adoption_args(host))
    assert adoption_snapshot(host) == before


@pytest.mark.parametrize('fault', ['before_current_replace', 'after_current_replace', 'final_receipt'])
def test_adoption_interruption_preserves_actual_state_and_blocks_replay(adoption_host, monkeypatch, fault):
    host = adoption_host; args = adoption_args(host); original_atomic = control.atomic_json
    physical_before = (host.state/'TRANSACTION.json').read_bytes()
    def fail(path, value):
        if path == host.state/'CURRENT.json':
            if fault == 'before_current_replace': raise OSError('synthetic write failure')
            original_atomic(path, value)
            if fault == 'after_current_replace': raise OSError('synthetic fsync failure after replace')
            return
        if fault == 'final_receipt' and path == host.state/'compatibility/entry-bundle.json' and value['phase'] == 'complete':
            raise OSError('synthetic receipt failure')
        original_atomic(path, value)
    monkeypatch.setattr(control, 'atomic_json', fail)
    with pytest.raises(OSError): run_cli(monkeypatch, host, *args)
    expected = 'live-bundle' if fault == 'before_current_replace' else 'entry-bundle'
    assert json.loads((host.state/'CURRENT.json').read_bytes()) == {'bundle_id': expected}
    assert json.loads((host.state/'compatibility/entry-bundle.json').read_bytes())['phase'] == 'needs_inspection'
    assert (host.state/'TRANSACTION.json').read_bytes() == physical_before
    assert host.clients() == ACTIVE_VERSIONS
    monkeypatch.setattr(control, 'atomic_json', original_atomic)
    before_replay = adoption_snapshot(host)
    with pytest.raises(control.Refused, match='already attempted'): run_cli(monkeypatch, host, *args)
    assert adoption_snapshot(host) == before_replay


@pytest.fixture
def reconciliation_host(adoption_host):
    host = adoption_host
    # Reproduce a completed older transaction whose ledger no longer describes
    # the independently switched live backend/Web and migrated database.
    stale = host.load('live-bundle')
    stale.update(host.artifacts('old', 'old'))
    stale['database'] = {'name': 'sales_saas', 'schema': 'V201', 'ledger_sha256': '0'*64}
    control.atomic_json(host.state/'live-bundle.json', stale)
    return host


def reconciliation_args(host):
    args = adoption_args(host)
    args[0] = 'reconcile-current'
    return [*args, '--drift-fields', 'backend', 'web', 'database']


def test_reconciliation_cli_repairs_only_ledger_preserving_runtime_and_history(reconciliation_host, monkeypatch, capsys):
    host = reconciliation_host
    before = adoption_snapshot(host)
    run_cli(monkeypatch, host, *reconciliation_args(host))
    result = json.loads(capsys.readouterr().out)
    assert result['format'] == 'salesbuddy-current-reconciliation-v1'
    assert result['phase'] == 'complete'
    assert result['declared_drift'] == ['backend', 'database', 'web']
    assert result['historical_switch_status'] == 'not_reconstructed'
    assert result['runtime_action'] == 'none'
    assert result['stale_recorded_state']['database']['schema'] == 'V201'
    assert result['live']['database']['schema'] == 'V202'
    after = adoption_snapshot(host)
    assert {path for path in before.keys() | after.keys() if before.get(path) != after.get(path)} == {
        str(host.state/'CURRENT.json'), str(host.state/'reconciliations/entry-bundle.json')}
    assert host.events == ['health-new']
    assert result == json.loads((host.state/'reconciliations/entry-bundle.json').read_bytes())
    assert hashlib.sha256((host.state/'TRANSACTION.json').read_bytes()).hexdigest() == result['physical_transaction_sha256']


@pytest.mark.parametrize('problem', [
    'stale_expected_id', 'stale_current_sha', 'stale_target_sha',
    'target_backend', 'target_web', 'target_database', 'target_ledger', 'target_configuration',
    'client_missing', 'source_evidence', 'client_evidence', 'evidence_pending',
    'unfinished_transaction', 'missing_transaction', 'unfinished_reconciliation', 'unfinished_adoption',
    'no_drift', 'incomplete_drift', 'extra_drift', 'duplicate_drift', 'current_symlink',
    'schema_verification', 'health_verification',
])
def test_reconciliation_refuses_unverified_or_stale_plan_before_any_write(reconciliation_host, monkeypatch, problem):
    host = reconciliation_host
    target = host.load('entry-bundle')
    if problem == 'target_backend': target['backend'] = host.artifacts('old', 'new')['backend']
    if problem == 'target_web': target['web'] = host.artifacts('new', 'old')['web']
    if problem == 'target_database': target['database']['schema'] = 'V999'
    if problem == 'target_ledger': target['database']['ledger_sha256'] = 'e'*64
    if problem == 'target_configuration': target['configuration']['service_metadata_sha256'] = 'e'*64
    if problem == 'client_missing': target['supported_mini_versions'].remove('1.1.1')
    if problem == 'source_evidence': target['validation']['tested_source_sha256'] = 'e'*64
    if problem == 'client_evidence': target['validation']['tested_mini_versions'] = ACTIVE_VERSIONS
    if problem == 'evidence_pending': target['validation']['status'] = 'pending'
    if problem == 'unfinished_transaction': control.atomic_json(host.state/'TRANSACTION.json', {'phase': 'switching'})
    if problem == 'missing_transaction': (host.state/'TRANSACTION.json').unlink()
    if problem == 'unfinished_reconciliation': control.atomic_json(host.state/'reconciliations/previous.json', {'phase': 'prepared'})
    if problem == 'unfinished_adoption': control.atomic_json(host.state/'compatibility/previous.json', {'phase': 'prepared'})
    if problem == 'no_drift':
        stale = host.load('live-bundle'); stale.update(host.live())
        control.atomic_json(host.state/'live-bundle.json', stale)
    if problem == 'current_symlink':
        (host.state/'CURRENT.json').rename(host.state/'saved-current.json')
        (host.state/'CURRENT.json').symlink_to('saved-current.json')
    control.atomic_json(host.state/'entry-bundle.json', target)
    args = reconciliation_args(host)
    if problem == 'stale_expected_id': args[args.index('--expected-current')+1] = 'new'
    if problem == 'stale_current_sha': args[args.index('--current-sha256')+1] = '0'*64
    if problem == 'stale_target_sha': args[args.index('--target-sha256')+1] = '0'*64
    if problem == 'incomplete_drift': args.remove('database')
    if problem == 'extra_drift': args.append('configuration')
    if problem == 'duplicate_drift': args.append('database')
    if problem in ('schema_verification', 'health_verification'):
        def refuse(*args): raise control.Refused('synthetic unverifiable state')
        setattr(host, 'assert_source_database' if problem == 'schema_verification' else 'health', refuse)
    before = adoption_snapshot(host)
    with pytest.raises(control.Refused): run_cli(monkeypatch, host, *args)
    assert adoption_snapshot(host) == before
    assert not (host.state/'reconciliations/entry-bundle.json').exists()


@pytest.mark.parametrize('drift', ['source', 'database', 'config', 'clients', 'pointer', 'transaction', 'current', 'target'])
def test_reconciliation_detects_concurrent_drift_before_preparation(reconciliation_host, monkeypatch, drift):
    host = reconciliation_host
    current_before = (host.state/'CURRENT.json').read_bytes()
    def health(backend):
        if drift == 'source': (host.root/'releases/new/backend/src/app.py').write_text('changed concurrently')
        if drift == 'database': host.db['ledger_sha256'] = 'e'*64
        if drift == 'config': host.config['service_metadata_sha256'] = 'e'*64
        if drift == 'clients': control.atomic_json(host.state/'ACTIVE_MINI_CLIENTS.json', {'versions': ['1.1.1']})
        if drift == 'pointer': control.switch_pointer(host.root, 'previous', 'new')
        if drift == 'transaction': control.atomic_json(host.state/'TRANSACTION.json', {'phase': 'prepared'})
        if drift == 'current': control.atomic_json(host.state/'CURRENT.json', {'bundle_id': 'new'})
        if drift == 'target':
            value = host.load('entry-bundle'); value['validation']['status'] = 'pending'
            control.atomic_json(host.state/'entry-bundle.json', value)
    host.health = health
    with pytest.raises(control.Refused): run_cli(monkeypatch, host, *reconciliation_args(host))
    if drift != 'current': assert (host.state/'CURRENT.json').read_bytes() == current_before
    else: assert json.loads((host.state/'CURRENT.json').read_bytes()) == {'bundle_id': 'new'}
    assert not (host.state/'reconciliations/entry-bundle.json').exists()


@pytest.mark.parametrize('point', ['prepared', 'current_written'])
def test_reconciliation_detects_late_drift_and_leaves_inspection_receipt(reconciliation_host, monkeypatch, point):
    host = reconciliation_host
    original = control.atomic_json
    def write(path, value):
        original(path, value)
        if ((point == 'prepared' and path == host.state/'reconciliations/entry-bundle.json' and value['phase'] == 'prepared')
                or (point == 'current_written' and path == host.state/'CURRENT.json')):
            host.db['ledger_sha256'] = 'e'*64
    monkeypatch.setattr(control, 'atomic_json', write)
    with pytest.raises(control.Refused): run_cli(monkeypatch, host, *reconciliation_args(host))
    assert json.loads((host.state/'CURRENT.json').read_bytes()) == {
        'bundle_id': 'live-bundle' if point == 'prepared' else 'entry-bundle'}
    assert json.loads((host.state/'reconciliations/entry-bundle.json').read_bytes())['phase'] == 'needs_inspection'
    assert transaction(host)['phase'] == 'complete'  # Never rewrites physical history.


def test_reconciliation_obeys_deploy_lock(reconciliation_host, monkeypatch):
    host = reconciliation_host; before = adoption_snapshot(host)
    with host.lock():
        with pytest.raises(control.Refused, match='Another deployment'):
            run_cli(monkeypatch, host, *reconciliation_args(host))
    assert adoption_snapshot(host) == before


@pytest.mark.parametrize('fault', ['before_current', 'after_current', 'final_receipt'])
def test_reconciliation_write_interruption_cannot_be_replayed(reconciliation_host, monkeypatch, fault):
    host = reconciliation_host; args = reconciliation_args(host)
    original = control.atomic_json
    def write(path, value):
        if path == host.state/'CURRENT.json':
            if fault == 'before_current': raise OSError('synthetic failure')
            original(path, value)
            if fault == 'after_current': raise OSError('synthetic failure')
            return
        if fault == 'final_receipt' and path == host.state/'reconciliations/entry-bundle.json' and value['phase'] == 'complete':
            raise OSError('synthetic failure')
        original(path, value)
    monkeypatch.setattr(control, 'atomic_json', write)
    with pytest.raises(OSError): run_cli(monkeypatch, host, *args)
    assert json.loads((host.state/'reconciliations/entry-bundle.json').read_bytes())['phase'] == 'needs_inspection'
    monkeypatch.setattr(control, 'atomic_json', original)
    before = adoption_snapshot(host)
    with pytest.raises(control.Refused, match='already attempted'): run_cli(monkeypatch, host, *args)
    assert adoption_snapshot(host) == before


def test_switch_refuses_stale_current_ledger_before_downtime(reconciliation_host):
    host = reconciliation_host; before = adoption_snapshot(host)
    with pytest.raises(control.Refused, match='Current bundle drift'):
        control.switch(host, 'entry-bundle', 'new', 'new')
    assert adoption_snapshot(host) == before
    assert not host.events


def test_recovery_refuses_previous_bundle_that_does_not_match_recorded_before(host):
    control.make_bundle(host, 'stale', 'new', 'new', '1.1.2', ['1.1.1'],
                        bundle_evidence(host, 'new', ['1.1.1']))
    journal = {'id': 'interrupted', 'before': host.live(), 'previous_bundle': {'bundle_id': 'stale'},
               'active_mini_versions': host.clients(), 'phase': 'switching'}
    path = host.state/'TRANSACTION.json'; control.atomic_json(path, journal)
    control.switch_pointer(host.root, 'current', 'new')
    control.switch_pointer(host.web, 'current', 'new')
    with pytest.raises(control.Refused, match='requires attention'):
        control.recover(host, journal, path)
    assert transaction(host)['phase'] == 'recovery_required'
    assert control.pointer(host.root) == control.pointer(host.web) == 'new'
    assert 'stop' not in host.events
    assert not (host.state/'CURRENT.json').exists()


def test_recovery_pins_previous_registered_bundle_bytes(host):
    clients = ['1.1.1']
    control.make_bundle(host, 'valid-before', 'old', 'old', '1.1.2', clients, bundle_evidence(host, 'old', clients))
    path = host.state/'valid-before.json'
    original_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    journal = {'id': 'interrupted', 'before': host.live(), 'previous_bundle': {'bundle_id': 'valid-before'},
               'previous_bundle_sha256': original_sha, 'active_mini_versions': clients, 'phase': 'switching'}
    value = host.load('valid-before'); value['validation']['status'] = 'pending'
    control.atomic_json(path, value)
    with pytest.raises(control.Refused, match='requires attention'):
        control.recover(host, journal, host.state/'TRANSACTION.json')
    assert 'stop' not in host.events
    assert transaction(host)['phase'] == 'recovery_required'


def test_configuration_probe_comes_from_controller_but_imports_live_source(host):
    calls = []
    def run(arguments, **kwargs):
        calls.append(arguments)
        if arguments[0] == 'systemctl': return 'User=sales\nGroup=sales\nWorkingDirectory=/current'
        return '{"schema_version":1,"bindings_sha256":"synthetic"}'
    host.run = run
    result = control.Host.configuration(host)
    probes = [args for args in calls if args[0] != 'systemctl']
    assert len(probes) == 2 and result['agent_metadata']['schema_version'] == 1
    for args in probes:
        assert args[0] == host.root/'current/.venv/bin/python'
        assert args[1] == DEPLOY.resolve()/'inherit_service_environment.py'
        assert args[-3] == host.root/'current/backend/src'
        assert args[-2] == DEPLOY.resolve()
        assert args[-1] == DEPLOY.resolve()/'verify_agent_runtime.py'
        assert args[args.index('--')+1] == host.root/'current/.venv/bin/python'
    assert not (host.root/'current/backend/deploy/verify_agent_runtime.py').exists()


def test_configuration_probe_executes_with_current_source_over_stale_pythonpath(host, tmp_path):
    import os
    import subprocess
    controller = tmp_path/'reviewed-control'
    controller.mkdir()
    (controller/'probe.py').write_text('from sales_backend.config import identity\nprint(identity)\n')
    live = host.root/'current/backend/src'
    stale = tmp_path/'stale-installed-source'
    for root, identity in ((live, 'live-product'), (stale, 'stale-product')):
        package = root/'sales_backend'; package.mkdir(parents=True)
        (package/'__init__.py').write_text('')
        (package/'config.py').write_text('identity = ' + repr(identity))
    commands = []
    def run(arguments, **kwargs):
        if arguments[0] == 'systemctl': return ''
        commands.append(arguments)
        return '{"same":"metadata"}'
    host.run = run
    control.Host.configuration(host)
    args = commands[0]
    bootstrap = args[args.index('-c')+1]
    result = subprocess.run([sys.executable, '-c', bootstrap, str(live), str(controller), str(controller/'probe.py')],
                            env=dict(os.environ, PYTHONPATH=str(stale)), capture_output=True, text=True, check=True)
    assert result.stdout.strip() == 'live-product'


@pytest.mark.parametrize('command', ['switch', 'adopt-compatibility', 'require-clients'])
def test_unfinished_reconciliation_blocks_other_metadata_commands(adoption_host, monkeypatch, command):
    host = adoption_host
    control.atomic_json(host.state/'reconciliations/interrupted.json', {'phase': 'needs_inspection'})
    args = {'switch': ['switch', '--target', 'entry-bundle', '--expected-backend', 'new', '--expected-web', 'new'],
            'adopt-compatibility': adoption_args(host),
            'require-clients': ['require-clients', '--versions', *ACTIVE_VERSIONS]}
    before = adoption_snapshot(host)
    with pytest.raises(control.Refused, match='Unfinished bundle metadata'):
        run_cli(monkeypatch, host, *args[command])
    assert adoption_snapshot(host) == before


@pytest.mark.parametrize('version', ['1.2.2', '2.0.0', '3.14.159'])
def test_new_version_mini_identity_still_requires_exact_build_evidence(host, version):
    exact = version + '+mini.' + 'b'*40
    with pytest.raises(control.Refused, match='compatibility'):
        control.make_bundle(host, 'missing-exact', 'new', 'new', version, [*ACTIVE_VERSIONS, exact],
                            bundle_evidence(host, 'new', [*ACTIVE_VERSIONS, version]))
    assert not (host.state/'missing-exact.json').exists()
    result = control.make_bundle(host, 'exact-proven', 'new', 'new', version, [*ACTIVE_VERSIONS, exact],
                                 bundle_evidence(host, 'new', [*ACTIVE_VERSIONS, exact]))
    assert exact in result['supported_mini_versions']
    assert host.events == []
