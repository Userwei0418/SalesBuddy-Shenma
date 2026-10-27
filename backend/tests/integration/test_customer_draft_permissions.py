"""Real HTTP, current permissions and Worker persistence under non-bypass RLS.

Only audio decoding, model configuration and external provider responses are
fixtures. Business writes, authorization, snapshots and run storage are real.
"""

import os
import re
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from sales_backend.api import audio
from sales_backend.contracts.authorization import AccountAuthorizationSave
from sales_backend.db import Database, set_request_context
from sales_backend.main import app
from sales_backend.repositories.authorization import AuthorizationRepository
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.services.agent_run import handler as handler_module
from sales_backend.services.agent_run.handler import AgentRunHandler
from sales_backend.services.agent_run.persist import AgentRunStore
from sales_backend.services.runtime_config import RuntimeConfiguration
from tests.integration.feishu_fixtures import seed_execute, seed_fetchval
from tests.integration.test_operations_api import client_for
from tests.integration.test_sales_opportunity_permissions import account_fixture, native_login
from tests.test_customer_creation_contract import payload

pytestmark = pytest.mark.asyncio


class WorkerDatabase(Database):
    """Use the real transaction implementation with the managed Worker ACL.

    A shared rollback connection keeps the synthetic fixture disposable; this
    exercises the handler, not queue claiming or process authentication.
    """

    def __init__(self, connection, settings):
        super().__init__(settings)
        self.conn = connection
        self.role = os.environ.get("SALES_TEST_WORKER_ROLE", "")
        assert re.fullmatch(r"salegent_verify_role_[a-f0-9]+", self.role)

    @asynccontextmanager
    async def connection(self):
        previous = await self.conn.fetchval("SELECT current_user")
        try:
            await self.conn.execute(f'SET LOCAL ROLE "{self.role}"')
            assert not await self.conn.fetchval(
                "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
            )
            yield self.conn
        finally:
            await self.conn.execute('SET LOCAL ROLE "' + previous.replace('"', '""') + '"')


def providers(monkeypatch):
    state = dict(
        audio_calls=0,
        model_calls=0,
        after_audio=None,
        before_start=None,
        before_dispatch=None,
        before_retry=None,
        after_model=None,
    )
    settings = replace(app.state.settings, agent_execution_policy={"definition": {"strategy": "direct_only"}})
    runtime = AsyncMock(return_value=RuntimeConfiguration(settings, {}))
    monkeypatch.setattr(audio, "load_runtime_configuration", runtime)
    monkeypatch.setattr(handler_module, "load_runtime_configuration", runtime)
    monkeypatch.setattr(audio, "transcode_for_asr", AsyncMock(return_value=("fixture.wav", b"fixture", "audio/wav")))

    class Provider:
        def __init__(self, *args, observer, **kwargs):
            self.observer = observer

        async def transcribe(self, **kwargs):
            state["audio_calls"] += 1
            if state["after_audio"]:
                await state["after_audio"]()
            return SimpleNamespace(text="合成客户建档：示例企业，联系人李经理", duration_seconds=2, trace_id="fixture")

        async def chat_json(self, **kwargs):
            # Exercise the production audit_original observer at the same
            # start/dispatch/retry boundaries as the actual provider client.
            assert self.observer.__class__.__name__ == "PermissionCheckedObserver"
            if state["before_start"]:
                await state["before_start"]()
            invocation = await self.observer.start("/v1/chat/completions", "synthetic", {}, 1)
            if state["before_dispatch"]:
                await state["before_dispatch"]()
            sent = False
            try:
                await self.observer.prepare_dispatch(invocation)
                state["model_calls"] += 1
                sent = True
            except BaseException as error:
                await self.observer.finish(invocation, None, error, network_sent=sent)
                raise
            await self.observer.finish(
                invocation,
                httpx.Response(200, json={"usage": {"prompt_tokens": 1, "completion_tokens": 1}}),
                None,
                network_sent=True,
            )
            if state["before_retry"]:
                await state["before_retry"]()
                await self.observer.start("/v1/chat/completions", "synthetic", {}, 2)
                raise AssertionError("Revoked retry was admitted")
            if state["after_model"]:
                await state["after_model"]()
            return dict(customer_name="合成客户草稿", contact_name="李经理", missing_fields=[], summary="合成草稿")

        async def close(self):
            pass

    monkeypatch.setattr(audio, "SenseAudioClient", Provider)
    monkeypatch.setattr(handler_module, "SenseAudioClient", Provider)
    return state, settings


async def transcribe(client):
    return await client.post(
        "/api/v1/audio/transcriptions",
        data={"purpose": "customer_create"},
        files={"file": ("synthetic.wav", b"synthetic-only", "audio/wav")},
    )


