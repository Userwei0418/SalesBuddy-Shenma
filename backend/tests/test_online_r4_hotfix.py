"""Focused r3 regressions; all IO is local/mocked, never production."""
import hashlib
import importlib.util
import json
import sys
from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sales_backend.services import opportunities as service

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend/deploy'))
spec = importlib.util.spec_from_file_location(
    'forward_r4_under_test', ROOT / 'backend/deploy/release_forward_bundle.py')
forward = importlib.util.module_from_spec(spec)
spec.loader.exec_module(forward)


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['forecast', 'name', 'amount', 'stage', 'material', 'mixed', 'unchanged'])
async def test_real_save_classifies_legacy_changes_without_skipping_business_reviews(monkeypatch, change):
    before = dict(id='opportunity', name='existing', status='open', probability=10,
                  amount=Decimal('1000'), expected_close_date=date(2026, 12, 31),
                  version_no=1, owner_user_ref_id='owner')
    old = [dict(year=2026, quarter=4, recognized_amount=Decimal('100'),
                collection_amount=Decimal('100'), collection_confidence='high')]
    data = dict(before, action='update', opportunity_id='opportunity', forecast_schema_version=2)
    data.pop('id')
    if change in ('forecast', 'mixed'):
        data['quarterly_forecasts'] = [{**old[0], 'recognized_amount': Decimal('70'),
                                       'collection_amount': Decimal('70')}]
    if change == 'name':
        data['name'] = 'changed'
    if change == 'amount':
        data['amount'] = Decimal('900')
    if change == 'stage':
        data.update(probability=30, quarterly_forecasts=old)
    material_changes = ([dict(field='material_lines', label='交易物料', before=[], after=[])]
                        if change in ('material', 'mixed') else [])
    materials = SimpleNamespace(prepare=AsyncMock(return_value=('direct', None, [], material_changes)),
                                save=AsyncMock(), rows=AsyncMock(return_value=[]))
    monkeypatch.setattr(service, 'OpportunityMaterialRepository', lambda: materials)
    values = dict(require_permission=None, require_amount_confirmation=None,
                  customer_for_opportunity=dict(id='customer', name='customer', business_identities=['partner']),
                  lock_opportunity=before, forecasts=old, resolve_opportunity_partner={},
                  opportunity_name_available=True,
                  prepare_people=dict(changes=[], fde_ids=None), write_project_people=None,
                  write_opportunity=2, write_forecasts=None, members_by_opportunity={},
                  record_change='event', enqueue_change_review=None,
                  enqueue_battle_map_review=None, enqueue_customer_risk_review=None)
    mocks = {}
    for name, value in values.items():
        mocks[name] = AsyncMock(return_value=value)
        monkeypatch.setattr(service, name, mocks[name])
    monkeypatch.setattr(service, 'profile_changes', lambda before, data: ({}, []))

    async def projection(connection, rows):
        return rows

    monkeypatch.setattr(service, 'profile_projection', projection)
    result = await service.save_opportunity(SimpleNamespace(execute=AsyncMock()),
        SimpleNamespace(user_id='owner', workspace_id='workspace'), customer_id='customer', data=data)
    assert result['changed'] is (change != 'unchanged')
    if change == 'unchanged':
        mocks['write_opportunity'].assert_not_awaited()
        mocks['record_change'].assert_not_awaited()
        return
    mocks['write_opportunity'].assert_awaited_once()
    mocks['write_forecasts'].assert_awaited_once()
    event = mocks['record_change'].await_args.kwargs
    review = change != 'material'
    assert event['assess_change'] is review and event['notify'] is review
    for name in ('enqueue_change_review', 'enqueue_battle_map_review', 'enqueue_customer_risk_review'):
        assert mocks[name].await_count == int(review)
    if change in ('forecast', 'mixed'):
        assert result['quarterly_forecasts'][0]['recognized_amount'] == Decimal('70')
        # Real legacy differences() cards do not acquire an invented field key.
        assert any('field' not in card for card in event['changes'])


def canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


