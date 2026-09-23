"""Read complete, permission-scoped competency evidence before aggregation."""


async def framework(connection, workspace_id):
    row = await connection.fetchrow(
        """SELECT version_no,display_name,dimensions,scoring_rules
        FROM config.sales_competency_framework
        WHERE status='active' AND effective_to='infinity'
          AND (workspace_id IS NULL OR workspace_id=$1::uuid)
        ORDER BY (workspace_id IS NOT NULL) DESC,version_no DESC LIMIT 1""",
        workspace_id,
    )
    return dict(row) if row else None


async def eligible_members(connection, workspace_id, members):
    return [
        str(row["id"])
        for row in await connection.fetch(
            """SELECT u.id FROM platform.user_ref u
        WHERE u.workspace_id=$1::uuid AND u.id=ANY($2::uuid[])
          AND u.deleted_at IS NULL AND u.status='active'
          AND EXISTS(SELECT 1 FROM platform.role_binding ur WHERE ur.user_ref_id=u.id
            AND ur.workspace_id=u.workspace_id AND ur.role_code IN ('sales','supervisor')
            AND clock_timestamp()>=ur.valid_from AND clock_timestamp()<ur.valid_to)""",
            workspace_id,
            members,
        )
    ]


async def latest_reviews(connection, workspace_id, members, days):
    return [
        dict(row)
        for row in await connection.fetch(
            """SELECT DISTINCT ON(subject_user_ref_id) id::text,subject_user_ref_id::text,
          review_date,reviewed_at,dimension_scores,input_snapshot
        FROM insight.sales_competency_review
        WHERE workspace_id=$1::uuid AND subject_user_ref_id=ANY($2::uuid[])
          AND status='succeeded'
          AND review_date>=timezone('Asia/Shanghai',clock_timestamp())::date-$3::int
        ORDER BY subject_user_ref_id,review_date DESC,reviewed_at DESC,id DESC""",
            workspace_id,
            members,
            days,
        )
    ]
