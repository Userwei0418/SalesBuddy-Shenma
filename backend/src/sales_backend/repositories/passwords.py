from sales_backend.db import json_value


class PasswordRepository:
    async def login_identifier(self, connection, workspace, account, login_type="auto"):
        row = await connection.fetchrow(
            "SELECT * FROM security.password_login_identifier($1,$2,$3)", workspace, account, login_type,
        )
        return dict(row) if row else None

    async def lock_login_identifier(self, connection, account, login_type="auto"):
        return await connection.fetchval("SELECT security.lock_password_login_identifier($1,$2)", account, login_type)

    async def account_workspace(self, connection, account):
        return await connection.fetchval("SELECT security.password_account_workspace($1)", account)

    async def consume_attempt(self, connection, key, limit):
        return await connection.fetchval("SELECT security.login_attempt($1,$2)", key, limit)

    async def limit_status(self, connection, key, limit):
        return dict(await connection.fetchrow("SELECT * FROM security.login_limit_status($1,$2)", key, limit))

    async def candidate(self, connection, workspace, account, role=None, login_type="auto"):
        return json_value(
            await connection.fetchval(
                "SELECT security.password_candidate($1,$2,$3,$4)", workspace, account, role, login_type,
            )
        )

    async def complete_login(self, connection, session_id, password_hash, attempt_key):
        await connection.execute(
            "SELECT security.complete_password_login($1::uuid,$2,$3)", session_id, password_hash, attempt_key
        )

    async def session_credentials(self, connection, session_id):
        return json_value(await connection.fetchval("SELECT security.session_credentials($1::uuid)", session_id)) or {}

    async def change_own(self, connection, old_hash, new_hash, session_id):
        return await connection.fetchval(
            "SELECT security.change_own_password($1,$2,$3::uuid)", old_hash, new_hash, session_id
        )

    async def candidate_for_self(self, connection):
        return json_value(await connection.fetchval("SELECT security.own_password_candidate()"))
