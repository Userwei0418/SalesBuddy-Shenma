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


async def denied(connection, query, *args, case='unlabelled'):
    transaction = connection.transaction()
    await transaction.start()
    try:
        result = await connection.execute(query, *args)
        assert result.endswith(" 0"), f"Unexpectedly authorized mutation [{case}]: {result}"
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


async def history_grants(connection, role, scope, teams):
    # Re-evaluate the real fixture grants after context/scope changes.
    await connection.execute("SELECT set_config('app.authorization_context','',false)")
    rows = [dict(row) for row in await connection.fetch("SELECT permission_code,scope_code,team_ids,source_code,effect "
        "FROM security.authorization_current_grants() WHERE permission_code='profile.fde_read'")]
    assert rows == [dict(permission_code='profile.fde_read', scope_code=scope, team_ids=teams,
                         source_code=role, effect='allow')], rows
    assert await connection.fetchval("SELECT security.authorization_has('profile.fde_read')")
    return rows


async def history_ids(connection, expected, named, case):
    actual = {row['visit_id'] for row in await connection.fetch('SELECT * FROM security.fde_recorded_visit_history()')}
    assert actual == expected, {'case': case, 'actual': sorted(map(str, actual)),
        'expected': sorted(map(str, expected)), 'named_fixtures': {key: str(value) for key, value in named.items()}}
    return actual


