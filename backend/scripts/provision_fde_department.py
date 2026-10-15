"""Provision a department through the existing operations account services.

Use an inherited application DATABASE_URL; never reads or writes credential files.
Manifest contains department {code,name,parent_team_id?}, parent_team_code (default ALL), and accounts
[{account_code,display_name,role: fde|fde_lead}]. Run with --apply to write.
One temporary password is read from stdin only for newly created accounts.
Existing codes are reused only when name, department and role match exactly;
existing passwords, memberships and roles are never overwritten. New accounts
retain the normal first-login password-change requirement.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from sales_backend.config import get_settings
from sales_backend.contracts.operations import DepartmentSave
from sales_backend.db import Database
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.repositories.operations_accounts import OperationsAccountRepository
from sales_backend.request_metadata import RequestMetadata, request_metadata
from sales_backend.services.operations_accounts import OperationsAccountService


class ProvisionConflict(ValueError):
    """Safe business validation message, never contains credentials."""


class Member(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    account_code: str = Field(pattern=r"^[A-Za-z0-9._-]{3,64}$")
    display_name: str = Field(min_length=1, max_length=100)
    role: Literal["fde", "fde_lead"]


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    department: DepartmentSave
    parent_team_code: str = Field(default="ALL", min_length=1, max_length=64)
    accounts: list[Member] = Field(min_length=1, max_length=100)


def preflight(manifest, organization):
    codes = [m.account_code.upper() for m in manifest.accounts]
    if len(codes) != len(set(codes)):
        raise ProvisionConflict("Manifest has duplicate account codes")
    names = [m.display_name for m in manifest.accounts]
    if len(names) != len(set(names)):
        raise ProvisionConflict("Manifest has duplicate member names; verify identities first")
    parents = [t for t in organization["departments"] if t["code"] == manifest.parent_team_code]
    if len(parents) != 1 or parents[0]["status"] != "active":
        raise ProvisionConflict("An existing active parent department is required")
    parent_id = parents[0]["id"]
    if manifest.department.parent_team_id and str(manifest.department.parent_team_id) != parent_id:
        raise ProvisionConflict("Parent department ID and code do not match")
    if manifest.department.code == manifest.parent_team_code:
        raise ProvisionConflict("FDE department cannot be its own parent")
    department = manifest.department.model_copy(update={"parent_team_id": UUID(parent_id)})
    if department.status != "active" or department.version_no is not None:
        raise ProvisionConflict("Provisioning only accepts an active department without an update version")
    teams = [t for t in organization["departments"] if t["code"] == department.code]
    if len(teams) > 1:
        raise ProvisionConflict("Department code is ambiguous")
    team = teams[0] if teams else None
    if team and (
        team["name"] != department.name.strip()
        or team["status"] != "active"
        or str(team.get("parent_team_id") or "") != str(department.parent_team_id or "")
    ):
        raise ProvisionConflict("Existing department differs from the manifest; no changes made")
    planned = []
    for member in manifest.accounts:
        code = member.account_code.upper()
        found = [a for a in organization["accounts"] if a["account_code"] == code]
        if len(found) > 1:
            raise ProvisionConflict("Account code is ambiguous")
        account = found[0] if found else None
        if account and (
            account["display_name"] != member.display_name
            or account["status"] != "active"
            or set(account["roles"]) != {member.role}
            or not team
            or account["team_id"] != team["id"]
            or not account["has_password"]
        ):
            raise ProvisionConflict(f"Existing account {code} differs from the manifest; no changes made")
        if not account and any(a["display_name"] == member.display_name for a in organization["accounts"]):
            raise ProvisionConflict(f"Name for {code} already exists under another code; verify identity first")
        planned.append((member, account))
    return team, planned, department


async def provision(connection, actor, manifest, password, *, apply=False, repository=None, service=None):
    if actor.role.value != "administrator":
        raise ProvisionConflict("An existing administrator is required")
    repository = repository or OperationsAccountRepository()
    service = service or OperationsAccountService()
    await repository.lock_workspace(connection, actor.workspace_id)
    team, planned, department = preflight(manifest, await repository.organization(connection))
    result = {
        "applied": apply,
        "department": {"code": department.code, "parent_team_id": str(department.parent_team_id),
                       "action": "reuse" if team else "create"},
        "accounts": [],
    }
    if apply and any(existing is None for _, existing in planned) and not password:
        raise ProvisionConflict("A temporary password for new accounts is required on stdin")
    if apply and not team:
        team = await repository.save_department(connection, actor, None, department.model_dump())
    if team:
        result["department"]["id"] = team["id"]
    for member, existing in planned:
        item = {
            "account_code": member.account_code.upper(),
            "display_name": member.display_name,
            "role": member.role,
            "action": "reuse" if existing else "create",
        }
        if existing:
            item["id"] = existing["id"]
        elif apply:
            saved = await service.create(
                connection,
                actor,
                {
                    "account_code": member.account_code,
                    "display_name": member.display_name,
                    "roles": [member.role],
                    "team_id": team["id"],
                },
                password,
            )
            item.update(id=saved["id"], must_change_password=saved["must_change_password"])
        result["accounts"].append(item)
    return result


async def run(args):
    manifest = Manifest.model_validate_json(args.manifest.read_text())
    database = Database(get_settings())
    password = ""
    metadata = request_metadata.set(
        RequestMetadata(
            request_id=str(uuid4()),
            user_agent="provision_fde_department",
            client_ip="",
        )
    )
    try:
        await database.connect()
        async with database.connection() as connection:
            row = await connection.fetchrow(
                "SELECT * FROM security.resolve_account_actor($1,'OPSADMIN','administrator')",
                args.workspace,
            )
        if not row or row["account_code"] != "OPSADMIN" or row["role_code"] != "administrator":
            raise ProvisionConflict("An existing active OPSADMIN administrator is required")
        actor = IdentityRepository()._actor(row).context
        if args.apply:
            password = sys.stdin.readline().rstrip("\r\n")
        # One transaction holds the same account-management lock used by Web.
        # All writes receive the real administrator audit context and rollback together.
        async with database.transaction(actor) as connection:
            output = await provision(connection, actor, manifest, password, apply=args.apply)
        return output
    finally:
        password = ""
        request_metadata.reset(metadata)
        await database.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--workspace", default="demo-sales-workspace")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        output = asyncio.run(run(args))
    except Exception as exc:
        # Validation/driver exception text can contain input values. Emit only our
        # own safe conflict messages; never a traceback or arbitrary server detail.
        print(
            str(exc)
            if isinstance(exc, ProvisionConflict)
            else f"Provisioning failed ({type(exc).__name__}); no credential output",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
