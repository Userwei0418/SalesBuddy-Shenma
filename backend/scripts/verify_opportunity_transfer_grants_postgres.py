# ruff: noqa: E402, E501, S108, S608 -- Fixed SQL and synthetic local database identifiers.
"""V255 authorization integration with a prospective transfer catalog fixture.

This does not apply V256 or prove transfer writes/RLS. Catalog registration is
explicit fixture setup; effective grants use the actual DB authorization stack
and audited role/account configuration functions under a restricted API role.
Only an explicit private local socket is accepted. No saved DSN is read.
"""

import argparse
import asyncio
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path
from uuid import uuid4

import asyncpg

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend/scripts"))
from verify_review_targets_postgres import grants, migrate_through

from sales_backend.contracts.authorization import AccountAuthorizationSave, RoleSave
from sales_backend.db import set_request_context
from sales_backend.domain.agent import ActorContext
from sales_backend.domain.authorization import ObjectScope
from sales_backend.domain.opportunity_access import OpportunityAccessError, OwnerTarget, require_transfer_scope
from sales_backend.domain.permission_catalog import CATALOG
from sales_backend.repositories.authorization import AuthorizationRepository


async def verify(args):
    socket = str(Path(args.socket).resolve())
    if not socket.startswith(("/tmp/sb122-grants-pg.", "/private/tmp/sb122-grants-pg.")):
        raise ValueError("Requires this test's private disposable socket")
    config = dict(host=socket, port=args.port, user="postgres", password="", passfile="/dev/null", ssl=False)
    suffix = uuid4().hex[:12]
    name, role = "sb122_grants_" + suffix, "sb122_api_" + suffix
    result = dict(status="started", checks=[], schema="V255", catalog_fixture_only=True,
                  transfer_mutation_tested=False, production_accessed=False)
    admin = await asyncpg.connect(database="postgres", **config)
    c = None
    try:
        await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
        c = await asyncpg.connect(database=name, **config)
        with redirect_stdout(io.StringIO()):
            await migrate_through(c, 255)
        for typ in ("json", "jsonb"):
            await c.set_type_codec(typ, schema="pg_catalog", encoder=json.dumps, decoder=json.loads, format="text")
        ws, east, north, operator = uuid4(), uuid4(), uuid4(), uuid4()
        await c.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'Synthetic grants')", ws, suffix)
        await c.execute("UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1", ws)
        for team in (east, north):
            await c.execute("INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,$3,$3)", team, ws, str(team))
        actors = {}
        specs = [("operator", "administrator", "workspace", operator)]
        specs += [(f"{r}_{s}", r, s, uuid4()) for r in ("administrator", "operations", "manager", "supervisor") for s in ("self", "team", "workspace")]
        specs += [(r, r, "self", uuid4()) for r in ("sales", "fde")]
        for label, job, scope, user in specs:
            await c.execute("INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name,account_code) VALUES($1,$2,$3,$3,$3)", user, ws, label)
            await c.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) VALUES($1,$2,$3,$4,$5)", ws, user, job, scope, east)
            membership = job if job in ("supervisor", "fde") else "sales"
            await c.execute("INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) VALUES($1,$2,$3,$4)", ws, user, east, membership)
            actors[label] = ActorContext(workspace_id=str(ws), user_id=str(user), role=job, data_scope=scope, team_ids=[str(east)])
        await c.execute("INSERT INTO platform.password_credential(user_ref_id,workspace_id,password_hash) VALUES($1,$2,'synthetic-not-login-capable')", operator, ws)
        definition = CATALOG["opportunity.transfer"]
        await c.execute("INSERT INTO config.permission_catalog(code,label,module,scopes,sensitive) VALUES($1,$2,$3,$4,$5)", definition.code, definition.label, definition.module, list(definition.scopes), definition.sensitive)
        await c.execute("INSERT INTO control.permission_module(permission_code,module_code) VALUES('opportunity.transfer','opportunity')")
        assert await c.fetchval("SELECT count(*) FROM config.permission_role_grant WHERE permission_code='opportunity.transfer'") == 0
        result["checks"].append("catalog_registration_does_not_backfill_published_tenant_roles")
        await c.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE')
        await grants(c, role)
        await c.execute(f'SET ROLE "{role}"')
        await c.execute("SET row_security=on")
        assert not await c.fetchval("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
        repo = AuthorizationRepository()

        async def effective(label):
            async with c.transaction():
                await set_request_context(c, actors[label])
                return await repo.effective(c)

        async with c.transaction():
            await set_request_context(c, actors["operator"])
            for current in await repo.roles(c):
                if current["builtin_role_code"] not in {"administrator", "operations", "manager", "supervisor"}:
                    continue
                body = RoleSave(name=current["name"], description=current["description"], status=current["status"],
                    version_no=current["version_no"], reason="Synthetic explicit enablement",
                    permissions=current["permissions"] + [{"permission": "opportunity.transfer", "scope": "inherit", "team_ids": []}])
                await repo.save_role(c, current["id"], body.model_dump(mode="json"))
        result["checks"].append("restricted_api_uses_audited_normal_role_save_to_enable_inherit")

        source = ObjectScope(str(ws), str(uuid4()), str(east))
        for job in ("administrator", "operations", "manager", "supervisor"):
            for scope in ("self", "team", "workspace"):
                auth = await effective(f"{job}_{scope}")
                for source_team, target_team in ((east, east), (east, north), (north, east), (north, north)):
                    expected = scope == "workspace" or (scope == "team" and source_team == east and target_team == east)
                    try:
                        require_transfer_scope(auth, ObjectScope(str(ws), source.owner_user_id, str(source_team)),
                            OwnerTarget(str(ws), str(uuid4()), str(target_team), True, True))
                        actual = True
                    except OpportunityAccessError:
                        actual = False
                    assert actual == expected, (job, scope, source_team, target_team)
                assert all(g.scope in {"teams", "workspace"} for g in auth.for_permission("opportunity.transfer"))
        result["checks"].append("48_source_destination_cases_use_actual_inherited_scope_without_label_upgrade")
        for job in ("sales", "fde"):
            assert not (await effective(job)).allows("opportunity.transfer")
        result["checks"].append("sales_and_fde_have_no_default_transfer")

        denied_actor = actors["manager_workspace"]
        async with c.transaction():
            await set_request_context(c, actors["operator"])
            state = await repo.account(c, denied_actor.user_id)
            body = AccountAuthorizationSave(version_no=state["version_no"], roles=state["roles"],
                overrides=[{"permission": "opportunity.transfer", "scope": "inherit", "effect": "deny"}],
                reason="Synthetic explicit deny")
            await repo.save_account(c, denied_actor.user_id, body.model_dump(mode="json"))
        assert not (await effective("manager_workspace")).allows("opportunity.transfer", source)
        result["checks"].append("normal_account_override_deny_defeats_workspace_grant")
        auth = await effective("operations_workspace")
        assert not auth.allows("opportunity.transfer", ObjectScope(str(uuid4()), source.owner_user_id, str(east)))
        result["checks"].append("foreign_workspace_denied")

        await c.execute("RESET ROLE")
        await c.execute("UPDATE platform.user_ref SET status='inactive' WHERE id=$1", actors["operations_workspace"].user_id)
        await c.execute(f'SET ROLE "{role}"')
        assert not (await effective("operations_workspace")).allows("opportunity.transfer")
        result["checks"].append("inactive_account_loses_transfer")
        await c.execute("RESET ROLE")
        await c.execute("UPDATE control.tenant_module SET enabled=false WHERE workspace_id=$1 AND module_code='opportunity'", ws)
        await c.execute(f'SET ROLE "{role}"')
        assert not (await effective("administrator_workspace")).allows("opportunity.transfer")
        result["checks"].append("disabled_opportunity_module_removes_transfer_even_for_administrator")
        result["status"] = "passed"
    finally:
        if c:
            await c.execute("RESET ROLE")
            await c.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        await admin.close()
        result["disposable_database_and_role_removed"] = True
        Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True)
    parser.add_argument("--port", type=int, default=55432)
    parser.add_argument("--output", required=True)
    asyncio.run(verify(parser.parse_args()))
