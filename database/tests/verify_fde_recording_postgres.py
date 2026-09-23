"""V071 -> V072 owned recording and historical statistics in a disposable DB.

Uses only the local maintenance socket, a random database and a non-bypass
runtime role. Historical fixtures and all mutations disappear in finally.
No business DATABASE_URL, credentials, provider calls or deployment are used.
"""
import asyncio
import json
import os
from pathlib import Path
import sys
import uuid

import asyncpg

from verify_fde_postgres import context, fixture_before_upgrade
from verify_runtime_grants_postgres import migrate_through

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from migrate import migrate


async def denied(connection, query, *args):
    transaction = connection.transaction()
    await transaction.start()
    try:
        result = await connection.execute(query, *args)
        assert result.endswith(" 0"), f"Unexpectedly authorized mutation: {result}"
    except (asyncpg.InsufficientPrivilegeError, asyncpg.CheckViolationError):
        pass
    finally:
        await transaction.rollback()


async def publish(connection, workspace, administrator, team, enabled):
    await context(connection, workspace, administrator, "administrator", team)
    current = await connection.fetchval("SELECT security.active_company_rule('fde_capabilities')")
    identifier = await connection.fetchval(
        "SELECT security.save_company_rule('fde_capabilities','Recording regression',$1::jsonb,"
        "'Isolated recording regression',NULL,NULL,$2)",
        json.dumps({"schema_version": 1, "visit_entry_enabled": enabled,
                    "role_overrides": {}, "user_overrides": {}}), current,
    )
    await connection.fetchval("SELECT security.publish_company_rule($1,1)", identifier)
    return identifier


async def visit(connection, workspace, customer, opportunity, recorder, team, form, *,
                status="archived", creator=None, confirmer=None):
    return await connection.fetchval(
        "INSERT INTO activity.visit(workspace_id,customer_id,opportunity_id,recorder_user_ref_id,"
        "recorder_team_id,form_version_id,status,interaction_at,created_by_user_ref_id,"
        "confirmed_by_user_ref_id,confirmed_at,archived_at,archived_fields) "
        "VALUES($1,$2,$3,$4,$5,$6,$7,clock_timestamp(),$8,$9,"
        "CASE WHEN $7='archived' THEN clock_timestamp() END,"
        "CASE WHEN $7='archived' THEN clock_timestamp() END,"
        "'{\"customer_name\":\"Recorded customer\",\"opportunity_name\":\"Recorded opportunity\"}') RETURNING id",
        workspace, customer, opportunity, recorder, team, form, status,
        creator if creator is not None else recorder,
        confirmer if confirmer is not None else recorder if status == "archived" else None,
    )


