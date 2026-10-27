"""Focused exact-ledger, additive-ACL and atomic failure-boundary checks."""
import asyncio
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'backend/deploy'))
import release126_task_contract as contract
import release126_task_security as security
import release_forward_bundle as forward
from release_bundle_control import digest, Refused


def source_plan():
    spec = importlib.util.spec_from_file_location('_task_test_migrate', ROOT/'database/scripts/migrate.py')
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module)
    return [{'key': m.key, 'file': m.path.relative_to(ROOT/'database').as_posix(), 'sha256': m.checksum}
            for m in [module.item('BASELINE_V1', ROOT/'database/baseline/V001__current_schema.sql'),
                      module.item('S001', ROOT/'database/seeds/S001__system_configuration.sql'),
                      *(m for m in module.discover(ROOT/'database') if int(m.key[1:]) <= 286)]]


def baseline():
    plan = source_plan()
    registered = [f'V{i:03}' for i in range(1, 285) if i not in (14,205,206,207,208,247,248,249,251,252)]
    checksums = {r['key']:r['sha256'] for r in plan if r['key'] not in contract.PENDING}
    return plan, {'registered': registered, 'checksums': checksums, 'running_jobs': 0}


def release_plan():
    migrations, old = baseline()
    return migrations, {'product_version': '1.2.6', 'release_scope': contract.RELEASE_SCOPE,
        'pending': contract.PENDING, 'source_schema': 'V284', 'target_schema': 'V286',
        'expected_ledger': old, 'expected_database': {'name': 'sales_saas', 'schema': 'V284',
        'ledger_sha256': contract.BASE_COMPLETE_LEDGER}, 'candidate': {'backend': {'revision': 'a'*40}},
        'mini_source_revision': 'a'*40, 'active_mini_versions': contract.ACTIVE_MINI_VERSIONS,
        'supported_mini_versions': contract.ACTIVE_MINI_VERSIONS+['1.2.6','1.2.6+mini.'+'a'*40]}


def test_exact_current_source_and_complete_production_prefix():
    migrations, p = release_plan()
    contract.validate(p, migrations, digest=digest, require=forward.require)
    final = {r['key']:r['sha256'] for r in migrations}
    contract.validate_migrations(migrations, final, sorted(p['expected_ledger']['registered']+contract.PENDING),
        'V286','V286', [], digest=digest, require=forward.require)
    assert forward.knowledge_contract_for(p) is contract
    assert len(p['active_mini_versions']) == 18


@pytest.mark.parametrize('change', ['old_checksum','history','client','configuration','pending','version'])
def test_exact_contract_refuses_drift(change):
    migrations, p = release_plan(); p = deepcopy(p)
    if change == 'old_checksum': p['expected_ledger']['checksums']['BASELINE_V1'] = '0'*64
    elif change == 'history': p['expected_ledger']['registered'].remove('V001')
    elif change == 'client': p['active_mini_versions'].pop()
    elif change == 'configuration': p['prepared_configuration_delta'] = {'local_test': True}
    elif change == 'pending': p['pending'] = ['V285']
    elif change == 'version': p['product_version'] = '1.2.4'
    with pytest.raises(Refused): contract.validate(p, migrations, digest=digest, require=forward.require)


def acl_objects():
    def acl(role, privilege): return {'grantor':'postgres','grantee':role,'privilege':privilege,'grantable':False}
    objects = {'tables': {}, 'functions': {}}
    for table, roles in security.TABLE_GRANTS.items():
        objects['tables'][table] = {'owner':'postgres','kind':'r','rls':False,'force_rls':False,'columns':[{'name':n,'acl':None} for n in ('singleton','last_task_id','updated_at')],'policies':[],'triggers':[],
            'acl':[acl('postgres','SELECT')]+[acl(security.ROLES[role],privilege) for role, privileges in roles.items() for privilege in privileges]}
    for signature, roles in security.FUNCTION_GRANTS.items():
        objects['functions'][signature] = {'properties': {'owner':'postgres','definer':signature != 'workflow.task_execution_validate_definition_v2(jsonb,jsonb)',
            'settings':['search_path=pg_catalog']+(['app.workspace_id='] if signature in ('workflow.task_execution_coordination_status(uuid)','workflow.task_execution_coordination_recipients(uuid)') else [])},
            'acl':[acl('postgres','EXECUTE')]+[acl(security.ROLES[role],'EXECUTE') for role in roles]}
    objects['task_constraint']=[{'name':security.TASK_CONSTRAINT,'valid':True}]
    objects['task_index']=[{'owner':'sales_owner','table_owner':'sales_owner','acl':None,'valid':True,'ready':True,'unique':False}]
    return objects


