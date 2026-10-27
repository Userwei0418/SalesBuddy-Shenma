"""Exact 1.2.1 admission, historical SQL and final-ledger guards; offline only."""
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import asyncpg
import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / 'backend/deploy'
sys.path.insert(0, str(DEPLOY))
import release120_production_acl as acl
import release121_contract as contract
import release_forward_bundle as forward
import release_upgrade as upgrade
import report_release_contract as report_contract

FIXTURE = json.loads((Path(__file__).with_name('fixtures') / 'release121_contract.synthetic.json').read_text())


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('An offline source test attempted database access')
    monkeypatch.setattr(asyncpg, 'connect', forbidden)
    monkeypatch.setattr(asyncpg, 'create_pool', forbidden)


def plan():
    return dict(product_version='1.2.1', release_scope=contract.RELEASE_SCOPE.copy(),
                source_schema='V238', target_schema='V238', pending=['V234'],
                expected_ledger={**deepcopy(FIXTURE['expected_ledger']), 'running_jobs': 0})


def target():
    return dict(checksums={r['key']: r['sha256'] for r in FIXTURE['migration_plan']},
                registered=sorted([*FIXTURE['expected_ledger']['registered'], 'V234']), running_jobs=0)


def write(path, value):
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    return forward.file_ref(path)


@pytest.fixture
def bound(tmp_path, monkeypatch):
    tmp_path = tmp_path.resolve()
    monkeypatch.setattr(forward, 'ROOT', tmp_path / 'host')
    deploy = forward.ROOT / 'releases/new/backend/deploy'
    deploy.mkdir(parents=True)
    names = ['release120_production_acl.py', 'release121_contract.py',
             'release_upgrade.py', 'release_forward_bundle.py', 'report_release_contract.py']
    for name in names:
        (deploy / name).write_bytes((DEPLOY / name).read_bytes())
    source_paths = [*[DEPLOY / name for name in names], *[deploy / name for name in names],
                    *[ROOT / 'tools/installation' / (name + '.py') for name in acl.DEPENDENCIES]]
    ap = dict(format='salesbuddy-release120-production-acl-v1', mode='normal', roles=acl.ROLES.copy(),
              target=dict(database='sales_saas', oid=18684), attempt_id='offline121', window_id='offline121',
              release_scope=contract.RELEASE_SCOPE.copy(), expected_registered_versions=target()['registered'],
              policy_ref=write(tmp_path / 'policy.json', FIXTURE['normal_policy']),
              source_files={str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths})
    p = dict(**plan(), expected_database={'name': 'sales_saas'}, bundle_id='offline121', backend='new',
             web='new-web', expected_backend='r14', expected_web='r14-web',
             migration_plan=write(tmp_path / 'migration.json', FIXTURE['migration_plan']),
             acl_plan=write(tmp_path / 'acl.json', ap), acl_source=forward.file_ref(deploy / names[0]),
             validation=write(tmp_path / 'validation.json', dict(status='passed', tested_source_sha256='a'*64,
                                                               tested_mini_versions=['1.2.0', '1.2.1'])),
             candidate={'backend': {'sha256': 'a'*64}}, supported_mini_versions=['1.2.0', '1.2.1'])
    binding = write(forward.ROOT / 'bundles/forward/offline121/database/PLAN.json', p)
    return p, ap, binding, tmp_path


def test_exact_candidate_and_normal_acl_admitted(bound):
    p, ap, binding, _ = bound
    assert forward.inputs(binding) == (p, FIXTURE['migration_plan'], forward.document(p['validation']))
    assert acl.load_plan(p['acl_plan']) == (ap, FIXTURE['normal_policy'])
    assert acl.schema_contract(ap) == ('V238', dict(count=202, ledger=contract.TARGET_LEDGER,
                                                  registered=contract.TARGET_REGISTERED))
    assert forward.verify_release_ledger(p, FIXTURE['migration_plan'], p['expected_ledger']) == ['V234']
    assert forward.verify_release_ledger(p, FIXTURE['migration_plan'], target(), final=True) == []


