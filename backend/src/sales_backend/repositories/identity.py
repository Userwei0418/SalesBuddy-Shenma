from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

import asyncpg

from sales_backend.auth.tokens import SessionReference, TokenService
from sales_backend.db import json_value
from sales_backend.domain.agent import ActorContext, DataScope, RoleCode


@dataclass(frozen=True, slots=True)
class ActorRecord:
    context: ActorContext
    account_code: str | None
    display_name: str
    team_names: tuple[str, ...]
    role_title: str | None = None


@dataclass(frozen=True, slots=True)
class RefreshSessionRecord:
    session_id: str
    actor: ActorRecord


ROLE_NAMES = {
    "sales": "一线销售",
    "supervisor": "销售主管",
    "manager": "销售总经理",
    "fde": "FDE",
    "fde_lead": "FDE主管",
    "operations": "运营",
    "administrator": "系统管理员",
}
SCOPE_NAMES = {"self": "仅本人", "team": "直属团队", "workspace": "全部团队"}


class IdentityRepository:
    async def find_company_management_actor(self, connection: asyncpg.Connection, target: str) -> ActorRecord | None:
        selected = json_value(await connection.fetchval(
            "SELECT security.company_management_actor($1::uuid)", target))
        return self._actor(selected) if selected else None

    async def find_actor_by_account(
        self,
        connection: asyncpg.Connection,
        *,
        workspace_external_id: str,
        account_code: str,
    ) -> ActorRecord | None:
        row = await connection.fetchrow(
            "SELECT * FROM security.resolve_demo_actor($1, $2)",
            workspace_external_id,
            account_code.strip().upper(),
        )
        return await self._with_role_title(connection, self._actor(row)) if row else None

    async def find_maintenance_administrator(
        self, connection: asyncpg.Connection, *, workspace_external_id: str, account_code: str,
    ) -> ActorRecord | None:
        """Existing formal account lookup for trusted server maintenance, not login.

        Does not create a session, read a password or require demo authentication.
        SQL selects an actual active administrator binding, never a supplied role.
        """
        row = await connection.fetchrow(
            "SELECT * FROM security.resolve_account_actor($1,$2,'administrator')",
            workspace_external_id, account_code.strip().upper(),
        )
        return await self._with_role_title(connection, self._actor(row)) if row else None

    async def find_actor_by_id(
        self,
        connection: asyncpg.Connection,
        *,
        workspace_id: str,
        user_id: str,
        role: str | None = None,
    ) -> ActorRecord | None:
        row = await connection.fetchrow(
            """
            SELECT
              u.workspace_id::text AS workspace_id,
              u.id::text AS user_id,
              u.account_code,
              u.display_name,
              rb.role_code,
              rb.data_scope_code,
              COALESCE((SELECT array_agg(d.team_id ORDER BY d.is_primary DESC,d.team_id)
                FROM security.account_team_scope(u.workspace_id,u.id,rb.role_code) d),ARRAY[]::text[]) AS team_ids,
              COALESCE((SELECT array_agg(d.team_name ORDER BY d.is_primary DESC,d.team_id)
                FROM security.account_team_scope(u.workspace_id,u.id,rb.role_code) d),ARRAY[]::text[]) AS team_names
            FROM platform.user_ref u
            JOIN platform.role_binding rb
              ON rb.workspace_id = u.workspace_id AND rb.user_ref_id = u.id
             AND clock_timestamp() >= rb.valid_from AND clock_timestamp() < rb.valid_to
            LEFT JOIN platform.team_membership tm
              ON tm.workspace_id = u.workspace_id AND tm.user_ref_id = u.id
             AND clock_timestamp() >= tm.valid_from AND clock_timestamp() < tm.valid_to
             AND (rb.role_code IN ('manager','operations','administrator') OR (
               (tm.membership_role=rb.role_code OR (rb.role_code='fde' AND tm.membership_role='fde_lead'))
               AND (rb.role_code NOT IN ('fde','fde_lead') OR rb.team_id IS NULL OR rb.team_id=tm.team_id)))
            LEFT JOIN platform.team t ON t.id = tm.team_id AND t.deleted_at IS NULL
            WHERE u.workspace_id = $1::uuid AND u.id = $2::uuid
              AND u.status = 'active' AND u.deleted_at IS NULL
              AND ($3::text IS NULL OR rb.role_code=$3)
              AND (rb.role_code IN ('manager','operations','administrator') OR (t.id IS NOT NULL AND t.status='active'
                AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to))
            GROUP BY u.workspace_id, u.id, u.account_code, u.display_name,
                     rb.role_code, rb.data_scope_code
            ORDER BY rb.role_code
            LIMIT 1
            """,
            workspace_id,
            user_id,
            role,
        )
        return await self._with_role_title(connection, self._actor(row)) if row else None

    async def create_session(
        self,
        connection: asyncpg.Connection,
        *,
        session_id: str,
        actor: ActorContext,
        refresh_token_hash: str,
        expires_at: datetime,
        client: dict[str, str],
    ) -> None:
        await connection.execute(
            "SELECT security.create_auth_session($1::uuid, $2::uuid, $3::uuid, $4, $5, $6::jsonb)",
            session_id,
            actor.workspace_id,
            actor.user_id,
            refresh_token_hash,
            expires_at,
            client,
        )

    async def rotate_refresh_token(
        self,
        connection: asyncpg.Connection,
        *,
        refresh_token: str,
        new_hash: str,
        new_expires_at: datetime,
    ) -> RefreshSessionRecord | None:
        old_hash = TokenService.hash_refresh_token(refresh_token)
        row = await connection.fetchrow(
            "SELECT * FROM security.rotate_auth_session($1, $2, $3)",
            old_hash,
            new_hash,
            new_expires_at,
        )
        if not row:
            return None
        actor = ActorRecord(
            context=ActorContext(
                workspace_id=row["workspace_id"],
                user_id=row["user_id"],
                role=RoleCode(row["role_code"]),
                data_scope=DataScope(row["data_scope_code"]),
                team_ids=tuple(row["team_ids"]),
            ),
            account_code=row["account_code"],
            display_name=row["display_name"],
            team_names=tuple(row["team_names"]),
        )
        return RefreshSessionRecord(session_id=row["session_id"], actor=await self._with_role_title(connection, actor))

    async def session_is_active(self, connection: asyncpg.Connection, *, session_id: str, actor: ActorContext) -> bool:
        return bool(
            await connection.fetchval(
                "SELECT security.auth_session_is_active($1::uuid, $2::uuid, $3::uuid)",
                session_id,
                actor.workspace_id,
                actor.user_id,
            )
        )

    async def revoke_session(self, connection: asyncpg.Connection, *, session_id: str, actor: ActorContext) -> None:
        await self.revoke_identified_session(connection, SessionReference.from_actor(actor, session_id))

    async def session_audit_identity(
        self, connection: asyncpg.Connection, reference: SessionReference,
    ) -> ActorRecord | None:
        """Read attribution for a verified session ID, not an authorization profile.

        Only owner/workspace GUCs are set. Role and teams remain empty while
        reading; the original role comes from auth_session, not caller claims.
        """
        await connection.execute(
            """SELECT set_config('app.workspace_id',$1,true),
                      set_config('app.user_ref_id',$2,true),
                      set_config('app.role_code','',true), set_config('app.team_ids','',true)""",
            reference.workspace_id, reference.user_id,
        )
        row = await connection.fetchrow(
            """SELECT u.account_code,u.display_name,s.active_role
               FROM platform.auth_session s JOIN platform.user_ref u
                 ON u.id=s.user_ref_id AND u.workspace_id=s.workspace_id
               WHERE s.id=$1::uuid AND s.workspace_id=$2::uuid AND s.user_ref_id=$3::uuid""",
            reference.session_id, reference.workspace_id, reference.user_id,
        )
        if not row or row["active_role"] not in ROLE_NAMES:
            return None
        # Minimal context solely for the audit receipt after revocation. It is
        # never returned as a login result or used to authorize a business read.
        return ActorRecord(
            ActorContext(workspace_id=reference.workspace_id, user_id=reference.user_id,
                         role=RoleCode(row["active_role"]), data_scope=DataScope.SELF),
            row["account_code"], row["display_name"], (),
        )

    async def revoke_identified_session(self, connection: asyncpg.Connection, reference: SessionReference) -> None:
        # Caller must supply a server-verified reference, never IDs from a request body.
        await connection.execute(
            "SELECT security.revoke_auth_session($1::uuid, $2::uuid, $3::uuid)",
            reference.session_id, reference.workspace_id, reference.user_id,
        )

    async def _with_role_title(self, connection, record):
        if record.context.role is not RoleCode.SUPERVISOR:
            return record
        title = await connection.fetchval("SELECT security.account_supervisor_title($1::uuid,$2::uuid)",
                                          record.context.workspace_id, record.context.user_id)
        return replace(record, role_title=title or ROLE_NAMES["supervisor"])

    @staticmethod
    def _actor(row: asyncpg.Record | dict[str, Any]) -> ActorRecord:
        return ActorRecord(
            context=ActorContext(
                workspace_id=row["workspace_id"],
                user_id=row["user_id"],
                role=RoleCode(row["role_code"]),
                data_scope=DataScope(row["data_scope_code"]),
                team_ids=tuple(row["team_ids"]),
            ),
            account_code=row["account_code"],
            display_name=row["display_name"],
            team_names=tuple(row["team_names"]),
        )
