"""Fixed report migration inputs reject wrong targets before DB/host actions."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

DEPLOY = Path(__file__).resolve().parents[1] / 'deploy'
sys.path.insert(0, str(DEPLOY))
import release_forward_bundle as forward
import report_release_contract as contract

FIXTURE = json.loads((Path(__file__).with_name('fixtures') / 'report_release_contract.synthetic.json').read_text())


def acl_plan():
    return dict(release_scope=['work_report'], expected_registered_versions=sorted(
        [*FIXTURE['expected_ledger']['registered'], *contract.PENDING]))


def test_exact_new_combination_and_acl_target():
    plan = dict(release_scope=['work_report'], pending=contract.PENDING,
                expected_ledger=FIXTURE['expected_ledger'])
    contract.validate(plan, FIXTURE['migration_plan'], digest=forward.original.digest, require=forward.require)
    contract.validate_acl(acl_plan(), FIXTURE['report_policy'], digest=forward.original.digest, require=forward.require)


@pytest.mark.parametrize('schema', ['V233', 'V234'])
@pytest.mark.parametrize('entry', ['inputs', 'database_phase'])
def test_wrong_old_acl_rejected_before_any_connection_or_apply(tmp_path, monkeypatch, schema, entry):
    def ref(name, value):
        path = tmp_path / name
        path.write_text(json.dumps(value))
        return forward.file_ref(path)
    acl = acl_plan()
    acl['expected_registered_versions'] = sorted(FIXTURE['expected_ledger']['registered'] +
                                                 (['V234'] if schema == 'V234' else []))
    policy = {**FIXTURE['report_policy'], 'schema': schema}
    acl['policy_ref'] = ref('policy.json', policy)
    p = dict(product_version='1.2.0', source_schema='V233', target_schema='V238',
             release_scope=['work_report'], pending=contract.PENDING, expected_ledger=FIXTURE['expected_ledger'],
             expected_database={'name':'sales_saas'}, bundle_id='report-test', backend='new', web='new-web',
             expected_backend='r11', expected_web='f66', migration_plan=ref('migration.json', FIXTURE['migration_plan']),
             acl_plan=ref('acl.json', acl), acl_source=ref('acl.py', 'source placeholder'), validation=ref('validation.json', {}))
    plan_ref = ref('plan.json', p)
    def forbidden(*args, **kwargs):
        raise AssertionError('Wrong ACL reached DB/module/host effects')
    monkeypatch.setattr(forward, 'load', forbidden)
    import asyncpg
    monkeypatch.setattr(asyncpg, 'connect', forbidden)
    with pytest.raises(forward.Refused, match='exact V238 registered ledger'):
        if entry == 'inputs':
            forward.inputs(plan_ref)
        else:
            asyncio.run(forward.database_phase(plan_ref))


@pytest.mark.parametrize('field,value', [('schema','V234'), ('migration_fingerprint','0'*64), ('services',{})])
def test_acl_policy_cannot_be_substituted(field, value):
    policy = deepcopy(FIXTURE['report_policy']); policy[field] = value
    with pytest.raises(forward.Refused, match='exact V238 report permission policy'):
        contract.validate_acl(acl_plan(), policy, digest=forward.original.digest, require=forward.require)


def test_additional_finance_sql_is_rejected():
    plan = dict(release_scope=['work_report'], pending=contract.PENDING,
                expected_ledger=FIXTURE['expected_ledger'])
    migrations = FIXTURE['migration_plan'] + [dict(key='V234', file='unapproved.sql', sha256='0'*64)]
    with pytest.raises(forward.Refused, match='exclude V234/V239'):
        contract.validate(plan, migrations, digest=forward.original.digest, require=forward.require)


def test_old_r11_bundle_fails_database_guard_after_migration():
    old = dict(database={'ledger_sha256':contract.BASE_LEDGER}, configuration={}, supported_mini_versions=['1.2.0'])
    live = dict(database={'ledger_sha256':contract.TARGET_LEDGER}, configuration={})
    with pytest.raises(forward.original.Refused):
        forward.original.guard(old, live, ['1.2.0'])
