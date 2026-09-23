"""Exercise forward/late runtime grants with production-style limited ACLs.

Only disposable databases and NOLOGIN/NOBYPASSRLS roles are created. In contrast
to broad integration fixtures, this never grants schema-wide table mutations or
DELETE: the migration must supply the two relationship DELETE privileges.
"""
import asyncio
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import uuid

import asyncpg

from verify_fde_postgres import context, fixture_before_upgrade

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from migrate import migrate


async def migrate_through(connection,version):
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory)/'database'
        shutil.copytree(ROOT,root)
        for path in (root/'migrations').glob('V*.sql'):
            if int(path.name.split('__',1)[0][1:])>version:
                path.unlink()
        await migrate(connection,root)


async def base_runtime_grants(connection,role):
    await connection.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
    for schema in ('platform','activity','workflow','crm','config','common','security','ops'):
        await connection.execute(f'GRANT USAGE ON SCHEMA {schema} TO "{role}"')
    # Exact relevant production-group shape: arw on visits/assignments, r on
    # configuration. No DELETE on any table, no broad credential/audit grants.
    await connection.execute(f'GRANT SELECT,INSERT,UPDATE ON activity.visit,workflow.task,workflow.task_assignee,workflow.task_candidate,workflow.task_event TO "{role}"')
    await connection.execute(f'GRANT SELECT ON config.rule_set,crm.customer,crm.opportunity,platform.user_ref,platform.team,platform.role_binding,platform.team_membership TO "{role}"')


async def denied(connection,query,*args):
    async with connection.transaction():
        try:
            await connection.execute(query,*args)
        except asyncpg.InsufficientPrivilegeError:
            return
        raise AssertionError('Expected table/function-level permission denial')


