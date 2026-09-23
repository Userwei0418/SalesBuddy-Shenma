"""Verify the V068 -> V069/V071 historical contract in a disposable PostgreSQL DB.

Like verify_migrations_postgres.py, this uses only a local maintenance socket and
creates its own randomized database and NOLOGIN runtime role. It never reads a
business DATABASE_URL, credentials or a production database. The caller must have
CREATEDB/CREATEROLE; all generated fixtures and grants are removed in finally.
"""
import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import uuid

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from migrate import migrate

ROOT = Path(__file__).resolve().parents[1]


async def context(connection, workspace, user, role, team=None):
    await connection.execute(
        "SELECT set_config('app.workspace_id',$1,false),set_config('app.user_ref_id',$2,false),"
        "set_config('app.role_code',$3,false),set_config('app.team_ids',$4,false)",
        str(workspace), str(user), role, str(team) if team else "",
    )


async def forbidden(connection, sql, *args):
    transaction = connection.transaction()
    await transaction.start()
    try:
        result = await connection.execute(sql, *args)
        assert result.endswith(" 0"), f"Unexpectedly authorized mutation: {result}"
    except asyncpg.InsufficientPrivilegeError:
        pass
    finally:
        await transaction.rollback()


async def fixture_before_upgrade(connection):
    workspace, team, sales, customer, visit = [uuid.uuid4() for _ in range(5)]
    await connection.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'FDE migration test')", workspace, str(workspace))
    await context(connection, workspace, sales, "sales", team)
    # Actor attribution is NULL until the first account exists.
    await connection.execute("SELECT set_config('app.user_ref_id','',false)")
    await connection.execute("INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,'SALES_TEST','Sales test')", team, workspace)
    await connection.execute("INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name) VALUES($1,$2,$3,'LEGACY','Legacy colleague')", sales, workspace, str(sales))
    await context(connection, workspace, sales, "sales", team)
    await connection.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) VALUES($1,$2,'sales','self',$3)", workspace, sales, team)
    await connection.execute("INSERT INTO platform.team_membership(workspace_id,team_id,user_ref_id,membership_role) VALUES($1,$2,$3,'sales')", workspace, team, sales)
    await connection.execute("INSERT INTO crm.customer(id,workspace_id,name,normalized_name,created_by_user_ref_id,owner_user_ref_id,owner_team_id) VALUES($1,$2,'Legacy customer','legacy customer',$3,$3,$4)", customer, workspace, sales, team)
    form = await connection.fetchval("SELECT id FROM config.form_version WHERE status='active' ORDER BY version_no DESC LIMIT 1")
    await connection.execute(
        "INSERT INTO activity.visit(id,workspace_id,customer_id,recorder_user_ref_id,recorder_team_id,form_version_id,status,interaction_at,created_by_user_ref_id,collaborator_user_ref_ids) "
        "VALUES($1,$2,$3,$4,$5,$6,'archived',clock_timestamp(),$4,ARRAY[$4,$4]::uuid[])",
        visit, workspace, customer, sales, team, form,
    )
    return workspace, team, sales, customer, visit, form


