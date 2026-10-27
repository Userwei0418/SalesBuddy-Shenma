"""Receipt replay must reauthorize after waiting; legacy callers stay compatible."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from sales_backend.services.idempotency import execute_mutation


@pytest.mark.parametrize("replay", [False, True])
def test_revocation_while_waiting_blocks_receipt_read_and_business_write(replay):
    async def scenario():
        events = []
        allowed = True

        async def sql(query, *args):
            nonlocal allowed
            events.append("lock")
            allowed = False  # Another transaction revoked permission while we waited.

        async def receipt(*args):
            events.append("receipt")
            return {"response": {"saved": True}} if replay else None

        async def authorize():
            events.append("authorize")
            if not allowed:
                raise PermissionError("revoked")

        async def write():
            events.append("write")

        db = SimpleNamespace(execute=sql, fetchrow=receipt, is_in_transaction=lambda: True)
        actor = SimpleNamespace(workspace_id="workspace", user_id="owner", model_dump=lambda: {})
        with pytest.raises(PermissionError, match="revoked"):
            await execute_mutation(db, actor, uuid4(), "collaborators", {}, write, authorize=authorize)
        assert events == ["lock", "authorize"]

    asyncio.run(scenario())


def test_successful_receipt_replay_reauthorizes_without_duplicate_write():
    async def scenario():
        events, saved = [], {}

        async def sql(query, *args):
            if "pg_advisory_xact_lock" in query:
                events.append("lock")
            else:
                events.append("save_receipt")
                saved.update(request_digest=args[4], response=args[5])

        async def receipt(*args):
            events.append("receipt")
            return saved or None

        async def authorize():
            events.append("authorize")

        async def write():
            events.append("write")
            return {"saved": True}

        db = SimpleNamespace(execute=sql, fetchrow=receipt, is_in_transaction=lambda: True)
        actor = SimpleNamespace(workspace_id="workspace", user_id="owner", model_dump=lambda: {})
        key = uuid4()
        first = await execute_mutation(db, actor, key, "collaborators", {}, write, authorize=authorize)
        second = await execute_mutation(db, actor, key, "collaborators", {}, write, authorize=authorize)
        assert first == second == {"saved": True}
        assert events == ["lock", "authorize", "receipt", "write", "save_receipt", "lock", "authorize", "receipt"]

    asyncio.run(scenario())


@pytest.mark.parametrize("with_authorizer", [False, True])
def test_keyless_legacy_mutation_remains_supported(with_authorizer):
    async def scenario():
        events = []

        async def authorize():
            events.append("authorize")

        async def write():
            events.append("write")
            return 7

        assert await execute_mutation(None, None, None, "legacy", {}, write,
                                      authorize=authorize if with_authorizer else None) == 7
        assert events == (["authorize", "write"] if with_authorizer else ["write"])

    asyncio.run(scenario())
