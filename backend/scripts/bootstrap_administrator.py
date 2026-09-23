"""First administrator provisioning by a database maintainer, never a public API.
Password is read from a terminal or mode-0600 file, never accepted in argv/output.
Refuses to run when this workspace already has an active administrator.
"""

import argparse
import asyncio
import getpass
import os
from pathlib import Path
import stat
import re
from uuid import uuid4
import asyncpg
from sales_backend.auth.passwords import encode_password
from sales_backend.db import normalize_database_url


async def bootstrap(args, password):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{2,63}", args.account) or not args.name.strip():
        raise ValueError("Valid account code and display name are required")
    encoded = encode_password(password)
    connection = await asyncpg.connect(normalize_database_url(os.environ["DATABASE_URL"]))
    try:
        if not await connection.fetchval("SELECT rolsuper FROM pg_roles WHERE rolname=current_user"):
            raise RuntimeError("Bootstrap requires a local database maintenance role")
        async with connection.transaction():
            workspace = await connection.fetchval(
                "SELECT id FROM platform.workspace WHERE external_workspace_id=$1 AND status='active'", args.workspace
            )
            if not workspace:
                raise ValueError("Workspace does not exist")
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1,0))", f"account-management:{workspace}"
            )
            if await connection.fetchval(
                "SELECT EXISTS(SELECT 1 FROM platform.role_binding r JOIN platform.user_ref u ON u.id=r.user_ref_id WHERE r.workspace_id=$1 AND r.role_code='administrator' AND clock_timestamp()>=r.valid_from AND clock_timestamp()<r.valid_to AND u.status='active' AND u.deleted_at IS NULL)",
                workspace,
            ):
                raise ValueError("An active administrator already exists; use account management")
            team = await connection.fetchval(
                "SELECT id FROM platform.team WHERE workspace_id=$1 AND code=$2 AND status='active' AND deleted_at IS NULL",
                workspace,
                args.department,
            )
            if not team:
                raise ValueError("Department does not exist")
            uid = uuid4()
            await connection.execute(
                "SELECT set_config('app.workspace_id',$1,true),set_config('app.role_code','administrator',true),set_config('app.user_ref_id','',true)",
                str(workspace),
            )
            await connection.execute(
                "INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name) VALUES($1,$2,$3,$4,$5)",
                uid,
                workspace,
                f"local:{uid}",
                args.account.upper(),
                args.name,
            )
            await connection.execute("SELECT set_config('app.user_ref_id',$1,true)", str(uid))
            await connection.execute(
                "INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id,valid_from) VALUES($1,$2,'administrator','workspace',$3,clock_timestamp())",
                workspace,
                uid,
                team,
            )
            await connection.execute(
                "INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role,valid_from) VALUES($1,$2,$3,'administrator',clock_timestamp())",
                workspace,
                uid,
                team,
            )
            await connection.execute(
                "INSERT INTO platform.password_credential(user_ref_id,workspace_id,password_hash,must_change_password) VALUES($1,$2,$3,true)",
                uid,
                workspace,
                encoded,
            )
            print(f"Administrator {args.account.upper()} created; password change required on first login")
    finally:
        await connection.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workspace", required=True)
    p.add_argument("--department", required=True)
    p.add_argument("--account", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--password-file", type=Path)
    args = p.parse_args()
    if args.password_file:
        info = args.password_file.stat()
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise SystemExit("Password file must be mode 0600")
        password = args.password_file.read_text().strip()
    else:
        password = getpass.getpass("Initial administrator password: ")
    asyncio.run(bootstrap(args, password))
