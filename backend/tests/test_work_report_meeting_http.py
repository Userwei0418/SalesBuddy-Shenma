"""ASGI -> real collector/domain/repository with synthetic SQL, never real RLS proof."""

from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from sales_backend.api import work_reports as api
from sales_backend.api.dependencies import get_database, get_identity
from sales_backend.domain.work_report import WorkReportError
from sales_backend.services.idempotency import IdempotencyConflict
from tests.test_work_report_http import DB, SQL
from tests.test_work_report_meeting_facts import SINCE, TEAM, bundle, opportunity
from tests.test_work_report_repository import ACTOR, NOW, READER


class MeetingSQL(SQL):
    def __init__(self):
        super().__init__()
        self.managed = True
        self.material = bundle(opportunity(owner_before=ACTOR.user_id, owner_after=ACTOR.user_id))
        self.material["members"] = [
            dict(id=ACTOR.user_id, name="负责人", membership_state="current"),
            dict(id=READER.user_id, name="成员", membership_state="current"),
        ]

    async def execute(self, sql, *args):
        result = await super().execute(sql, *args)
        if sql.strip().startswith("INSERT INTO agent.work_report_document"):
            parent = self.rows["snapshot"][args[3]]
            if parent["payload"].get("meeting_facts"):
                self.rows["document"][args[0]]["context"]["meeting_facts"] = deepcopy(
                    parent["payload"]["meeting_facts"]
                )
        return result

    async def fetchrow(self, sql, *args):
        if "current_setting('transaction_isolation')" in sql and "workspace_id" in sql:
            return dict(
                isolation=self.isolation.replace("_", " "),
                rls="on",
                workspace_id=self.actor.workspace_id,
                user_id=self.actor.user_id,
            )
        result = await super().fetchrow(sql, *args)
        if sql.strip().startswith("UPDATE agent.work_report_snapshot") and "status='ready'" in sql:
            if args[3].get("meeting_facts"):
                result["source_manifest"] = deepcopy(args[3]["meeting_dependencies"])
                self.rows["snapshot"][args[0]]["source_manifest"] = deepcopy(result["source_manifest"])
        return result

    async def fetchval(self, sql, *args):
        if "security.work_report_source_access(" in sql:
            return not any(s["record_id"] in self.inaccessible for s in args[0])
        if "WHERE version='V235'" in sql or "WHERE version IN ('V235','V383')" in sql:
            return SINCE
        if sql in {
            'SELECT bool_and(COALESCE(security.authorization_customer($1,ident),false)) FROM unnest($2::uuid[]) AS ident',
            'SELECT bool_and(COALESCE(security.authorization_opportunity($1,ident),false)) FROM unnest($2::uuid[]) AS ident',
            "SELECT bool_and(COALESCE(security.authorization_subject($1,'person',ident,NULL),false)) FROM unnest($2::uuid[]) AS ident",
        }:
            # The accepted 019a collector batches the same resource checks.
            # Preserve per-target denials in this synthetic SQL witness.
            return args[0] not in self.denied and all(ident not in self.inaccessible for ident in args[1])
        if sql.startswith("SELECT security.authorization_subject($1"):
            return args[0] not in self.denied and args[1] not in self.inaccessible
        if sql.startswith("SELECT security.authorization_opportunity($1") or sql.startswith(
            "SELECT security.authorization_customer($1"
        ):
            return args[0] not in self.denied and args[1] not in self.inaccessible
        return await super().fetchval(sql, *args)

    async def fetch(self, sql, *args):
        if "membership_state" in sql:
            assert "security.authorization_subject" in sql and "m.valid_to>$4" in sql
            return deepcopy(self.material["members"])
        if "FROM platform.team t" in sql:
            return [dict(id=TEAM, name="团队")] if self.managed else []
        if "AS forecast_events" in sql:
            assert "security.authorization_opportunity('work_report.write',o.id)" in sql
            result = deepcopy(self.material["opportunities"])
            for row in result:
                row["creation_date"] = dict(created_at=row.pop("business_created_at"), historical=False)
            return result
        if "FROM crm.customer_ownership_event e" in sql:
            assert "e.event_type='approved'" in sql and "r.status='approved'" in sql
            return deepcopy(self.material["claims"])
        if "FROM activity.visit v JOIN crm.customer" in sql:
            assert "v.status IN ('confirmed','archived')" in sql and "v.interaction_at>=$4" in sql
            return deepcopy(self.material["visits"])
        if "FROM workflow.task t WHERE" in sql:
            return deepcopy(self.material["tasks"])
        return await super().fetch(sql, *args)


