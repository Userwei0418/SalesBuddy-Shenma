"""Only narrow V287 security functions; no ordinary business actor or table access."""
from sales_backend.db import json_value


class RestrictedRepository:
    async def candidate(self, connection, workspace, identifier, login_type):
        return json_value(await connection.fetchval(
            "SELECT security.demo_readonly_candidate($1,$2,$3)", workspace, identifier, login_type,
        ))

    async def create_session(self, connection, session_id, candidate, attempt_key):
        return json_value(await connection.fetchval(
            "SELECT security.demo_readonly_create_session($1::uuid,$2::uuid,$3::uuid,$4::int,$5,$6)",
            session_id, candidate["workspace_id"], candidate["identity_id"],
            candidate["permission_version"], candidate["password_hash"], attempt_key,
        ))

    async def admit(self, connection, identity):
        return await connection.fetchval(
            "SELECT security.demo_readonly_admit($1::uuid,$2::uuid,$3::uuid,$4::int)",
            identity.session_id, identity.workspace_id, identity.identity_id, identity.permission_version,
        )

    async def session(self, connection, identity):
        return json_value(await connection.fetchval(
            "SELECT security.demo_readonly_session($1::uuid,$2::uuid,$3::uuid,$4::int)",
            identity.session_id, identity.workspace_id, identity.identity_id, identity.permission_version,
        ))

    async def logout(self, connection, identity):
        return await connection.fetchval(
            "SELECT security.demo_readonly_revoke_session($1::uuid,$2::uuid,$3::uuid)",
            identity.session_id, identity.workspace_id, identity.identity_id,
        )

    async def read(self, connection, identity, resource, resource_id=None, query="", offset=0, limit=20):
        return json_value(await connection.fetchval(
            "SELECT security.demo_readonly_read($1::uuid,$2::uuid,$3::uuid,$4::int,$5,$6::uuid,$7,$8::int,$9::int)",
            identity.session_id, identity.workspace_id, identity.identity_id, identity.permission_version,
            resource, resource_id, query, offset, limit,
        ))


async def is_restricted_identity(connection, workspace_id, user_id):
    return bool(await connection.fetchval(
        "SELECT security.is_demo_readonly_identity($1::uuid,$2::uuid)", workspace_id, user_id,
    ))
