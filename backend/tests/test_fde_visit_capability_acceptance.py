import copy
import importlib.util
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

spec = importlib.util.spec_from_file_location(
    "fde_capability_acceptance", Path(__file__).parents[1] / "scripts/acceptance/fde_visit_capability_live.py"
)
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_enabled_http", [False, True])
async def test_policy_is_completely_restored_even_when_enabled_http_fails(monkeypatch, fail_enabled_http):
    original = {
        "schema_version": 1,
        "visit_entry_enabled": False,
        "role_overrides": {"fde_lead": True},
        "user_overrides": {str(uuid4()): False},
    }
    state = {"id": str(uuid4()), "definition": copy.deepcopy(original), "publishes": []}
    connection = AsyncMock()
    connection.fetchrow.side_effect = lambda sql, *args: {
        "user_id": script.TARGET_ID,
        "account_code": "OPSADMIN" if "OPSADMIN" in sql else "FDE003",
        "role_code": "administrator" if "OPSADMIN" in sql else "fde",
    }

    class Database:
        def __init__(self, settings):
            pass

        async def connect(self):
            pass

        async def close(self):
            pass

        @asynccontextmanager
        async def connection(self):
            yield connection

        @asynccontextmanager
        async def transaction(self, actor, readonly=False):
            yield connection

    class Repo:
        async def active(self, connection, code):
            return {"id": state["id"], "definition": copy.deepcopy(state["definition"])}

        async def save(self, connection, code, data):
            state["draft"] = copy.deepcopy(data["definition"])
            return str(uuid4())

        async def version(self, connection, id):
            return {"rule_code": script.CODE, "definition": state["draft"], "revision_no": 1}

        async def publish(self, connection, id, revision):
            state.update(id=id, definition=state["draft"])
            state["publishes"].append(copy.deepcopy(state["definition"]))

    class Client:
        def __init__(self, **kwargs):
            self.headers = {}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, path, **kwargs):
            if path == "/auth/password/login":
                return httpx.Response(
                    200, json={"actor": {"user_id": script.TARGET_ID, "role": "fde"}, "access_token": "testing-only"}
                )
            return httpx.Response(204 if path == "/auth/logout" else 403)

        async def get(self, path):
            enabled = bool(state["definition"]["user_overrides"].get(script.TARGET_ID))
            if enabled and fail_enabled_http:
                raise httpx.ConnectError("simulated disconnect")
            return httpx.Response(
                200, json={"capabilities": {"visit.create": enabled}, "permission_version": state["id"]}
            )

    async def gate(connection, actor, mode, **kwargs):
        if kwargs.get("permission_version") and kwargs["permission_version"] != state["id"]:
            raise PermissionError("stale")

    monkeypatch.setattr(script, "Database", Database)
    monkeypatch.setattr(script, "CompanyRulesRepository", Repo)
    monkeypatch.setattr(
        script, "IdentityRepository", lambda: SimpleNamespace(_actor=lambda row: SimpleNamespace(context=row))
    )
    monkeypatch.setattr(script.httpx, "AsyncClient", Client)
    monkeypatch.setattr(script, "require_agent_access", gate)
    monkeypatch.setattr(
        script,
        "CapabilityRepository",
        lambda: SimpleNamespace(
            analysis_identity=AsyncMock(side_effect=lambda *args: {"permission_version": state["id"]})
        ),
    )
    result = await script.run(SimpleNamespace(workspace="test", base_url="https://test.invalid"), "testing-only")
    assert result["restoration_completed"]
    assert state["definition"] == original
    assert len(state["publishes"]) == 2
    assert state["publishes"][0]["user_overrides"][script.TARGET_ID]
    assert result["passed"] is (not fail_enabled_http)
    assert "testing-only" not in repr(result)
