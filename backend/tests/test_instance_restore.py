"""Detached recovery boundaries, without opening any application database."""
import importlib.util
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools/installation'))
from instance_backup import digest, validate_contents
from instance_files import InstanceError, atomic_json, no_pending_upgrade, read_private, source_check
from instance_restore import (mapped_security, material_destination, trusted_backup, verify_bound_rows,
                              prepare_files, verify_recovery_audit, bound_rows, revoke_source_sessions,
                              verify_source_sessions_revoked, build_database)
import instance_restore
from release_bundle import json_bytes

spec = importlib.util.spec_from_file_location('restore_backup_fixture',Path(__file__).with_name('test_instance_backup.py'))
fixtures = importlib.util.module_from_spec(spec); spec.loader.exec_module(fixtures)
source, prepared, saved = fixtures.source, fixtures.prepared, fixtures.saved


def test_detached_manifest_does_not_need_the_source_instance(saved,tmp_path):
    import shutil
    root,directory = saved
    detached = tmp_path.resolve()/'detached'; shutil.copytree(directory,detached)
    root.rename(root.with_name('unavailable-source'))
    manifest,metadata = validate_contents(detached)
    assert metadata['root'] == str(root) and not root.exists()
    assert manifest['instance_id'] == metadata['instance_id']


def test_trusted_digest_is_required_before_any_restore_work(saved):
    _,directory = saved
    with pytest.raises(InstanceError,match='trusted digest'): trusted_backup(directory,'f'*64)
    with pytest.raises(InstanceError,match='complete restore evidence'): trusted_backup(directory,digest(directory/'BACKUP.json'))


@pytest.mark.parametrize('value',['relative.txt','/old/materials/../escape','/old/materials-else/file','/else/file'])
def test_material_rebinding_rejects_unrelated_or_traversing_paths(value,tmp_path):
    with pytest.raises(InstanceError): material_destination('/old',value,tmp_path)


def test_material_rebinding_preserves_nested_reserved_looking_names(tmp_path):
    assert material_destination('/old','/old/materials/nested/BACKUP.json',tmp_path) == tmp_path/'materials/nested/BACKUP.json'


def test_security_mapping_is_structured_and_never_rewrites_predicates_or_role_substrings():
    source = {'database':{'administrator':'old_owner'},'roles':{'api':'old_api'}}
    target = {'database':{'administrator':'new_owner'},'roles':{'api':'new_api'}}
    original = {'tables':[{'owner':'old_owner','acl':['old_api=r/old_owner','=r/old_owner','old_api_extra=r/old_owner']}],
                'policies':[{'roles':['public','old_api'],'qual':"name='old_api'"}],
                'schemas':[{'owner':'pg_database_owner','acl':[]}],
                'defaults':[{'owner':'old_owner','acl':['old_owner=X/old_owner']}]}
    mapped = mapped_security(original,source,target)
    assert mapped['tables'][0] == {'owner':'new_owner','acl':['=r/new_owner','new_api=r/new_owner','old_api_extra=r/new_owner']}
    assert mapped['policies'][0] == {'roles':['new_api','public'],'qual':"name='old_api'"}
    assert mapped['schemas'][0]['owner'] == 'pg_database_owner'
    assert original['tables'][0]['owner'] == 'old_owner'


def test_pending_recovery_blocks_normal_setup_and_service_paths(prepared):
    root = prepared.root
    atomic_json(root/'run/recovery.json',{'stage':'restoring_data'})
    with pytest.raises(InstanceError,match='recovery is pending'): no_pending_upgrade(root)


def test_recovery_marker_blocks_even_historical_strict_source_checks(prepared):
    root = prepared.root
    data = read_private(root/'INSTANCE.json')
    atomic_json(root/'release/RECOVERY_PENDING',{'instance_id':data['instance_id']})
    with pytest.raises(InstanceError,match='Installed source differs'): source_check(root)
    assert source_check(root,recovery=True)[0] == data


def test_prepared_recovery_never_accepts_changed_bootstrap_passwords(saved):
    root,directory = saved
    data = read_private(root/'INSTANCE.json')
    bootstrap = read_private(root/'private/bootstrap.json')
    bootstrap['database_passwords']['api'] = 'arbitrary-local-change'
    atomic_json(root/'private/bootstrap.json',bootstrap)
    with pytest.raises(InstanceError,match='private configuration was changed'):
        prepare_files(root,{'original_metadata':data},directory,data)


