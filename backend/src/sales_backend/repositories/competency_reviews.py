"""Database access for sales competency reviews; provider selection stays in services."""

from sales_backend.repositories.jobs import record_job_effect


class CompetencyReviewRepository:
    async def start(self, connection, actor, review_id):
        review = await connection.fetchrow(
            """SELECT id::text, subject_user_ref_id::text, review_date, framework_version, status
               FROM insight.sales_competency_review
               WHERE id=$1::uuid AND status IN ('queued','running') FOR UPDATE""",
            review_id,
        )
        if not review:
            raise LookupError("competency review not found or not runnable")
        if str(review["subject_user_ref_id"]) != actor.user_id:
            raise PermissionError("competency review actor does not match subject")
        framework = await connection.fetchrow(
            """SELECT version_no, display_name, dimensions, scoring_rules
               FROM config.sales_competency_framework
               WHERE version_no=$1 AND status='active'
                 AND (workspace_id IS NULL OR workspace_id=$2::uuid)
               ORDER BY (workspace_id IS NOT NULL) DESC LIMIT 1""",
            review["framework_version"], actor.workspace_id,
        )
        if not framework:
            raise LookupError("active competency framework not found")
        await connection.execute(
            """UPDATE insight.sales_competency_review
               SET status='running',error_code=NULL,error_detail=NULL WHERE id=$1::uuid""",
            review_id,
        )
        return dict(review), dict(framework)

    async def facts(self, connection, actor, review_date, start, end):
        visits = await connection.fetch(
            """SELECT v.id::text AS visit_id,v.interaction_at,
                 COALESCE(c.name,security.customer_reference(v.customer_id)->>'name') AS customer_name,
                 o.name AS opportunity_name,o.amount AS opportunity_amount,
                 v.partner_name_snapshot,v.lead_source_snapshot,v.contact_category_snapshot,v.contact_title_snapshot,
                 v.contact_name_snapshot,v.interaction_mode_code,v.visit_location,v.duration_minutes,v.expectation_code,
                 v.follow_up_record,v.next_action
               FROM activity.visit v
               LEFT JOIN crm.customer c ON c.id=v.customer_id
               LEFT JOIN crm.opportunity o ON o.id=v.opportunity_id
               WHERE v.deleted_at IS NULL AND v.recorder_user_ref_id=$1::uuid
                 AND v.status IN ('confirmed','archived')
                 AND v.interaction_at >= $2::timestamptz AND v.interaction_at < $3::timestamptz
               ORDER BY v.interaction_at DESC,v.created_at DESC,v.id DESC LIMIT 80""",
            actor.user_id, start, end,
        )
        prior = await connection.fetchrow(
            """SELECT review_date,overall_score,dimension_scores FROM insight.sales_competency_review
               WHERE subject_user_ref_id=$1::uuid AND status='succeeded' AND review_date<$2::date
               ORDER BY review_date DESC LIMIT 1""",
            actor.user_id, review_date,
        )
        return [dict(row) for row in visits], dict(prior) if prior else None

    async def save(self, connection, actor, review_id, values, snapshot, model_ref):
        saved = await connection.fetchval(
            """UPDATE insight.sales_competency_review
               SET status='succeeded',overall_score=$2,dimension_scores=$3::jsonb,summary=$4,
                 strengths=$5::jsonb,improvements=$6::jsonb,evidence=$7::jsonb,input_snapshot=$8::jsonb,
                 model_id=$9,reviewed_at=clock_timestamp(),error_code=NULL,error_detail=NULL
               WHERE id=$1::uuid AND subject_user_ref_id=$10::uuid AND status='running' RETURNING id::text""",
            review_id, values["overall_score"], values["dimension_scores"], values["summary"],
            values["strengths"], values["improvements"], values["evidence"], snapshot, model_ref, actor.user_id,
        )
        if saved is None:
            raise LookupError("competency review is no longer writable")
        await record_job_effect(connection, actor.workspace_id)
