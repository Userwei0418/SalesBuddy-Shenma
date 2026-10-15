# ruff: noqa: E402, E501, S108 -- Isolated Unix socket; synthetic fixture SQL.
"""V240 target business history with original route and management gates."""

import argparse
import asyncio
import hashlib
import io
import json
import os
import sys
import traceback
from contextlib import asynccontextmanager, redirect_stdout
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "database/tests"), str(ROOT / "backend/scripts")]
from verify_review_targets_postgres import grants
from verify_runtime_grants_postgres import migrate_through

from sales_backend.api import auth, management_dependencies, operations_targets, permission_gate, targets
from sales_backend.api.dependencies import RequestIdentity, get_identity
from sales_backend.api.management_dependencies import get_management_identity
from sales_backend.api.permission_gate import enforce_route_permission
from sales_backend.db import set_request_context
from sales_backend.domain.agent import ActorContext
from sales_backend.domain.route_permissions import route_permission
from sales_backend.repositories import targets as target_repository
from sales_backend.repositories.authorization import AuthorizationRepository


async def verify(args, result):
    modules = {"auth": auth, "business_targets": targets, "console_targets": operations_targets,
               "permission_gate": permission_gate, "management_dependencies": management_dependencies,
               "target_repository": target_repository}
    origins = {key: Path(module.__file__).resolve() for key, module in modules.items()}
    source_root = ROOT / "backend/src"
    assert source_root in [Path(p).resolve() for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]
    assert all(path.is_relative_to(source_root) for path in origins.values())
    hashes = {key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in origins.items()}
    result["source"] = {"root": str(ROOT), "python": sys.executable, "PYTHONPATH": os.environ["PYTHONPATH"],
                        "origins": {k: str(p) for k, p in origins.items()}, "sha256": hashes,
                        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    assert 'client_channel="wechat-mini-program"' in origins["auth"].read_text()
    assert route_permission("targets", "history") == "target.history"
    assert route_permission("operations_targets", "history") == "target.history"
    assert permission_gate.get_identity is get_identity
    assert management_dependencies.get_management_identity is get_management_identity
    socket = str(Path(args.socket).resolve())
    assert socket.startswith(("/tmp/sb-review-pg.", "/private/tmp/sb-review-pg."))
    config = dict(host=socket, port=args.port, user="postgres", password="", passfile="/dev/null", ssl=False)
    suffix = uuid4().hex[:12]
    name, role = "sb_business_history_" + suffix, "sb_history_api_" + suffix
    admin = await asyncpg.connect(database="postgres", **config)
    c = None
    result["fixture"] = {"database": name, "role": role, "schema": "V240"}
    try:
        result["stage"] = "private V240 migration and limited API role"
        await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
        c = await asyncpg.connect(database=name, **config)
        with redirect_stdout(io.StringIO()):
            await migrate_through(c, 240)
        await c.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION')
        await grants(c, role)
        for typ in ("json", "jsonb"):
            await c.set_type_codec(typ, schema="pg_catalog", encoder=json.dumps, decoder=json.loads, format="text")
        await c.execute("SET row_security=on")
        people, identities, target_ids = {}, {}, {}
        for tenant in ("own", "foreign"):
            ws, team = uuid4(), uuid4()
            await c.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,$2)", ws, "history-" + str(ws))
            await c.execute("UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1", ws)
            await c.execute("INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,'sales','Synthetic sales')", team, ws)
            # Use explicit self grants to exercise scope boundaries; no new permission is granted.
            await c.execute("""UPDATE config.permission_role_grant g SET scope_code='self',team_ids='{}'
              FROM config.permission_role r WHERE r.id=g.role_id AND r.workspace_id=$1
              AND r.builtin_role_code='sales' AND g.permission_code IN ('target.read','target.history')""", ws)
            for label, business_role in (("owner", "sales"), ("peer", "sales"), ("ops", "operations")):
                uid = uuid4()
                key = tenant + "_" + label
                people[key] = (ws, uid)
                await c.execute("INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name,account_code) VALUES($1,$2,$3,$3,$3)", uid, ws, key + uid.hex[:6])
                scope = "workspace" if business_role == "operations" else "self"
                await c.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) VALUES($1,$2,$3,$4,$5)", ws, uid, business_role, scope, team if scope == "self" else None)
                if scope == "self":
                    await c.execute("INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) VALUES($1,$2,$3,'sales')", ws, uid, team)
                actor = ActorContext(workspace_id=str(ws), user_id=str(uid), role=business_role, data_scope=scope, team_ids=[str(team)] if scope == "self" else [])
                identities[key] = RequestIdentity(actor=actor, session_id=str(uuid4()), profile=None,
                    auth_method="password", must_change_password=False,
                    client_channel="web" if scope == "workspace" else "wechat-mini-program")
                await set_request_context(c, actor)
                target_ids[key] = await c.fetchval("""INSERT INTO crm.sales_target(workspace_id,target_year,scope_type,user_ref_id,kind,amount,
                  updated_by_user_ref_id,period_type,period_start,period_end)
                  VALUES($1,2026,'person',$2,'collection',100,$2,'year','2026-01-01','2026-12-31') RETURNING id""", ws, uid)
                if key == "own_owner":
                    for amount in (200, 300, 400):
                        await c.execute("UPDATE crm.sales_target SET amount=$2,version_no=version_no+1,change_reason=$3 WHERE id=$1", target_ids[key], amount, "history " + str(amount))
        ws, uid = people["own_owner"]
        await c.execute("INSERT INTO config.account_authorization(workspace_id,user_ref_id) VALUES($1,$2)", ws, uid)
        await c.execute("""INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect)
          SELECT $1,$2,code,'deny' FROM config.permission_catalog
          WHERE code='access.console' OR code LIKE 'audit.%'""", ws, uid)
        expected_ids = [r["id"] for r in await c.fetch("SELECT id FROM ops.audit_log WHERE workspace_id=$1 AND object_type='sales_target' AND object_id=$2 ORDER BY occurred_at DESC,id DESC", ws, target_ids["own_owner"])]
        assert len(expected_ids) == 4, expected_ids
        active = identities["own_owner"]

        class DB:
            @asynccontextmanager
            async def transaction(self, actor, readonly=False):
                async with c.transaction(readonly=readonly):
                    await c.execute(f'SET LOCAL ROLE "{role}"')
                    await set_request_context(c, actor)
                    assert await c.fetchval("SELECT current_user") == role
                    assert not await c.fetchval("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
                    yield c

        db = DB()
        app = FastAPI(dependencies=[Depends(enforce_route_permission)])
        app.state.database, app.state.settings = db, SimpleNamespace()
        app.include_router(targets.router)
        app.include_router(operations_targets.router)

        @app.middleware("http")
        async def trusted_session(request: Request, call_next):
            # Only the prior authenticated identity is synthetic. Both the gate's
            # direct get_identity call and FastAPI dependencies use the real function.
            request.state.verified_identity = active
            return await call_next(request)

        @app.exception_handler(PermissionError)
        async def permission_error(request, exc):
            return JSONResponse(status_code=403, content={"detail": str(exc)})

        assert app.dependency_overrides == {}
        console_route = next(r for r in operations_targets.router.routes if r.endpoint is operations_targets.history)
        assert any(d.call is get_management_identity for d in console_route.dependant.dependencies)
        assert any(d.dependency is enforce_route_permission for d in app.router.dependencies)
        async with db.transaction(active.actor, readonly=True) as scoped:
            effective = await AuthorizationRepository().effective(scoped)
            assert effective.allows("target.read") and effective.allows("target.history")
            assert effective.allows("access.mini_program") and not effective.allows("access.console")
            assert not any(effective.allows(code) for code in ("audit.read", "audit.events_read", "audit.business_read"))
            # The API role can call only the bounded projection under this actor;
            # technical audit rows stay hidden by their original RLS policy.
            assert await scoped.fetchval("SELECT count(*) FROM ops.audit_log") == 0
            result["owner_grants"] = {k: effective.allows(k) for k in ("target.read", "target.history", "access.mini_program", "access.console", "audit.read", "audit.events_read", "audit.business_read")}

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as client:
            async def get(path, expected=200):
                before = await c.fetchval("SELECT count(*) FROM ops.audit_log")
                response = await client.get(path)
                after = await c.fetchval("SELECT count(*) FROM ops.audit_log")
                result["http_calls"].append({"path": path, "status": response.status_code, "channel": active.client_channel, "audit_rows_added": after-before})
                assert response.status_code in (expected if isinstance(expected, tuple) else (expected,)), (path, response.status_code, response.text)
                assert after == before, "read created audit entries"
                return response.json()

            own_path = "/api/v1/targets/" + str(target_ids["own_owner"]) + "/history"
            console_path = "/api/v1/console/targets/" + str(target_ids["own_owner"]) + "/history"
            result["stage"] = "ordinary business session full history without audit or console"
            full = await get(own_path)
            assert set(full) == {"items", "limit", "offset"}
            assert [r["id"] for r in full["items"]] == expected_ids
            assert {r["after_snapshot"]["version_no"] for r in full["items"]} == {1, 2, 3, 4}
            assert all(set(r) == {"id", "occurred_at", "actor_name", "action_code", "before_snapshot", "after_snapshot"} for r in full["items"])
            first, second = await get(own_path + "?limit=2&offset=0"), await get(own_path + "?limit=2&offset=2")
            assert first["items"] + second["items"] == full["items"]
            assert first["limit"] == second["limit"] == 2 and second["offset"] == 2
            assert (await get(own_path + "?limit=2&offset=4"))["items"] == []
            await get(own_path + "?limit=101", 422)
            await get(own_path + "?offset=-1", 422)
            await get(console_path, 403)
            result["checks"].append("ordinary business-entry session (server channel wechat-mini-program) sees all four exact history rows sorted and paginated with no audit/console grants; console remains 403")
            result["stage"] = "object scope and explicit history deny"
            for key in ("own_peer", "foreign_owner"):
                denied = await get("/api/v1/targets/" + str(target_ids[key]) + "/history", (403, 404))
                assert "items" not in denied
            await get("/api/v1/targets/" + str(uuid4()) + "/history", (403, 404))
            await c.execute("INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect) VALUES($1,$2,'target.history','deny')", ws, uid)
            await get(own_path, 403)
            await c.execute("DELETE FROM config.account_permission_override WHERE workspace_id=$1 AND user_ref_id=$2 AND permission_code='target.history'", ws, uid)
            assert await get(own_path) == full
            result["checks"].append("same-tenant peer, cross-tenant and unknown target denied; explicit target.history deny is re-read and yields 403, restoring grant restores identical data")
            result["stage"] = "console channel and original management dependency"
            active = replace(identities["own_owner"], client_channel="web")
            await get(own_path, 403)
            active = identities["own_ops"]
            console = await get(console_path)
            assert console == full
            assert await get(own_path) == full
            # This path passes the global password/console gate then fails the
            # original get_management_identity password-change restriction.
            active = replace(active, must_change_password=True)
            assert (await get(console_path, 403))["detail"] == "PASSWORD_CHANGE_REQUIRED"
            result["checks"].append("server web/console channel with no console grant stays 403; operations original console history remains exact same shape/content; real management dependency rejects required password change")
            result["samples"] = {"business": full, "console": console, "first_page": first, "second_page": second}
        assert all(hashlib.sha256(path.read_bytes()).hexdigest() == hashes[key] for key, path in origins.items())
        result["source_drift"] = False
        result["gates"] = {"enforce_route_permission": "original callable with live SQL snapshot", "get_identity": "original callable; trusted prior identity injected via request state", "get_management_identity": "original dependency with explicit password-change negative probe", "dependency_overrides": []}
        result["boundary"] = "Private V240 synthetic data, actual NOLOGIN/NOBYPASSRLS API role and RLS; POST /api/v1/auth/password/login source binds wechat-mini-program channel even for ordinary Web; no real credential/browser/production verification"
        result["status"], result["stage"] = "passed", "complete"
    finally:
        if c:
            await c.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        result["cleanup"] = {"database_count": await admin.fetchval("SELECT count(*) FROM pg_database WHERE datname=$1", name), "role_count": await admin.fetchval("SELECT count(*) FROM pg_roles WHERE rolname=$1", role)}
        await admin.close()
        assert result["cleanup"] == {"database_count": 0, "role_count": 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    assert not output.exists()
    result = {"status": "running", "stage": "import provenance", "checks": [], "http_calls": []}
    try:
        asyncio.run(verify(args, result))
    except Exception as exc:
        result["status"] = "failed"
        result["failure"] = {"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()}
        raise
    finally:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n")
        print(json.dumps({"status": result["status"], "stage": result["stage"], "checks": len(result["checks"]), "HTTP": len(result["http_calls"]), "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}, ensure_ascii=False))
