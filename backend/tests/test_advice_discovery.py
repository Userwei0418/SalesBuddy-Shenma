"""Offline discovery contracts and real SELECT predicates; no live DB/model calls."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
import json
import re
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from sales_backend.api import advice as api
from sales_backend.domain.advice import AdviceError, AdviceRequest, fingerprint, messages
from sales_backend.domain.agent import ActorContext
from sales_backend.domain.route_permissions import route_permission
from sales_backend.repositories.capabilities import CapabilityRepository
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.services.advice import AdviceService, cache_key
from sales_backend.services.agent_platform.inference import InferenceService


class ReadDatabase:
    """Execute repository SELECTs in memory; enforce read-only transaction use."""

    def __init__(self):
        self.settings = object()
        self.sql = sqlite3.connect(":memory:")
        self.sql.row_factory = sqlite3.Row
        self.sql.create_function("gen_random_uuid", 0, lambda: str(uuid4()))
        self.sql.execute("ATTACH DATABASE ':memory:' AS insight")
        self.sql.execute("""CREATE TABLE insight.business_advice (
            id TEXT DEFAULT (gen_random_uuid()), workspace_id TEXT, actor_user_ref_id TEXT, actor_role_code TEXT,
            subject_kind TEXT, subject_id TEXT, section TEXT, created_at TEXT DEFAULT (clock_timestamp()),
            customer_id TEXT, opportunity_id TEXT, status TEXT DEFAULT 'queued', summary TEXT,
            facts_snapshot TEXT, facts_fingerprint TEXT, cache_key TEXT, completed_at TEXT,
            updated_at TEXT DEFAULT (clock_timestamp()), configuration_snapshot TEXT, error_code TEXT,
            visit_id TEXT, configuration_fingerprint TEXT, identity_snapshot TEXT)""")
        self.read_allowed = True
        self.transactions = 0

    def insert(self, row):
        values = {**row, "facts_snapshot": json.dumps(row["facts_snapshot"])}
        values["updated_at"] = row.get("updated_at", row["created_at"])
        values["configuration_snapshot"] = json.dumps(row.get("configuration_snapshot", {}))
        self.sql.execute("INSERT INTO insight.business_advice (" + ",".join(values) + ") VALUES ("
                         + ",".join("?" for _ in values) + ")",
                         list(values.values()))

    @asynccontextmanager
    async def transaction(self, actor, *, readonly=False):
        assert readonly, "Discovery must never open a writable transaction"
        self.transactions += 1
        yield self

    async def fetchrow(self, sql, *args):
        assert sql.startswith("SELECT ") and "FOR UPDATE" not in sql
        sql = re.sub(r"::(?:uuid|text|bigint)", "", sql)
        sql = re.sub(r"\$\d+", "?", sql)
        row = self.sql.execute(sql, args).fetchone()
        if row is None:
            return None
        value = dict(row)
        if "facts_snapshot" in value:
            value["facts_snapshot"] = json.loads(value["facts_snapshot"])
        return value

    async def fetchval(self, sql, *args):
        if sql.startswith("SELECT security.authorization_"):
            assert args[0] == "advice.read"
            return self.read_allowed
        row = await self.fetchrow(sql, *args)
        return next(iter(row.values())) if row else None

    async def fetch(self, sql, *args):
        assert sql.lstrip().startswith("SELECT ")
        return []  # No suggestion/task fixtures are required for discovery.


class RequestDatabase(ReadDatabase):
    """Replay explicit requests and later worker/decision SQL in memory only."""

    def __init__(self):
        super().__init__()
        self.modes = []
        self.writes = []
        self.readonly = True
        self.clock = datetime(2026, 10, 14, 12, tzinfo=UTC)
        self.sql.create_function("clock_timestamp", 0, self.now)
        self.sql.create_function("to_jsonb", 1, json.dumps)
        self.sql.create_function("jsonb_set", 4, self.jsonb_set)
        self.sql.execute("""CREATE TABLE insight.business_suggestion (
            id TEXT, advice_id TEXT, version_no INTEGER, decision TEXT,
            decision_note TEXT, decided_by_user_ref_id TEXT, decided_at TEXT, task_id TEXT)""")

    def now(self):
        self.clock += timedelta(microseconds=1)
        return self.clock.isoformat()

    @staticmethod
    def jsonb_set(value, path, replacement, create):
        assert path == "{_request_sequence}" and create
        return json.dumps({**json.loads(value), "_request_sequence": json.loads(replacement)})

    @asynccontextmanager
    async def transaction(self, actor, *, readonly=False):
        previous = self.readonly
        self.readonly = readonly
        self.modes.append(readonly)
        try:
            yield self
        finally:
            self.readonly = previous

    async def fetchrow(self, sql, *args):
        if "FOR UPDATE" in sql:
            assert not self.readonly
            sql = sql.replace(" FOR UPDATE", "")
        return await super().fetchrow(sql, *args)

    async def fetchval(self, sql, *args):
        if sql.startswith("INSERT INTO insight.business_advice"):
            assert not self.readonly
            self.writes.append(sql)
            sql = re.sub(r"::(?:uuid|text|jsonb)", "", sql)
            sql = re.sub(r"\$\d+", "?", sql)
            values = [json.dumps(arg) if isinstance(arg, dict) else arg for arg in args]
            return self.sql.execute(sql, values).fetchone()[0]
        if sql.startswith("SELECT security.authorization_"):
            assert args[0] in {"advice.read", "advice.request", "advice.decide"}
            return self.read_allowed
        return await super().fetchval(sql, *args)

    async def execute(self, sql, *args):
        assert not self.readonly, "GET must not write request time or advice state"
        if sql.startswith("SELECT pg_advisory_xact_lock"):
            return  # No concurrent requests in this in-memory replay.
        assert sql.startswith("UPDATE insight.business_")
        self.writes.append(sql)
        positions = [int(index) - 1 for index in re.findall(r"\$(\d+)", sql)]
        sql = re.sub(r"::(?:uuid|text|bigint)", "", sql)
        sql = re.sub(r"\$\d+", "?", sql)
        return self.sql.execute(sql, [args[index] for index in positions])


@pytest.fixture
def harness(monkeypatch):
    actor = ActorContext(workspace_id=str(uuid4()), user_id=str(uuid4()), role="sales", data_scope="self")
    subject = AdviceRequest(subject_kind="opportunity", subject_id=str(uuid4()))
    db = ReadDatabase()
    service = AdviceService(db)
    service.runtime = AsyncMock(return_value=object())
    service.facts.load = AsyncMock(return_value={"fingerprint": "facts-v1"})
    identity = AsyncMock(return_value=SimpleNamespace(context=actor))
    monkeypatch.setattr(IdentityRepository, "find_actor_by_id", identity)
    permission = AsyncMock(return_value={"permission_version": 3})
    monkeypatch.setattr(CapabilityRepository, "analysis_identity", permission)
    config = {"prompt": "v1"}
    monkeypatch.setattr("sales_backend.services.advice.configuration", lambda *args: config)
    forbidden = AsyncMock(side_effect=AssertionError("Read path must not generate or enqueue"))
    monkeypatch.setattr(InferenceService, "evaluate", forbidden)
    monkeypatch.setattr(service, "request", forbidden)
    monkeypatch.setattr(service.repo, "enqueue", forbidden)
    row = dict(id=str(uuid4()), workspace_id=actor.workspace_id, actor_user_ref_id=actor.user_id,
               actor_role_code="sales", subject_kind=subject.subject_kind, subject_id=subject.subject_id,
               section="overview", created_at="2026-10-13T10:00:00Z", customer_id=str(uuid4()),
               opportunity_id=subject.subject_id, status="succeeded", summary="已确认建议",
               facts_snapshot={"coverage": {}}, facts_fingerprint="facts-v1",
               cache_key=cache_key(actor, subject.subject_kind, subject.subject_id, subject.section,
                                   "facts-v1", config, 3), completed_at=None)
    yield SimpleNamespace(actor=actor, subject=subject, db=db, service=service, row=row,
                          config=config, permission=permission, identity=identity, forbidden=forbidden)
    forbidden.assert_not_awaited()
    db.sql.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["customer", "opportunity", "visit"])
@pytest.mark.parametrize("section", ["overview", "tasks", "visits", "opportunity"])
async def test_none_validates_subject_without_loading_generation_configuration(harness, kind, section):
    h = harness
    subject = AdviceRequest(subject_kind=kind, subject_id=str(uuid4()), section=section)
    assert await h.service.latest(h.actor, subject) == {"state": "none", "analysis": None}
    h.service.facts.load.assert_awaited_once_with(h.db, h.actor, kind, subject.subject_id)
    h.service.runtime.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("workspace_id", str(uuid4())), ("actor_user_ref_id", str(uuid4())),
    ("actor_role_code", "fde"), ("subject_kind", "customer"),
    ("subject_id", str(uuid4())), ("section", "tasks"),
])
async def test_latest_select_excludes_every_other_scope(harness, field, value):
    h = harness
    h.db.insert({**h.row, field: value})
    assert await h.service.latest(h.actor, h.subject) == {"state": "none", "analysis": None}


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["queued", "running", "succeeded", "failed", "superseded"])
async def test_latest_returns_newest_batch_including_failed_and_pending(harness, status):
    h = harness
    h.db.insert({**h.row, "id": "00000000-0000-0000-0000-000000000001"})
    newest = {**h.row, "id": "ffffffff-ffff-ffff-ffff-ffffffffffff", "status": status}
    h.db.insert(newest)  # Equal timestamps exercise deterministic ID tie breaking.
    result = await h.service.latest(h.actor, h.subject)
    assert result["state"] == ("stale" if status == "superseded" else status)
    assert result["analysis"]["id"] == newest["id"]
    assert result["analysis"]["status"] == status
    assert (await h.service.get(h.actor, newest["id"])) == result["analysis"]


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["facts", "permission", "configuration"])
async def test_discovery_keeps_full_freshness_check(harness, changed):
    h = harness
    h.db.insert(h.row)
    if changed == "facts":
        h.service.facts.load.return_value = {"fingerprint": "facts-v2"}
    elif changed == "permission":
        h.permission.return_value = {"permission_version": 4}
    else:
        h.config["prompt"] = "v2"
    result = await h.service.latest(h.actor, h.subject)
    assert result["state"] == "stale"
    assert result["analysis"]["status"] == "superseded"


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["workspace_id", "actor_user_ref_id", "actor_role_code"])
async def test_cached_analysis_id_cannot_cross_identity_or_role(harness, field):
    h = harness
    h.db.insert({**h.row, field: "fde" if field == "actor_role_code" else str(uuid4())})
    with pytest.raises(AdviceError) as exc:
        await h.service.get(h.actor, h.row["id"])
    assert exc.value.status == 404
    h.service.runtime.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 422])
async def test_missing_forbidden_or_pure_partner_subject_is_not_none(harness, status):
    h = harness
    h.service.facts.load.side_effect = AdviceError("不可读取或不适用", status)
    with pytest.raises(AdviceError) as exc:
        await h.service.latest(h.actor, h.subject)
    assert exc.value.status == status


@pytest.mark.asyncio
async def test_read_permission_and_current_identity_are_required_even_without_history(harness):
    h = harness
    h.db.read_allowed = False
    with pytest.raises(AdviceError) as exc:
        await h.service.latest(h.actor, h.subject)
    assert exc.value.status == 404
    h.service.facts.load.assert_not_awaited()
    h.identity.return_value = None
    with pytest.raises(AdviceError) as exc:
        await h.service.latest(h.actor, h.subject)
    assert exc.value.status == 403


@pytest.mark.asyncio
async def test_permission_revocation_during_configuration_load_is_rechecked(harness):
    h = harness
    h.db.insert(h.row)

    async def revoke(*args):
        h.db.read_allowed = False
        return object()

    h.service.runtime.side_effect = revoke
    with pytest.raises(AdviceError) as exc:
        await h.service.latest(h.actor, h.subject)
    assert exc.value.status == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["lookup", "configuration", "facts"])
async def test_read_errors_propagate_without_empty_or_generation_fallback(harness, failure):
    h = harness
    h.db.insert(h.row)
    error = RuntimeError("read unavailable")
    target = {"lookup": h.service.repo, "configuration": h.service, "facts": h.service.facts}[failure]
    name = {"lookup": "latest_id", "configuration": "runtime", "facts": "load"}[failure]
    setattr(target, name, AsyncMock(side_effect=error))
    with pytest.raises(RuntimeError, match="read unavailable"):
        await h.service.latest(h.actor, h.subject)


@pytest.mark.asyncio
async def test_http_latest_precedes_uuid_route_validates_input_and_preserves_errors(monkeypatch, harness):
    h = harness
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.get_identity] = lambda: SimpleNamespace(actor=h.actor)
    app.dependency_overrides[api.get_database] = lambda: h.db
    app.dependency_overrides[api.get_settings] = lambda: h.db.settings
    monkeypatch.setattr(api, "AdviceService", lambda *args: h.service)
    params = {"subject_kind": "visit", "subject_id": str(uuid4()), "section": "tasks"}
    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as client:
        result = await client.get("/api/v1/advice/latest", params=params)
        assert result.status_code == 200 and result.json() == {"state": "none", "analysis": None}
        for field, value in (("subject_kind", "partner"), ("subject_id", "invalid"), ("section", "unknown")):
            assert (await client.get("/api/v1/advice/latest", params={**params, field: value})).status_code == 422
        for status in (403, 404, 422):
            h.service.latest = AsyncMock(side_effect=AdviceError("读取失败", status))
            assert (await client.get("/api/v1/advice/latest", params=params)).status_code == status
        h.service.latest = AsyncMock(side_effect=RuntimeError("database unavailable"))
        assert (await client.get("/api/v1/advice/latest", params=params)).status_code == 500


def test_discovery_uses_existing_read_permission():
    assert route_permission("advice", "latest_advice") == "advice.read"
    assert route_permission("advice", "advice_detail") == "advice.read"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["reactivate", "fresh_cache", "retry_failed", "read_failed"])
async def test_explicit_request_selects_older_cached_batch_and_readonly_reentry_recovers_it(harness, mode):
    h = harness
    db = RequestDatabase()
    h.service.database = db
    del h.service.request  # Exercise the actual explicit POST service, not a stub.
    enqueue = AsyncMock()
    h.service.repo.enqueue = enqueue
    old_status = {"reactivate": "superseded", "fresh_cache": "succeeded",
                  "retry_failed": "failed", "read_failed": "failed"}[mode]
    older = {**h.row, "id": "00000000-0000-0000-0000-000000000001", "status": old_status,
             "summary": None if "failed" in mode else "已确认旧建议"}
    newer = {**h.row, "id": "00000000-0000-0000-0000-000000000002",
             "created_at": "2026-10-13T11:00:00Z", "facts_fingerprint": "facts-v2",
             "status": "failed" if mode == "fresh_cache" else "succeeded",
             "cache_key": cache_key(h.actor, h.subject.subject_kind, h.subject.subject_id,
                                    h.subject.section, "facts-v2", h.config, 3)}
    try:
        db.insert(older)
        db.insert(newer)
        request = h.subject.model_copy(update={"retry": mode == "retry_failed"})
        accepted = await h.service.request(h.actor, request)
        expected = "queued" if mode == "retry_failed" else ("failed" if mode == "read_failed" else "succeeded")
        assert accepted["id"] == older["id"] and accepted["status"] == expected
        assert enqueue.await_count == (1 if mode == "retry_failed" else 0)
        before = [dict(row) for row in db.sql.execute("SELECT * FROM insight.business_advice ORDER BY id")]
        write_count, mode_count = len(db.writes), len(db.modes)
        recovered = await h.service.latest(h.actor, h.subject)
        assert recovered == {"state": expected, "analysis": accepted}
        assert recovered["analysis"] == await h.service.get(h.actor, older["id"])
        after = [dict(row) for row in db.sql.execute("SELECT * FROM insight.business_advice ORDER BY id")]
        assert before == after and len(db.writes) == write_count
        assert all(db.modes[mode_count:])
        selected = next(row for row in after if row["id"] == older["id"])
        assert selected["created_at"] == older["created_at"]
        assert selected["cache_key"] == older["cache_key"]
        assert json.loads(selected["configuration_snapshot"])["_request_sequence"] == 1
    finally:
        db.sql.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("late_status", ["succeeded", "failed", "queued"])
async def test_late_worker_status_or_updated_time_does_not_override_explicit_request(harness, late_status):
    h = harness
    db = RequestDatabase()
    h.service.database = db
    del h.service.request
    old = {**h.row, "id": "00000000-0000-0000-0000-000000000001", "status": "superseded"}
    later = {**h.row, "id": "00000000-0000-0000-0000-000000000002", "status": "queued",
             "created_at": "2026-10-13T11:00:00Z", "cache_key": "different-facts-key"}
    try:
        db.insert(old)
        db.insert(later)
        assert (await h.service.request(h.actor, h.subject))["id"] == old["id"]
        # The Python worker's late error can overwrite a superseded status.
        async with db.transaction(h.actor):
            await db.execute("UPDATE insight.business_advice SET status=$2,error_code=$3 WHERE id=$1::uuid",
                             later["id"], "queued", "LateError")
            # V222 invalid-actor handling also advances updated_at for queued/running.
            await db.execute("UPDATE insight.business_advice SET status=$2,error_code='INVALID_JOB_ACTOR',"
                             "completed_at=clock_timestamp(),updated_at=clock_timestamp() "
                             "WHERE id=$1::uuid AND status IN ('queued','running')", later["id"], late_status)
        recovered = await h.service.latest(h.actor, h.subject)
        assert recovered["analysis"]["id"] == old["id"] and recovered["state"] == "succeeded"
        # An actual manual decision changes the suggestion, not request order.
        suggestion = str(uuid4())
        db.sql.execute("INSERT INTO insight.business_suggestion(id,advice_id,version_no,decision) VALUES (?,?,1,'pending')",
                       (suggestion, old["id"]))
        async with db.transaction(h.actor):
            await h.service.decide(db, h.actor, suggestion, "no_task", "人工确认无需待办", 1, None, object())
        assert (await h.service.latest(h.actor, h.subject))["analysis"]["id"] == old["id"]
        config = json.loads(db.sql.execute("SELECT configuration_snapshot FROM insight.business_advice WHERE id=?",
                                          (old["id"],)).fetchone()[0])
        assert config == {"_request_sequence": 1}
    finally:
        db.sql.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["queued", "failed"])
async def test_a_later_explicit_request_still_supersedes_reactivated_success(harness, status):
    h = harness
    db = RequestDatabase()
    h.service.database = db
    del h.service.request
    old = {**h.row, "id": "00000000-0000-0000-0000-000000000001", "status": "superseded"}
    newer = {**h.row, "id": "00000000-0000-0000-0000-000000000002", "status": status,
             "created_at": "2026-10-13T11:00:00Z", "facts_fingerprint": "facts-v2",
             "summary": None, "cache_key": cache_key(h.actor, h.subject.subject_kind, h.subject.subject_id,
                                                        h.subject.section, "facts-v2", h.config, 3)}
    try:
        db.insert(old)
        db.insert(newer)
        assert (await h.service.request(h.actor, h.subject))["id"] == old["id"]
        # Model the later existing batch's queued/failed status before its explicit request.
        async with db.transaction(h.actor):
            await db.execute("UPDATE insight.business_advice SET status=$2 WHERE id=$1::uuid", newer["id"], status)
        h.service.facts.load.return_value = {"fingerprint": "facts-v2"}
        selected = await h.service.request(h.actor, h.subject)
        assert selected["id"] == newer["id"] and selected["status"] == status
        assert await h.service.latest(h.actor, h.subject) == {"state": status, "analysis": selected}
        snapshots = [json.loads(row[0]) for row in db.sql.execute(
            "SELECT configuration_snapshot FROM insight.business_advice ORDER BY id")]
        assert [row["_request_sequence"] for row in snapshots] == [1, 2]
    finally:
        db.sql.close()


@pytest.mark.asyncio
async def test_new_request_gets_sequence_without_changing_config_fingerprint_or_prompt_facts(harness):
    h = harness
    db = RequestDatabase()
    h.service.database = db
    del h.service.request
    h.service.repo.enqueue = AsyncMock()
    facts = {"subject": {"id": h.subject.subject_id}, "records": {}, "coverage": {}}
    h.service.facts.load.return_value = {"fingerprint": "facts-v1", "customer_id": h.row["customer_id"],
                                       "facts": facts}
    try:
        accepted = await h.service.request(h.actor, h.subject)
        assert accepted["status"] == "queued"
        h.service.repo.enqueue.assert_awaited_once_with(db, h.actor, accepted["id"])
        saved = dict(db.sql.execute("SELECT * FROM insight.business_advice").fetchone())
        assert json.loads(saved["configuration_snapshot"]) == {**h.config, "_request_sequence": 1}
        assert h.config == {"prompt": "v1"}
        assert saved["configuration_fingerprint"] == fingerprint(h.config)
        assert saved["cache_key"] == cache_key(h.actor, h.subject.subject_kind, h.subject.subject_id,
                                              h.subject.section, "facts-v1", h.config, 3)
        assert json.loads(saved["facts_snapshot"]) == facts
        # AdviceHandler builds model input from facts_snapshot and current runtime,
        # never from the stored configuration snapshot's request metadata.
        prompt = messages(h.subject.subject_kind, h.subject.section, json.loads(saved["facts_snapshot"]))
        assert all("_request_sequence" not in message.content for message in prompt)
        assert await h.service.latest(h.actor, h.subject) == {"state": "queued", "analysis": accepted}
        # Repeated explicit POST returns the pending ID without another enqueue.
        assert (await h.service.request(h.actor, h.subject))["id"] == accepted["id"]
        assert h.service.repo.enqueue.await_count == 1
        config = json.loads(db.sql.execute("SELECT configuration_snapshot FROM insight.business_advice").fetchone()[0])
        assert config["_request_sequence"] == 2
    finally:
        db.sql.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("workspace_id", str(uuid4())), ("actor_user_ref_id", str(uuid4())),
    ("actor_role_code", "fde"), ("subject_kind", "customer"),
    ("subject_id", str(uuid4())), ("section", "tasks"),
])
async def test_request_sequence_max_is_scoped_to_the_same_discovery_identity(harness, field, value):
    h = harness
    db = RequestDatabase()
    foreign_id = str(uuid4())
    try:
        db.insert(h.row)
        db.insert({**h.row, "id": foreign_id, field: value,
                   "configuration_snapshot": {"_request_sequence": 900}})
        async with db.transaction(h.actor):
            await h.service.repo.requested(db, h.actor, h.subject, h.row["id"])
        values = {row["id"]: json.loads(row["configuration_snapshot"])["_request_sequence"]
                  for row in db.sql.execute("SELECT id,configuration_snapshot FROM insight.business_advice")}
        assert values == {h.row["id"]: 1, foreign_id: 900}
    finally:
        db.sql.close()