def test_historical_exception_is_not_a_generic_option():
    before = FIXTURE['expected_ledger']
    args = (FIXTURE['migration_plan'], before['checksums'], before['registered'], 'V238', 'V238')
    with pytest.raises(upgrade.ReleaseError, match='historical migration'):
        upgrade.verify_migration_plan(*args)
    with pytest.raises(upgrade.ReleaseError, match='Unknown exact migration contract'):
        upgrade.verify_migration_plan(*args, release_contract='allow_historical')
    assert upgrade.verify_migration_plan(*args, release_contract=contract.CONTRACT_ID) == ['V234']


@pytest.mark.parametrize('change', ['wrong_sql', 'wrong_path', 'changed_old_checksum', 'extra_pending',
                                  'wrong_scope', 'wrong_version', 'configuration', 'agent', 'wrong_source',
                                  'duplicate_registered', 'duplicate_migration'])
@pytest.mark.parametrize('entry', ['inputs', 'database_phase'])
def test_invalid_transition_rejected_before_module_or_connection(bound, monkeypatch, change, entry):
    p, _, binding, tmp = bound
    migrations = deepcopy(FIXTURE['migration_plan'])
    if change in ('wrong_sql', 'wrong_path'):
        row = next(r for r in migrations if r['key'] == 'V234')
        row['sha256' if change == 'wrong_sql' else 'file'] = '0'*64 if change == 'wrong_sql' else 'other.sql'
    elif change == 'changed_old_checksum':
        p['expected_ledger']['checksums']['V238'] = '0'*64
    elif change == 'extra_pending':
        migrations.append(dict(key='V239', file='other.sql', sha256='0'*64))
        p['pending'].append('V239')
    elif change == 'wrong_scope':
        p['release_scope'] = ['work_report']
    elif change == 'wrong_version':
        p['product_version'] = '1.2.0'
    elif change == 'configuration':
        p['prepared_configuration_delta'] = {}
    elif change == 'agent':
        p['agent_stage'] = {}
    elif change == 'wrong_source':
        p['source_schema'] = 'V233'
    elif change == 'duplicate_registered':
        p['expected_ledger']['registered'].append('V238')
    else:
        migrations.append(deepcopy(migrations[-1]))
    p['migration_plan'] = write(tmp / 'migration.json', migrations)
    binding = write(Path(binding['path']), p)
    def forbidden(*args, **kwargs):
        raise AssertionError('Invalid input reached candidate module loading')
    monkeypatch.setattr(forward, 'load', forbidden)
    with pytest.raises((forward.Refused, upgrade.ReleaseError)):
        if entry == 'inputs':
            forward.inputs(binding)
        else:
            asyncio.run(forward.database_phase(binding))


@pytest.mark.parametrize('change', ['old_policy', 'matrix', 'rls', 'registration', 'missing_gate'])
@pytest.mark.parametrize('entry', ['inputs', 'database_phase', 'acl_load'])
def test_wrong_acl_rejected_before_sql(bound, monkeypatch, change, entry):
    p, ap, binding, tmp = bound
    policy = deepcopy(FIXTURE['normal_policy'])
    if change == 'old_policy':
        policy['migration_fingerprint'] = contract.BASE_LEDGER
    elif change == 'matrix':
        policy['services']['api']['functions'] = []
    elif change == 'rls':
        policy['rls_fingerprint'] = '0'*64
    elif change == 'registration':
        ap['expected_registered_versions'].remove('V234')
    else:
        ap['source_files'] = {key: val for key, val in ap['source_files'].items()
                              if not key.endswith('release121_contract.py')}
    ap['policy_ref'] = write(tmp / 'policy.json', policy)
    p['acl_plan'] = write(tmp / 'acl.json', ap)
    binding = write(Path(binding['path']), p)
    def forbidden(*args, **kwargs):
        raise AssertionError('Invalid ACL reached candidate module loading')
    monkeypatch.setattr(forward, 'load', forbidden)
    with pytest.raises((forward.Refused, acl.Refused)):
        if entry == 'inputs':
            forward.inputs(binding)
        elif entry == 'database_phase':
            asyncio.run(forward.database_phase(binding))
        else:
            acl.load_plan(p['acl_plan'])