async def verify(connection, runtime_role, original):
    results = []
    role_checks=await connection.fetch("SELECT conrelid::regclass::text AS object_name,conname,pg_get_constraintdef(oid) AS definition FROM pg_constraint WHERE contype='c' AND pg_get_constraintdef(oid) LIKE '%supervisor%' AND pg_get_constraintdef(oid) ~ '(role|position)'")
    assert len(role_checks)>=7
    assert not [dict(row) for row in role_checks if "'fde'" not in row["definition"] or "'fde_lead'" not in row["definition"]]
    results.append("all_native_role_and_position_enumerations_accept_both_fde_identities")
    workspace, sales_team, sales, customer, old_visit, form = original
    assert not await connection.fetchval("SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='activity' AND table_name='visit' AND column_name='collaborator_user_ref_ids')")
    migrated = await connection.fetch("SELECT participant_role,team_id_at_event,role_code_at_event FROM activity.visit_participant WHERE visit_id=$1", old_visit)
    assert len(migrated) == 1 and migrated[0]["participant_role"] == "collaborator"
    assert migrated[0]["team_id_at_event"] is None and migrated[0]["role_code_at_event"] is None
    results.append("legacy_array_is_one_normalized_fact_without_invented_fde_history")
    team, other_team, member, peer, lead, outside, administrator = [uuid.uuid4() for _ in range(7)]
    await connection.executemany("INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,$3,$3)", [(team, workspace, "FDE A"), (other_team, workspace, "FDE B")])
    for person, code, role, department in [(member,"FDE1","fde",team),(peer,"FDE2","fde",team),(lead,"FDEL","fde_lead",team),(outside,"OTHER","fde",other_team),(administrator,"ADMIN","administrator",sales_team)]:
        await connection.execute("INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name) VALUES($1,$2,$3,$4,$4)", person, workspace, str(person), code)
        await connection.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) VALUES($1,$2,$3,$4,$5)", workspace, person, role, "workspace" if role=="administrator" else "team" if role=="fde_lead" else "self", department)
        await connection.execute("INSERT INTO platform.team_membership(workspace_id,team_id,user_ref_id,membership_role) VALUES($1,$2,$3,$4)", workspace, department, person, role)
    project, sibling, unrelated, other_customer = [uuid.uuid4() for _ in range(4)]
    await connection.execute("INSERT INTO crm.customer(id,workspace_id,name,normalized_name,created_by_user_ref_id,owner_user_ref_id,owner_team_id) VALUES($1,$2,'Unrelated customer','unrelated customer',$3,$3,$4)", other_customer, workspace, sales, sales_team)
    await connection.execute("INSERT INTO crm.customer_sales_member(workspace_id,customer_id,user_ref_id) VALUES($1,$2,$3) ON CONFLICT DO NOTHING", workspace, customer, sales)
    for oid, cid, name in [(project, customer, "Direct project"), (sibling,customer,"Sibling project"),(unrelated,other_customer,"Unrelated project")]:
        await connection.execute("INSERT INTO crm.opportunity(id,workspace_id,customer_id,name,owner_user_ref_id,owner_team_id,amount) VALUES($1,$2,$3,$4,$5,$6,100)", oid, workspace, cid, name, sales, sales_team)
    await connection.execute("INSERT INTO crm.opportunity_participant(opportunity_id,workspace_id,user_ref_id,participant_role,assigned_by_user_ref_id,source_code) VALUES($1,$2,$3,'fde',$4,'manual')", project, workspace, member, sales)
    joined = await connection.fetchval("SELECT count(*) FROM workflow.notification WHERE recipient_user_ref_id=$1 AND template_code='fde_joined'", member)
    assert joined == 1
    # Grant runtime access only to tables/functions. This role is neither table
    # owner nor superuser nor BYPASSRLS; denial assertions exercise native RLS.
    await connection.execute(f'CREATE ROLE "{runtime_role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
    for schema in ["common","platform","crm","activity","workflow","insight","ops","config","agent","security"]:
        await connection.execute(f'GRANT USAGE ON SCHEMA "{schema}" TO "{runtime_role}"')
        await connection.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA "{schema}" TO "{runtime_role}"')
        await connection.execute(f'GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA "{schema}" TO "{runtime_role}"')
    await connection.execute("SET row_security=on")
    await connection.execute(f'SET ROLE "{runtime_role}"')
    await context(connection,workspace,sales,"sales",sales_team)
    native_insert=await connection.fetchval("INSERT INTO crm.opportunity(workspace_id,customer_id,name,owner_user_ref_id,owner_team_id) VALUES($1,$2,'Native sales RETURNING',$3,$4) RETURNING id",workspace,customer,sales,sales_team)
    assert native_insert is not None
    await context(connection,workspace,administrator,"administrator",sales_team)
    native_admin=await connection.fetchval("INSERT INTO crm.opportunity(workspace_id,customer_id,name,owner_user_ref_id,owner_team_id) VALUES($1,$2,'Native admin RETURNING',$3,$4) RETURNING id",workspace,customer,sales,sales_team)
    assert native_admin is not None
    await connection.execute("DELETE FROM crm.opportunity WHERE id=ANY($1::uuid[])",[native_insert,native_admin])
    results.append("sales_and_operations_insert_returning_keeps_inline_owner_visibility")
    await context(connection, workspace, member, "fde", team)
    assert await connection.fetchval("SELECT security.is_fde_actor()")
    assert not await connection.fetchval("SELECT security.fde_visit_entry_enabled()")
    assert await connection.fetchval("SELECT security.fde_opportunity_in_scope($1)", project)
    assert not await connection.fetchval("SELECT security.fde_opportunity_in_scope($1)", sibling)
    assert await connection.fetchval("SELECT security.has_opportunity_read_access($1)", sibling)
    assert not await connection.fetchval("SELECT security.has_opportunity_access($1)", project)
    assert set(await connection.fetch("SELECT id FROM crm.opportunity")) == {(project,), (sibling,)}
    assert await connection.fetchval("SELECT count(*) FROM activity.visit WHERE id=$1", old_visit) == 1
    assert not await connection.fetchval("SELECT security.has_customer_access($1)", other_customer)
    results.append("direct_project_stats_and_whole_customer_read_are_different_scopes")
    await forbidden(connection,"UPDATE crm.opportunity SET amount=999 WHERE id=$1",project)
    await forbidden(connection,"UPDATE crm.customer SET name='forbidden' WHERE id=$1",customer)
    await forbidden(connection,"UPDATE activity.visit SET follow_up_record='forbidden' WHERE id=$1",old_visit)
    await forbidden(connection,"UPDATE platform.role_binding SET data_scope_code='workspace' WHERE user_ref_id=$1",member)
    await forbidden(connection,"INSERT INTO activity.visit(workspace_id,customer_id,recorder_user_ref_id,form_version_id,status,interaction_at,created_by_user_ref_id) VALUES($1,$2,$3,$4,'archived',clock_timestamp(),$3)",workspace,customer,member,form)
    results.append("panorama_does_not_grant_commercial_admin_or_default_visit_writes")
    # Configuration is a real versioned publication. Enabling entry permits only
    # own records in readable customers; per-user false overrides a role-wide true.
    await context(connection,workspace,administrator,"administrator",sales_team)
    for ai_role in ("fde","fde_lead"):
        assert await connection.fetchval("INSERT INTO ops.ai_usage_rule(workspace_id,name,role_code,period,calls_limit,created_by_user_ref_id) VALUES($1,'FDE usage regression',$2,'day',100,$3) RETURNING role_code",workspace,ai_role,administrator)==ai_role
    base=await connection.fetchval("SELECT security.active_company_rule('fde_capabilities')")
    rule=await connection.fetchval("SELECT security.save_company_rule('fde_capabilities','FDE test',$1::jsonb,'Verify configured entry',NULL,NULL,$2)",json.dumps({"schema_version":1,"visit_entry_enabled":False,"role_overrides":{"fde":True},"user_overrides":{}}),base)
    await connection.fetchval("SELECT security.publish_company_rule($1,1)",rule)
    await context(connection,workspace,member,"fde",team)
    assert await connection.fetchval("SELECT security.fde_visit_entry_enabled()")
    enabled_visit=await connection.fetchval("INSERT INTO activity.visit(workspace_id,customer_id,recorder_user_ref_id,form_version_id,status,interaction_at,created_by_user_ref_id) VALUES($1,$2,$3,$4,'archived',clock_timestamp(),$3) RETURNING id",workspace,customer,member,form)
    assert await connection.execute("UPDATE activity.visit SET follow_up_record='own edit' WHERE id=$1",enabled_visit)=="UPDATE 1"
    await forbidden(connection,"UPDATE activity.visit SET follow_up_record='another recorder' WHERE id=$1",old_visit)
    await context(connection,workspace,administrator,"administrator",sales_team)
    newer=await connection.fetchval("SELECT security.save_company_rule('fde_capabilities','FDE test',$1::jsonb,'Verify user exception',NULL,NULL,$2)",json.dumps({"schema_version":1,"visit_entry_enabled":False,"role_overrides":{"fde":True},"user_overrides":{str(member):False}}),rule)
    await connection.fetchval("SELECT security.publish_company_rule($1,1)",newer)
    await context(connection,workspace,member,"fde",team)
    assert not await connection.fetchval("SELECT security.fde_visit_entry_enabled()")
    await forbidden(connection,"UPDATE activity.visit SET follow_up_record='after capability revoked' WHERE id=$1",enabled_visit)
    results.append("versioned_capabilities_enable_only_own_entry_and_user_override_revokes_it")
    await context(connection,workspace,lead,"fde_lead",team)
    assert await connection.fetchval("SELECT security.fde_opportunity_in_scope($1)",project)
    assert await connection.fetchval("SELECT security.can_manage_fde_members($1)",project)
    await connection.execute("INSERT INTO crm.opportunity_participant(opportunity_id,workspace_id,user_ref_id,participant_role,assigned_by_user_ref_id,source_code) VALUES($1,$2,$3,'fde',$4,'manual')",project,workspace,peer,lead)
    await forbidden(connection,"INSERT INTO crm.opportunity_participant(opportunity_id,workspace_id,user_ref_id,participant_role,assigned_by_user_ref_id,source_code) VALUES($1,$2,$3,'fde',$4,'manual')",project,workspace,outside,lead)
    await forbidden(connection,"UPDATE crm.opportunity SET probability=90 WHERE id=$1",project)
    results.append("fde_lead_can_coordinate_only_existing_team_projects_not_commercial_fields")
    # The ownership-period exclusion prevents duplicate active and overlapping history.
    transaction=connection.transaction();await transaction.start()
    try:
        try:
            await connection.execute("INSERT INTO crm.opportunity_participant(opportunity_id,workspace_id,user_ref_id,participant_role,assigned_by_user_ref_id,source_code,valid_from) VALUES($1,$2,$3,'fde',$4,'manual',clock_timestamp()-interval '1 hour')",project,workspace,peer,lead)
            raise AssertionError("overlap accepted")
        except asyncpg.ExclusionViolationError:
            pass
    finally:
        await transaction.rollback()
    results.append("overlapping_participation_periods_are_rejected")
    await connection.execute("RESET ROLE")
    actual_visit=uuid.uuid4()
    source_import=uuid.uuid4()
    await context(connection,workspace,sales,"sales",sales_team)
    await connection.execute("INSERT INTO activity.visit(id,workspace_id,customer_id,opportunity_id,recorder_user_ref_id,form_version_id,status,interaction_at,created_by_user_ref_id) VALUES($1,$2,$3,$4,$5,$6,'archived',clock_timestamp(),$5)",actual_visit,workspace,customer,project,sales,form)
    await connection.execute("INSERT INTO activity.visit_participant(visit_id,workspace_id,user_ref_id,participant_role,team_id_at_event,role_code_at_event,created_by_user_ref_id) VALUES($1,$2,$3,'fde',$4,'fde',$5)",actual_visit,workspace,member,team,sales)
    await connection.execute("INSERT INTO activity.visit_import(id,workspace_id,created_by_user_ref_id,filename,file_path,file_size,status,extracted_text) VALUES($1,$2,$3,'meeting.txt','/unused/meeting.txt',100,'succeeded','private archived source')",source_import,workspace,sales)
    await connection.execute("UPDATE activity.visit SET source_import_id=$2 WHERE id=$1",actual_visit,source_import)
    task=uuid.uuid4()
    await connection.execute("INSERT INTO workflow.task(id,workspace_id,title,description,creator_user_ref_id,creator_team_id,customer_id,opportunity_id,due_at) VALUES($1,$2,'FDE task','actual task',$3,$4,$5,$6,clock_timestamp()+interval '1 day')",task,workspace,sales,sales_team,customer,project)
    await connection.execute("INSERT INTO workflow.task_assignee(task_id,workspace_id,assignee_user_ref_id,assignee_team_id,assignee_role) VALUES($1,$2,$3,$4,'fde')",task,workspace,member,team)
    await connection.execute(f'SET ROLE "{runtime_role}"')
    await context(connection,workspace,member,"fde",team)
    assert await connection.fetchval("SELECT security.fde_task_eligible($1,$2,'fde',$3)",task,member,team)
    assert await connection.fetchval("SELECT extracted_text FROM activity.visit_import WHERE id=$1",source_import)=="private archived source"
    old_version=await connection.fetchval("SELECT security.fde_permission_version()")
    identity=json.dumps({"workspace_id":str(workspace),"user_id":str(member),"role":"fde","team_ids":[str(team)],"permission_version":old_version})
    assert await connection.fetchval("SELECT security.has_analysis_scope($1::jsonb,security.current_fact_scope_version())",identity)
    history=await connection.fetch("SELECT * FROM security.fde_participation_history()")
    assert len(history)==1 and history[0]["visit_id"]==actual_visit
    await connection.execute("RESET ROLE")
    await context(connection,workspace,sales,"sales",sales_team)
    await connection.execute("UPDATE crm.opportunity_participant SET valid_to=clock_timestamp(),ended_by_user_ref_id=$2,end_reason='test removal' WHERE opportunity_id=$1 AND user_ref_id=$3 AND valid_to='infinity'",project,sales,member)
    await connection.execute(f'SET ROLE "{runtime_role}"')
    await context(connection,workspace,member,"fde",team)
    assert not await connection.fetchval("SELECT security.has_customer_access($1)",customer)
    assert not await connection.fetchval("SELECT security.fde_task_eligible($1,$2,'fde',$3)",task,member,team)
    assert await connection.fetchval("SELECT count(*) FROM activity.visit WHERE id=$1",actual_visit)==0
    assert await connection.fetchval("SELECT count(*) FROM activity.visit_import WHERE id=$1",source_import)==0
    assert await connection.fetchval("SELECT count(*) FROM security.fde_participation_history() WHERE visit_id=$1",actual_visit)==1
    assert await connection.fetchval("SELECT count(*) FROM workflow.notification WHERE template_code='fde_removed'")==1
    assert old_version!=await connection.fetchval("SELECT security.fde_permission_version()")
    assert not await connection.fetchval("SELECT security.has_analysis_scope($1::jsonb,security.current_fact_scope_version())",identity)
    results.append("removal_revokes_detail_task_and_ai_scope_but_preserves_own_history_and_receipt")
    # A lead can cancel an orphaned assignment and notify its past owner in the
    # same transaction. An old event cannot authorize a later replay or outsiders.
    await context(connection,workspace,lead,"fde_lead",team)
    receipt_payload=json.dumps({"previous_owners":[{"user_id":str(member),"role":"fde","team_id":str(team)}],"new_owner":None})
    async with connection.transaction():
        await connection.execute("UPDATE workflow.task SET status='cancelled',version_no=version_no+1 WHERE id=$1",task)
        await connection.execute("INSERT INTO workflow.task_event(workspace_id,task_id,event_type,from_status,to_status,actor_user_ref_id,payload) VALUES($1,$2,'cancel','pending_confirm','cancelled',$3,$4::jsonb)",workspace,task,lead,receipt_payload)
        notice_sql="SELECT workflow.enqueue_task_notification($1,$2,'task_cancelled','Task cancelled','A controlled receipt',$3,$4,'{\"event_version\":2}'::jsonb)"
        assert await connection.fetchval(notice_sql,workspace,member,task,"cancelled-receipt")
        assert not await connection.fetchval(notice_sql,workspace,outside,task,"forbidden-outsider")
    transaction=connection.transaction();await transaction.start()
    try:
        try:
            await connection.fetchval(notice_sql,workspace,member,task,"replayed-receipt")
            raise AssertionError("Old task event authorized another transaction")
        except asyncpg.RaiseError as error:
            assert "TASK_NOTIFICATION_FORBIDDEN" in str(error)
    finally:
        await transaction.rollback()
    await context(connection,workspace,member,"fde",team)
    assert await connection.fetchval("SELECT count(*) FROM workflow.notification WHERE template_code='task_cancelled' AND dedupe_key='cancelled-receipt'")==1
    results.append("task_coordination_notifications_require_same_transaction_receipt_and_actual_recipient")
    await context(connection,workspace,lead,"fde_lead",team)
    transaction=connection.transaction();await transaction.start()
    try:
        assert await connection.execute("UPDATE crm.opportunity_participant SET valid_to=clock_timestamp(),ended_by_user_ref_id=$2,end_reason='last team member' WHERE opportunity_id=$1 AND user_ref_id=$3 AND valid_to='infinity'",project,lead,peer)=="UPDATE 1"
        assert not await connection.fetchval("SELECT security.fde_opportunity_in_scope($1)",project)
    finally:
        await transaction.rollback()
    # Having another live department never revives a disabled/expired department.
    await connection.execute("RESET ROLE")
    await connection.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) VALUES($1,$2,'fde_lead','team',$3)",workspace,lead,other_team)
    await connection.execute("INSERT INTO platform.team_membership(workspace_id,team_id,user_ref_id,membership_role) VALUES($1,$2,$3,'fde_lead')",workspace,other_team,lead)
    await connection.execute(f'SET ROLE "{runtime_role}"')
    await context(connection,workspace,lead,"fde_lead",team)
    department_version=await connection.fetchval("SELECT security.fde_permission_version()")
    for change in ("status='inactive'", "valid_to=clock_timestamp()-interval '1 second'"):
        transaction=connection.transaction();await transaction.start()
        try:
            await connection.execute("RESET ROLE")
            await connection.execute(f"UPDATE platform.team SET {change} WHERE id=$1",team)
            await connection.execute(f'SET ROLE "{runtime_role}"')
            assert await connection.fetchval("SELECT security.is_fde_actor()")
            assert not await connection.fetchval("SELECT security.fde_opportunity_in_scope($1)",project)
            assert not await connection.fetchval("SELECT security.fde_can_coordinate_task($1)",task)
            assert await connection.fetchval("SELECT count(*) FROM security.fde_participation_history() WHERE visit_id=$1",actual_visit)==0
            assert await connection.fetchval("SELECT security.fde_permission_version()")!=department_version
        finally:
            await transaction.rollback()
    results.append("leader_last_membership_removal_and_multi_department_status_expiry_are_scoped")
    # Rejoining another project at the same customer restores READ, not task eligibility.
    await connection.execute("RESET ROLE")
    await context(connection,workspace,sales,"sales",sales_team)
    await connection.execute("INSERT INTO crm.opportunity_participant(opportunity_id,workspace_id,user_ref_id,participant_role,assigned_by_user_ref_id,source_code) VALUES($1,$2,$3,'fde',$4,'manual')",sibling,workspace,member,sales)
    await connection.execute(f'SET ROLE "{runtime_role}"')
    await context(connection,workspace,member,"fde",team)
    assert await connection.fetchval("SELECT security.has_opportunity_read_access($1)",project)
    assert not await connection.fetchval("SELECT security.fde_task_eligible($1,$2,'fde',$3)",task,member,team)
    results.append("same_customer_other_project_does_not_reactivate_old_project_task")
    await connection.execute("RESET ROLE")
    await connection.execute("UPDATE platform.user_ref SET status='inactive' WHERE id=$1",member)
    await connection.execute(f'SET ROLE "{runtime_role}"')
    await context(connection,workspace,member,"fde",team)
    assert not await connection.fetchval("SELECT security.is_fde_actor()")
    assert not await connection.fetchval("SELECT security.has_customer_access($1)",customer)
    assert await connection.fetchval("SELECT count(*) FROM security.fde_participation_history()")==0
    await connection.execute("RESET ROLE")
    assert await connection.fetchval("SELECT count(*) FROM activity.visit_participant WHERE user_ref_id=$1",member)==1
    assert await connection.fetchval("SELECT count(*) FROM ops.audit_log WHERE object_type='opportunity_participant' AND object_id=$1",project)>=3
    results.append("inactive_identity_loses_access_while_audit_and_historical_facts_remain")
    await connection.execute(f'SET ROLE "{runtime_role}"')
    await context(connection,workspace,administrator,"administrator",sales_team)
    activities=await connection.fetch("SELECT action_code,object_name,payload FROM security.business_activity_rows(clock_timestamp()-interval '1 day',clock_timestamp()+interval '1 day') WHERE action_code IN ('opportunity.fde_members','visit.fde_participants','company_rule.change')")
    assert {r["action_code"] for r in activities}=={"opportunity.fde_members","visit.fde_participants","company_rule.change"}
    for row in activities:
        body=json.loads(row["payload"])
        if row["action_code"]!="company_rule.change":
            assert body["changes"] and all(change["label"] in {"FDE1","FDE2"} for change in body["changes"])
        else:
            assert body["rule_name"] and body["rule_version"] and body["change_reason"]
    await context(connection,workspace,lead,"fde_lead",team)
    assert await connection.fetchval("SELECT count(*) FROM security.business_activity_rows(clock_timestamp()-interval '1 day',clock_timestamp()+interval '1 day')")==0
    results.append("participation_and_rule_business_history_is_human_readable_and_management_only")
    return results


