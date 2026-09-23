"""SQL for visit review provenance, replay and human confirmation in the caller transaction."""


class VisitReviewRepository:
    async def lock_previous_archive(self, connection, actor, run_id):
        await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", actor.user_id + run_id)
        previous = await connection.fetchrow(
            """SELECT v.id::text,v.customer_id::text,v.opportunity_id::text,
            r.business_context,v.visit_goal,v.follow_up_record,v.next_action,v.is_first_visit,v.first_visit_profile,v.opportunity_mutation_hash
            FROM activity.visit v JOIN agent.artifact a ON a.id=v.source_artifact_id
            JOIN agent.run r ON r.id=a.run_id
            WHERE r.id=$1::uuid AND v.recorder_user_ref_id=$2::uuid""",
            run_id,
            actor.user_id,
        )
        return previous

    async def bind_archive(self, connection, visit_id, artifact_id, mutation_hash):
        await connection.execute(
            "UPDATE activity.visit SET source_artifact_id=$2::uuid,opportunity_mutation_hash=$3,"
            "follow_up_task_mode='human_advice' WHERE id=$1::uuid",
            visit_id,
            artifact_id,
            mutation_hash,
        )

    async def lock_review(self, connection, actor, run_id):
        row = await connection.fetchrow(
            """SELECT a.id::text, a.payload, a.status, m.text_content,r.business_context,r.identity_context
               FROM agent.artifact a JOIN agent.run r ON r.id = a.run_id
               JOIN agent.message m ON m.id = r.trigger_message_id
               WHERE r.id = $1::uuid AND a.workspace_id = $2::uuid
                 AND r.workspace_id = $2::uuid AND a.created_by_user_ref_id = $3::uuid
                 AND r.identity_context->>'user_id' = $3::text
                 AND r.intent_code = 'visit_entry' AND r.status = 'waiting_human'
                 AND a.artifact_type = 'visit_entry'
               FOR UPDATE OF a""",
            run_id,
            actor.workspace_id,
            actor.user_id,
        )
        return row

    async def confirm(self, connection, actor, artifact_id, payload_hash, trusted_fields):
        await connection.execute(
            """INSERT INTO agent.confirmation
               (workspace_id, artifact_id, confirmer_user_ref_id, decision, original_payload_hash, edited_payload)
               VALUES ($1::uuid, $2::uuid, $3::uuid, 'edited_and_confirmed', $4, $5::jsonb)""",
            actor.workspace_id,
            artifact_id,
            actor.user_id,
            payload_hash,
            trusted_fields,
        )
        await connection.execute(
            "UPDATE agent.artifact SET status='applied', updated_at=clock_timestamp() WHERE id=$1::uuid",
            artifact_id,
        )