def test_report_policy_delta_is_only_two_worker_selects_and_forward_ledger():
    policy = json.loads((ROOT / 'tools/installation/runtime_permissions.json').read_text())
    prior = deepcopy(policy['services'])
    for table in ('activity.visit_opportunity', 'activity.visit_participant'):
        assert prior['worker']['tables'].pop(table) == ['SELECT']
    assert canonical(prior) == '98cc1fc25687489ba608168d64dbde4741a01558357ff06dfcebe2e99eaa6709'
    assert policy['rls_fingerprint'] == '10ef14e0c996f95ea145e3a1d8afa6a42734ba5ad18f96257a407bced904356b'
    assert policy['schema'] == 'V233'
    db = ROOT / 'database'
    files = [db / 'baseline/V001__current_schema.sql', *(db / 'seeds').glob('S*.sql'),
             *db.glob('V*.sql'), *(db / 'migrations').glob('V*.sql')]
    ledger = {('BASELINE_V1' if path.parent.name == 'baseline' else path.stem.split('__')[0]):
              hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
    assert len(ledger) == 197 and canonical(ledger) == policy['migration_fingerprint']
    del ledger['V233']
    assert canonical(ledger) == '74eed85228f9a6608929e4045e9f99da77dde0737ac2e8d5c8b0c141dff6305d'


@pytest.fixture
def forward_plan(monkeypatch):
    migration = [{'key': 'BASELINE_V1', 'sha256': 'd' * 64},
                 {'key': 'V232', 'sha256': 'a' * 64}, {'key': 'V233', 'sha256': 'b' * 64}]
    plan = dict(bundle_id='r4', backend='r4', web='e3', expected_backend='r3', expected_web='e3',
        product_version='1.2.0', source_schema='V232', target_schema='V233', pending=['V233'],
        expected_database={'name': 'sales_saas'}, migration_plan='migration', validation='validation',
        acl_source={'path': str(forward.ROOT / 'releases/r4/backend/deploy/release120_production_acl.py')},
        acl_plan='acl', candidate={'backend': {'sha256': 'c' * 64}}, supported_mini_versions=['1.1.1'],
        expected_ledger={'checksums': {'BASELINE_V1': 'd' * 64, 'V232': 'a' * 64}, 'registered': ['V232']})
    docs = dict(plan=plan, migration=migration, validation=dict(status='passed',
        tested_source_sha256='c' * 64, tested_mini_versions=['1.1.1']), acl={})
    monkeypatch.setattr(forward, 'document', lambda key: docs[key])
    monkeypatch.setattr(forward, 'read_ref', lambda ref: b'checked fixture')
    return plan, docs


def test_v233_exact_forward_reuses_same_web_without_agent_stage(forward_plan):
    plan, _ = forward_plan
    assert forward.inputs('plan')[0] == plan


@pytest.mark.parametrize('change', ['web', 'pending', 'stage', 'configuration', 'source', 'target', 'backend'])
def test_v233_rejects_unrelated_upgrade_or_configuration_changes(forward_plan, change):
    plan, _ = forward_plan
    if change == 'web':
        plan['web'] = 'other'
    elif change == 'pending':
        plan['pending'] = ['V232', 'V233']
    elif change == 'stage':
        plan['agent_stage'] = {}
    elif change == 'configuration':
        plan['prepared_configuration_delta'] = {}
    elif change == 'source':
        plan['source_schema'] = 'V202'
    elif change == 'target':
        plan['target_schema'] = 'V234'
    else:
        plan['backend'] = 'r3'
    with pytest.raises(forward.Refused):
        forward.inputs('plan')


def test_initial_v202_to_v232_keeps_new_web_requirement(forward_plan):
    plan, docs = forward_plan
    plan.update(source_schema='V202', target_schema='V232', pending=['V232'], web='new-web',
                expected_ledger={'checksums': {'BASELINE_V1': 'd' * 64, 'V202': 'a' * 64}, 'registered': ['V202']})
    docs['migration'] = [{'key': 'BASELINE_V1', 'sha256': 'd' * 64},
                         {'key': 'V202', 'sha256': 'a' * 64}, {'key': 'V232', 'sha256': 'b' * 64}]
    assert forward.inputs('plan')[0] == plan
    plan['web'] = plan['expected_web']
    with pytest.raises(forward.Refused):
        forward.inputs('plan')


@pytest.mark.parametrize('web_drift', [False, True])
def test_v233_forward_preserves_live_web_pointers_and_agent_configuration(tmp_path, monkeypatch, web_drift):
    from tests.test_release_bundle_control import FixtureHost

    host = FixtureHost(tmp_path)
    host.db['schema'] = 'V232'
    old_evidence = dict(status='passed', tested_mini_versions=['1.1.1'],
                        tested_source_sha256=host.artifacts('old', 'old')['backend']['sha256'])
    old = forward.original.make_bundle(host, 'old', 'old', 'old', '1.2.0', ['1.1.1'], old_evidence)
    host.record_current(old)
    current = {'bundle_id': 'old'}
    raw = dict(registered=['V232'], checksums={'BASELINE_V1': 'd' * 64, 'V232': 'a' * 64}, running_jobs=0)
    host.database_raw = lambda: deepcopy(raw)
    candidate = host.artifacts('new', 'old')
    if web_drift:
        candidate['web']['sha256'] = '0' * 64
    plan = dict(bundle_id='r4', backend='new', web='old', expected_backend='old', expected_web='old',
                target_schema='V233', product_version='1.2.0', candidate=candidate,
                expected_database=host.database(), expected_current_bundle=current,
                active_mini_versions=['1.1.1'], supported_mini_versions=['1.1.1'], expected_ledger=deepcopy(raw))
    migration = [dict(key=k, sha256=v) for k, v in raw['checksums'].items()]
    migration.append(dict(key='V233', sha256='b' * 64))
    evidence = {**old_evidence, 'tested_source_sha256': candidate['backend']['sha256']}
    monkeypatch.setattr(forward, 'inputs', lambda ref: (plan, migration, evidence))
    monkeypatch.setattr(forward, 'source_plan', lambda host, plan: None)
    monkeypatch.setattr(forward, 'peer_access', lambda host, plan, owned: None)
    stamps = (forward.pointer_stamp(host.web), (host.web / 'previous').lstat().st_ino)
    configuration = host.configuration()

    def database_phase(host, ref, folder):
        raw['registered'].append('V233')
        raw['checksums']['V233'] = 'b' * 64
        host.db.update(schema='V233', ledger_sha256='b' * 64)
        return dict(returncode=0, result=dict(success=True, closed=True, errors=[]))

    if web_drift:
        with pytest.raises(forward.Refused, match='preserve registered Web'):
            forward.forward(host, {}, database_phase=database_phase)
        assert 'stop' not in host.events
    else:
        result = forward.forward(host, {}, database_phase=database_phase)
        assert result['success'] and result['phase'] == 'complete'
        assert forward.original.pointer(host.root) == 'new'
        assert json.loads((host.state / 'CURRENT.json').read_text()) == {'bundle_id': 'r4'}
        assert result['registered_bundle']['database']['schema'] == 'V233'
    assert stamps == (forward.pointer_stamp(host.web), (host.web / 'previous').lstat().st_ino)
    assert host.configuration() == configuration
