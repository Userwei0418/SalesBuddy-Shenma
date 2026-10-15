"""Exact identity V239 admission and unchanged 202-checksum final-ledger guards; offline only."""
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
import identity_release_contract as contract
import release_forward_bundle as forward
import release_upgrade as upgrade

BASE = json.loads((Path(__file__).with_name('fixtures') / 'release121_contract.synthetic.json').read_text())
FIXTURE = {
    'expected_ledger': {'checksums': {r['key']:r['sha256'] for r in BASE['migration_plan']},
                        'registered': sorted(BASE['expected_ledger']['registered']+['V234'])},
    'migration_plan': BASE['migration_plan'] + [{'key':'V239','file':contract.SQL['V239'][0],
                                                'sha256':contract.SQL['V239'][1]}],
    'normal_policy': json.loads((ROOT/'tools/installation/runtime_permissions.json').read_text()),
}


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('An offline source test attempted database access')
    monkeypatch.setattr(asyncpg, 'connect', forbidden)
    monkeypatch.setattr(asyncpg, 'create_pool', forbidden)


def plan():
    return dict(product_version='1.2.1', release_scope=contract.RELEASE_SCOPE.copy(),
                source_schema='V238', target_schema='V239', pending=['V239'],
                expected_ledger={**deepcopy(FIXTURE['expected_ledger']), 'running_jobs': 0})


def target():
    return dict(checksums={r['key']: r['sha256'] for r in FIXTURE['migration_plan']},
                registered=sorted([*FIXTURE['expected_ledger']['registered'], 'V239']), running_jobs=0)


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
             'release_upgrade.py', 'release_forward_bundle.py', 'report_release_contract.py', 'identity_release_contract.py']
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
    assert acl.schema_contract(ap) == ('V239', dict(count=203, ledger=contract.TARGET_LEDGER,
                                                  registered=contract.TARGET_REGISTERED))
    assert forward.verify_release_ledger(p, FIXTURE['migration_plan'], p['expected_ledger']) == ['V239']
    assert forward.verify_release_ledger(p, FIXTURE['migration_plan'], target(), final=True) == []


@pytest.mark.parametrize('change', ['sql','path','old_checksum','extra_migration','extra_pending',
    'scope','product','config','agent','source','target','duplicate_registration','local_registration_count',
    'old_policy','worker_grant','missing_api_grant','rls','missing_gate','compatibility','unverified_source'])
@pytest.mark.parametrize('entry',['inputs','database_phase'])
def test_invalid_identity_release_stops_before_database_or_module_loading(bound, monkeypatch, change, entry):
    p,ap,binding,tmp=bound
    migrations=deepcopy(FIXTURE['migration_plan'])
    policy=deepcopy(FIXTURE['normal_policy'])
    if change in ('sql','path'):
        migrations[-1]['sha256' if change=='sql' else 'file']='0'*64 if change=='sql' else 'other.sql'
    elif change=='old_checksum': p['expected_ledger']['checksums']['V238']='0'*64
    elif change=='extra_migration': migrations.append({'key':'V240','sha256':'0'*64,'file':'other.sql'})
    elif change=='extra_pending': p['pending'].append('V240')
    elif change=='scope': p['release_scope']=['release_1.2.1']
    elif change=='product': p['product_version']='1.2.0'
    elif change=='config': p['prepared_configuration_delta']={}
    elif change=='agent': p['agent_stage']={}
    elif change=='source': p['source_schema']='V237'
    elif change=='target': p['target_schema']='V240'
    elif change=='duplicate_registration': p['expected_ledger']['registered'].append('V238')
    elif change=='local_registration_count': p['expected_ledger']['registered']=p['expected_ledger']['registered'][33:]
    elif change=='old_policy': policy=deepcopy(BASE['normal_policy'])
    elif change=='worker_grant': policy['services']['worker']['functions'].append('security.partner_enterprise_reference(uuid)')
    elif change=='missing_api_grant': policy['services']['api']['functions'].remove('security.partner_enterprise_reference(uuid)')
    elif change=='rls': policy['rls_fingerprint']='0'*64
    elif change=='missing_gate': ap['source_files']={k:v for k,v in ap['source_files'].items() if not k.endswith('identity_release_contract.py')}
    elif change=='compatibility': p['supported_mini_versions'].append('1.2.1+mini.'+'b'*40)
    elif change=='unverified_source': p['candidate']['backend']['sha256']='b'*64
    ap['policy_ref']=write(tmp/'policy.json',policy)
    p['acl_plan']=write(tmp/'acl.json',ap)
    p['migration_plan']=write(tmp/'migration.json',migrations)
    binding=write(Path(binding['path']),p)
    def forbidden(*args,**kwargs):
        raise AssertionError('Rejected plan reached runtime/module access')
    monkeypatch.setattr(forward,'load',forbidden)
    with pytest.raises((forward.Refused,upgrade.ReleaseError)):
        if entry=='inputs': forward.inputs(binding)
        else: asyncio.run(forward.database_phase(binding))


@pytest.mark.parametrize('change',['missing_checksum','changed_checksum','missing_registered','extra_registered','duplicate_registered'])
def test_final_203_234_ledger_must_match_exactly(change):
    after=target()
    if change=='missing_checksum': del after['checksums']['V239']
    elif change=='changed_checksum': after['checksums']['V238']='0'*64
    elif change=='missing_registered': after['registered'].remove('V239')
    elif change=='extra_registered': after['registered'].append('V999')
    else: after['registered'].append('V239')
    with pytest.raises((forward.Refused,upgrade.ReleaseError)):
        forward.verify_release_ledger(plan(),FIXTURE['migration_plan'],after,final=True)


def test_policy_is_only_api_three_grants_and_schema_fingerprint():
    expected=deepcopy(BASE['normal_policy'])
    expected.update(schema='V239',migration_fingerprint=contract.TARGET_LEDGER)
    additions=['security.set_customer_business_identity(uuid,text[],uuid)',
        'security.partner_enterprise_reference(uuid)',
        'security.company_customer_directory_search(text,integer,integer,text,text,text[],text)']
    expected['services']['api']['functions']=sorted(expected['services']['api']['functions']+additions)
    assert expected==FIXTURE['normal_policy']
    sql=ROOT/'database'/contract.SQL['V239'][0]
    assert hashlib.sha256(sql.read_bytes()).hexdigest()==contract.SQL['V239'][1]


def test_old_v238_backend_bundle_not_a_v239_recovery_target():
    old=dict(database={'ledger_sha256':contract.BASE_LEDGER},configuration={},supported_mini_versions=['1.2.1'])
    live=dict(database={'ledger_sha256':contract.TARGET_LEDGER},configuration={})
    with pytest.raises(forward.original.Refused,match='DATABASE_MISMATCH'):
        forward.original.guard(old,live,['1.2.1'])


def test_acl_reads_full_production_registration_and_checksum_sets(bound):
    _,ap,_,_=bound
    after=target()
    value={'checksums':[{'migration_key':k,'checksum_sha256':v} for k,v in after['checksums'].items()],
           'registered':[{'version':v} for v in after['registered']]}
    async def fetch(sql):
        assert sql==acl.LEDGER_SQL
        return value
    assert asyncio.run(acl.ledger(SimpleNamespace(fetchval=fetch),ap))==value

