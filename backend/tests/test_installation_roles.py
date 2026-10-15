import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/installation'))
from runtime_roles import RolePolicyError, fingerprint, identifier, load_policy


def test_committed_policy_separates_service_authority():
    p = load_policy()['services']
    assert len(p) == 4
    assert 'ops.claim_job(text,integer,text[],boolean)' not in p['api']['functions']
    assert 'ops.claim_job(text,integer,text[],boolean)' in p['worker']['functions']
    assert 'security.password_candidate(text,text,text)' in p['api']['functions']
    assert 'security.password_candidate(text,text,text)' not in p['worker']['functions']
    assert not p['platform']['tables']
    assert p['platform']['schemas'] == ['control']
    for service in ('api', 'worker', 'platform'):
        assert 'security.feishu_credential' not in p[service]['tables']
        assert 'ops.feishu_source(uuid,text,uuid)' not in p[service]['functions']
    for service in p.values():
        assert 'platform.password_credential' not in service['tables']
        assert 'ops.migration_checksum' not in service['tables']
        assert not any('reconcile_runtime_grants' in f for f in service['functions'])


def test_inference_receipts_finish_through_narrow_function_without_direct_mutation():
    services = load_policy()['services']
    finish = 'security.finish_inference_operation(uuid,text,jsonb,jsonb,text)'
    for service in ('api', 'worker'):
        assert finish in services[service]['functions']
        assert 'INSERT' in services[service]['tables']['agent.inference_operation']
        assert not {'UPDATE', 'DELETE'} & set(services[service]['tables']['agent.inference_operation'])
    assert 'SELECT' not in services['api']['tables']['agent.inference_operation']
    for service in ('platform', 'feishu'):
        assert finish not in services[service]['functions']


def test_task_import_reparse_is_limited_to_api_without_private_projection_access():
    services = load_policy()['services']
    reparse = 'security.reparse_task_import_batch(uuid,integer,jsonb,jsonb,jsonb)'
    assert reparse in services['api']['functions']
    for name, policy in services.items():
        if name != 'api':
            assert reparse not in policy['functions']
        assert not any('task_import_editable_projection' in function for function in policy['functions'])


@pytest.mark.parametrize('value', ['', 'pg_admin;', 'role\n', 'Role', 'x'*64, 'a"b', '../role'])
def test_installation_identity_rejects_ambiguous_or_unsafe_names(value):
    with pytest.raises(RolePolicyError):
        identifier(value)


@pytest.mark.parametrize('case', ['wildcard', 'function_sql', 'schema', 'inheritance', 'column', 'fingerprint'])
def test_policy_rejects_unreviewable_grants(tmp_path, case):
    policy = copy.deepcopy(load_policy())
    rules = policy['services']['api']
    if case == 'wildcard': rules['tables']['crm.customer'] = ['ALL']
    if case == 'function_sql': rules['functions'].append('security.foo(); SELECT 1')
    if case == 'schema': rules['schemas'].append('pg_catalog')
    if case == 'inheritance': rules['memberships'] = ['pg_read_all_data']
    if case == 'column': rules['columns'] = {'crm.customer': {'UPDATE': ['bad;name']}}
    if case == 'fingerprint': policy['migration_fingerprint'] = 'V166'
    path = tmp_path/'policy.json'; path.write_text(json.dumps(policy))
    with pytest.raises(RolePolicyError): load_policy(path)


def test_migration_policy_fingerprint_is_order_independent_but_content_bound():
    assert fingerprint({'V2':'b', 'V1':'a'}) == fingerprint({'V1':'a', 'V2':'b'})
    assert fingerprint({'V1':'a'}) != fingerprint({'V1':'b'})


def test_permission_contract_is_bound_to_complete_product_ledger():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'tools/installation'))
    from release_bundle import migration_manifest
    from runtime_roles import fingerprint, load_policy
    root=Path(__file__).resolve().parents[2]
    paths=[*(root/'database').glob('V*.sql'),*(root/'database/migrations').glob('V*.sql'),
           *(root/'database/seeds').glob('S*.sql'),root/'database/baseline/V001__current_schema.sql']
    ledger={row['key']:row['sha256'] for row in migration_manifest({p.relative_to(root).as_posix():p.read_bytes() for p in paths})}
    assert load_policy()['migration_fingerprint']==fingerprint(ledger)
    assert load_policy()['schema']==max(key for key in ledger if key.startswith('V'))