async def main():
    config={"host":os.environ.get("PGHOST","/tmp"),"user":os.environ.get("PGUSER","postgres")}
    admin=await asyncpg.connect(database="postgres",**config)
    suffix=uuid.uuid4().hex[:16]
    name="salegent_fde_verify_"+suffix
    runtime_role="salegent_fde_verify_role_"+suffix
    connection=None
    try:
        await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
        connection=await asyncpg.connect(database=name,**config)
        with tempfile.TemporaryDirectory() as temporary:
            old_root=Path(temporary)/"database"
            shutil.copytree(ROOT,old_root)
            for path in (old_root/"migrations").glob("V*.sql"):
                if int(path.name.split("__",1)[0][1:])>=69:
                    path.unlink()
            await migrate(connection,old_root)
            original=await fixture_before_upgrade(connection)
            # V072 intentionally changes default recording and project eligibility.
            # Preserve this historical contract; its successor has a separate
            # verify_fde_recording_postgres.py upgrade/recording regression.
            for path in (ROOT/"migrations").glob("V*.sql"):
                if 69<=int(path.name.split("__",1)[0][1:])<=71:
                    shutil.copy2(path,old_root/"migrations"/path.name)
            await migrate(connection,old_root)
        checks=await verify(connection,runtime_role,original)
        print(json.dumps({"checks":checks,"passed":len(checks)},ensure_ascii=False))
    finally:
        if connection:
            await connection.execute("RESET ROLE")
            await connection.close()
        assert name.startswith("salegent_fde_verify_")
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.execute(f'DROP ROLE IF EXISTS "{runtime_role}"')
        await admin.close()


if __name__=="__main__":
    asyncio.run(main())
