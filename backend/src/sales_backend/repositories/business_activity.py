"""Management activity projection, filtered and paginated in PostgreSQL."""

from datetime import datetime, timezone

from sales_backend.domain.business_activity import ACTIONS, present_activity


class BusinessActivityRepository:
    async def list(
        self,
        connection,
        *,
        start,
        end,
        actor=None,
        category=None,
        action=None,
        q=None,
        limit=50,
        offset=0,
        outcome=None,
        department=None,
    ):
        matched_actions = [code for code, label in ACTIONS.items() if q and q in label]
        row = await connection.fetchrow(
            """WITH classified AS (
                SELECT *,CASE WHEN action_code='material.upload' THEN CASE
                  WHEN payload->>'processing_status'='failed' THEN 'failed'
                  WHEN payload->>'processing_status' IN ('queued','processing') THEN 'pending' ELSE 'success' END
                  WHEN action_code='material.process' AND payload->'entries'->-1->'after'->>'status'='failed' THEN 'failed'
                  WHEN action_code='data.export' AND payload->'entries'->-1->>'result'<>'success' THEN 'failed'
                  ELSE 'success' END AS outcome FROM security.business_activity_rows($1,$2)
            ), filtered AS MATERIALIZED (
                SELECT * FROM classified
                WHERE ($3::uuid IS NULL OR actor_id=$3) AND ($4::text IS NULL OR split_part(action_code,'.',1)=$4)
                AND ($5::text IS NULL OR action_code=$5)
                AND ($6::text IS NULL OR concat_ws(' ',actor_name,actor_department,object_name,customer_name,payload::text) ILIKE '%'||$6||'%'
                     OR action_code=ANY($7::text[]))
                AND ($10::text IS NULL OR outcome=$10)
                AND ($11::uuid IS NULL OR actor_id IN (SELECT user_ref_id FROM platform.team_membership
                  WHERE team_id=$11 AND workspace_id=common.current_workspace_id() AND valid_from<=clock_timestamp() AND valid_to>clock_timestamp()))
            ), page AS (SELECT * FROM filtered ORDER BY occurred_at DESC,event_id DESC LIMIT $8 OFFSET $9)
            SELECT (SELECT count(*) FROM filtered)::integer AS total,
              (SELECT count(DISTINCT actor_id) FROM filtered)::integer AS people,
              (SELECT count(*) FROM filtered WHERE execution_kind='system')::integer AS system_count,
              COALESCE((SELECT jsonb_agg(to_jsonb(page) ORDER BY occurred_at DESC,event_id DESC) FROM page),'[]'::jsonb) AS items""",
            start,
            end,
            actor,
            category,
            action,
            q,
            matched_actions,
            limit,
            offset,
            outcome,
            department,
        )
        return {**dict(row), "items": [present_activity(r) for r in row["items"]]}

    async def detail(self, connection, event_id):
        row = await connection.fetchrow(
            "SELECT * FROM security.business_activity_rows($1,$2) WHERE event_id=$3",
            datetime(1900, 1, 1, tzinfo=timezone.utc),
            datetime(9999, 1, 1, tzinfo=timezone.utc),
            event_id,
        )
        if row is None:
            raise LookupError("操作记录不存在或不可见")
        return present_activity(row)