@pytest_asyncio.fixture
async def meeting_http(monkeypatch):
    import socket

    import asyncpg

    def forbidden(*a, **kw):
        raise AssertionError("offline meeting test opened network")

    monkeypatch.setattr(asyncpg, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    db = DB()
    db.connection = MeetingSQL()
    identity = SimpleNamespace(actor=ACTOR)
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_database] = lambda: db
    app.dependency_overrides[get_identity] = lambda: identity

    @app.exception_handler(WorkReportError)
    async def report_error(_, error):
        return JSONResponse(status_code=error.status, content=dict(code=error.code, detail=error.detail))

    @app.exception_handler(IdempotencyConflict)
    async def idempotency_error(_, error):
        return JSONResponse(status_code=409, content=dict(code="IDEMPOTENCY_CONFLICT"))

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://offline.invalid") as http:
        yield http, db, identity


async def post(http, path, body, *, key=None, status=200):
    response = await http.post(
        "/api/v1/work-reports" + path, json=body, headers={"Idempotency-Key": key or str(uuid4())}
    )
    assert response.status_code == status, response.text
    return response.json()


@pytest.mark.asyncio
async def test_prepare_read_private_save_edit_share_and_replay(meeting_http):
    http, db, identity = meeting_http
    body = dict(period=dict(kind="week"), scope=dict(kind="managed_team", team_id=TEAM))
    key = str(uuid4())
    value = await post(http, "/meeting/snapshots", body, key=key, status=202)
    assert value["status"] == "ready" and value["scope"]["kind"] == "managed_team"
    assert value["meeting_facts"]["amounts"]["acv"]["change"] == "-300000.00"
    assert await post(http, "/meeting/snapshots", body, key=key, status=202) == value
    assert len(db.connection.rows["snapshot"]) == 1 and not db.connection.jobs
    assert (await http.get("/api/v1/work-reports/snapshots/" + value["id"])).status_code == 404
    snapshot_read = await http.get("/api/v1/work-reports/meeting/snapshots/" + value["id"])
    assert snapshot_read.json() == value
    save = dict(
        snapshot_id=value["id"],
        snapshot_hash=value["snapshot_hash"],
        title="团队周会",
        builtin_scheme_id="meeting.week.v1",
    )
    key = str(uuid4())
    doc = await post(http, "/meeting/documents", save, key=key, status=201)
    assert doc["status"] == "private" and doc["shared_revision_id"] is None
    assert doc["meeting_facts"] == value["meeting_facts"]
    assert doc["revision"]["run_id"] is None and not db.connection.rows["run"]
    assert await post(http, "/meeting/documents", save, key=key, status=201) == doc
    assert len(db.connection.rows["document"]) == 1
    edit = deepcopy(doc["revision"]["content"])
    edit["sections"][0]["items"] = [dict(kind="manual", text="会议补充")]
    changed = await post(http, f"/documents/{doc['id']}/revisions", dict(version_no=doc["version_no"], content=edit))
    assert changed["meeting_facts"] == value["meeting_facts"] and changed["status"] == "private"
    identity.actor = READER
    assert (await http.get("/api/v1/work-reports/documents/" + doc["id"])).status_code == 404
    identity.actor = ACTOR
    shared = await post(
        http,
        f"/documents/{doc['id']}/share",
        dict(
            version_no=changed["version_no"], confirm=True, audience=dict(kind="members", member_ids=[READER.user_id])
        ),
    )
    identity.actor = READER
    received = (await http.get("/api/v1/work-reports/documents/" + doc["id"])).json()
    assert (
        received["meeting_facts"] == value["meeting_facts"]
        and received["revision"]["content"] == changed["revision"]["content"]
    )
    assert received["shared_revision_id"] == shared["shared_revision_id"]
    exported = await http.get(f"/api/v1/work-reports/documents/{doc['id']}/export")
    assert exported.status_code == 200 and "会议补充" in exported.json()["text"]