async def fixtures(connection, original):
    workspace, sales_team, sales, customer, _, form = original
    team, other_team, member, peer, lead, outside_lead, administrator = [uuid.uuid4() for _ in range(7)]
    await connection.executemany(
        "INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,$3,$3)",
        [(team, workspace, "FDE recording A"), (other_team, workspace, "FDE recording B")],
    )
    for person, code, role, department in (
        (member, "OWNED_FDE", "fde", team), (peer, "PEER_FDE", "fde", team),
        (lead, "OWNED_LEAD", "fde_lead", team), (outside_lead, "OUTSIDE_LEAD", "fde_lead", other_team),
        (administrator, "RECORDING_ADMIN", "administrator", sales_team),
    ):
        await connection.execute(
            "INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name) "
            "VALUES($1,$2,$3,$4,$4)", person, workspace, str(person), code,
        )
        await connection.execute(
            "INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) "
            "VALUES($1,$2,$3,$4,$5)", workspace, person, role,
            "workspace" if role == "administrator" else "team" if role == "fde_lead" else "self", department,
        )
        await connection.execute(
            "INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) "
            "VALUES($1,$2,$3,$4)", workspace, person, department, role,
        )
    await context(connection, workspace, sales, "sales", sales_team)
    sibling, direct, foreign_project, foreign_customer = [uuid.uuid4() for _ in range(4)]
    await connection.execute(
        "INSERT INTO crm.customer(id,workspace_id,name,normalized_name,created_by_user_ref_id,"
        "owner_user_ref_id,owner_team_id) VALUES($1,$2,'Other customer','other customer',$3,$3,$4)",
        foreign_customer, workspace, sales, sales_team,
    )
    await connection.execute(
        "INSERT INTO crm.customer_sales_member(workspace_id,customer_id,user_ref_id) VALUES($1,$2,$3) "
        "ON CONFLICT DO NOTHING", workspace, customer, sales,
    )
    for identifier, customer_id, name in ((direct, customer, "My project"), (sibling, customer, "Peer project"),
                                         (foreign_project, foreign_customer, "Other customer's project")):
        await connection.execute(
            "INSERT INTO crm.opportunity(id,workspace_id,customer_id,name,owner_user_ref_id,owner_team_id) "
            "VALUES($1,$2,$3,$4,$5,$6)", identifier, workspace, customer_id, name, sales, sales_team,
        )
    for project, person in ((direct, member), (sibling, peer), (foreign_project, member)):
        await connection.execute(
            "INSERT INTO crm.opportunity_participant(workspace_id,opportunity_id,user_ref_id,participant_role,"
            "assigned_by_user_ref_id,source_code) VALUES($1,$2,$3,'fde',$4,'manual')",
            workspace, project, person, sales,
        )
    await publish(connection, workspace, administrator, sales_team, True)
    await context(connection, workspace, member, "fde", team)
    own_history = await visit(connection, workspace, customer, direct, member, team, form)
    await context(connection, workspace, lead, "fde_lead", team)
    lead_history = await visit(connection, workspace, customer, direct, lead, team, form)
    await context(connection, workspace, sales, "sales", sales_team)
    colleague_history = await visit(connection, workspace, customer, direct, sales, sales_team, form)
    await connection.execute(
        "INSERT INTO activity.visit_participant(visit_id,workspace_id,user_ref_id,participant_role,"
        "team_id_at_event,role_code_at_event,created_by_user_ref_id) VALUES($1,$2,$3,'fde',$4,'fde',$5)",
        colleague_history, workspace, member, team, sales,
    )
    # Historical source says sales, regardless of this person's role today.
    await context(connection, workspace, member, "sales", team)
    sales_history = await visit(connection, workspace, customer, direct, member, team, form)
    await context(connection, workspace, administrator, "administrator", sales_team)
    proxy_history = await visit(connection, workspace, customer, direct, member, team, form)
    # Simulate a genuinely pre-audit row. Never invent missing historical proof.
    await connection.execute("ALTER TABLE activity.visit DISABLE TRIGGER business_audit")
    try:
        await context(connection, workspace, member, "fde", team)
        unknown_history = await visit(connection, workspace, customer, direct, member, team, form)
    finally:
        await connection.execute("ALTER TABLE activity.visit ENABLE TRIGGER business_audit")
    overlay = await publish(connection, workspace, administrator, sales_team, False)
    return dict(workspace=workspace, sales_team=sales_team, sales=sales, customer=customer, form=form,
                team=team, other_team=other_team, member=member, peer=peer, lead=lead, outside_lead=outside_lead,
                administrator=administrator, direct=direct, sibling=sibling, foreign_project=foreign_project,
                foreign_customer=foreign_customer, own_history=own_history, lead_history=lead_history,
                colleague_history=colleague_history, sales_history=sales_history, proxy_history=proxy_history,
                unknown_history=unknown_history, overlay=overlay)


