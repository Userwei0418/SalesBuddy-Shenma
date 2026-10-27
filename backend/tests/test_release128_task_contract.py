"""Focused exact-ledger, explicit compatibility and restore-provenance checks."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend/deploy'))
import release128_task_contract as contract
import release128_task_security as security
from release_bundle_control import digest, Refused
from release_visit_index_security import require


def source_plan():
    spec=importlib.util.spec_from_file_location('_release128_unit_migrate',ROOT/'database/scripts/migrate.py')
    m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
    return [{'key':v.key,'file':v.path.relative_to(ROOT/'database').as_posix(),'sha256':v.checksum}
      for v in [m.item('BASELINE_V1',ROOT/'database/baseline/V001__current_schema.sql'),m.item('S001',ROOT/'database/seeds/S001__system_configuration.sql'),*m.discover(ROOT/'database')]]


def release_plan():
    migrations=source_plan()
    prior={v['key']:v['sha256'] for v in migrations if v['key'] not in contract.PENDING}
    registered=[f'V{i:03}' for i in range(1,287) if i not in (14,205,206,207,208,247,248,249,251,252)]
    return migrations, {'product_version':'1.2.8','release_scope':contract.RELEASE_SCOPE,
     'pending':contract.PENDING,'source_schema':'V286','target_schema':'V288',
     'expected_ledger':{'registered':registered,'checksums':prior,'running_jobs':0},
     'expected_database':{'name':'sales_saas','schema':'V286','ledger_sha256':contract.BASE_COMPLETE_LEDGER},
     'candidate':{'backend':{'revision':'a'*40}},'mini_source_revision':'b'*40,
     'active_mini_versions':contract.ACTIVE_MINI_VERSIONS,
     'supported_mini_versions':contract.ACTIVE_MINI_VERSIONS+['1.2.7','1.2.8','1.2.8+mini.'+'b'*40]}


def test_exact_complete_v286_to_v288_without_seeds():
    migrations,p=release_plan();contract.validate(p,migrations,digest=digest,require=require)
    contract.validate_migrations(migrations,{v['key']:v['sha256'] for v in migrations},
     p['expected_ledger']['registered']+contract.PENDING,'V288','V288',[],digest=digest,require=require)
    assert len(migrations)==247 and len(p['active_mini_versions'])==20
    assert set(contract.SQL)=={'V287','V288'}
    assert len([sig for sig,roles in security.FUNCTION_GRANTS.items() if sig.startswith('workflow.') and roles])==4
    assert not any('GRANT ALL' in sql or 'PASSWORD' in sql or 'INSERT' in sql for sql in security.GRANTS)


@pytest.mark.parametrize('change',['checksum','history','omit_v287','extra_client','missing_client','config','jobs','source','version','unknown_mini127'])
def test_reject_unapproved_drift(change):
    migrations,p=release_plan();p=deepcopy(p)
    if change=='checksum':p['expected_ledger']['checksums']['BASELINE_V1']='0'*64
    elif change=='history':p['expected_ledger']['registered'].remove('V001')
    elif change=='omit_v287':p['pending']=['V288']
    elif change=='extra_client':p['supported_mini_versions'].append('9.9.9')
    elif change=='missing_client':p['active_mini_versions'].pop()
    elif change=='config':p['prepared_configuration_delta']={'local':True}
    elif change=='jobs':p['expected_ledger']['running_jobs']=1
    elif change=='source':p['source_schema']='V287'
    elif change=='version':p['product_version']='1.2.7'
    elif change=='unknown_mini127':p['supported_mini_versions'].append('1.2.7+mini.'+'c'*40)
    with pytest.raises(Refused):contract.validate(p,migrations,digest=digest,require=require)


def evidence(objects):
    return {'format':'salesbuddy-release128-restore-rehearsal-v1','status':'passed',
     'source_database':{'database':'sales_saas','oid':18684},'restored_database':{'database':'independent_restore','oid':20000},
     'source_backup_sha256':'a'*64,'evidence_kind':'independent_production_restore','restored_from_production_backup':True,
     'source_schema':'V286','target_schema':'V288','source_ledger_sha256':contract.BASE_COMPLETE_LEDGER,
     'target_ledger_sha256':contract.TARGET_COMPLETE_LEDGER,'migration_plan_sha256':contract.MIGRATION_PLAN_SHA,
     'new_objects_sha256':digest(objects),'security_preserved':True,'business_state_preserved':True,'effective_privileges_verified':True}


def test_synthetic_receipt_never_substitutes_for_production_restore():
    objects={};e=evidence(objects)
    contract.validate_restore_evidence(e,objects,digest=digest,require=require)
    for delta in ({'evidence_kind':'synthetic'},{'restored_from_production_backup':False},
                  {'restored_database':e['source_database']},{'business_state_preserved':False},
                  {'new_objects_sha256':'0'*64}):
        with pytest.raises(Refused):contract.validate_restore_evidence({**e,**delta},objects,digest=digest,require=require)


def test_policy_unwrap_preserves_literals_and_rejects_widened_branch():
    original="((workspace_id = common.current_workspace_id()) AND\nCASE WHEN true THEN 'a  b' ELSE 'x' END = 'a  b')"
    wrapped="\nCASE\n WHEN (NULLIF(current_setting('app.demo_readonly_context'::text, true), ''::text) IS NOT NULL) THEN false\n ELSE "+original.replace('\nCASE','\n    CASE')+"\nEND"
    assert security.unwrap_policy(wrapped)==security.unwrap_policy(original)
    assert "'a  b'" in security.unwrap_policy(wrapped)
    assert security.unwrap_policy(wrapped.replace('THEN false','THEN true'))!=security.unwrap_policy(original)
