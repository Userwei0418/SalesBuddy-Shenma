"""Current permission snapshots and audited configuration writes."""

from sales_backend.db import json_value
from sales_backend.domain.authorization import Authorization, Grant


class AuthorizationRepository:
    async def directory(self, connection):
        return json_value(await connection.fetchval("SELECT security.authorization_directory()"))

    async def snapshot(self, connection, user_id=None):
        return json_value(await connection.fetchval("SELECT security.authorization_snapshot($1::uuid)", user_id))

    async def effective(self, connection, user_id=None):
        snapshot = await self.snapshot(connection, user_id)
        return self.from_snapshot(snapshot)

    @staticmethod
    def from_snapshot(snapshot):
        allowed, denied = [], []
        for row in snapshot["grants"]:
            if row["effect"] == "deny":
                denied.append(row["permission_code"])
            else:
                allowed.append(Grant(row["permission_code"], row["scope_code"], frozenset(row["team_ids"]),
                                     row["source_code"] + ":" + row["source_id"]))
        return Authorization.combine(snapshot["workspace_id"], snapshot["user_id"], allowed, account_denies=denied)

    async def roles(self, connection):
        rows = await connection.fetch("""
            SELECT r.id::text,r.name,r.description,r.builtin_role_code,r.status,r.version_no,
              COALESCE((SELECT jsonb_agg(jsonb_build_object('permission',g.permission_code,
                'scope',g.scope_code,'team_ids',g.team_ids) ORDER BY g.permission_code)
                FROM config.permission_role_grant g WHERE g.role_id=r.id),'[]'::jsonb) permissions
            FROM config.permission_role r WHERE r.workspace_id=common.current_workspace_id()
            ORDER BY (r.builtin_role_code IS NOT NULL) DESC,r.name,r.id
        """)
        return [dict(row) for row in rows]

    async def account(self, connection, user_id):
        snapshot = await self.snapshot(connection, user_id)
        row = await connection.fetchrow("""
            SELECT COALESCE((SELECT version_no FROM config.account_authorization
                WHERE user_ref_id=$1::uuid AND workspace_id=common.current_workspace_id()),0) version_no,
             COALESCE((SELECT jsonb_agg(jsonb_build_object('role_id',a.role_id,'scope',a.scope_code,'team_ids',a.team_ids))
                FROM config.account_permission_role a WHERE user_ref_id=$1::uuid
                AND workspace_id=common.current_workspace_id()),'[]') roles,
             COALESCE((SELECT jsonb_agg(jsonb_build_object('permission',o.permission_code,'effect',o.effect,
                 'scope',COALESCE(o.scope_code,'inherit'),'team_ids',o.team_ids))
                FROM config.account_permission_override o WHERE user_ref_id=$1::uuid
                AND workspace_id=common.current_workspace_id()),'[]') overrides
        """, user_id)
        return {**snapshot, **dict(row)}

    async def save_role(self, connection, role_id, body):
        return json_value(await connection.fetchval("SELECT security.save_permission_role($1::uuid,$2::jsonb)",
                                                    role_id, body))

    async def save_account(self, connection, user_id, body):
        return json_value(await connection.fetchval("SELECT security.save_account_authorization($1::uuid,$2::jsonb)",
                                                    user_id, body))

    async def audit(self, connection, limit=50):
        rows = await connection.fetch("""
            SELECT id::text,actor_user_ref_id::text,subject_type,subject_id::text,reason,
                   before_snapshot,after_snapshot,created_at
            FROM config.authorization_audit WHERE workspace_id=common.current_workspace_id()
            ORDER BY created_at DESC,id DESC LIMIT $1
        """, limit)
        return [dict(row) for row in rows]
