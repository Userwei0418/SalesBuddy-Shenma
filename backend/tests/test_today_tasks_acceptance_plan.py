"""Operational authorization and bounded rollout cannot expand the live task test."""

import copy
import importlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest


@pytest.fixture
def modules(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / 'scripts' / 'acceptance'))
    return importlib.import_module('today_tasks_business_live'), importlib.import_module('today_tasks_operator')


@pytest.fixture
def plan(modules):
    live, _ = modules
    expires = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    return {
        'account': 'XS001', 'actor_id': live.ACTOR, 'workspace_id': live.WORKSPACE,
        'maximum_new_tasks_if_auto_materialized': 4, 'approval_status': 'approved',
        'business_rule': 'auto_own_pending_execution_backend_future_due',
        'publication_api_live_enable_authorized': True,
        'cases': [{
            'scenario': scenario, 'customer_name': '【演示】' + scenario,
            'expected_candidate_count': 1, 'new_task_limit_if_auto_materialized': 1,
            'scope_file_content': {live.WORKSPACE: {live.ACTOR: {
                'approval_status': 'approved', 'expires_at': expires,
                'sources': {f'10000000-0000-0000-0000-{index:012d}': 'a' * 64},
            }}},
        } for index, scenario in enumerate(live.SCENARIOS, start=1)],
    }


def test_rule_confirmation_is_not_publication_or_live_write_approval(modules, plan):
    live, _ = modules
    plan['approval_status'] = 'pending'
    plan['publication_api_live_enable_authorized'] = False
    assert live.select_scope(plan, 'normal', live=False).source_ids
    with pytest.raises(AssertionError):
        live.select_scope(plan, 'normal', live=True)
    plan['approval_status'] = 'approved'
    with pytest.raises(AssertionError):
        live.select_scope(plan, 'normal', live=True)


@pytest.mark.parametrize('change', ['wrong_actor', 'expanded_source', 'duplicate_source', 'expired', 'real_customer'])
def test_invalid_live_plan_rejected_before_session(modules, plan, change):
    live, _ = modules
    first = plan['cases'][0]['scope_file_content'][live.WORKSPACE][live.ACTOR]
    if change == 'wrong_actor':
        plan['actor_id'] = '02000000-0000-0000-0000-000000000005'
    elif change == 'expanded_source':
        first['sources']['10000000-0000-0000-0000-000000000009'] = 'b' * 64
    elif change == 'duplicate_source':
        plan['cases'][1]['scope_file_content'][live.WORKSPACE][live.ACTOR]['sources'] = first['sources'].copy()
    elif change == 'expired':
        first['expires_at'] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    else:
        plan['cases'][0]['customer_name'] = '未标记真实客户'
    with pytest.raises(AssertionError):
        live.select_scope(plan, 'normal', live=True)


def test_each_mode_preserves_other_agents_and_same_scope_even_when_off(modules, plan):
    live, operator = modules
    previous = {live.WORKSPACE: {'capabilities': {
        name: {'enabled': True, 'rollout': 'production'} for name in operator.PRIOR
    }}}
    original = copy.deepcopy(previous)
    for mode in live.SCENARIOS:
        config, scope = operator.configuration(plan, mode, previous)
        caps = config[live.WORKSPACE]['capabilities']
        assert {name: caps[name] for name in operator.PRIOR} == original[live.WORKSPACE]['capabilities']
        assert scope[live.WORKSPACE][live.ACTOR]['sources'] == dict(live.select_scope(plan, mode, live=True).sources)
        assert caps['today_tasks']['enabled'] == (mode != 'off')
        assert caps['today_tasks'].get('block_platform_requests', False) == (mode == 'blocked')
        if mode == 'production':
            assert caps['today_tasks'] == {'enabled': True, 'rollout': 'production'}
        assert previous == original
    released, scope = operator.configuration(plan, 'release', config)
    assert released == config
    assert scope[live.WORKSPACE][live.ACTOR]['enabled'] is False


def test_do_not_replace_changed_existing_rollout(modules, plan):
    live, operator = modules
    previous = {live.WORKSPACE: {'capabilities': {
        name: {'enabled': True, 'rollout': 'production'} for name in operator.PRIOR
    }}}
    previous[live.WORKSPACE]['capabilities']['visit_entry']['enabled'] = False
    with pytest.raises(AssertionError):
        operator.configuration(plan, 'normal', previous)
