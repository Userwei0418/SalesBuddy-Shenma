"""Bootstrap rollout hints must not disable the legacy path or existing V1 tasks."""

from itertools import product
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sales_backend.services.capabilities import capability_snapshot
from tests.authorization_fixtures import permission_snapshot


def connection_for(actor, *, available=True, task=True, rollout=False, v2=False, v3=False):
    state = {"available": available, "task": task, "rollout": rollout, "v2": v2, "v3": v3}

    async def fetchval(sql, *args):
        if "security.authorization_snapshot" in sql:
            return permission_snapshot(actor, {"task.create_daily": "self"})
        if "task_execution_initialize_v3" in sql:
            return state["v3"]
        if "task_execution_initialize_v2" in sql:
            return state["v2"]
        if "has_function_privilege" in sql:
            return state["available"]
        if "workspace_module_enabled" in sql:
            assert "common.current_workspace_id(),'task')" in sql
            return state["task"] and (state["rollout"] if "task_execution_v1" in sql else True)
        raise AssertionError(sql)

    return SimpleNamespace(fetchval=AsyncMock(side_effect=fetchval)), state


@pytest.fixture
def actor():
    return SimpleNamespace(workspace_id="workspace", user_id="user", team_ids=())


@pytest.mark.asyncio
@pytest.mark.parametrize("available,task,rollout", list(product([False, True], repeat=3)))
async def test_creation_requires_infrastructure_and_both_modules(actor, available, task, rollout):
    connection, _ = connection_for(actor, available=available, task=task, rollout=rollout)
    snapshot = await capability_snapshot(connection, actor)
    assert snapshot["capabilities"]["task_execution"] == {
        "protocol_version": 1,
        "available": available,
        "legacy_available": True,
        "default_creation_mode": "legacy",
        "creation_enabled": available and task and rollout,
    }


@pytest.mark.asyncio
async def test_rollout_changes_are_read_live_and_do_not_disable_execution(actor):
    connection, state = connection_for(actor)
    for enabled in [False, True, False]:
        state["rollout"] = enabled
        snapshot = await capability_snapshot(connection, actor)
        execution = snapshot["capabilities"]["task_execution"]
        assert execution["creation_enabled"] is enabled
        assert execution["available"] and execution["legacy_available"]
        assert snapshot["capabilities"]["task.create"]


@pytest.mark.asyncio
@pytest.mark.parametrize("available,rollout", [(None, True), (True, None)])
async def test_unknown_infrastructure_or_rollout_does_not_enable_creation(actor, available, rollout):
    connection, _ = connection_for(actor, available=available, rollout=rollout)
    execution = (await capability_snapshot(connection, actor))["capabilities"]["task_execution"]
    assert execution["creation_enabled"] is False
    assert execution["legacy_available"] is True


@pytest.mark.asyncio
async def test_action_permission_checks_do_not_query_execution_rollout(actor):
    connection, _ = connection_for(actor)
    snapshot = await capability_snapshot(connection, actor, include_execution=False)
    assert "task_execution" not in snapshot["capabilities"]
    assert snapshot["capabilities"]["task.create"]
    assert connection.fetchval.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("rollout", [False, True])
async def test_v2_creation_adds_capability_without_changing_old_client_contract(actor, rollout):
    from sales_backend.auth.models import TaskExecutionAvailability

    connection, state = connection_for(actor, v2=True, rollout=rollout)
    execution = (await capability_snapshot(connection, actor))["capabilities"]["task_execution"]
    # Match the old Mini executor's strict support.protocol_version===1 gate.
    assert execution["available"] and execution["protocol_version"] == 1
    # The old creation screen only reads creation_enabled; no new V1 offer when off.
    assert execution["creation_enabled"] is rollout
    assert execution["v1_creation_enabled"] is rollout
    assert execution["supported_versions"] == [1, 2]
    assert execution["v2_creation_enabled"] is True
    assert execution["default_creation_mode"] == "execution_v2"
    assert TaskExecutionAvailability.model_validate(execution).model_dump()["v2_creation_enabled"] is True
    state["task"] = False
    disabled = (await capability_snapshot(connection, actor))["capabilities"]["task_execution"]
    assert disabled["creation_enabled"] is False and disabled["v2_creation_enabled"] is False


@pytest.mark.asyncio
async def test_v3_creation_disables_older_creation_but_keeps_old_task_protocols(actor):
    connection, _ = connection_for(actor, v2=True, v3=True, rollout=True)
    execution = (await capability_snapshot(connection, actor))["capabilities"]["task_execution"]
    assert execution["protocol_version"] == 1
    assert execution["supported_versions"] == [1, 2, 3]
    assert execution["default_creation_mode"] == "execution_v3"
    assert execution["v3_creation_enabled"] is True
    assert execution["creation_enabled"] is False
    assert execution["v1_creation_enabled"] is False
    assert execution["v2_creation_enabled"] is False
    assert execution["legacy_available"] is True
