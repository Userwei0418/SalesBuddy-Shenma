import hashlib
import json
from uuid import uuid4


class ModelCallRepository:
    async def start(
        self,
        connection,
        actor,
        *,
        operation,
        operation_id,
        run_id,
        endpoint,
        model,
        metadata,
        attempt,
        request_id,
        provider,
    ):
        invocation_id = str(uuid4())
        digest = hashlib.sha256(json.dumps(metadata, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        await connection.execute(
            """INSERT INTO agent.model_invocation(id,workspace_id,run_id,provider_code,model_id,endpoint_code,
            request_hash,request_snapshot,status,attempt_no,record_kind,actor_user_ref_id,actor_role_code,operation_code,operation_id,request_id)
            VALUES($1::uuid,$2::uuid,$3::uuid,$14,$4,$5,$6,$7::jsonb,'running',$8,'provider_attempt',$9::uuid,$10,$11,$12::uuid,$13::uuid)""",
            invocation_id,
            actor.workspace_id,
            run_id,
            model,
            endpoint,
            digest,
            metadata,
            attempt,
            actor.user_id,
            actor.role.value,
            operation,
            operation_id,
            request_id,
            provider,
        )
        return invocation_id

    async def finish(self, connection, invocation_id, usage, status, error_code, *, response_metadata=None):
        await connection.execute(
            """UPDATE agent.model_invocation SET status=$2,http_status=$3,
            input_tokens=$4,output_tokens=$5,audio_seconds=$6,
            usage_detail=$7::jsonb,upstream_trace_id=$8,error_code=$9,completed_at=clock_timestamp(),
            response_snapshot=response_snapshot || $10::jsonb,
            latency_ms=GREATEST(0,extract(epoch FROM clock_timestamp()-started_at)*1000)::integer
            WHERE id=$1::uuid AND record_kind='provider_attempt'""",
            invocation_id,
            status,
            usage["http_status"],
            usage["input_tokens"],
            usage["output_tokens"],
            usage["audio_seconds"],
            {"characters": usage["characters"]},
            usage["trace_id"],
            error_code,
            response_metadata or {},
        )
