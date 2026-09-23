"""One customer and one effective assessment per selected sales subject, shared by map and profile."""


async def subject_customers(connection, *, member_ids, scope, team_ids, structure_start=None, structure_end=None,
                            customer_ids=None):
    rows = await connection.fetch(
        """SELECT c.id::text,q.potential_score,q.relationship_score,q.quadrant_code,
          q.input_snapshot->'company_policy' AS quadrant_policy,q.subject_user_ref_id::text,
          q.id::text AS assessment_id,q.calculated_at
        FROM crm.customer c
        LEFT JOIN LATERAL (
          SELECT s.* FROM insight.quadrant_score s WHERE s.customer_id=c.id
            AND s.workspace_id=c.workspace_id AND s.valid_to='infinity'
            AND s.subject_user_ref_id=ANY($1::uuid[])
          ORDER BY s.calculated_at DESC,s.id DESC LIMIT 1
        ) q ON true
        WHERE c.deleted_at IS NULL
          AND ($6::uuid[] IS NULL OR c.id=ANY($6::uuid[]))
          AND (security.profile_customer_owner(c.id)=ANY($1::uuid[]) OR ($2='team' AND c.owner_team_id=ANY($3::uuid[]))
            OR ($2='department' AND EXISTS(SELECT 1 FROM platform.team_membership tm
              WHERE tm.team_id=c.owner_team_id AND tm.user_ref_id=ANY($1::uuid[])
                AND tm.workspace_id=c.workspace_id AND clock_timestamp()>=tm.valid_from
                AND clock_timestamp()<tm.valid_to)))
          AND ($4::date IS NULL OR c.created_at>=($4::date::timestamp AT TIME ZONE 'Asia/Shanghai'))
          AND ($5::date IS NULL OR c.created_at<(($5::date+1)::timestamp AT TIME ZONE 'Asia/Shanghai'))
        ORDER BY c.id""", member_ids, scope, team_ids, structure_start, structure_end, customer_ids)
    return [dict(row) for row in rows]
