"""Opt-in live FDE003 switch check; restores the complete original policy in finally.

Run via a transient system service inheriting the existing application environment.
Reads one FDE003 login password from stdin; never reads credential files or emits
credentials. Uses existing OPSADMIN and native validate/save/publish services.
Publishes a per-user override, so ALL FDE permission versions change on publish
and restore. Run in a coordinated window with no FDE AI in flight. Does not create
conversations, AI tasks, visits or business records. Evidence is JSON on stdout.
"""

import argparse
import asyncio
import copy
import json
import sys
from uuid import UUID, uuid4

import httpx

from sales_backend.config import get_settings
from sales_backend.db import Database
from sales_backend.domain.company_rules import FdeCapabilitiesPolicy, validate_policy
from sales_backend.repositories.capabilities import CapabilityRepository
from sales_backend.repositories.company_rules import CompanyRulesRepository
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.request_metadata import RequestMetadata, request_metadata
from sales_backend.services.agent_access import require_agent_access

TARGET_ID = "36c7bea3-fa0a-4a1e-b16c-047d8afb5b5c"
CODE = "fde_capabilities"


async def run(args, password):
    evidence = {"account": "FDE003", "business_writes": 0, "ai_tasks_created": 0, "checks": []}
    database = Database(get_settings())
    repo = CompanyRulesRepository()
    original = enabled_id = admin = member = None
    enabled_raw_version = None
    current_actor = None
    logged_in = False
    metadata = request_metadata.set(
        RequestMetadata(request_id=str(uuid4()), user_agent="fde_visit_capability_acceptance")
    )

    def check(label, passed, **values):
        evidence["checks"].append({"check": label, "passed": bool(passed), **values})
        if not passed:
            raise RuntimeError(label)

    async def publish(definition, base, reason):
        definition = validate_policy(CODE, definition)
        async with database.transaction(admin) as connection:
            key = await repo.save(connection, CODE, {"definition": definition, "base_id": base, "reason": reason})
            draft = await repo.version(connection, UUID(key))
            validate_policy(draft["rule_code"], draft["definition"])
            await repo.publish(connection, key, draft["revision_no"])
        return key

    async def assert_disabled(client, label):
        me = await client.get("/auth/me")
        check(
            label + " capability",
            me.status_code == 200 and me.json()["capabilities"]["visit.create"] is False,
            http_status=me.status_code,
            visit_create=False,
        )
        denied = await client.post("/conversations", json={"mode": "visit_entry"})
        check(label + " API rejects entry", denied.status_code == 403, http_status=denied.status_code)
        return me.json()

    async with httpx.AsyncClient(base_url=args.base_url.rstrip("/"), timeout=30) as client:
        try:
            await database.connect()
            async with database.connection() as connection:
                row = await connection.fetchrow(
                    "SELECT * FROM security.resolve_account_actor($1,'OPSADMIN','administrator')", args.workspace
                )
                if not row or row["account_code"] != "OPSADMIN" or row["role_code"] != "administrator":
                    raise RuntimeError("Existing OPSADMIN administrator required")
                admin = IdentityRepository()._actor(row).context
                row = await connection.fetchrow(
                    "SELECT * FROM security.resolve_account_actor($1,'FDE003','fde')", args.workspace
                )
                if not row or row["user_id"] != TARGET_ID:
                    raise RuntimeError("FDE003 identity differs from the approved target")
                member = IdentityRepository()._actor(row).context
            login = await client.post(
                "/auth/password/login",
                json={"workspace": args.workspace, "account_code": "FDE003", "role": "fde", "password": password},
            )
            check("FDE003 normal password login", login.status_code == 200, http_status=login.status_code)
            session = login.json()
            check("FDE003 identity", session["actor"]["user_id"] == TARGET_ID and session["actor"]["role"] == "fde")
            client.headers["Authorization"] = "Bearer " + session["access_token"]
            logged_in = True
            del session, login
            async with database.transaction(admin, readonly=True) as connection:
                original = await repo.active(connection, CODE)
            evidence["original_rule_id"] = original["id"]
            original_definition = copy.deepcopy(original["definition"])
            check(
                "original target switch is closed",
                not FdeCapabilitiesPolicy(**original_definition).enabled("fde", TARGET_ID),
            )
            before = await assert_disabled(client, "before")
            enabled_definition = copy.deepcopy(original_definition)
            enabled_definition.setdefault("user_overrides", {})[TARGET_ID] = True
            check(
                "only target user override changes",
                {**enabled_definition, "user_overrides": original_definition.get("user_overrides", {})}
                == original_definition,
            )
            enabled_id = await publish(
                enabled_definition, original["id"], "FDE003自主录入验收：单人临时开启，验收后完整恢复"
            )
            evidence["enabled_rule_id"] = enabled_id
            current = await client.get("/auth/me")
            current_actor = current.json()
            check(
                "enabled capability",
                current.status_code == 200 and current_actor["capabilities"]["visit.create"] is True,
                http_status=current.status_code,
                visit_create=True,
            )
            check(
                "permission version changes on publish",
                current_actor["permission_version"] != before["permission_version"],
            )
            async with database.transaction(member, readonly=True) as connection:
                await require_agent_access(connection, member, "visit_entry")
                enabled_raw_version = (await CapabilityRepository().analysis_identity(connection, member))[
                    "permission_version"
                ]
            check("enabled native API permission gate", True)
        except Exception as exc:
            evidence["error_type"] = type(exc).__name__
        finally:
            try:
                if enabled_id:
                    async with database.transaction(admin, readonly=True) as connection:
                        active = await repo.active(connection, CODE)
                    check("restore has unchanged active base", active["id"] == enabled_id)
                    restored_id = await publish(
                        original["definition"], enabled_id, "FDE003自主录入验收结束：完整恢复验收前公司规则定义"
                    )
                    evidence["restored_rule_id"] = restored_id
                    async with database.transaction(admin, readonly=True) as connection:
                        restored = await repo.active(connection, CODE)
                    check("complete original definition restored", restored["definition"] == original["definition"])
                    after = await assert_disabled(client, "restored")
                    if current_actor and current_actor.get("permission_version"):
                        check(
                            "permission version changes on restoration",
                            after["permission_version"] != current_actor["permission_version"],
                        )
                    if enabled_raw_version:
                        async with database.transaction(member, readonly=True) as connection:
                            try:
                                await require_agent_access(
                                    connection, member, "chatbi", permission_version=enabled_raw_version
                                )
                            except PermissionError:
                                rejected = True
                            else:
                                rejected = False
                        check("old analysis permission version rejected", rejected)
                    evidence["restoration_completed"] = True
            except Exception as exc:
                evidence["restore_error_type"] = type(exc).__name__
                evidence["restoration_completed"] = False
            if logged_in:
                try:
                    logout = await client.post("/auth/logout")
                    check("logout", logout.status_code == 204, http_status=logout.status_code)
                    client.headers.pop("Authorization", None)
                except Exception as exc:
                    evidence["logout_error_type"] = type(exc).__name__
            await database.close()
            request_metadata.reset(metadata)
    evidence["passed"] = (
        bool(evidence.get("restoration_completed"))
        and not any(name in evidence for name in ("error_type", "restore_error_type", "logout_error_type"))
        and all(c["passed"] for c in evidence["checks"])
    )
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default="demo-sales-workspace")
    parser.add_argument("--base-url", default="https://www.ericepc.com/api/v1")
    args = parser.parse_args()
    password = sys.stdin.readline().rstrip("\r\n")
    if not password:
        print(json.dumps({"passed": False, "error_type": "MissingPassword"}))
        return 1
    result = asyncio.run(run(args, password))
    password = ""
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