async def enqueue(client):
    conversation = await client.post("/api/v1/conversations", json={"mode": "customer_create"})
    assert conversation.status_code == 201, conversation.text
    cid = conversation.json()["id"]
    body = dict(text="合成客户建档", input_source="audio_transcript", client_message_id=str(uuid4()))
    sent = await client.post(f"/api/v1/conversations/{cid}/messages", json=body)
    assert sent.status_code == 202, sent.text
    return cid, sent.json()["run_id"], body


async def identity(connection, account, admin):
    record = await IdentityRepository().find_actor_by_id(
        connection, workspace_id=admin.workspace_id, user_id=account["id"], role="sales"
    )
    assert record
    return record.context


async def deny(connection, admin, account, codes):
    await set_request_context(connection, admin)
    await AuthorizationRepository().save_account(
        connection,
        account["id"],
        AccountAuthorizationSave(
            version_no=0, reason="隔离测试撤权", overrides=[dict(permission=code, effect="deny") for code in codes]
        ).model_dump(mode="json"),
    )


async def business_counts(connection):
    return [
        await seed_fetchval(connection, f"SELECT count(*) FROM {table}")  # noqa: S608 -- fixed test table names
        for table in ("crm.customer", "crm.opportunity", "activity.visit")
    ]


@pytest.mark.parametrize("kind,direct", [("sales", False), ("product_sales", False), ("sales", True)])
async def test_sales_audio_draft_submission_and_account_isolation(connection, monkeypatch, kind, direct):
    assert not await connection.fetchval("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
    first, teams, admin = await account_fixture(connection, ["sales"], kind=kind)
    second, _, _ = await account_fixture(connection, ["sales"], kind=kind)
    if direct:
        await set_request_context(connection, admin)
        await AuthorizationRepository().save_account(
            connection,
            first["id"],
            AccountAuthorizationSave(
                version_no=0,
                reason="隔离测试直接建档权限",
                overrides=[
                    dict(permission="customer.create", effect="allow", scope="teams", team_ids=[teams["sales"]]),
                    dict(permission="customer.create_request", effect="deny"),
                    dict(permission="access.console", effect="allow", scope="workspace"),
                ],
            ).model_dump(mode="json"),
        )
    async with await client_for(connection) as client:
        auth = await native_login(client, first)
        permissions = auth["actor"]["permissions"]
        assert permissions["visit.transcribe"]
        assert permissions["customer.create_request"] is (not direct)
        assert permissions["customer.create"] is direct
        state, settings = providers(monkeypatch)
        before = await business_counts(connection)
        result = await transcribe(client)
        assert result.status_code == 200, result.text
        cid, run_id, message = await enqueue(client)
        actor = await identity(connection, first, admin)
        db = WorkerDatabase(connection, settings)
        handler = AgentRunHandler(db, settings)
        await handler.handle(run_id, actor)
        readback = await client.get(f"/api/v1/agent/runs/{run_id}")
        assert readback.status_code == 200, readback.text
        assert readback.json()["status"] == "waiting_human"
        assert readback.json()["result"]["customer_name"] == "合成客户草稿"
        assert await business_counts(connection) == before
        assert state["audio_calls"] == state["model_calls"] == 1
        replay = await client.post(f"/api/v1/conversations/{cid}/messages", json=message)
        assert replay.json()["run_id"] == run_id
        await native_login(client, second)
        assert (await client.get(f"/api/v1/agent/runs/{run_id}")).status_code == 404
        assert (await client.post(f"/api/v1/conversations/{cid}/messages", json=message)).status_code == 404
        await native_login(client, first)
        body = payload(name="合成申请-" + uuid4().hex, target_team="权限测试-sales", target_team_id=teams["sales"])
        if direct:
            assert (await client.post("/api/v1/customers/creation-requests", json=body)).status_code == 403
            saved = await client.post("/api/v1/customers", json=body)
            assert saved.status_code == 201, saved.text
            after = await business_counts(connection)
            assert after == [before[0] + 1, *before[1:]]
            return
        assert (await client.post("/api/v1/customers", json=body)).status_code == 403
        submitted = await client.post("/api/v1/customers/creation-requests", json=body)
        assert submitted.status_code == 201, submitted.text
        assert submitted.json()["status"] == "pending"
        assert await business_counts(connection) == before
        assert (await client.get("/api/v1/console/customers/creation-requests")).status_code == 403


@pytest.mark.parametrize(
    "codes",
    [
        ["visit.transcribe"],
        ["customer.create", "customer.create_request"],
        ["access.mini_program"],
    ],
)
async def test_audio_current_denials_stop_before_provider(connection, monkeypatch, codes):
    account, _, admin = await account_fixture(connection, ["sales"])
    async with await client_for(connection) as client:
        await native_login(client, account)
        state, _ = providers(monkeypatch)
        await deny(connection, admin, account, codes)
        response = await transcribe(client)
        assert response.status_code == 403, response.text
        assert state["audio_calls"] == 0


@pytest.mark.parametrize("stage", ["audio_save", "enqueue", "worker_start", "worker_save"])
async def test_revocation_stops_each_async_boundary(connection, monkeypatch, stage):
    account, _, admin = await account_fixture(connection, ["sales"])
    async with await client_for(connection) as client:
        await native_login(client, account)
        state, settings = providers(monkeypatch)
        actor = await identity(connection, account, admin)
        database = WorkerDatabase(connection, settings)
        handler = AgentRunHandler(database, settings)

        async def revoke():
            await deny(connection, admin, account, ["customer.create_request"])

        if stage == "audio_save":
            state["after_audio"] = revoke
            assert (await transcribe(client)).status_code == 403
        else:
            cid, run_id, message = await enqueue(client)
            run = await handler._load_and_start(run_id, actor) if stage == "worker_save" else None
            facts = await handler.facts_loader.load(run) if run else None
            if run:
                assert (
                    await connection.fetchval("SELECT current_setting('app.authorized_feature')")
                    == "customer.create_request"
                )
            await revoke()
            if stage == "enqueue":
                # Replaying an already queued message must re-authorize too.
                assert (await client.post(f"/api/v1/conversations/{cid}/messages", json=message)).status_code == 403
            elif stage == "worker_start":
                with pytest.raises((PermissionError, LookupError)):
                    await handler.handle(run_id, actor)
            else:
                with pytest.raises(PermissionError):
                    await AgentRunStore(database, settings).persist_result(run, {"summary": "旧结果"}, facts)
        assert state["model_calls"] == 0
        assert (
            await seed_fetchval(
                connection, "SELECT count(*) FROM agent.artifact WHERE created_by_user_ref_id=$1::uuid", account["id"]
            )
            == 0
        )


async def test_tenant_module_and_history_gates_are_preserved(connection, monkeypatch):
    account, _, admin = await account_fixture(connection, ["sales"])
    async with await client_for(connection) as client:
        await native_login(client, account)
        state, _ = providers(monkeypatch)
        cid, run_id, message = await enqueue(client)
        await deny(connection, admin, account, ["agent.history"])
        assert (await client.get(f"/api/v1/agent/runs/{run_id}")).status_code == 403
        assert (await client.post(f"/api/v1/conversations/{cid}/messages", json=message)).status_code == 403
        # Isolated fixture setup only; actual HTTP uses the restricted role.
        await seed_execute(
            connection,
            "UPDATE control.tenant_module SET enabled=false WHERE workspace_id=$1::uuid AND module_code='ai'",
            admin.workspace_id,
        )
        response = await transcribe(client)
        assert response.status_code == 403, response.text
        assert state["audio_calls"] == 0


@pytest.mark.parametrize(
    "stage,sent",
    [
        ("after_facts", 0),
        ("before_start", 0),
        ("before_dispatch", 0),
        ("before_retry", 1),
        ("after_model", 1),
    ],
)
async def test_original_handler_rechecks_observer_and_return_boundaries(connection, monkeypatch, stage, sent):
    account, _, admin = await account_fixture(connection, ["sales"])
    async with await client_for(connection) as client:
        await native_login(client, account)
        state, settings = providers(monkeypatch)
        _, run_id, _ = await enqueue(client)
        actor = await identity(connection, account, admin)
        before = await business_counts(connection)

        async def revoke():
            await deny(connection, admin, account, ["customer.create_request"])

        handler = AgentRunHandler(WorkerDatabase(connection, settings), settings)
        if stage == "after_facts":
            original = handler.facts_loader.load

            async def load(*args, **kwargs):
                facts = await original(*args, **kwargs)
                await revoke()
                return facts

            monkeypatch.setattr(handler.facts_loader, "load", load)
        else:
            state[stage] = revoke
        with pytest.raises(PermissionError):
            await handler.handle(run_id, actor)
        assert state["model_calls"] == sent
        assert await business_counts(connection) == before
        assert await seed_fetchval(connection, "SELECT count(*) FROM agent.artifact WHERE run_id=$1::uuid", run_id) == 0
        assert await seed_fetchval(
            connection, "SELECT count(*) FROM agent.model_invocation WHERE run_id=$1::uuid", run_id
        ) == (stage not in {"after_facts", "before_start"})