async def main():
    config={'host':os.environ.get('PGHOST','/tmp'),'user':os.environ.get('PGUSER','postgres')}
    admin=await asyncpg.connect(database='postgres',**config)
    suffix=uuid.uuid4().hex[:16]
    database='salegent_verify_acl_'+suffix
    runtime='salegent_acl_runtime_'+suffix
    late='salegent_acl_late_'+suffix
    reader='salegent_acl_reader_'+suffix
    connection=None
    checks=[]
    try:
        await admin.execute(f'CREATE DATABASE "{database}" TEMPLATE template0')
        connection=await asyncpg.connect(database=database,**config)
        await migrate_through(connection,68)
        workspace,team,sales,customer,visit,form=await fixture_before_upgrade(connection)
        await migrate_through(connection,69)
        await base_runtime_grants(connection,runtime)
        # Mirror V069's existing live normalized-table grant, which inherited arw.
        await connection.execute(f'GRANT SELECT,INSERT,UPDATE ON activity.visit_participant TO "{runtime}"')
        await connection.execute(f'CREATE ROLE "{reader}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        await connection.execute(f'GRANT USAGE ON SCHEMA activity TO "{reader}"')
        await connection.execute(f'GRANT SELECT ON activity.visit TO "{reader}"')
        for table in ('workflow.task_assignee','activity.visit_participant'):
            assert not await connection.fetchval('SELECT has_table_privilege($1,$2,\'DELETE\')',runtime,table)
        await connection.execute('SET row_security=on')
        await connection.execute(f'SET ROLE "{runtime}"')
        await denied(connection,'DELETE FROM workflow.task_assignee WHERE false')
        await denied(connection,'DELETE FROM activity.visit_participant WHERE false')
        await connection.execute('RESET ROLE')
        checks.append('production_arw_acl_reproduces_both_denials_even_for_zero_row_delete')
        await migrate(connection)
        for table in ('workflow.task_assignee','activity.visit_participant'):
            assert await connection.fetchval('SELECT has_table_privilege($1,$2,\'DELETE\')',runtime,table)
            assert not await connection.fetchval('SELECT has_table_privilege($1,$2,\'DELETE\')',reader,table)
        assert await connection.fetchval('SELECT has_table_privilege($1,\'activity.visit_participant\',\'SELECT\')',reader)
        assert await connection.fetchval('SELECT has_table_privilege($1,\'activity.v_visit_collaborators\',\'SELECT\')',reader)
        for function in ('security.fde_visit_entry_enabled()','security.fde_permission_version()',
                         'security.fde_participation_history()','security.bump_fde_membership_version(uuid,integer)',
                         'security.save_company_rule(text,text,jsonb,text,uuid,integer,uuid,uuid)'):
            assert await connection.fetchval('SELECT has_function_privilege($1,$2,\'EXECUTE\')',runtime,function)
        for table,privilege in (('platform.password_credential','SELECT'),('ops.audit_log','DELETE'),
                                ('workflow.task','DELETE'),('crm.opportunity','DELETE')):
            assert not await connection.fetchval('SELECT has_table_privilege($1,$2,$3)',runtime,table,privilege)
        assert not await connection.fetchval('SELECT has_function_privilege($1,\'security.reconcile_runtime_grants()\',\'EXECUTE\')',runtime)
        checks.append('forward_migration_repairs_relationship_grants_without_business_deletes_or_credentials')
        other,own_task,unrelated_task=[uuid.uuid4() for _ in range(3)]
        await connection.execute("INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name) VALUES($1,$2,$3,'OTHER','Another fixture')",other,workspace,str(other))
        for task,creator in ((own_task,sales),(unrelated_task,other)):
            await connection.execute("INSERT INTO workflow.task(id,workspace_id,title,description,creator_user_ref_id,creator_team_id,due_at) VALUES($1,$2,'ACL fixture','ACL fixture',$3,$4,clock_timestamp()+interval '1 day')",task,workspace,creator,team)
            await connection.execute("INSERT INTO workflow.task_assignee(task_id,workspace_id,assignee_user_ref_id,assignee_team_id,assignee_role) VALUES($1,$2,$3,$4,'sales')",task,workspace,creator,team)
        await connection.execute(f'SET ROLE "{runtime}"')
        await context(connection,workspace,sales,'sales',team)
        assert await connection.execute('DELETE FROM workflow.task_assignee WHERE task_id=$1',own_task)=='DELETE 1'
        assert await connection.execute('DELETE FROM workflow.task_assignee WHERE task_id=$1',unrelated_task)=='DELETE 0'
        assert await connection.execute('DELETE FROM activity.visit_participant WHERE visit_id=$1',visit)=='DELETE 1'
        await denied(connection,'SELECT security.reconcile_runtime_grants()')
        await connection.execute('RESET ROLE')
        assert await connection.fetchval('SELECT count(*) FROM workflow.task_assignee WHERE task_id=$1',unrelated_task)==1
        checks.append('native_rls_allows_real_replacement_and_rejects_deleting_someone_elses_task_owner')
        # Empty installation may finish before the application role is created.
        # A later deployment must repair it even though every version is unchanged.
        await base_runtime_grants(connection,late)
        assert not await connection.fetchval('SELECT has_table_privilege($1,\'activity.visit_participant\',\'SELECT\')',late)
        assert all(step['status']=='unchanged' for step in await migrate(connection))
        for table in ('activity.visit_participant','workflow.task_assignee'):
            for privilege in ('SELECT','INSERT','UPDATE','DELETE'):
                assert await connection.fetchval('SELECT has_table_privilege($1,$2,$3)',late,table,privilege)
        assert await connection.fetchval('SELECT has_table_privilege($1,\'activity.v_visit_collaborators\',\'SELECT\')',late)
        assert all(step['status']=='unchanged' for step in await migrate(connection))
        checks.append('late_runtime_role_is_reconciled_by_repeat_deployment_without_migration_replay')
        login_helpers = ('security.login_limit_status(text,integer)',
                         'security.account_login_status(uuid)',
                         'security.unlock_account_login(uuid,text)')
        # V105 preserves existing applications' effective EXECUTE grants while
        # removing PUBLIC access to SECURITY DEFINER functions. A role created
        # after V105 needs an explicit, reviewed function allowlist; V070's
        # relationship-table reconciliation must not grant security helpers.
        for function in login_helpers:
            assert not await connection.fetchval(
                'SELECT has_function_privilege($1,$2,\'EXECUTE\')',late,function)
            await connection.execute(f'GRANT EXECUTE ON FUNCTION {function} TO "{late}"')
            assert not await connection.fetchval(
                "SELECT has_function_privilege('salegent_feishu_worker',$1,'EXECUTE')",function)
        checks.append('post_v105_late_role_requires_explicit_login_helper_allowlist_without_worker_access')
        for app_role in (runtime,late):
            for function in login_helpers:
                assert await connection.fetchval('SELECT has_function_privilege($1,$2,\'EXECUTE\')',app_role,function)
            for privilege in ('SELECT','INSERT','UPDATE','DELETE'):
                assert not await connection.fetchval('SELECT has_table_privilege($1,\'security.login_throttle\',$2)',app_role,privilege)
        await connection.execute(f'SET ROLE "{late}"')
        await context(connection,workspace,sales,'sales',team)
        assert await connection.fetchval("SELECT retry_after_seconds FROM security.login_limit_status(repeat('a',64),5)")==0
        await denied(connection,'SELECT security.unlock_account_login($1,\'拒绝越权\')',sales)
        await denied(connection,'SELECT * FROM security.account_login_status($1)',sales)
        await denied(connection,'DELETE FROM security.login_throttle WHERE false')
        await connection.execute('RESET ROLE')
        checks.append('account_limit_helpers_work_for_existing_and_late_roles_without_private_table_grants')
        print(json.dumps({'checks':checks,'passed':len(checks)},ensure_ascii=False))
    finally:
        if connection:
            await connection.execute('RESET ROLE')
            await connection.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
        for role in (runtime,late,reader):
            await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        await admin.close()


if __name__=='__main__':
    asyncio.run(main())