@pytest.mark.parametrize('change', ['missing_checksum', 'old_checksum', 'missing_registered',
                                  'extra_registered', 'duplicate_registered'])
def test_final_state_must_be_exact_not_merely_same_head(change):
    after = target()
    if change == 'missing_checksum':
        del after['checksums']['V234']
    elif change == 'old_checksum':
        after['checksums']['V238'] = '0'*64
    elif change == 'missing_registered':
        after['registered'].remove('V234')
    elif change == 'extra_registered':
        after['registered'].append('V014')
    else:
        after['registered'].append('V238')
    with pytest.raises((forward.Refused, upgrade.ReleaseError)):
        pending = forward.verify_release_ledger(plan(), FIXTURE['migration_plan'], after, final=True)
        forward.require(pending == [], 'Incomplete final state')


def test_old_report_acl_contract_still_distinguishes_201_from_202():
    assert acl.schema_contract(dict(release_scope=['work_report'],
        expected_registered_versions=FIXTURE['expected_ledger']['registered'])) == (
            'V238', dict(count=201, ledger=report_contract.TARGET_LEDGER,
                         registered=report_contract.TARGET_REGISTERED))
    with pytest.raises(acl.Refused):
        acl.schema_contract(dict(release_scope=['work_report'], expected_registered_versions=target()['registered']))
    with pytest.raises(forward.Refused):
        report_contract.validate_acl(dict(release_scope=['work_report'],
            expected_registered_versions=target()['registered']), FIXTURE['normal_policy'],
            digest=forward.original.digest, require=forward.require)


def test_old201_bundle_remains_forbidden_after202():
    old = dict(database={'ledger_sha256': contract.BASE_LEDGER}, configuration={}, supported_mini_versions=['1.2.0'])
    live = dict(database={'ledger_sha256': contract.TARGET_LEDGER}, configuration={})
    with pytest.raises(forward.original.Refused):
        forward.original.guard(old, live, ['1.2.0'])


@pytest.mark.parametrize('fault', [None, 'old201', 'old_checksum_changed', 'missing_registration', 'duplicate'])
def test_acl_reads_exact_202_ledger_instead_of_relying_on_version_head(bound, fault):
    _, ap, _, _ = bound
    observed = target()
    if fault == 'old201':
        observed = deepcopy(FIXTURE['expected_ledger'])
    elif fault == 'old_checksum_changed':
        observed['checksums']['V238'] = '0'*64
    elif fault == 'missing_registration':
        observed['registered'].remove('V001')
    value = dict(checksums=[dict(migration_key=k, checksum_sha256=v) for k, v in observed['checksums'].items()],
                 registered=[dict(version=v) for v in observed['registered']])
    if fault == 'duplicate':
        value['checksums'].append(deepcopy(value['checksums'][0]))
    async def fetch(sql):
        assert sql == acl.LEDGER_SQL
        return value
    if fault is None:
        assert asyncio.run(acl.ledger(SimpleNamespace(fetchval=fetch), ap)) == value
    else:
        with pytest.raises(acl.Refused, match='LIVE_FULL_LEDGER_NOT_TARGET'):
            asyncio.run(acl.ledger(SimpleNamespace(fetchval=fetch), ap))


@pytest.mark.parametrize('fault', [None, 'source_sql_changed', 'live_prefix_changed', 'apply_unknown',
                                  'final_still201', 'final_extra_registered', 'old_row_rewritten'])
