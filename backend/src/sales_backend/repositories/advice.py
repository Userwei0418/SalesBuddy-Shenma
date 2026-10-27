from sales_backend.domain.advice import AdviceError


class AdviceRepository:
    async def latest_id(self, connection, actor, subject):
        # RLS still applies. Role is explicit because advice RLS is user-scoped.
        return await connection.fetchval(
            "SELECT id::text FROM insight.business_advice "
            "WHERE workspace_id=$1::uuid AND actor_user_ref_id=$2::uuid "
            "AND actor_role_code=$3 AND subject_kind=$4 AND subject_id=$5::uuid AND section=$6 "
            "ORDER BY COALESCE((configuration_snapshot->>'_request_sequence')::bigint,0) DESC,"
            "created_at DESC,id DESC LIMIT 1",
            actor.workspace_id, actor.user_id, actor.role.value,
            subject.subject_kind, subject.subject_id, subject.section,
        )

    async def requested(self, connection, actor, subject, advice_id):
        # Caller holds the existing subject-scoped request advisory lock.
        # Keep request order separate from worker-controlled status/updated_at.
        # This server-owned metadata is not part of the runtime configuration
        # fingerprint; legacy snapshots without it keep their creation order.
        await connection.execute(
            "UPDATE insight.business_advice SET configuration_snapshot=jsonb_set("
            "configuration_snapshot,'{_request_sequence}',to_jsonb(("
            "SELECT COALESCE(MAX((a.configuration_snapshot->>'_request_sequence')::bigint),0)+1 "
            "FROM insight.business_advice a WHERE a.workspace_id=$2::uuid "
            "AND a.actor_user_ref_id=$3::uuid AND a.actor_role_code=$4 "
            "AND a.subject_kind=$5 AND a.subject_id=$6::uuid AND a.section=$7)),true) "
            "WHERE id=$1::uuid AND workspace_id=$2::uuid "
            "AND actor_user_ref_id=$3::uuid AND actor_role_code=$4 "
            "AND subject_kind=$5 AND subject_id=$6::uuid AND section=$7",
            advice_id, actor.workspace_id, actor.user_id, actor.role.value,
            subject.subject_kind, subject.subject_id, subject.section,
        )
        return await self.get(connection, advice_id, actor=actor)

    async def suggestion_kind(self, connection, suggestion_id):
        return await connection.fetchval(
            "SELECT a.subject_kind FROM insight.business_suggestion s JOIN insight.business_advice a ON a.id=s.advice_id "
            "WHERE s.id=$1::uuid",
            suggestion_id,
        )

    async def get(self, connection, advice_id, *, lock=False, actor=None):
        sql = "SELECT * FROM insight.business_advice WHERE id=$1::uuid"
        args = [advice_id]
        if actor is not None:
            sql += " AND workspace_id=$2::uuid AND actor_user_ref_id=$3::uuid AND actor_role_code=$4"
            args.extend((actor.workspace_id, actor.user_id, actor.role.value))
        row = await connection.fetchrow(sql + (" FOR UPDATE" if lock else ""), *args)
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