@pytest.mark.asyncio
async def test_meeting_generation_and_saved_source_revocations_are_rechecked(meeting_http):
    http, db, identity = meeting_http
    selection = dict(period=dict(kind="day"), scope=dict(kind="managed_team", team_id=TEAM))
    value = await post(http, "/meeting/snapshots", selection, status=202)
    save = dict(
        snapshot_id=value["id"], snapshot_hash=value["snapshot_hash"], title="日会", builtin_scheme_id="meeting.day.v1"
    )
    await post(http, "/meeting/documents", {**save, "snapshot_hash": "f" * 64}, status=409)
    await post(http, "/meeting/documents", {**save, "builtin_scheme_id": "meeting.week.v1"}, status=422)
    db.connection.managed = False
    await post(http, "/meeting/documents", save, status=403)
    await post(http, "/meeting/snapshots", selection, status=403)
    db.connection.managed = True
    doc = await post(http, "/meeting/documents", save, status=201)
    db.connection.inaccessible.add(ACTOR.user_id)
    assert (await http.get("/api/v1/work-reports/meeting/snapshots/" + value["id"])).status_code == 403
    assert (await http.get("/api/v1/work-reports/documents/" + doc["id"])).status_code == 403
    db.connection.inaccessible.clear()
    identity.actor = READER
    assert (await http.get("/api/v1/work-reports/meeting/snapshots/" + value["id"])).status_code == 404
    identity.actor = ACTOR.model_copy(update={"workspace_id": str(UUID(int=999))})
    assert (await http.get("/api/v1/work-reports/documents/" + doc["id"])).status_code == 404


@pytest.mark.asyncio
async def test_schema_and_meeting_options_are_real_five_period_server_choices(meeting_http):
    http, db, _ = meeting_http
    value = (await http.get("/api/v1/work-reports/meeting/options")).json()
    assert len(value["default_schemes"]) == 5 and value["available_scope_kinds"] == ["self", "managed_team"]
    assert value["teams"][0]["team_id"] == TEAM
    db.connection.managed = False
    value = (await http.get("/api/v1/work-reports/meeting/options")).json()
    assert value["available_scope_kinds"] == ["self"] and value["teams"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source_case", ["visit_opportunity", "task_opportunity", "visit_customer", "opportunity_customer"]
)
async def test_linked_source_revocation_blocks_snapshot_document_export_and_replay(meeting_http, source_case):
    http, db, _ = meeting_http
    linked, customer, subject = [str(UUID(int=i)) for i in (900, 901, 902)]
    db.connection.material["opportunities"] = []
    if source_case.startswith("visit"):
        db.connection.material["visits"] = [
            dict(
                id=subject,
                version_no=1,
                customer_id=customer,
                customer_name="关联客户",
                name="跟进",
                opportunity_id=linked,
                opportunity_name="范围外但可读的关联商机",
                interaction_at=NOW - timedelta(hours=1),
                summary="正式跟进",
                next_action="本月底前评审",
                member_ids=[ACTOR.user_id],
            )
        ]
    elif source_case.startswith("task"):
        db.connection.material["tasks"] = [
            dict(
                id=subject,
                version_no=1,
                title="已登记任务",
                opportunity_id=linked,
                member_ids=[ACTOR.user_id],
                due_at=None,
            )
        ]
    else:
        row = opportunity(owner_before=ACTOR.user_id, owner_after=ACTOR.user_id)
        row["customer_id"], row["customer_name"] = customer, "关联客户"
        db.connection.material["opportunities"] = [row]
    selection = dict(period=dict(kind="week"), scope=dict(kind="managed_team", team_id=TEAM))
    prepare_key = str(uuid4())
    snapshot = await post(http, "/meeting/snapshots", selection, key=prepare_key, status=202)
    save = dict(
        snapshot_id=snapshot["id"],
        snapshot_hash=snapshot["snapshot_hash"],
        title="来源权限测试",
        builtin_scheme_id="meeting.week.v1",
    )
    save_key = str(uuid4())
    document = await post(http, "/meeting/documents", save, key=save_key, status=201)
    # Keep the visit/task/opportunity itself readable; revoke only its referenced
    # business object, which is outside the meeting monetary population.
    db.connection.inaccessible.add(customer if source_case.endswith("customer") else linked)
    assert (await http.get("/api/v1/work-reports/meeting/snapshots/" + snapshot["id"])).status_code == 403
    assert (await http.get("/api/v1/work-reports/documents/" + document["id"])).status_code == 403
    assert (await http.get("/api/v1/work-reports/documents/" + document["id"] + "/export")).status_code == 403
    await post(
        http,
        f"/documents/{document['id']}/share",
        dict(
            version_no=document["version_no"], confirm=True, audience=dict(kind="members", member_ids=[READER.user_id])
        ),
        status=403,
    )
    await post(
        http,
        f"/documents/{document['id']}/revisions",
        dict(version_no=document["version_no"], content=document["revision"]["content"]),
        status=403,
    )
    await post(http, "/meeting/snapshots", selection, key=prepare_key, status=403)
    await post(http, "/meeting/documents", save, key=save_key, status=403)