def test_narrow_acl_rejects_public_feishu_and_private_helper_access():
    obj = acl_objects(); security.validate_new_acl(obj)
    for grantee in ('PUBLIC', security.ROLES['feishu']):
        changed = deepcopy(obj)
        changed['tables']['ops.task_coordination_scan_cursor']['acl'].append({'grantor':'postgres','grantee':grantee,'privilege':'SELECT','grantable':False})
        with pytest.raises(Refused): security.validate_new_acl(changed)
    changed = deepcopy(obj)
    changed['functions']['workflow.task_execution_adopt_v2(uuid,jsonb,uuid)']['acl'].append({'grantor':'postgres','grantee':security.ROLES['api'],'privilege':'EXECUTE','grantable':False})
    with pytest.raises(Refused): security.validate_new_acl(changed)
    assert len(security.GRANTS) == 2
    assert all('GRANT ALL' not in sql and 'sales_feishu' not in sql and 'sales_platform' not in sql for sql in security.GRANTS)


@pytest.mark.parametrize('changed_owner', ['postgres', None, ''])
def test_task_index_preserves_existing_table_owner(changed_owner):
    objects = acl_objects(); security.validate_new_acl(objects)
    objects['task_index'][0]['owner'] = changed_owner
    with pytest.raises(Refused): security.validate_new_acl(objects)
    objects['task_index'][0]['table_owner'] = changed_owner
    if not changed_owner:
        with pytest.raises(Refused): security.validate_new_acl(objects)


def test_restore_evidence_binds_independent_database_and_new_objects():
    objects = acl_objects()
    evidence = {'format':'salesbuddy-task-restore-rehearsal-v1','status':'passed',
        'source_database':{'database':'sales_saas','oid':18684},
        'restored_database':{'database':'synthetic_restore','oid':20000},
        'source_backup_sha256':'a'*64,'restored_from_production_backup':True,
        'source_schema':'V284','target_schema':'V286',
        'source_ledger_sha256':contract.BASE_COMPLETE_LEDGER,'target_ledger_sha256':contract.TARGET_COMPLETE_LEDGER,
        'migration_plan_sha256':contract.MIGRATION_PLAN_SHA,'new_objects_sha256':digest(objects),
        'security_preserved':True,'business_state_preserved':True,'effective_privileges_verified':True}
    contract.validate_restore_evidence(evidence, objects, digest=digest, require=forward.require)
    for delta in ({'restored_database':evidence['source_database']}, {'new_objects_sha256':'0'*64},
                  {'business_state_preserved':False}):
        with pytest.raises(Refused):
            contract.validate_restore_evidence({**evidence,**delta}, objects, digest=digest, require=forward.require)


