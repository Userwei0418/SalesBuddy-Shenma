"""Resolve task targets from current workspace accounts and effective role/team bindings."""

from sales_backend.domain.tasks import TaskForbidden, TaskNotFound
from sales_backend.domain.role_presentation import business_role_title
from sales_backend.repositories.companies import CompanyRepository
from sales_backend.repositories.identity import ROLE_NAMES

POSITIONS = {"self": "自己", "supervisor": "主管", "manager": "总经理", "operations": "运营",
             "fde": "FDE", "fde_lead": "FDE主管"}


class TaskTargetRepository:
    async def recipient_page(self, connection, actor, *, q="", limit=100, offset=0):
        """Metadata for the existing company-wide recipient scope.

        Call only after the create or task-specific coordination authorization.
        No directory.read permission is needed: handing over an existing task
        intentionally remains available without permission to create a new one.
        """
        from sales_backend.repositories.team_directory import attach_member_teams, default_team_id

        rows = await self.recipients(connection, actor, q=q, limit=limit+1, offset=offset)
        teams = [dict(row) for row in await connection.fetch(
            """SELECT t.id::text,t.name,t.parent_team_id::text AS parent_id
            FROM platform.team t
            WHERE t.workspace_id=$1::uuid AND t.status='active' AND t.deleted_at IS NULL
              AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to
              AND NOT EXISTS (SELECT 1 FROM platform.team child
                WHERE child.workspace_id=t.workspace_id AND child.parent_team_id=t.id
                  AND child.deleted_at IS NULL)
            ORDER BY t.name,t.id""", actor.workspace_id)]
        items = await attach_member_teams(connection, actor, rows[:limit], teams)
        if items and teams:
            # Task pickers group existing recipients by organization as well as
            # appointments. This is display metadata, never an assignment scope.
            affiliations = await connection.fetch(
                """SELECT u.id::text AS user_id,t.id::text AS team_id
                FROM platform.user_ref u JOIN platform.team t
                  ON t.workspace_id=u.workspace_id
                  AND t.id::text=u.attributes->>'organization_team_id'
                WHERE u.workspace_id=$1::uuid AND u.id=ANY($2::uuid[])
                  AND u.status='active' AND u.deleted_at IS NULL
                  AND t.id=ANY($3::uuid[]) AND t.status='active' AND t.deleted_at IS NULL
                  AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to""",
                actor.workspace_id, [item['id'] for item in items], [team['id'] for team in teams],
            )
            affiliation_by_user = {row['user_id']: row['team_id'] for row in affiliations}
            for item in items:
                if affiliation := affiliation_by_user.get(item['id']):
                    item['team_ids'] = sorted({*item['team_ids'], affiliation})
                    item['team'] = ' · '.join(team['name'] for team in teams if team['id'] in item['team_ids'])
            # Keep team_id unchanged: resolve()/position_members() use the real
            # business appointment when persisting a task's assignment history.
        labels = (await CompanyRepository().role_labels(connection, actor.workspace_id))["labels"]
        kinds = await connection.fetch(
            """SELECT p.user_id::text,p.role,
                ARRAY(SELECT COALESCE(t.attributes->>'kind','general')
                  FROM security.account_team_scope($1::uuid,p.user_id,p.role) s
                  JOIN platform.team t ON t.id=s.team_id::uuid AND t.workspace_id=$1::uuid) AS department_kinds
                FROM unnest($2::uuid[],$3::text[]) AS p(user_id,role)""",
            actor.workspace_id, [item["id"] for item in items], [item["role"] for item in items],
        )
        by_member = {(row["user_id"], row["role"]): row["department_kinds"] for row in kinds}
        for item in items:
            item["role_name"] = (
                business_role_title(item["role"], labels, by_member.get((item["id"], item["role"]), ()))
                or ROLE_NAMES.get(item["role"], item["role"])
            )
        more = len(rows) > limit
        return {"items": items, "has_more": more, "next_offset": offset+limit if more else None,
                "teams": teams, "defaults": {"team_id": await default_team_id(connection, actor, teams)}}

    async def recipients(self, connection, actor, *, q="", limit=None, offset=0, target_role=None, business_only=False):
        """Workspace recipient directory; selecting a colleague grants no CRM access."""
        rows = await connection.fetch(
            """WITH people AS (
              SELECT DISTINCT ON(u.id) u.id::text,u.account_code,u.display_name AS name,
                rb.role_code AS role,t.id::text AS team_id,COALESCE(t.name,'未分组') AS team
              FROM platform.user_ref u JOIN platform.role_binding rb
                ON rb.user_ref_id=u.id AND rb.workspace_id=u.workspace_id
                AND clock_timestamp()>=rb.valid_from AND clock_timestamp()<rb.valid_to
              LEFT JOIN platform.team_membership tm ON tm.user_ref_id=u.id AND tm.workspace_id=u.workspace_id
                AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
                AND (rb.team_id IS NULL OR rb.team_id=tm.team_id)
                AND (rb.role_code NOT IN ('fde','fde_lead') OR (tm.membership_role IN ('fde','fde_lead')
                  AND (rb.role_code<>'fde_lead' OR tm.membership_role='fde_lead')))
              LEFT JOIN platform.team t ON t.id=tm.team_id AND t.workspace_id=u.workspace_id
                AND t.deleted_at IS NULL AND t.status='active'
              WHERE u.workspace_id=$1::uuid AND u.status='active' AND u.deleted_at IS NULL
                AND NULLIF(btrim(u.account_code),'') IS NOT NULL
                AND rb.role_code IN ('sales','supervisor','manager','operations','administrator','fde','fde_lead')
                AND (NOT $8::boolean OR rb.role_code IN ('sales','supervisor','manager','fde','fde_lead'))
                AND ($4::text IS NULL OR rb.role_code=$4)
                AND (rb.role_code NOT IN ('fde','fde_lead') OR security.fde_user_is_active(u.id,rb.role_code,t.id))
                AND ($5::text='' OR u.display_name ILIKE '%'||$5||'%' OR u.account_code ILIKE '%'||$5||'%')
              ORDER BY u.id,(u.id=$2::uuid AND rb.role_code=$3) DESC,
                CASE rb.role_code WHEN 'administrator' THEN 1 WHEN 'operations' THEN 2 WHEN 'manager' THEN 3
                  WHEN 'supervisor' THEN 4 WHEN 'fde_lead' THEN 5 WHEN 'fde' THEN 6 ELSE 7 END,
                tm.is_primary DESC NULLS LAST,tm.valid_from DESC,tm.id,rb.id
            ) SELECT * FROM people ORDER BY name,id LIMIT $6 OFFSET $7""",
            actor.workspace_id, actor.user_id, actor.role.value, target_role, q, limit, offset, business_only,
        )
        return [dict(row) for row in rows]

    async def position_members(self, connection, actor, position, *, opportunity_id=None, customer_id=None):
        if position not in POSITIONS:
            raise TaskForbidden("未知接收岗位")
        people = await self.recipients(connection, actor, target_role=actor.role.value if position == "self" else position)
        # The supervisor position retains its managerial meaning; explicit person
        # selection is company-wide and independent of this convenience target.
        return [dict(user_id=p["id"], display_name=p["name"], account_code=p["account_code"],
                     role_code=p["role"], team_id=p["team_id"])
                for p in people if (position != "self" or p["id"] == actor.user_id)
                and (position != "supervisor" or p["team_id"] in actor.team_ids)]

    async def resolve(self, connection, actor, *, account=None, position=None, opportunity_id=None, customer_id=None):
        if bool(account) == bool(position):
            raise TaskForbidden("请选择一个接收人或岗位")
        if position:
            rows = await self.position_members(connection, actor, position, opportunity_id=opportunity_id, customer_id=customer_id)
            if not rows:
                raise TaskNotFound("该岗位暂无有效接收人，请联系运营配置账号及部门")
            return rows
        # Resolve against the same company-wide recipient directory exposed to the picker.
        rows = await self.recipients(connection, actor)
        person = next(
            (
                row
                for row in rows
                if row["account_code"] and str(row["account_code"]).upper() == account.strip().upper()
            ),
            None,
        )
        if not person:
            raise TaskForbidden("ASSIGNEE_OUT_OF_SCOPE")
        return [
            dict(
                user_id=person["id"],
                display_name=person["name"],
                account_code=person["account_code"],
                role_code=person["role"],
                team_id=person["team_id"],
            )
        ]

    async def available_positions(self, connection, actor, *, opportunity_id=None, customer_id=None):
        result = []
        for code, label in POSITIONS.items():
            members = await self.position_members(connection, actor, code, opportunity_id=opportunity_id, customer_id=customer_id)
            result.append({"code": code, "label": label, "candidate_count": len(members), "available": bool(members)})
        return result

    async def current_candidate(self, connection, actor, task_id):
        return await connection.fetchrow(
            """SELECT c.*,t.target_position FROM workflow.task_candidate c
              JOIN workflow.task t ON t.id=c.task_id
              JOIN platform.user_ref u ON u.id=c.user_ref_id AND u.workspace_id=c.workspace_id
            WHERE c.task_id=$1::uuid AND c.user_ref_id=$2::uuid AND c.workspace_id=$3::uuid
              AND u.status='active' AND u.deleted_at IS NULL
              AND EXISTS(SELECT 1 FROM platform.role_binding rb WHERE rb.user_ref_id=u.id
                AND rb.workspace_id=u.workspace_id AND rb.role_code=c.role_code
                AND clock_timestamp()>=rb.valid_from AND clock_timestamp()<rb.valid_to)
              AND (t.target_position<>'supervisor' OR EXISTS(
                SELECT 1 FROM platform.team_membership tm WHERE tm.user_ref_id=u.id
                  AND tm.workspace_id=u.workspace_id AND tm.team_id=c.team_id
                  AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to))
              AND (c.role_code NOT IN ('fde','fde_lead') OR
                security.fde_task_eligible(t.id,c.user_ref_id,c.role_code,c.team_id))""",
            task_id,
            actor.user_id,
            actor.workspace_id,
        )