@pytest.mark.parametrize('field',['filename','file_size','workspace_id'])
def test_rebinding_does_not_allow_whole_original_rows_to_change(field):
    before = {'activity.visit_import':{'one':{'filename':'original','file_size':4,'workspace_id':'alpha',
                                          'storage_key':'/old','file_path':'/old','updated_at':'old'}}}
    after = {'activity.visit_import':{'one':{**before['activity.visit_import']['one'],
                                         'storage_key':'/new','file_path':'/new','updated_at':'new',field:'changed'}}}
    with pytest.raises(InstanceError,match='unapproved binding field'):
        verify_bound_rows(before,after,{'one':'/new'},{})


def test_rebinding_allows_only_known_paths_and_existing_update_timestamp():
    before = {'activity.visit_import':{'one':{'filename':'original','storage_key':'/old','file_path':'/old','updated_at':'old'}}}
    after = {'activity.visit_import':{'one':{'filename':'original','storage_key':'/new','file_path':'/new','updated_at':'new'}}}
    verify_bound_rows(before,after,{'one':'/new'},{})


def test_revocation_keeps_existing_session_identity_and_never_deletes_rows():
    before = {'control.session':{'hash':{'operator_id':'original','revoked_at':None}}}
    after = {'control.session':{'hash':{'operator_id':'original','revoked_at':'now'}}}
    verify_bound_rows(before,after,{}, {})
    with pytest.raises(InstanceError,match='lost a binding row'):
        verify_bound_rows(before,{'control.session':{}},{},{})


@pytest.fixture
def closed_sessions():
    return {key:{'id':key,'token_hash':str(index)*64,'workspace_id':'company',
        'user_id':'administrator','credential_fingerprint':'f'*64,
        'created_at':'2026-09-27T10:00:00+00:00','expires_at':expires,'revoked_at':revoked}
        for index,(key,expires,revoked) in enumerate((
            ('live','2026-09-27T10:15:00+00:00',None),
            ('expired','2026-09-27T10:01:00+00:00',None),
            ('revoked','2026-09-27T10:15:00+00:00','2026-09-27T10:00:30+00:00')),start=1)}


def test_closed_session_rebinding_only_revokes_and_preserves_already_revoked_time(closed_sessions):
    before = {'security.closed_export_session':closed_sessions}
    after = copy.deepcopy(before)
    for row in after['security.closed_export_session'].values():
        if row['revoked_at'] is None: row['revoked_at'] = '2026-09-27T10:02:00+00:00'
    verify_bound_rows(before,after,{},{})
    verify_bound_rows(after,copy.deepcopy(after),{},{})


@pytest.mark.parametrize('mutation',['id','token_hash','workspace_id','user_id','credential_fingerprint',
    'created_at','expires_at','extra_field','dropped_field','deleted_row','added_row',
    'rewritten_revocation','revived','live_not_revoked','expired_not_revoked'])
def test_closed_session_rebinding_refuses_loss_or_unapproved_change(closed_sessions,mutation):
    before = {'security.closed_export_session':closed_sessions}
    after = copy.deepcopy(before)
    rows = after['security.closed_export_session']
    rows['live']['revoked_at'] = rows['expired']['revoked_at'] = '2026-09-27T10:02:00+00:00'
    if mutation == 'deleted_row': del rows['live']
    elif mutation == 'added_row': rows['extra'] = {**rows['live'],'id':'extra'}
    elif mutation == 'dropped_field': del rows['live']['token_hash']
    elif mutation == 'rewritten_revocation': rows['revoked']['revoked_at'] = '2026-09-27T10:03:00+00:00'
    elif mutation == 'revived': rows['revoked']['revoked_at'] = None
    elif mutation == 'live_not_revoked': rows['live']['revoked_at'] = None
    elif mutation == 'expired_not_revoked': rows['expired']['revoked_at'] = None
    else: rows['live'][mutation] = 'changed'
    with pytest.raises(InstanceError): verify_bound_rows(before,after,{},{})


@pytest.mark.parametrize('status,revoked_at',[('active',None),('expired',None),('revoked',None),
    ('active','previous'),('revoked','previous')])
def test_business_session_revocation_keeps_an_existing_timestamp(status,revoked_at):
    before = {'platform.auth_session':{'one':{'id':'one','status':status,'revoked_at':revoked_at}}}
    after = {'platform.auth_session':{'one':{'id':'one','status':'revoked','revoked_at':revoked_at or 'now'}}}
    verify_bound_rows(before,after,{},{})
    if revoked_at:
        after['platform.auth_session']['one']['revoked_at'] = 'rewritten'
        with pytest.raises(InstanceError,match='unapproved binding field'): verify_bound_rows(before,after,{},{})