@pytest.mark.parametrize('fail_at', [None, 'V286', 'grant', 'commit', 'business'])
def test_two_migrations_and_grants_have_one_commit(monkeypatch, fail_at):
    events = []; state = {'transaction':False}
    class Tx:
        async def start(self): state['transaction']=True; events.append('start')
        async def rollback(self): state['transaction']=False; events.append('rollback')
        async def commit(self):
            events.append('commit')
            if fail_at == 'commit': raise ConnectionError('commit response lost')
            state['transaction']=False
    class Conn:
        def is_in_transaction(self): return state['transaction']
        def transaction(self, **kwargs): return Tx()
        async def execute(self, sql): pass
    async def ledger(conn): return {'same':True}
    async def apply(conn, item, root):
        assert conn.is_in_transaction(); events.append(item.key)
        if fail_at == item.key: raise RuntimeError('migration failed')
        return 'applied'
    async def verify(conn, **kwargs): assert conn.is_in_transaction(); return {'ok':True}
    async def grant(conn, **kwargs):
        assert conn.is_in_transaction(); events.append('grant')
        if fail_at == 'grant': raise RuntimeError('grant failed')
        return {'ok':True}
    async def readonly(conn, **kwargs): assert not conn.is_in_transaction(); events.append('readonly'); return {'status':'verified_readonly'}
    async def business(conn):
        return {'state': 'changed' if fail_at == 'business' and 'grant' in events else 'same'}
    monkeypatch.setattr(forward,'ledger',ledger)
    monkeypatch.setattr(forward,'verify_ledger_rows',lambda *args:None)
    result = {'migrations':[],'errors':[]}; saved = []
    def save(): saved.append(deepcopy(result))
    args = (Conn(), {'pending':contract.PENDING,'acl_plan':{}}, [], {'same':True},
        SimpleNamespace(apply=apply), [SimpleNamespace(key=k,checksum=k) for k in contract.PENDING], ROOT,
        SimpleNamespace(verify_state=verify,grant_execute=grant,verify_release_permissions=readonly,business_state=business),result,save)
    if fail_at:
        with pytest.raises((RuntimeError,ConnectionError,Refused)): asyncio.run(forward.apply_task_execution(*args))
        assert 'rollback' in events and 'readonly' not in events
        assert all(r['commit_outcome'] != 'acknowledged' for r in result['migrations'])
        assert events.count('commit') == (1 if fail_at == 'commit' else 0)
    else:
        asyncio.run(forward.apply_task_execution(*args))
        assert events == ['start','V285','V286','grant','commit','readonly']
        assert all(r['commit_outcome']=='acknowledged' for r in result['migrations'])
        assert any(s['phase']=='v286_commit' and all(r['commit_outcome']=='unknown' for r in s['migrations']) for s in saved)


@pytest.mark.parametrize('private_mode', [0o600, 0o644])
def test_new_object_receipt_is_checked_before_stopping_services(tmp_path, monkeypatch, private_mode):
    import json
    import os
    import hashlib
    def ref(name, value, mode=0o600):
        path = tmp_path/name; path.write_text(json.dumps(value)); path.chmod(mode)
        return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    receipt = ref('objects.json', {'format':'salesbuddy-task-new-objects-v1'}, private_mode)
    policy = ref('policy.json', {})
    restore = ref('restore.json', {'status':'passed'})
    acl = ref('acl.json', {'new_objects_ref':receipt,'restore_evidence_ref':restore,'policy_ref':policy,'source_files':{}})
    plan = ref('migrations.json', [])
    source = ref('source.json', {})
    p = {'release_scope':contract.RELEASE_SCOPE, 'backend':'candidate','acl_plan':acl,
         'migration_plan':plan, 'validation':source, 'acl_source':source}
    release = tmp_path/'release'; (release/'database/scripts').mkdir(parents=True)
    (release/'database/scripts/migrate.py').write_text('# fixed test source')
    owned = tmp_path/'owned'; owned.mkdir()
    monkeypatch.setattr(forward.pwd, 'getpwnam', lambda name:SimpleNamespace(pw_uid=os.getuid(),pw_gid=os.getgid()))
    monkeypatch.setattr(forward.os, 'chown', lambda *args:None)
    monkeypatch.setattr(forward.os, 'chmod', lambda *args:None)
    monkeypatch.setattr(forward.original,'release_path',lambda *args:release)
    captured = []
    def run(args):
        if '-c' in args:
            captured.extend(json.loads(args[-1])); return json.dumps({'status':'verified','count':len(captured)})
        return ''
    host = SimpleNamespace(root=tmp_path, run=run)
    if private_mode == 0o644:
        with pytest.raises(Refused, match='postgres-owned 0600'): forward.peer_access(host,p,owned)
        assert not captured
    else:
        forward.peer_access(host,p,owned)
        assert ('acl_new_objects', receipt) in [(name,value) for name,value in captured]
        assert ('acl_restore_evidence', restore) in [(name,value) for name,value in captured]
