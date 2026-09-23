from uuid import uuid4

from sales_backend.db import json_value


class OperationsAccountRepository:
    async def password_policy(self, connection):
        return json_value(await connection.fetchval("SELECT security.get_password_policy()"))

    async def set_password_policy(self, connection, required, version):
        return json_value(await connection.fetchval("SELECT security.set_password_policy($1,$2)", required, version))

    async def organization(self, connection):
        teams = await connection.fetch(
            "SELECT id::text,code,name,parent_team_id::text,status,version_no,COALESCE(attributes->>'kind','general') AS kind FROM platform.team WHERE workspace_id=common.current_workspace_id() AND deleted_at IS NULL ORDER BY name"
        )
        users = await connection.fetch(
            """SELECT u.id::text,u.account_code,u.phone_number,u.email,u.display_name,u.status,u.version_no,u.created_at,
            COALESCE((u.attributes->>'platform_managed')::boolean,false) AS platform_managed,
            security.account_has_password(u.id) AS has_password,
            ls.login_locked,ls.login_retry_at,ls.login_attempts,
            COALESCE((SELECT array_agg(DISTINCT r.role_code ORDER BY r.role_code) FROM platform.role_binding r
            WHERE r.user_ref_id=u.id AND clock_timestamp()>=r.valid_from AND clock_timestamp()<r.valid_to),ARRAY[]::text[]) AS roles,
            COALESCE((SELECT array_agg(DISTINCT r.role_code ORDER BY r.role_code) FROM platform.role_binding r
            WHERE r.user_ref_id=u.id AND r.team_id IS NULL AND r.role_code IN ('operations','administrator')
            AND clock_timestamp()>=r.valid_from AND clock_timestamp()<r.valid_to),ARRAY[]::text[]) AS company_roles,
            u.attributes->>'organization_team_id' AS organization_team_id,ot.name AS organization_team_name,
            tm.team_id::text,t.name AS team_name FROM platform.user_ref u
            LEFT JOIN platform.team ot ON ot.id::text=u.attributes->>'organization_team_id'
              AND ot.workspace_id=u.workspace_id AND ot.deleted_at IS NULL
            LEFT JOIN LATERAL(SELECT team_id FROM platform.team_membership m WHERE m.user_ref_id=u.id
            AND m.is_primary AND clock_timestamp()>=m.valid_from AND clock_timestamp()<m.valid_to ORDER BY m.created_at DESC LIMIT 1) tm ON true
            LEFT JOIN platform.team t ON t.id=tm.team_id
            CROSS JOIN LATERAL security.account_login_status(u.id) ls
            WHERE u.workspace_id=common.current_workspace_id() AND u.deleted_at IS NULL ORDER BY u.created_at DESC,u.id"""
        )
        # A membership is a department appointment, not a duplicated account.
        appointments = await connection.fetch("""SELECT m.user_ref_id::text,m.team_id::text,t.name AS team_name,
          COALESCE(t.attributes->>'kind','general') AS kind,bool_or(m.is_primary) AS is_primary,bool_or(COALESCE((m.attributes->>'acting')::boolean,false)) AS acting,
          array_agg(DISTINCT m.membership_role ORDER BY m.membership_role) AS roles
          FROM platform.team_membership m JOIN platform.team t ON t.id=m.team_id AND t.workspace_id=m.workspace_id
          WHERE m.workspace_id=common.current_workspace_id() AND clock_timestamp()>=m.valid_from AND clock_timestamp()<m.valid_to AND t.deleted_at IS NULL
          GROUP BY m.user_ref_id,m.team_id,t.name,t.attributes ORDER BY t.name,m.team_id""")
        accounts = [dict(r) for r in users]
        for account in accounts:
            account["memberships"] = [dict(m) for m in appointments if m["user_ref_id"] == account["id"]]
        return {"departments": [dict(r) for r in teams], "accounts": accounts}

    async def lock_workspace(self, connection, workspace_id):
        await connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1,0))", f"account-management:{workspace_id}"
        )

    async def member(self, connection, user_id):
        row = await connection.fetchrow(
            "SELECT id::text,display_name,status,version_no,COALESCE((attributes->>'platform_managed')::boolean,false) AS platform_managed FROM platform.user_ref WHERE id=$1::uuid AND deleted_at IS NULL FOR UPDATE",
            user_id,
        )
        if not row:
            raise LookupError("账号不存在")
        roles = await connection.fetch(
            "SELECT role_code,team_id FROM platform.role_binding WHERE user_ref_id=$1::uuid AND clock_timestamp()>=valid_from AND clock_timestamp()<valid_to",
            user_id,
        )
        return {**dict(row), "roles": [r["role_code"] for r in roles],
                "company_roles": [r["role_code"] for r in roles if r["team_id"] is None and r["role_code"] in {"operations", "administrator"}]}

    async def validate_team(self, connection, team_id):
        if not await connection.fetchval(
            "SELECT EXISTS(SELECT 1 FROM platform.team WHERE id=$1::uuid AND status='active' AND deleted_at IS NULL)",
            team_id,
        ):
            raise ValueError("请选择有效部门")

    async def administrator_count(self, connection):
        return await connection.fetchval("""SELECT count(DISTINCT u.id) FROM platform.user_ref u JOIN platform.role_binding r ON r.user_ref_id=u.id
        WHERE u.status='active' AND u.deleted_at IS NULL AND r.role_code='administrator' AND clock_timestamp()>=r.valid_from AND clock_timestamp()<r.valid_to""")

    async def create(self, connection, actor, data):
        uid = str(uuid4())
        await connection.execute(
            "INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name,phone_number,email,attributes) VALUES($1::uuid,$2::uuid,$3,$4,$5,$6,$7,$8::jsonb)",
            uid,
            actor.workspace_id,
            f"local:{uid}",
            data["account_code"].strip().upper(),
            data["display_name"].strip(),
            data.get("phone_number"), data.get("email"),
            {"organization_team_id": str(data["organization_team_id"])} if data.get("organization_team_id") else {},
        )
        await self.roles_and_team(connection, actor, uid, data["roles"], data.get("team_id"), data.get("memberships"), data.get("company_roles"))
        return {"id": uid, "account_code": data["account_code"].strip().upper(), "version_no": 1}

    async def assignments(self, connection, uid):
        return [
            dict(row)
            for row in await connection.fetch(
                """SELECT team_id::text,
          bool_or(is_primary) AS is_primary,bool_or(COALESCE((attributes->>'acting')::boolean,false)) AS acting,array_agg(DISTINCT membership_role ORDER BY membership_role) AS roles
          FROM platform.team_membership WHERE user_ref_id=$1::uuid
          AND clock_timestamp()>=valid_from AND clock_timestamp()<valid_to GROUP BY team_id""",
                uid,
            )
        ]

    async def roles_and_team(self, connection, actor, uid, roles, team, memberships=None, company_roles=None):
        if memberships is None:
            memberships = [{"team_id": team, "roles": roles}] if team else []
        instant = await connection.fetchval("SELECT clock_timestamp()")
        await connection.execute(
            "UPDATE platform.role_binding SET valid_to=$2 WHERE user_ref_id=$1::uuid AND $2>=valid_from AND $2<valid_to",
            uid,
            instant,
        )
        await connection.execute(
            "UPDATE platform.team_membership SET valid_to=$2 WHERE user_ref_id=$1::uuid AND $2>=valid_from AND $2<valid_to",
            uid,
            instant,
        )
        for role in sorted(set(company_roles or [])):
            await connection.execute(
                """INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,valid_from)
                VALUES($1::uuid,$2::uuid,$3,'workspace',$4)""", actor.workspace_id, uid, role, instant,
            )
        priority = ["administrator", "operations", "manager", "supervisor", "fde_lead", "fde", "sales"]
        for membership in memberships:
            selected = sorted(set(membership["roles"]), key=priority.index)
            for index, role in enumerate(selected):
                scope = {"sales": "self", "supervisor": "team", "fde": "self", "fde_lead": "team"}.get(
                    role, "workspace"
                )
                await connection.execute(
                    """INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id,valid_from)
                    VALUES($1::uuid,$2::uuid,$3,$4,$5::uuid,$6)""",
                    actor.workspace_id,
                    uid,
                    role,
                    scope,
                    membership["team_id"],
                    instant,
                )
                await connection.execute(
                    """INSERT INTO platform.team_membership(workspace_id,team_id,user_ref_id,membership_role,is_primary,valid_from,attributes)
                    VALUES($1::uuid,$2::uuid,$3::uuid,$4,$5,$6,jsonb_build_object('acting',$7::boolean))""",
                    actor.workspace_id,
                    membership["team_id"],
                    uid,
                    role,
                    str(membership["team_id"]) == str(team) and index == 0,
                    instant,
                    membership.get("acting", False),
                )

    async def update(self, connection, actor, uid, data):
        # Check the existing management identity before its own roles can change.
        await self.revoke_sessions(connection, uid)
        await connection.execute(
            """UPDATE platform.user_ref SET display_name=$2,status=$3,
            account_code=COALESCE($4,account_code),
            phone_number=CASE WHEN $5 THEN $6 ELSE phone_number END,
            email=CASE WHEN $8 THEN $9 ELSE email END,
            attributes=CASE WHEN $10 THEN (attributes - 'organization_team_id') || $11::jsonb ELSE attributes END,
            version_no=version_no+1,updated_at=clock_timestamp()
            WHERE id=$1::uuid AND workspace_id=$7::uuid""",
            uid, data["display_name"].strip(), data["status"],
            data["account_code"].strip().upper() if data.get("account_code") is not None else None,
            "phone_number" in data, data.get("phone_number"), actor.workspace_id,
            "email" in data, data.get("email"),
            "organization_team_id" in data,
            {"organization_team_id": str(data["organization_team_id"])} if data.get("organization_team_id") else {},
        )
        await self.roles_and_team(connection, actor, uid, data["roles"], data.get("team_id"), data.get("memberships"), data.get("company_roles"))
        return {"id": str(uid), "version_no": data["version_no"] + 1, "status": data["status"]}

    async def revoke_sessions(self, connection, uid):
        await connection.execute(
            "SELECT security.revoke_managed_account_sessions($1::uuid)",
            uid,
        )

    async def rename_account(self, connection, actor, uid, account_code, email):
        await self.revoke_sessions(connection, uid)
        return dict(await connection.fetchrow(
            """UPDATE platform.user_ref SET account_code=$3,email=$4,
            version_no=version_no+1,updated_at=clock_timestamp()
            WHERE workspace_id=$1::uuid AND id=$2::uuid
            RETURNING id::text,account_code,version_no""",
            actor.workspace_id, uid, account_code.strip().upper(), email,
        ))

    async def set_password(self, connection, uid, encoded):
        await connection.execute("SELECT security.set_account_password($1::uuid,$2,true)", uid, encoded)

    async def login_status(self, connection, uid):
        return dict(await connection.fetchrow("SELECT * FROM security.account_login_status($1::uuid)", uid))

    async def unlock_login(self, connection, uid, reason):
        await connection.execute("SELECT security.unlock_account_login($1::uuid,$2)", uid, reason)

    async def bump_version(self, connection, uid):
        return await connection.fetchval(
            "UPDATE platform.user_ref SET version_no=version_no+1,updated_at=clock_timestamp() WHERE id=$1::uuid RETURNING version_no",
            uid,
        )

    async def save_department(self, connection, actor, team_id, data):
        await self.lock_workspace(connection, actor.workspace_id)
        if data["parent_team_id"]:
            await self.validate_team(connection, data["parent_team_id"])
        if team_id:
            row = await connection.fetchrow(
                "SELECT version_no FROM platform.team WHERE id=$1::uuid AND deleted_at IS NULL FOR UPDATE", team_id
            )
            if not row:
                raise LookupError("部门不存在")
            if row["version_no"] != data["version_no"]:
                raise ValueError("部门已修改，请刷新")
            cycle = await connection.fetchval(
                """WITH RECURSIVE parents AS (SELECT id,parent_team_id FROM platform.team WHERE id=$1::uuid
            UNION SELECT t.id,t.parent_team_id FROM platform.team t JOIN parents p ON t.id=p.parent_team_id)
            SELECT EXISTS(SELECT 1 FROM parents WHERE id=$2::uuid)""",
                data["parent_team_id"],
                team_id,
            )
            if cycle:
                raise ValueError("部门层级不能形成循环")
            if data["status"] == "inactive" and await connection.fetchval(
                """SELECT EXISTS(SELECT 1 FROM platform.team_membership m JOIN platform.user_ref u ON u.id=m.user_ref_id
                WHERE m.team_id=$1::uuid AND u.status='active' AND clock_timestamp()>=m.valid_from AND clock_timestamp()<m.valid_to)
                OR EXISTS(SELECT 1 FROM platform.user_ref WHERE attributes->>'organization_team_id'=$1::text AND status='active' AND deleted_at IS NULL)
                OR EXISTS(SELECT 1 FROM platform.team WHERE parent_team_id=$1::uuid AND status='active' AND deleted_at IS NULL)""",
                team_id,
            ):
                raise ValueError("请先迁移有效成员和子部门，再停用部门")
            await connection.execute(
                "UPDATE platform.team SET code=$2,name=$3,parent_team_id=$4::uuid,status=$5,version_no=version_no+1,updated_at=clock_timestamp() WHERE id=$1::uuid",
                team_id,
                data["code"],
                data["name"].strip(),
                data["parent_team_id"],
                data["status"],
            )
        else:
            team_id = str(uuid4())
            await connection.execute(
                "INSERT INTO platform.team(id,workspace_id,code,name,parent_team_id,status) VALUES($1::uuid,$2::uuid,$3,$4,$5::uuid,$6)",
                team_id,
                actor.workspace_id,
                data["code"],
                data["name"].strip(),
                data["parent_team_id"],
                data["status"],
            )
        if data.get("kind") is not None:
            await connection.execute(
                "UPDATE platform.team SET attributes=jsonb_set(attributes,'{kind}',to_jsonb($2::text)) WHERE id=$1::uuid",
                team_id,
                data["kind"],
            )
        return {"id": str(team_id)}