def test_peer_phase_preserves_pre_sql_guards_and_uncertain_commit_receipts(bound, monkeypatch, fault):
    """Exercise the real peer orchestration with inert in-memory collaborators."""
    p, _, binding, _ = bound
    release = forward.ROOT / 'releases/new'
    monkeypatch.setattr(forward, '__file__', str(release / 'backend/deploy/release_forward_bundle.py'))
    monkeypatch.setattr(forward.os, 'getuid', lambda: 12345)
    monkeypatch.setattr(forward.pwd, 'getpwnam', lambda name: SimpleNamespace(pw_uid=12345))
    calls = []
    before, after = deepcopy(p['expected_ledger']), target()
    for state in (before, after):
        state['checksum_rows'] = [dict(migration_key=k, checksum_sha256=v,
            provenance='executed' if k == 'V234' else 'adopted', applied_at='synthetic-original')
            for k, v in state['checksums'].items()]
        state['registered_rows'] = [dict(version=k, description='synthetic-original') for k in state['registered']]
    if fault == 'live_prefix_changed':
        before['checksums']['V238'] = '0'*64
    elif fault == 'final_still201':
        after = deepcopy(before)
    elif fault == 'final_extra_registered':
        after['registered'].append('V014')
    elif fault == 'old_row_rewritten':
        next(row for row in after['checksum_rows'] if row['migration_key'] == 'V238')['applied_at'] = 'rewritten'

    class Connection:
        closed = False
        def is_in_transaction(self):
            return False
        def is_closed(self):
            return self.closed
        async def close(self):
            self.closed = True
            calls.append('closed')
        async def fetchrow(self, sql):
            return dict(database='sales_saas', oid=18684, session_user='postgres', current_user='postgres')
        async def fetchval(self, sql, *args):
            return sql.startswith('SELECT pg_try_advisory_lock')
    conn = Connection()

    async def connect(**kwargs):
        calls.append('connected')
        return conn

    async def observed_ledger(connection):
        calls.append('ledger')
        return deepcopy(after if 'applied' in calls else before)

    async def apply(connection, item, root):
        # A durable unknown receipt must exist before the migration collaborator.
        receipt = json.loads((Path(binding['path']).parent / 'DATABASE.json').read_text())
        assert receipt['migrations'] == [dict(key='V234', sha256=contract.SQL['V234'][1], commit_outcome='unknown')]
        calls.append('applied')
        if fault == 'apply_unknown':
            raise ConnectionError('Synthetic lost commit acknowledgement')
        return 'applied'

    async def apply_acl(connection, **kwargs):
        calls.append('acl_applied')
        return dict(commit_attempted=True, commit_acknowledged=True, errors=[], cleanup_errors=[])

    async def verify_acl(connection, **kwargs):
        calls.append('acl_verified')
        return dict(status='verified_readonly', rolled_back=True, committed=False)

    item = SimpleNamespace(key='V234', checksum='0'*64 if fault == 'source_sql_changed' else contract.SQL['V234'][1],
                           path=release / 'database' / contract.SQL['V234'][0])
    migrator = SimpleNamespace(discover=lambda root: [item], DEPLOY_LOCK=202609102101, apply=apply)
    mock_acl = SimpleNamespace(apply_release_permissions=apply_acl, verify_release_permissions=verify_acl,
                               load_plan=acl.load_plan, dependencies=acl.dependencies)
    def load(name, path):
        return migrator if name == '_forward_candidate_migrate' else mock_acl
    monkeypatch.setattr(forward, 'load', load)
    monkeypatch.setattr(forward, 'ledger', observed_ledger)
    monkeypatch.setattr(asyncpg, 'connect', connect)
    result = asyncio.run(forward.database_phase(binding))
    assert result['closed'] is True
    assert result['success'] is (fault is None)
    if fault == 'source_sql_changed':
        assert calls == [] and result['migrations'] == []
        assert result['errors'][0]['code'] == 'Exact pending SQL source changed'
    elif fault == 'live_prefix_changed':
        assert 'applied' not in calls and result['migrations'] == []
        assert calls[-1] == 'closed'
    elif fault == 'apply_unknown':
        assert 'acl_applied' not in calls
        assert result['migrations'][0]['commit_outcome'] == 'unknown'
        assert calls[-1] == 'closed'
    else:
        assert calls == ['connected', 'ledger', 'applied', 'acl_applied', 'acl_verified', 'ledger', 'closed']
        assert result['migrations'][0]['commit_outcome'] == 'acknowledged'
        assert result['errors'] == [] if fault is None else bool(result['errors'])
