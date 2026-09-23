"""Explicit maintenance only: check or re-encrypt one workspace provider credential.

Read deployment settings from the process environment; never reads .env or prints
credentials. Database actions require an existing active workspace administrator;
keyring-check only validates local file access and an encryption roundtrip.
"""
import argparse
import asyncio
import json
import os
import sys

from sales_backend.config import get_settings
from sales_backend.db import Database, set_request_context
from sales_backend.domain.agent import RoleCode
from sales_backend.repositories.admin import AgentRuntimeConfigRepository
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.security.runtime_credentials import CredentialCipher, decrypt_credential
from sales_backend.services.runtime_config_management import RuntimeConfigService


def check_keyring(settings, workspace_id="00000000-0000-0000-0000-000000000000"):
    cipher = CredentialCipher.from_file(settings.config_credential_keyring_file, settings.config_credential_key_id)
    probe = cipher.encrypt(workspace_id, "preflight-roundtrip")
    if cipher.decrypt(workspace_id, cipher.key_id, probe["api_key_ciphertext"]) != "preflight-roundtrip":
        raise RuntimeError("CREDENTIAL_VERIFICATION_FAILED")
    return {"status": "ready", "target_encryption_key_id": cipher.key_id,
            "uid": os.geteuid(), "gid": os.getegid()}


async def run(args):
    settings = get_settings()
    if args.action == "keyring-check":
        # Must run before migrations: no DB construction, account lookup or schema dependency.
        print(json.dumps(check_keyring(settings)))
        return
    database = Database(settings)
    await database.connect()
    try:
        async with database.connection() as connection:
            identity = await IdentityRepository().find_maintenance_administrator(connection,
                workspace_external_id=args.workspace, account_code=args.administrator)
        if identity is None or identity.context.role != RoleCode.ADMINISTRATOR:
            raise PermissionError("An active workspace administrator is required")
        actor = identity.context
        async with database.transaction(actor, readonly=args.action == "check") as connection:
            await connection.execute("SET LOCAL statement_timeout='15s'")
            await connection.execute("SET LOCAL lock_timeout='5s'")
            await set_request_context(connection, actor)
            if args.action == "reencrypt":
                result = await RuntimeConfigService(settings).reencrypt(connection, actor)
            else:
                result = check_keyring(settings, actor.workspace_id)
                row = await AgentRuntimeConfigRepository().current(connection, actor)
                if row and row["api_key_ciphertext"] is not None:
                    await decrypt_credential(connection, row, settings)
                result.update({"revision_no": (row or {}).get("revision_no", 0),
                               "cipher_format": (row or {}).get("cipher_format")})
        print(json.dumps(result))
    finally:
        try:
            await asyncio.wait_for(database.close(), timeout=5)
        except TimeoutError:
            if database.pool is not None:
                database.pool.terminate()


async def bounded_run(args):
    try:
        await asyncio.wait_for(run(args), timeout=90)
        return 0
    except TimeoutError:
        code = "RUNTIME_CONFIG_MAINTENANCE_TIMEOUT"
    except PermissionError:
        code = "RUNTIME_CONFIG_ADMINISTRATOR_REQUIRED"
    except Exception:
        # Never print database parameters, private paths or secret-bearing stacks.
        code = "RUNTIME_CONFIG_MAINTENANCE_FAILED"
    print(json.dumps({"status": "failed", "code": code}), file=sys.stderr)
    return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("keyring-check", "check", "reencrypt"))
    parser.add_argument("--workspace")
    parser.add_argument("--administrator")
    args = parser.parse_args()
    if args.action != "keyring-check" and (not args.workspace or not args.administrator):
        parser.error("check/reencrypt require --workspace and --administrator")
    return asyncio.run(bounded_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