async def history_role_grants(connection, workspace):
    return [dict(row) for row in await connection.fetch("""SELECT r.builtin_role_code,g.role_id,to_jsonb(g)::text AS row
      FROM config.permission_role_grant g JOIN config.permission_role r
        ON r.id=g.role_id AND r.workspace_id=g.workspace_id
      WHERE g.workspace_id=$1 AND r.builtin_role_code IN ('fde','fde_lead')
        AND g.permission_code='profile.fde_read' ORDER BY r.builtin_role_code""", workspace)]


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
    await denied(connection, insert_sql, workspace, customer, direct, lead, team, form, lead, lead,
                 case='lead_without_direct_assignment')
    await context(connection, workspace, member, "fde", team)
    # V220 permits an assigned customer without selecting an opportunity.
    # Roll back this positive probe so authored-history expectations below keep
    # referring to their original three persistent fixture rows.
    probe = connection.transaction()
    await probe.start()
    try:
        customer_only = await connection.fetchval(insert_sql + ' RETURNING id',
            workspace, customer, None, member, team, form, member, member)
        row = await connection.fetchrow("SELECT opportunity_id,recording_role_code_snapshot,recording_team_id_snapshot FROM activity.visit WHERE id=$1", customer_only)
        assert tuple(row) == (None, 'fde', team)
    finally:
        await probe.rollback()
    assert await connection.fetchval('SELECT count(*) FROM activity.visit WHERE id=$1', customer_only) == 0
    checks.append('assigned_customer_only_recording_uses_existing_grant_and_verified_identity')
    for case, project, creator, recorder, confirmer in (
        ('sibling_not_assigned', f["sibling"], member, member, member),
        ('project_wrong_customer', f["foreign_project"], member, member, member),
        ('foreign_creator', direct, sales, member, member),
        ('foreign_recorder', direct, member, sales, member),
        ('foreign_confirmer', direct, member, member, sales),
        ('missing_confirmer', direct, member, member, None),
    ):
        await denied(connection, insert_sql, workspace, customer, project, recorder, team, form, creator, confirmer, case=case)
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
    checks.append("only_own_assigned_customer_or_same_customer_direct_project_can_archive_without_commercial_writes")

    draft = await visit(connection, workspace, customer, direct, member, team, form, status="pending_confirm")
    assert await connection.fetchval("SELECT count(*) FROM security.fde_recorded_visit_history() WHERE visit_id=$1", draft) == 0
    probe = connection.transaction()
    await probe.start()
    try:
        assert await connection.execute('UPDATE activity.visit SET opportunity_id=NULL WHERE id=$1', draft) == 'UPDATE 1'
        assert await connection.fetchval('SELECT opportunity_id FROM activity.visit WHERE id=$1', draft) is None
    finally:
        await probe.rollback()
    assert await connection.fetchval('SELECT opportunity_id FROM activity.visit WHERE id=$1', draft) == direct
    checks.append('pending_record_can_select_assigned_customer_only_without_rebinding_identity')
    for assignment, value in (("opportunity_id", f["sibling"]),
                              ("opportunity_id", f["foreign_project"]), ("created_by_user_ref_id", sales),
                              ("recorder_user_ref_id", sales), ("confirmed_by_user_ref_id", sales)):
        await denied(connection, f"UPDATE activity.visit SET {assignment}=$2 WHERE id=$1", draft, value,
                     case='pending_'+assignment+'_'+str(value))
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

    named_history = {key: f[key] for key in ('own_history', 'lead_history', 'colleague_history',
                                            'sales_history', 'proxy_history', 'unknown_history')}
    named_history.update(own=own, draft=draft)
    own_recorded = {f['own_history'], own, draft}
    department_recorded = own_recorded | {f['lead_history']}
    excluded_history = {f[key] for key in ('colleague_history', 'sales_history', 'proxy_history', 'unknown_history')}
    # Shipped V209 defaults: FDE reads its department; leaders read the workspace.
    await context(connection, workspace, member, 'fde', team)
    await history_grants(connection, 'fde', 'teams', [team])
    recorded = await history_ids(connection, department_recorded, named_history, 'default_fde_department')
    assert not recorded & excluded_history
    for person, department in ((lead, team), (f['outside_lead'], f['other_team'])):
        await context(connection, workspace, person, 'fde_lead', department)
        await history_grants(connection, 'fde_lead', 'workspace', [])
        await history_ids(connection, department_recorded, named_history, 'default_lead_workspace')
    checks.append('default_effective_department_includes_named_lead_history_and_workspace_leaders_read_same_company_history')

    # Separate constrained fixture, not a change to the product defaults.
    probe = connection.transaction()
    await probe.start()
    try:
        await connection.execute('RESET ROLE')
        default_history_grants = await history_role_grants(connection, workspace)
        assert {r['builtin_role_code']: json.loads(r['row'])['scope_code'] for r in default_history_grants} == {
            'fde': 'departments', 'fde_lead': 'workspace'}
        for row in default_history_grants:
            assert await connection.execute("UPDATE config.permission_role_grant SET scope_code=$2 "
                "WHERE role_id=$1 AND permission_code='profile.fde_read'", row['role_id'],
                'self' if row['builtin_role_code'] == 'fde' else 'departments') == 'UPDATE 1'
        await connection.execute(f'SET LOCAL ROLE "{runtime}"')
        await context(connection, workspace, member, 'fde', team)
        await history_grants(connection, 'fde', 'self', [])
        await history_ids(connection, own_recorded, named_history, 'constrained_fde_self')
        participated = {r['visit_id'] for r in await connection.fetch('SELECT * FROM security.fde_participation_history()')}
        assert f['colleague_history'] in participated and f['colleague_history'] not in own_recorded
        assert own not in participated  # Authorship does not require a participant selection.
        await context(connection, workspace, lead, 'fde_lead', team)
        await history_grants(connection, 'fde_lead', 'teams', [team])
        await history_ids(connection, department_recorded, named_history, 'constrained_lead_department')
        await context(connection, workspace, f['outside_lead'], 'fde_lead', f['other_team'])
        await history_grants(connection, 'fde_lead', 'teams', [f['other_team']])
        await history_ids(connection, set(), named_history, 'constrained_foreign_department')
    finally:
        await probe.rollback()
    probe = connection.transaction()
    await probe.start()
    try:
        await connection.execute('RESET ROLE')
        assert await history_role_grants(connection, workspace) == default_history_grants
    finally:
        await probe.rollback()
    await context(connection, workspace, member, 'fde', team)
    await history_grants(connection, 'fde', 'teams', [team])
    await history_ids(connection, department_recorded, named_history, 'restored_default_fde_department')
    await context(connection, workspace, f['outside_lead'], 'fde_lead', f['other_team'])
    await history_grants(connection, 'fde_lead', 'workspace', [])
    await history_ids(connection, department_recorded, named_history, 'restored_default_lead_workspace')
    checks.append('constrained_self_and_department_scopes_keep_authorship_proxy_and_foreign_department_controls_then_restore_defaults')

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
    # The newly authored same-department row also belongs to the default read scope.
    named_history['lead_own'] = lead_own
    recorded = department_recorded | {lead_own}
    await context(connection, workspace, member, 'fde', team)
    await history_grants(connection, 'fde', 'teams', [team])
    await history_ids(connection, recorded, named_history, 'default_department_after_lead_authors')

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
    # V209 department reads still include the peer/lead's customer projects.
    # Ending the member's last assignment revokes writes, not that read grant.
    assert await connection.fetchval("SELECT security.has_customer_access($1)", customer)
    assert await connection.fetchval("SELECT count(*) FROM activity.visit WHERE id=$1", own) == 1
    await denied(connection, insert_sql, workspace, customer, None, member, team, form, member, member,
                 case='customer_only_after_last_assignment_revoked')
    await denied(connection, "UPDATE activity.visit SET next_action='forbidden' WHERE id=$1", own,
                 case='department_read_does_not_restore_visit_write')
    assert {r["visit_id"] for r in await connection.fetch("SELECT * FROM security.fde_recorded_visit_history()")} == recorded
    checks.append("last_personal_assignment_exit_keeps_department_reads_but_revokes_writes")

    # Retain the original complete read-revocation negative under explicitly
    # assignment-scoped fixture reads. Restore the shipped grants by rollback.
    probe = connection.transaction()
    await probe.start()
    try:
        await connection.execute('RESET ROLE')
        grants = await connection.fetch("""SELECT g.permission_code,g.scope_code,g.team_ids
          FROM config.permission_role_grant g JOIN config.permission_role r
            ON r.id=g.role_id AND r.workspace_id=g.workspace_id
          WHERE g.workspace_id=$1 AND r.builtin_role_code='fde'
            AND g.permission_code=ANY($2::text[])""", workspace,
          ['customer.read','opportunity.read','visit.read'])
        assert {g['permission_code'] for g in grants} == {'customer.read','opportunity.read','visit.read'}
        assert all(g['scope_code']=='departments' and not g['team_ids'] for g in grants)
        await connection.execute("""UPDATE config.permission_role_grant g SET scope_code='assigned'
          FROM config.permission_role r WHERE r.id=g.role_id AND r.workspace_id=g.workspace_id
          AND g.workspace_id=$1 AND r.builtin_role_code='fde'
          AND g.permission_code=ANY($2::text[])""", workspace,
          ['customer.read','opportunity.read','visit.read'])
        assert await history_role_grants(connection, workspace) == default_history_grants
        member_role = next(r['role_id'] for r in default_history_grants if r['builtin_role_code'] == 'fde')
        assert await connection.execute("UPDATE config.permission_role_grant SET scope_code='self' "
            "WHERE role_id=$1 AND permission_code='profile.fde_read'", member_role) == 'UPDATE 1'
        await connection.execute(f'SET LOCAL ROLE "{runtime}"')
        await context(connection, workspace, member, 'fde', team)
        await history_grants(connection, 'fde', 'self', [])
        assert not await connection.fetchval("SELECT security.has_customer_access($1)", customer)
        assert await connection.fetchval("SELECT count(*) FROM activity.visit WHERE id=$1", own) == 0
        await history_ids(connection, own_recorded, named_history, 'assignment_scoped_exit_keeps_self_history')
    finally:
        await probe.rollback()
    probe = connection.transaction()
    await probe.start()
    try:
        await connection.execute('RESET ROLE')
        assert await history_role_grants(connection, workspace) == default_history_grants
    finally:
        await probe.rollback()
    await context(connection, workspace, member, 'fde', team)
    await history_grants(connection, 'fde', 'teams', [team])
    await history_ids(connection, recorded, named_history, 'restored_default_after_assignment_probe')
    assert await connection.fetchval("SELECT security.has_customer_access($1)", customer)
    assert await connection.fetchval("SELECT count(*) FROM activity.visit WHERE id=$1", own) == 1
    checks.append("assignment_scoped_last_customer_exit_preserves_only_minimal_authored_history_without_detail_access")

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
