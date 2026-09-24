from sales_backend.domain.advice import AdviceError


class AdviceRepository:
    async def suggestion_kind(self, connection, suggestion_id):
        return await connection.fetchval(
            "SELECT a.subject_kind FROM insight.business_suggestion s JOIN insight.business_advice a ON a.id=s.advice_id "
            "WHERE s.id=$1::uuid",
            suggestion_id,
        )

    async def get(self, connection, advice_id, *, lock=False):
        sql = "SELECT * FROM insight.business_advice WHERE id=$1::uuid"
        row = await connection.fetchrow(sql + (" FOR UPDATE" if lock else ""), advice_id)
        if not row:
            raise AdviceError("建议不存在或无权查看", 404)
        value = dict(row)
        value["suggestions"] = [
            dict(r)
            for r in await connection.fetch(
                "SELECT id::text,ordinal,title,evidence,action,evidence_refs,decision,decision_note,"
                "task_id::text,decided_at,version_no FROM insight.business_suggestion "
                "WHERE advice_id=$1::uuid ORDER BY ordinal",
                advice_id,
            )
        ]
        rows = await connection.fetch(
            """SELECT t.source_suggestion_id::text AS suggestion_id,t.id::text,t.status,
            COALESCE(string_agg(u.display_name,'、' ORDER BY u.display_name),'待确认负责人') AS assignee_name,
            COALESCE(string_agg(u.account_code,'、' ORDER BY u.display_name),'') AS assignee_account
            FROM workflow.task t
            JOIN insight.business_suggestion s ON s.id=t.source_suggestion_id
            LEFT JOIN workflow.task_assignee a ON a.task_id=t.id AND a.responsibility='owner'
            LEFT JOIN platform.user_ref u ON u.id=a.assignee_user_ref_id
            WHERE s.advice_id=$1::uuid GROUP BY t.id ORDER BY t.created_at,t.id""", advice_id,
        )
        by_suggestion = {}
        for task in rows:
            ref = dict(task)
            by_suggestion.setdefault(ref.pop("suggestion_id"), []).append(ref)
        for suggestion in value["suggestions"]:
            suggestion["tasks"] = by_suggestion.get(suggestion["id"], [])
        return value

    async def enqueue(self, connection, actor, analysis_id):
        return await connection.fetchval(
            "INSERT INTO ops.job(workspace_id,job_type,aggregate_type,aggregate_id,payload) "
            "VALUES($1::uuid,'business.advice','business_advice',$2::uuid,$3::jsonb) RETURNING id::text",
            actor.workspace_id,
            analysis_id,
            actor.model_dump(mode="json"),
        )

    async def statistics(self, connection):
        return dict(
            await connection.fetchrow(
                """SELECT count(*) FILTER(WHERE s.decision='adopted')::int AS adopted,
            count(*) FILTER(WHERE s.decision='no_task')::int AS no_task,
            count(*) FILTER(WHERE s.decision='pending')::int AS pending,
            round(100.0*count(*) FILTER(WHERE s.decision='adopted') /
              NULLIF(count(*) FILTER(WHERE s.decision IN ('adopted','no_task')),0),1) AS adoption_rate
            FROM insight.business_suggestion s JOIN insight.business_advice a ON a.id=s.advice_id
            WHERE s.decision IN ('adopted','no_task') OR a.status='succeeded'"""
            )
        )
