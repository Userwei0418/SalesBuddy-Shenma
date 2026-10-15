"""Real non-bypass SQL and HTTP: bounded delegation and indirect account takeover."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import asyncpg
import pytest

from sales_backend.contracts.authorization import AccountAuthorizationSave, RoleSave
from sales_backend.db import set_request_context
from sales_backend.repositories.authorization import AuthorizationRepository
from tests.integration.test_authorization_store import resolve
from tests.integration.test_operations_api import client_for, sign_in
from tests.integration.test_sales_opportunity_permissions import account_fixture, PASSWORD

pytestmark = pytest.mark.asyncio
repo = AuthorizationRepository()
BASE = "/api/v1/console/permissions"


def body(*, version=0, roles=None, overrides=None):
    return AccountAuthorizationSave(
        version_no=version, reason="隔离委派验收", roles=roles or [], overrides=overrides or []
    ).model_dump(mode="json")


def grant(code="opportunity.create", scope="self", **kwargs):
    return dict(permission=code, scope=scope, effect="allow", **kwargs)


async def fixture(c, *, scope="self", expiry=None):
    editor, _, admin = await account_fixture(c, ["fde"])
    target, teams, _ = await account_fixture(c, ["fde"])
    permissions = [
        dict(permission="opportunity.create", scope=scope, team_ids=[teams["fde"]] if scope == "teams" else [])
    ]
    policy = dict(
        enabled=True,
        recipient_ids=[target["id"]],
        permissions=permissions,
        version_no=0,
        expires_at=expiry,
        reason="委派一个功能",
    )
    await repo.save_account(
        c,
        editor["id"],
        body(
            overrides=[
                grant(p, "workspace")
                for p in (
                    "access.console",
                    "authorization.read",
                    "authorization.accounts_manage",
                    "account.update",
                    "account.reset_password",
                    "account.unlock",
                )
            ]
        ),
    )
    await repo.save_delegation(c, editor["id"], policy)
    return editor, target, teams, admin, policy


async def rejected(c, action, error=asyncpg.InsufficientPrivilegeError):
    with pytest.raises(error):
        async with c.transaction():
            await action()


async def test_delegated_scope_recipient_and_source_preservation(connection):
    c = connection
    editor, target, teams, admin, _ = await fixture(c, scope="teams")
    deny = dict(permission="profile.fde_read", effect="deny")
    await repo.save_account(c, target["id"], body(overrides=[deny]))
    await resolve(c, admin, editor, "fde")
    good = grant(scope="teams", team_ids=[teams["fde"]])
    await rejected(c, lambda: repo.save_account(c, target["id"], body(version=1, overrides=[good])))
    for invalid in [
        grant(scope="workspace"),
        grant(scope="self"),
        grant("task.create_daily"),
        grant(scope="teams", team_ids=[str(uuid4())]),
    ]:
        await rejected(c, lambda: repo.save_account(c, target["id"], body(version=1, overrides=[deny, invalid])))
    await rejected(c, lambda: repo.save_account(c, editor["id"], body(version=1)))
    await rejected(c, lambda: repo.save_account(c, admin.user_id, body()))
    await repo.save_account(c, target["id"], body(version=1, overrides=[deny, good]))
    current = await repo.account(c, target["id"])
    assert (
        next(g for g in current["overrides"] if g["permission"] == "opportunity.create")["delegation_user_ref_id"]
        == editor["id"]
    )
    assert "delegation_user_ref_id" not in next(
        g for g in current["overrides"] if g["permission"] == "profile.fde_read"
    )
    await resolve(c, admin, target, "fde")
    assert (await repo.effective(c)).allows("opportunity.create")
    assert not (await repo.effective(c)).allows("profile.fde_read")


async def test_policy_version_expiry_and_template_version_revoke_issued_permissions(connection):
    c = connection
    expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    editor, target, _, admin, policy = await fixture(c, expiry=expiry)
    role = await repo.save_role(
        c,
        None,
        RoleSave(name="委派模板", reason="隔离验收", permissions=[dict(permission="opportunity.create")]).model_dump(
            mode="json"
        ),
    )
    await resolve(c, admin, editor, "fde")
    await rejected(c, lambda: repo.save_account(c, target["id"], body(roles=[dict(role_id=role["id"], scope="self")])))
    assignment = dict(role_id=role["id"], scope="self", expires_at=expiry)
    await repo.save_account(c, target["id"], body(roles=[assignment]))
    await resolve(c, admin, target, "fde")
    assert (await repo.effective(c)).allows("opportunity.create")
    await set_request_context(c, admin)
    await repo.save_role(
        c,
        role["id"],
        RoleSave(
            name="模板更新",
            reason="新增授权需重新批准",
            version_no=1,
            permissions=[dict(permission="opportunity.create")],
        ).model_dump(mode="json"),
    )
    await resolve(c, admin, target, "fde")
    assert not (await repo.effective(c)).allows("opportunity.create")
    await resolve(c, admin, editor, "fde")
    await repo.save_account(c, target["id"], body(version=1, roles=[assignment]))
    await resolve(c, admin, target, "fde")
    assert (await repo.effective(c)).allows("opportunity.create")
    await set_request_context(c, admin)
    updated = await repo.save_delegation(c, editor["id"], {**policy, "version_no": 1, "enabled": False})
    assert updated["version_no"] == 2
    await rejected(
        c, lambda: repo.save_delegation(c, editor["id"], {**policy, "version_no": 1}), asyncpg.SerializationError
    )
    await resolve(c, admin, target, "fde")
    assert not (await repo.effective(c)).allows("opportunity.create")
    await resolve(c, admin, editor, "fde")
    assert not (await repo.effective(c)).allows("authorization.accounts_manage")


async def test_permission_management_requires_native_admin_or_live_delegation(connection):
    c = connection
    editor, _, admin = await account_fixture(c, ["fde"])
    actions = (
        "authorization.roles_manage",
        "authorization.accounts_manage",
        "account.create",
        "account.update",
        "account.reset_password",
        "account.unlock",
        "account.password_policy",
        "organization.manage",
    )
    await repo.save_account(c, editor["id"], body(overrides=[grant(p, "workspace") for p in actions]))
    await resolve(c, admin, editor, "fde")
    permissions = await repo.effective(c)
    assert not any(permissions.allows(p) for p in actions)
    await rejected(
        c, lambda: repo.save_role(c, None, RoleSave(name="不能自提权", reason="拒绝").model_dump(mode="json"))
    )
    await rejected(
        c,
        lambda: c.execute(
            "INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code) VALUES($1::uuid,$2::uuid,'administrator','workspace')",
            admin.workspace_id,
            editor["id"],
        ),
    )
    assert (
        await c.execute("UPDATE platform.user_ref SET phone_number='13900008888' WHERE id=$1::uuid", admin.user_id)
        == "UPDATE 0"
    )


async def test_delegate_cannot_reset_latent_higher_authority_or_mutate_native_roles(connection):
    c = connection
    editor, target, _, admin, _ = await fixture(c)
    # Target already has a much wider FDE appointment than the single delegated action.
    await resolve(c, admin, editor, "fde")
    await rejected(c, lambda: c.execute("SELECT security.revoke_managed_account_sessions($1::uuid)", target["id"]))
    await rejected(
        c,
        lambda: c.execute(
            "SELECT security.set_account_password($1::uuid,$2,false)", target["id"], "scrypt$32768$8$1$test"
        ),
    )
    await rejected(c, lambda: c.execute("SELECT security.unlock_account_login($1::uuid,'测试')", target["id"]))
    assert (
        await c.execute(
            "UPDATE platform.role_binding SET valid_to=clock_timestamp() WHERE user_ref_id=$1::uuid", target["id"]
        )
        == "UPDATE 0"
    )
    assert (
        await c.execute(
            "UPDATE platform.team_membership SET is_primary=NOT is_primary WHERE user_ref_id=$1::uuid", target["id"]
        )
        == "UPDATE 0"
    )
    await rejected(
        c,
        lambda: c.execute(
            "SELECT security.save_account_authorization_v156($1::uuid,$2::jsonb)", editor["id"], body(version=1)
        ),
    )
    await rejected(c, lambda: c.fetch("SELECT * FROM config.authorization_delegation"))


async def test_http_delegation_roundtrip_and_old_session_revocation(connection):
    c = connection
    editor, target, _, admin, policy = await fixture(c)
    async with await client_for(c) as client:
        await sign_in(client, editor["account_code"], PASSWORD)
        d = await client.get(BASE + "/delegations")
        assert d.status_code == 200, d.text
        assert not d.json()["company_administrator"] and d.json()["items"] == []
        assert (await client.put(BASE + "/delegations/" + target["id"], json=policy)).status_code == 403
        path = BASE + "/accounts/" + target["id"]
        saved = await client.put(path, json=body(overrides=[grant()]))
        assert saved.status_code == 200, saved.text
        await set_request_context(c, admin)
        await repo.save_delegation(c, editor["id"], {**policy, "enabled": False, "version_no": 1})
        assert (await client.put(path, json=body(version=1))).status_code == 403
        await sign_in(client, "ADMIN001")
        new = await client.put(BASE + "/delegations/" + editor["id"], json={**policy, "version_no": 2})
        assert new.status_code == 200, new.text
        assert (
            await client.put(BASE + "/delegations/" + editor["id"], json={**policy, "version_no": 2})
        ).status_code == 409
        assert (await client.get(BASE + "/delegations")).json()["company_administrator"]


async def test_delegated_reset_works_for_bounded_target_but_rejects_future_custom_authority(connection):
    from sales_backend.auth.passwords import encode_password

    c = connection
    editor, target, _, admin, policy = await fixture(c)
    fde = next(r for r in await repo.roles(c) if r["builtin_role_code"] == "fde")
    await repo.save_role(
        c,
        fde["id"],
        RoleSave(
            name=fde["name"],
            version_no=fde["version_no"],
            reason="隔离低权限角色",
            permissions=[dict(permission="opportunity.create", scope="self")],
        ).model_dump(mode="json"),
    )
    await resolve(c, admin, editor, "fde")
    await c.execute(
        "SELECT security.set_account_password($1::uuid,$2,false)", target["id"], encode_password("Bounded-Reset-2026")
    )
    await set_request_context(c, admin)
    # An inactive, expired custom role still represents potential takeover authority.
    high = await repo.save_role(
        c,
        None,
        RoleSave(
            name="潜在高权限",
            reason="暂时停用亦不能接管",
            status="inactive",
            permissions=[dict(permission="ai.config_publish", scope="workspace")],
        ).model_dump(mode="json"),
    )
    await repo.save_account(c, target["id"], body())
    role = await c.fetchval("SELECT current_user")
    await c.execute("RESET ROLE")
    await c.execute(
        "INSERT INTO config.account_permission_role(workspace_id,user_ref_id,role_id,scope_code,expires_at) VALUES($1::uuid,$2::uuid,$3::uuid,'workspace',clock_timestamp()-interval '1 day')",
        admin.workspace_id,
        target["id"],
        high["id"],
    )
    await c.execute(f'SET LOCAL ROLE "{role}"')
    await resolve(c, admin, editor, "fde")
    await rejected(
        c,
        lambda: c.execute(
            "SELECT security.set_account_password($1::uuid,$2,false)",
            target["id"],
            encode_password("Must-Not-Be-Reset-2026"),
        ),
    )


async def test_cross_company_recipient_policy_and_native_appointment_bypass_fail(connection):
    c = connection
    editor, target, _, admin, policy = await fixture(c)
    runtime = await c.fetchval("SELECT current_user")
    await c.execute("RESET ROLE")
    other, user = uuid4(), uuid4()
    await c.execute(
        "INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'另一隔离公司')",
        other,
        "cross-" + uuid4().hex,
    )
    await c.execute(
        "INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name,account_code) VALUES($1,$2,$3,'同名成员','SAMEUSER')",
        user,
        other,
        str(user),
    )
    await c.execute(f'SET LOCAL ROLE "{runtime}"')
    await set_request_context(c, admin)
    await rejected(
        c,
        lambda: repo.save_delegation(c, editor["id"], {**policy, "version_no": 1, "recipient_ids": [str(user)]}),
        asyncpg.InvalidParameterValueError,
    )
    await resolve(c, admin, editor, "fde")
    await rejected(c, lambda: repo.save_account(c, str(user), body(overrides=[grant()])))
    await rejected(
        c,
        lambda: c.execute(
            "INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code) VALUES($1::uuid,$2::uuid,'administrator','workspace')",
            admin.workspace_id,
            target["id"],
        ),
    )
    await rejected(c, lambda: repo.save_delegation(c, target["id"], policy))


async def test_policy_expiration_and_issuer_removal_disable_existing_issued_grants(connection):
    c = connection
    editor, target, _, admin, policy = await fixture(c)
    await resolve(c, admin, editor, "fde")
    await repo.save_account(c, target["id"], body(overrides=[grant()]))
    await set_request_context(c, admin)
    await repo.save_delegation(
        c,
        editor["id"],
        {**policy, "version_no": 1, "expires_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()},
    )
    await resolve(c, admin, target, "fde")
    assert not (await repo.effective(c)).allows("opportunity.create")
    await set_request_context(c, admin)
    await repo.save_delegation(c, editor["id"], {**policy, "version_no": 2})
    await resolve(c, admin, editor, "fde")
    await repo.save_account(c, target["id"], body(version=1, overrides=[grant()]))
    await resolve(c, admin, target, "fde")
    assert (await repo.effective(c)).allows("opportunity.create")
    runtime = await c.fetchval("SELECT current_user")
    await c.execute("RESET ROLE")
    await c.execute(
        "UPDATE platform.role_binding SET valid_to=clock_timestamp() WHERE user_ref_id=$1::uuid AND role_code='administrator'",
        admin.user_id,
    )
    await c.execute(f'SET LOCAL ROLE "{runtime}"')
    await resolve(c, admin, target, "fde")
    assert not (await repo.effective(c)).allows("opportunity.create")