@pytest.mark.asyncio
@pytest.mark.parametrize('closed_table_present',[False,True])
async def test_session_tables_are_discovered_without_reading_absent_legacy_table(closed_sessions,closed_table_present):
    conn = MagicMock()
    conn.fetchval = AsyncMock(side_effect=lambda sql: closed_table_present if 'to_regclass' in sql else False)
    conn.execute = AsyncMock()
    async def fetch(sql):
        if 'FROM security.closed_export_session t' in sql:
            assert closed_table_present
            return [{'key':key,'body':json.dumps(row)} for key,row in closed_sessions.items()]
        return []
    conn.fetch = AsyncMock(side_effect=fetch)
    rows = await bound_rows(conn)
    assert ('security.closed_export_session' in rows) == closed_table_present
    if closed_table_present: assert rows['security.closed_export_session'] == closed_sessions
    await revoke_source_sessions(conn)
    await verify_source_sessions_revoked(conn)
    updates = [call.args[0] for call in conn.execute.call_args_list]
    checks = [call.args[0] for call in conn.fetchval.call_args_list if 'to_regclass' not in call.args[0]]
    expected_tables = ['platform.auth_session','control.session']
    if closed_table_present: expected_tables.append('security.closed_export_session')
    assert len(updates) == len(checks) == len(expected_tables)
    for table,update,check in zip(expected_tables,updates,checks,strict=True):
        assert update.startswith('UPDATE '+table+' SET ')
        assert 'COALESCE(revoked_at,clock_timestamp())' in update
        assert check.startswith('SELECT EXISTS(SELECT 1 FROM '+table+' WHERE ')
        assert 'expires_at' not in update+check
    assert all('DELETE' not in sql and 'TRUNCATE' not in sql for sql in updates)


@pytest.mark.asyncio
@pytest.mark.parametrize('closed_table_present,pending_table',[(False,None),(True,None),
    (False,'platform.auth_session'),(False,'control.session'),(True,'security.closed_export_session')])
async def test_committed_resume_rechecks_all_source_sessions_without_changing_database(
        tmp_path,monkeypatch,closed_table_present,pending_table):
    """A matching old commit hash is insufficient when source cookies survived."""
    data = {'instance_id':'1'*32,'database':{'name':'isolated_recovery'},'roles':{}}
    snapshot = {'tables':{'synthetic':{'rows':3}}}
    state = {'stage':'committing','post_inventory_sha256':hashlib.sha256(json_bytes(snapshot)).hexdigest()}
    admin = MagicMock()
    admin.fetchval = AsyncMock(side_effect=[True,'160004'])
    admin.fetchrow = AsyncMock(return_value={'marker':'salesbuddy-instance-v1:'+data['instance_id']})
    admin.execute = AsyncMock(); admin.close = AsyncMock()
    conn = MagicMock()
    conn.transaction.return_value = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={'instance_id':data['instance_id'],'metadata_digest':'digest'})
    conn.fetchval = AsyncMock(side_effect=lambda sql: closed_table_present if 'to_regclass' in sql
        else pending_table is not None and ('FROM '+pending_table+' WHERE ') in sql)
    conn.execute = AsyncMock(); conn.close = AsyncMock()
    monkeypatch.setattr(instance_restore.asyncpg,'connect',AsyncMock(side_effect=[admin,conn]))
    monkeypatch.setattr(instance_restore,'database_config',lambda *args,**kwargs:{})
    monkeypatch.setattr(instance_restore,'metadata_digest',lambda data:'digest')
    monkeypatch.setattr(instance_restore,'inventory',AsyncMock(return_value=snapshot))
    monkeypatch.setattr(instance_restore,'verify',AsyncMock())
    monkeypatch.setattr(instance_restore,'verify_sequence_positions',AsyncMock())
    monkeypatch.setattr(instance_restore,'verify_keys',AsyncMock(return_value={}))
    monkeypatch.setattr(instance_restore,'originals',AsyncMock(return_value=[]))
    monkeypatch.setattr(instance_restore,'material_tree',lambda root:{})
    remove_partial = AsyncMock(); monkeypatch.setattr(instance_restore,'remove_partial',remove_partial)
    save_journal = MagicMock(); monkeypatch.setattr(instance_restore,'atomic_json',save_journal)
    args = (tmp_path,tmp_path,{'keys_checked':{},'originals':[]},{},{},data,state,tmp_path)
    if pending_table:
        with pytest.raises(InstanceError,match='source sessions were not revoked'):
            await build_database(*args)
        assert state['last_attempt_failed'] is True and state['stage'] == 'committing'
        save_journal.assert_called_once_with(tmp_path/'run/recovery.json',state)
        instance_restore.verify.assert_not_awaited()
    else:
        await build_database(*args)
        save_journal.assert_not_called()
        instance_restore.verify.assert_awaited_once()
    remove_partial.assert_not_awaited()
    conn.execute.assert_not_awaited()
    assert len(admin.execute.await_args_list) == 1
    assert admin.execute.await_args.args[0].startswith('SELECT pg_advisory_lock')
    conn.close.assert_awaited_once(); admin.close.assert_awaited_once()


