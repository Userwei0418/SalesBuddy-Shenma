"""Verify task recipients and link scope with existing accounts in one rolled-back RLS transaction."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from sales_backend.db import set_request_context
from sales_backend.domain.tasks import TaskConflict, TaskForbidden, TaskNotFound
from sales_backend.repositories.customer_mutations import CustomerMutationRepository
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.repositories.notifications import NotificationRepository
from sales_backend.repositories.task_links import TaskLinkRepository
from sales_backend.repositories.task_targets import TaskTargetRepository
from sales_backend.repositories.tasks import TaskRepository
from sales_backend.services.task_coordination import coordinate_task
from sales_backend.services.tasks import TaskService, validate_task_links

PG_SOCKET = "/tmp"  # noqa: S108 -- managed PostgreSQL socket


async def verify(c):
    async def login(account, role=None):
        row = await c.fetchrow(
            "SELECT * FROM security.resolve_account_actor('demo-sales-workspace',$1,$2)", account, role)
        assert row, "Required existing verification account unavailable"
        actor = IdentityRepository._actor(row).context
        await set_request_context(c, actor)
        return actor

    links, tasks, service = TaskLinkRepository(), TaskRepository(), TaskService()
    operator = await login("OPSADMIN")
    marker = "任务范围回滚核验-" + uuid4().hex
    empty = await CustomerMutationRepository().create(c, operator, data={
        "name": marker, "industry": "软件", "customer_type": "潜在客户", "level_code": "Tier-2",
        "source": "销售线索", "target_team": "南区", "partner_name": "", "contact_name": "核验联系人",
        "contact_title": "经理", "contact_role": "决策者", "company_reference": marker,
    })
    assert not (await links.customers(c, operator, query=marker))["items"]
    creator = await login("XS001")
    first = await links.customers(c, creator, limit=1)
    assert first["items"], "Needs an existing customer with creator-authorized opportunities"
    cid = first["items"][0]["id"]
    if first["has_more"]:
        second = await links.customers(c, creator, limit=1, offset=first["next_offset"])
        assert second["items"] and second["items"][0]["id"] != cid
    choices = await links.opportunities(c, creator, customer_id=cid, limit=2)
    opp = choices["items"][0]
    oid = opp["id"]
    assert await links.allowed(c, creator, oid)
    assert not (await links.opportunities(c, creator, customer_id=empty["id"], opportunity_id=oid))["items"]
    people = await TaskTargetRepository().recipients(c, creator)
    selected = {}
    # Prefer another sales person to exercise the originally failing recipient boundary.
    for p in sorted(people, key=lambda p: p["id"] == creator.user_id):
        selected.setdefault(p["role"], p)
    assert {"sales", "supervisor", "manager", "administrator", "fde", "fde_lead"} <= set(selected)
    assert set(selected) <= {"sales", "supervisor", "manager", "operations", "administrator", "fde", "fde_lead"}
    assert (await TaskTargetRepository().recipients(c, creator, limit=1, offset=1))[0]["id"] == people[1]["id"]
    ids, role_results, restricted_roles = [], [], []
    before_members = await c.fetchval(
        "SELECT count(*) FROM crm.opportunity_participant WHERE opportunity_id=$1::uuid", oid)

    async def create(account, kind="customer"):
        await set_request_context(c, creator)
        row = await service.create(c, actor=creator, description=marker, due_at=datetime.now(UTC)+timedelta(days=2),
            priority_code="medium", assignee_account_code=account, association_kind=kind,
            customer_id=cid if kind=="customer" else None, opportunity_id=oid if kind=="customer" else None)
        ids.append(row["id"])
        return row

    for role, person in selected.items():
        recipient = await login(person["account_code"], role)
        before_access = await c.fetchval("SELECT security.has_opportunity_read_access($1::uuid)", oid)
        before_link = await links.allowed(c, recipient, oid)
        row = await create(person["account_code"])
        await set_request_context(c, recipient)
        detail = await tasks.detail(c, task_id=row["id"])
        assert detail and not detail["handover_required"] and detail["requires_action"]
        assert detail["customer_name"] and detail["opportunity_name"] == opp["name"]
        assert detail["can_open_opportunity"] == before_access
        notices = await NotificationRepository().list(c, unread_only=True, limit=100)
        assert any(n["object_id"]==row["id"] and n["template_code"]=="task_assigned" for n in notices)
        if not before_access:
            restricted_roles.append(role)
            assert not await c.fetchval("SELECT count(*) FROM crm.opportunity WHERE id=$1::uuid", oid)
        if not before_link:
            assert not (await links.opportunities(c, recipient, customer_id=cid, opportunity_id=oid))["items"]
            try:
                await validate_task_links(c, recipient, cid, oid, "customer")
            except (TaskConflict, TaskForbidden, TaskNotFound):
                pass
            else:
                raise AssertionError("Receiving task must not grant permission to associate its opportunity")
        accepted = await service.apply_event(c, actor=recipient, task_id=row["id"], event_type="accept",
                                             note=None, expected_version=detail["version_no"])
        assert accepted["status"] == "pending_execution"
        done = await service.complete(c, actor=recipient, task_id=row["id"], note="回滚核验完成",
                                      expected_version=accepted["version_no"])
        assert done["status"] == "completed"
        await set_request_context(c, creator)
        for filt in ({"customer_id":cid}, {"opportunity_id":oid, "customer_id":None}):
            rows = await tasks.list(c, status=None, limit=100, **filt)
            assert next(t for t in rows if t["id"]==row["id"])["status"] == "completed"
        notices = await NotificationRepository().list(c, unread_only=True, limit=100)
        templates = {n["template_code"] for n in notices if n["object_id"]==row["id"]}
        assert {"task_accepted", "task_completed"} <= templates
        role_results.append(role)
    assert "sales" in restricted_roles, "Cross-sales recipient must lack opportunity detail access"

    # Daily tasks still have no CRM links; legacy position processing remains untouched.
    daily = await create("XS001", "daily")
    assert daily["customer_id"] is None and daily["opportunity_id"] is None
    row = await create(selected["sales"]["account_code"])
    denied = False
    try:
        await service.apply_event(c, actor=creator, task_id=row["id"], event_type="accept",
                                  note=None, expected_version=row["version_no"])
    except TaskForbidden:
        denied = True
    assert denied, "Creator cannot accept on another person's behalf"
    recipient = await login(selected["sales"]["account_code"], "sales")
    rejected = await service.apply_event(c, actor=recipient, task_id=row["id"], event_type="reject",
                                        note="本期无法安排", expected_version=row["version_no"])
    assert rejected["status"] == "cancelled" and rejected["last_event_note"] == "本期无法安排"
    await set_request_context(c, creator)
    assert any(n["object_id"]==row["id"] and n["template_code"]=="task_rejected"
               for n in await NotificationRepository().list(c, unread_only=True, limit=100))
    row = await create(selected["sales"]["account_code"])
    transferred = await coordinate_task(c, actor=creator, task_id=row["id"], event_type="reassign",
        note="转交给技术同事处理", expected_version=row["version_no"], account=selected["fde"]["account_code"])
    assert transferred["status"] == "pending_confirm" and not transferred["handover_required"]
    recipient = await login(selected["fde"]["account_code"], "fde")
    assert (await service.apply_event(c, actor=recipient, task_id=row["id"], event_type="accept", note=None,
                                     expected_version=transferred["version_no"]))["status"] == "pending_execution"
    await set_request_context(c, creator)
    assert before_members == await c.fetchval(
        "SELECT count(*) FROM crm.opportunity_participant WHERE opportunity_id=$1::uuid", oid)
    await set_request_context(c, creator.model_copy(update={"workspace_id":str(uuid4())}))
    assert not (await links.customers(c, creator))["items"]
    assert await tasks.detail(c, task_id=ids[0]) is None
    return {"task_ids":ids, "customer_ids":[empty["id"]]}, {
        "roles_completed":role_results, "recipients_without_crm_access":restricted_roles,
        "checks":["no_opportunity_customer_excluded", "creator_scope_and_paging", "all_role_directory",
                  "assignment_notification", "accept_and_complete", "customer_and_opportunity_rollup",
                  "creator_response_notifications", "reject_reason", "cross_role_handover", "daily_no_links",
                  "non_owner_cannot_accept", "no_crm_scope_or_membership_grant", "workspace_isolation"],
    }


async def main(args):
    c = await asyncpg.connect(host=PG_SOCKET, database=args.database, user="postgres", command_timeout=20)
    for name in ("json", "jsonb"):
        await c.set_type_codec(name, schema="pg_catalog", encoder=json.dumps, decoder=json.loads)
    tx = c.transaction()
    await tx.start()
    try:
        await c.execute("SET LOCAL ROLE sales_runtime")
        assert not await c.fetchval("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
        ids, result = await verify(c)
    finally:
        await tx.rollback()
        await c.close()
    c = await asyncpg.connect(host=PG_SOCKET, database=args.database, user="postgres", command_timeout=20)
    try:
        assert not await c.fetchval("SELECT count(*) FROM workflow.task WHERE id=ANY($1::uuid[])", ids["task_ids"])
        assert not await c.fetchval("SELECT count(*) FROM crm.customer WHERE id=ANY($1::uuid[])", ids["customer_ids"])
    finally:
        await c.close()
    print(json.dumps({"status":"passed", "runtime_role":"sales_runtime", "rolled_back":True, **result}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    asyncio.run(main(parser.parse_args()))
