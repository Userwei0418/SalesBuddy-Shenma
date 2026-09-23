"""Resolve task targets from current workspace accounts and effective role/team bindings."""

from sales_backend.domain.tasks import TaskForbidden, TaskNotFound

POSITIONS = {"self": "自己", "supervisor": "主管", "manager": "总经理", "operations": "运营",
             "fde": "FDE", "fde_lead": "FDE主管"}


class TaskTargetRepository:
    async def recipients(self, connection, actor, *, q="", limit=None, offset=0, target_role=None):
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
                AND ($4::text IS NULL OR rb.role_code=$4)
                AND (rb.role_code NOT IN ('fde','fde_lead') OR security.fde_user_is_active(u.id,rb.role_code,t.id))
                AND ($5::text='' OR u.display_name ILIKE '%'||$5||'%' OR u.account_code ILIKE '%'||$5||'%')
              ORDER BY u.id,(u.id=$2::uuid AND rb.role_code=$3) DESC,
                CASE rb.role_code WHEN 'administrator' THEN 1 WHEN 'operations' THEN 2 WHEN 'manager' THEN 3
                  WHEN 'supervisor' THEN 4 WHEN 'fde_lead' THEN 5 WHEN 'fde' THEN 6 ELSE 7 END,
                tm.is_primary DESC NULLS LAST,tm.valid_from DESC,tm.id,rb.id
            ) SELECT * FROM people ORDER BY name,id LIMIT $6 OFFSET $7""",
            actor.workspace_id, actor.user_id, actor.role.value, target_role, q, limit, offset,
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
              AND c.role_code=$4 AND u.status='active' AND u.deleted_at IS NULL
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
            actor.role.value,
        )