@pytest.fixture
def recovery_audit():
    import copy
    expected = {'one':{'workspace_id':'alpha','actor_user_ref_id':None,'actor_role_code':'system',
        'action_code':'activity.visit_import.update','module_code':'activity','object_type':'visit_import',
        'object_id':'one','object_label':'visit_import','request_id':'this-recovery','client_ip':None,
        'user_agent':'SalesBuddy instance recovery','before_snapshot':{'storage_key':'/old','api_key':'[REDACTED]'},
        'after_snapshot':{'storage_key':'/new','api_key':'[REDACTED]'},'changed_fields':['storage_key'],
        'result_code':'success','sensitivity':'internal','actor_name_snapshot':None,'actor_team_snapshot':None,
        'execution_kind':'system','transaction_id':1024}}
    before = {'10':{'id':10,'historical_content':'must survive unchanged'}}
    after = {**copy.deepcopy(before),'11':{**copy.deepcopy(expected['one']),
        'id':11,'occurred_at':'2026-09-27T10:00:01+00:00'}}
    return before,after,expected,datetime(2026,9,27,10,tzinfo=timezone.utc),datetime(2026,9,27,10,0,2,tzinfo=timezone.utc)


def test_recovery_audit_preserves_history_and_accepts_exact_system_path_receipt(recovery_audit):
    verify_recovery_audit(*recovery_audit)
    before,_,_,start,end = recovery_audit
    verify_recovery_audit(before,before,{},start,end)


@pytest.mark.parametrize('mutation',['history_changed','history_deleted','missing','extra','duplicate',
    'wrong_request','wrong_transaction','wrong_actor','wrong_execution','wrong_before','wrong_after',
    'wrong_fields','early','late','no_timezone','reused_id','extra_field'])
def test_recovery_audit_refuses_any_unapproved_evidence(recovery_audit,mutation):
    before,after,expected,start,end = recovery_audit
    row = after['11']
    if mutation == 'history_changed': after['10']['historical_content'] = 'changed'
    elif mutation == 'history_deleted': del after['10']
    elif mutation == 'missing': del after['11']
    elif mutation in {'extra','duplicate'}:
        after['12'] = {**row,'id':12}
        if mutation == 'duplicate': expected['two'] = {**expected['one'],'object_id':'two'}
    elif mutation == 'wrong_request': row['request_id'] = 'other-recovery'
    elif mutation == 'wrong_transaction': row['transaction_id'] += 1
    elif mutation == 'wrong_actor': row['actor_user_ref_id'] = 'business-user'
    elif mutation == 'wrong_execution': row['execution_kind'] = 'human'
    elif mutation == 'wrong_before': row['before_snapshot']['storage_key'] = '/unrelated'
    elif mutation == 'wrong_after': row['after_snapshot']['api_key'] = 'not-redacted'
    elif mutation == 'wrong_fields': row['changed_fields'].append('workspace_id')
    elif mutation == 'early': row['occurred_at'] = '2026-09-27T09:59:59+00:00'
    elif mutation == 'late': row['occurred_at'] = '2026-09-27T10:00:03+00:00'
    elif mutation == 'no_timezone': row['occurred_at'] = '2026-09-27T10:00:01'
    elif mutation == 'reused_id': row['id'] = 9
    elif mutation == 'extra_field': row['unknown_field'] = 'unexpected'
    with pytest.raises(InstanceError,match='audit evidence'):
        verify_recovery_audit(before,after,expected,start,end)