async def verify(connection, runtime, f):
    checks = []
    workspace, customer, team, sales_team = (f[k] for k in ("workspace", "customer", "team", "sales_team"))
    member, lead, sales, form, direct = (f[k] for k in ("member", "lead", "sales", "form", "direct"))
    global_rules = await connection.fetch(
        "SELECT id,version_no,definition FROM config.rule_set WHERE rule_code='fde_capabilities' "
        "AND workspace_id IS NULL ORDER BY version_no"
    )
    assert len(global_rules) == 2
    assert json.loads(global_rules[0]["definition"])["visit_entry_enabled"] is False
    assert json.loads(global_rules[1]["definition"])["visit_entry_enabled"] is True
    assert await connection.fetchval(
        "SELECT count(*) FROM ops.audit_log WHERE object_id=$1 AND action_code='company_rule.migration_publish'",
        global_rules[1]["id"],
    ) == 1
    assert json.loads(await connection.fetchval("SELECT definition FROM config.rule_set WHERE id=$1", f["overlay"]))["visit_entry_enabled"] is False
    snapshots = {row["id"]: row["recording_role_code_snapshot"] for row in await connection.fetch(
        "SELECT id,recording_role_code_snapshot FROM activity.visit WHERE customer_id=$1", customer,
    )}
    assert snapshots[f["own_history"]] == "fde" and snapshots[f["lead_history"]] == "fde_lead"
    assert all(snapshots[f[k]] is None for k in ("colleague_history", "sales_history", "proxy_history", "unknown_history"))
    checks.append("global_default_is_new_audited_version_and_trustworthy_history_is_backfilled_once")

    await connection.execute(f'CREATE ROLE "{runtime}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
    for schema in ("common", "security", "config", "platform", "crm", "activity", "workflow", "insight", "ops", "agent"):
        await connection.execute(f'GRANT USAGE ON SCHEMA "{schema}" TO "{runtime}"')
        await connection.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA "{schema}" TO "{runtime}"')
        await connection.execute(f'GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA "{schema}" TO "{runtime}"')
    # The baseline's pg_dump session setting leaves row_security=off on this
    # reused migration connection. For a non-bypass runtime role that setting
    # raises instead of exercising row policies, including on legal writes.
    await connection.execute("SET row_security=on")
    await connection.execute(f'SET ROLE "{runtime}"')
    assert not await connection.fetchval("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
    assert await connection.fetchval("SHOW row_security") == "on"
    await context(connection, workspace, member, "fde", team)
    assert not await connection.fetchval("SELECT security.fde_visit_entry_enabled()")
    assert await connection.fetchval("SELECT security.fde_can_record_opportunity($1)", direct)
    await publish(connection, workspace, f["administrator"], sales_team, True)
    await context(connection, workspace, member, "fde", team)
    assert await connection.fetchval("SELECT security.fde_visit_entry_enabled()")
    assert await connection.fetchval("SELECT security.fde_can_record_opportunity($1)", direct)
    assert not await connection.fetchval("SELECT security.fde_can_record_opportunity($1)", f["sibling"])
    assert await connection.fetchval("SELECT security.has_opportunity_read_access($1)", f["sibling"])
    await context(connection, workspace, lead, "fde_lead", team)
    assert await connection.fetchval("SELECT security.fde_visit_entry_enabled()")
    assert await connection.fetchval("SELECT security.has_opportunity_read_access($1)", direct)
    assert not await connection.fetchval("SELECT security.fde_can_record_opportunity($1)", direct)
    checks.append("capability_switch_and_direct_member_qualification_are_separate_from_team_panorama")

    insert_sql = (
        "INSERT INTO activity.visit(workspace_id,customer_id,opportunity_id,recorder_user_ref_id,recorder_team_id,"
        "form_version_id,status,interaction_at,created_by_user_ref_id,confirmed_by_user_ref_id) "
        "VALUES($1,$2,$3,$4,$5,$6,'archived',clock_timestamp(),$7,$8)"
    )
    await denied(connection, insert_sql, workspace, customer, direct, lead, team, form, lead, lead)
    await context(connection, workspace, member, "fde", team)
    for project, creator, recorder, confirmer in (
        (None, member, member, member), (f["sibling"], member, member, member),
        (f["foreign_project"], member, member, member), (direct, sales, member, member),
        (direct, member, sales, member), (direct, member, member, sales), (direct, member, member, None),
    ):
        await denied(connection, insert_sql, workspace, customer, project, recorder, team, form, creator, confirmer)
    # Client-supplied snapshot fields are overwritten from verified identity.
    own = await connection.fetchval(
        "INSERT INTO activity.visit(workspace_id,customer_id,opportunity_id,recorder_user_ref_id,recorder_team_id,"
        "form_version_id,status,interaction_at,created_by_user_ref_id,confirmed_by_user_ref_id,"
        "recording_role_code_snapshot,recording_team_id_snapshot) "
        "VALUES($1,$2,$3,$4,$5,$6,'archived',clock_timestamp(),$4,$4,'sales',$7) RETURNING id",
        workspace, customer, direct, member, team, form, f["other_team"],
    )
    row = await connection.fetchrow("SELECT recording_role_code_snapshot,recording_team_id_snapshot FROM activity.visit WHERE id=$1", own)
    assert tuple(row) == ("fde", team)
    await denied(connection, "UPDATE crm.customer SET name='forbidden' WHERE id=$1", customer)
    await denied(connection, "UPDATE crm.opportunity SET amount=999 WHERE id=$1", direct)
    checks.append("only_own_nonempty_same_customer_direct_project_can_archive_without_commercial_writes")

    draft = await visit(connection, workspace, customer, direct, member, team, form, status="pending_confirm")
    assert await connection.fetchval("SELECT count(*) FROM security.fde_recorded_visit_history() WHERE visit_id=$1", draft) == 0
    for assignment, value in (("opportunity_id", None), ("opportunity_id", f["sibling"]),
                              ("opportunity_id", f["foreign_project"]), ("created_by_user_ref_id", sales),
                              ("recorder_user_ref_id", sales), ("confirmed_by_user_ref_id", sales)):
        await denied(connection, f"UPDATE activity.visit SET {assignment}=$2 WHERE id=$1", draft, value)
    await denied(connection, "UPDATE activity.visit SET status='archived',confirmed_by_user_ref_id=NULL WHERE id=$1", draft)
    await context(connection, workspace, f["administrator"], "administrator", sales_team)
    await denied(connection, "UPDATE activity.visit SET status='archived',confirmed_by_user_ref_id=$2 WHERE id=$1", draft, member)
    await context(connection, workspace, member, "fde", team)
    await connection.execute(
        "UPDATE activity.visit SET status='confirmed',confirmed_by_user_ref_id=$2,confirmed_at=clock_timestamp() WHERE id=$1",
        draft, member,
    )
    # A privileged non-FDE actor must not replace the confirmer while leaving
    # status='confirmed'; there is no status transition to trigger that guard.
    await context(connection, workspace, f["administrator"], "administrator", sales_team)
    await denied(connection, "UPDATE activity.visit SET confirmed_by_user_ref_id=$2 WHERE id=$1", draft, sales)
    await context(connection, workspace, member, "fde", team)
    await connection.execute(
        "UPDATE activity.visit SET status='archived',confirmed_by_user_ref_id=$2,confirmed_at=clock_timestamp(),"
        "archived_at=clock_timestamp() WHERE id=$1", draft, member,
    )
    await denied(connection, "UPDATE activity.visit SET recording_role_code_snapshot='sales' WHERE id=$1", own)
    await denied(connection, "UPDATE activity.visit SET recording_team_id_snapshot=$2 WHERE id=$1", own, f["other_team"])
    await context(connection, workspace, f["administrator"], "administrator", sales_team)
    await denied(connection, "UPDATE activity.visit SET recorder_user_ref_id=$2,created_by_user_ref_id=$2,confirmed_by_user_ref_id=$2 WHERE id=$1", own, sales)
    checks.append("new_row_guards_and_immutable_confirmed_or_archived_identity_block_rebinding_and_proxy_confirmation")

    await context(connection, workspace, member, "fde", team)
    recorded = {r["visit_id"] for r in await connection.fetch("SELECT * FROM security.fde_recorded_visit_history()")}
    assert recorded == {f["own_history"], own, draft}
    participated = {r["visit_id"] for r in await connection.fetch("SELECT * FROM security.fde_participation_history()")}
    assert f["colleague_history"] in participated and f["colleague_history"] not in recorded
    assert own not in participated  # Authorship never depends on self being selected as a participant.
    await context(connection, workspace, lead, "fde_lead", team)
    assert {r["visit_id"] for r in await connection.fetch("SELECT * FROM security.fde_recorded_visit_history()")} == recorded | {f["lead_history"]}
    await context(connection, workspace, f["outside_lead"], "fde_lead", f["other_team"])
    assert await connection.fetchval("SELECT count(*) FROM security.fde_recorded_visit_history()") == 0
    checks.append("authored_archive_and_actual_participation_are_distinct_with_historical_department_scope")

    await context(connection, workspace, sales, "sales", sales_team)
    await connection.execute(
        "INSERT INTO crm.opportunity_participant(workspace_id,opportunity_id,user_ref_id,participant_role,"
        "assigned_by_user_ref_id,source_code) VALUES($1,$2,$3,'fde',$4,'manual')", workspace, direct, lead, sales,
    )
    await context(connection, workspace, lead, "fde_lead", team)
    assert await connection.fetchval("SELECT security.fde_can_record_opportunity($1)", direct)
    lead_own = await visit(connection, workspace, customer, direct, lead, team, form)
    assert await connection.fetchval("SELECT recording_role_code_snapshot FROM activity.visit WHERE id=$1", lead_own) == "fde_lead"
    checks.append("leader_may_record_only_after_joining_the_actual_opportunity_in_person")

    await context(connection, workspace, sales, "sales", sales_team)
    await connection.execute(
        "INSERT INTO crm.opportunity_participant(workspace_id,opportunity_id,user_ref_id,participant_role,"
        "assigned_by_user_ref_id,source_code) VALUES($1,$2,$3,'fde',$4,'manual')", workspace, f["sibling"], member, sales,
    )
    await connection.execute(
        "UPDATE crm.opportunity_participant SET valid_to=clock_timestamp(),end_reason='left direct project' "
        "WHERE opportunity_id=$1 AND user_ref_id=$2 AND valid_to='infinity'", direct, member,
    )
    await context(connection, workspace, member, "fde", team)
    assert await connection.fetchval("SELECT security.has_customer_access($1)", customer)
    assert not await connection.fetchval("SELECT security.can_write_visit($1)", own)
    await denied(connection, "UPDATE activity.visit SET next_action='forbidden' WHERE id=$1", own)
    await denied(connection, insert_sql, workspace, customer, direct, member, team, form, member, member)
    assert await connection.fetchval("SELECT security.fde_can_record_opportunity($1)", f["sibling"])
    checks.append("leaving_one_project_revokes_its_writes_even_when_the_customer_remains_readable")

    await context(connection, workspace, sales, "sales", sales_team)
    await connection.execute(
        "UPDATE crm.opportunity_participant SET valid_to=clock_timestamp(),end_reason='left last customer project' "
        "WHERE opportunity_id=$1 AND user_ref_id=$2 AND valid_to='infinity'", f["sibling"], member,
    )
    await context(connection, workspace, member, "fde", team)
    assert not await connection.fetchval("SELECT security.has_customer_access($1)", customer)
    assert await connection.fetchval("SELECT count(*) FROM activity.visit WHERE id=$1", own) == 0
    assert {r["visit_id"] for r in await connection.fetch("SELECT * FROM security.fde_recorded_visit_history()")} == recorded
    checks.append("last_customer_exit_preserves_only_minimal_authored_history_without_detail_access")

    transaction = connection.transaction()
    await transaction.start()
    try:
        await connection.execute("RESET ROLE")
        await connection.execute("UPDATE platform.team SET status='inactive' WHERE id=$1", team)
        await connection.execute(f'SET LOCAL ROLE "{runtime}"')
        await context(connection, workspace, lead, "fde_lead", team)
        assert await connection.fetchval("SELECT count(*) FROM security.fde_recorded_visit_history()") == 0
    finally:
        await transaction.rollback()
    await context(connection, workspace, sales, "sales", sales_team)
    unlinked = await visit(connection, workspace, customer, None, sales, sales_team, form)
    assert await connection.fetchval("SELECT opportunity_id FROM activity.visit WHERE id=$1", unlinked) is None
    assert await connection.fetchval("SELECT recording_role_code_snapshot FROM activity.visit WHERE id=$1", unlinked) == "sales"
    checks.append("expired_department_cannot_read_team_history_and_sales_unlinked_recording_is_unchanged")
    return checks


async def main():
    config = {"host": os.environ.get("PGHOST", "/tmp"), "user": os.environ.get("PGUSER", "postgres")}
    admin = await asyncpg.connect(database="postgres", **config)
    suffix = uuid.uuid4().hex[:16]
    name = "salegent_verify_fde_recording_" + suffix
    runtime = "salegent_fde_recording_role_" + suffix
    connection = None
    try:
        await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
        connection = await asyncpg.connect(database=name, **config)
        await migrate_through(connection, 68)
        original = await fixture_before_upgrade(connection)
        await migrate_through(connection, 71)
        fixture = await fixtures(connection, original)
        await migrate(connection)
        checks = await verify(connection, runtime, fixture)
        await connection.execute("RESET ROLE")
        assert all(step["status"] == "unchanged" for step in await migrate(connection))
        checks.append("repeat_deployment_does_not_republish_defaults_or_rewrite_recording_snapshots")
        print(json.dumps({"checks": checks, "passed": len(checks)}, ensure_ascii=False))
    finally:
        if connection:
            await connection.execute("RESET ROLE")
            await connection.close()
        assert name.startswith("salegent_verify_fde_recording_")
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.execute(f'DROP ROLE IF EXISTS "{runtime}"')
        await admin.close()


if __name__ == "__main__":
    asyncio.run(main())
