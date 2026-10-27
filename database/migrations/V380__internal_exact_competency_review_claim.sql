-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V232__exact_competency_review_claim.sql.
-- Original SHA-256: 5e66a277b5dea27b77e2dbada4666ec046f1a4d662023fce094665c362367f33. See shenma_whole_121_manifest.json.
BEGIN;

-- Exact first attempt of an approved frozen generation. Never sweep, retry or
-- acknowledge other work. The existing handler still validates live permission,
-- original sources, frozen canonical hashes and the lease before paid calls.
CREATE FUNCTION ops.claim_exact_competency_review(
 p_worker_id text,p_lock_seconds integer,p_job_id uuid,p_workspace_id uuid,
 p_review_id uuid,p_user_id uuid,p_generation_no integer,p_input_fingerprint text,
 p_input_snapshot jsonb,p_trigger_type text,p_trigger_id uuid,p_trigger_version integer)
RETURNS SETOF ops.job
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE capacity integer; target record; review record;
BEGIN
 IF p_lock_seconds IS NULL OR p_lock_seconds NOT BETWEEN 3 AND 3600
  OR COALESCE(length(btrim(p_worker_id)),0) NOT BETWEEN 1 AND 200
  OR p_job_id IS NULL OR p_workspace_id IS NULL OR p_review_id IS NULL OR p_user_id IS NULL
  OR '00000000-0000-0000-0000-000000000000'::uuid IN (p_job_id,p_workspace_id,p_review_id,p_user_id)
  OR p_generation_no IS NULL OR p_generation_no<1
  OR p_input_fingerprint IS NULL OR p_input_fingerprint !~ '^[0-9a-f]{64}$'
  OR p_input_snapshot IS NULL OR jsonb_typeof(p_input_snapshot)<>'object'
  OR p_input_snapshot->>'contract_version' IS DISTINCT FROM 'sales.competency.v2'
  OR p_input_snapshot->>'input_fingerprint' IS DISTINCT FROM p_input_fingerprint
  OR p_input_snapshot ?| ARRAY['score_summary','inference_route']
  OR p_trigger_type IS NULL OR p_trigger_type NOT IN ('manual','visit.archived','visit.supplemented')
  OR (p_trigger_type='manual' AND (p_trigger_id IS NOT NULL OR p_trigger_version IS NOT NULL))
  OR (p_trigger_type<>'manual' AND (p_trigger_id IS NULL
   OR p_trigger_id='00000000-0000-0000-0000-000000000000'::uuid
   OR p_trigger_version IS NULL OR p_trigger_version<1)) THEN
  RAISE EXCEPTION 'INVALID_EXACT_COMPETENCY_CLAIM' USING ERRCODE='22023';
 END IF;

 -- Match the workspace/scheduler lock order used by ordinary and exact claims.
 PERFORM 1 FROM platform.workspace WHERE id=p_workspace_id AND status='active'
  AND deleted_at IS NULL AND security.workspace_is_active(id)
  AND security.workspace_job_enabled(id,'sales_competency.review') FOR SHARE SKIP LOCKED;
 IF NOT FOUND THEN RETURN; END IF;
 SELECT concurrency_limit INTO capacity FROM control.tenant_job_schedule
  WHERE workspace_id=p_workspace_id FOR UPDATE SKIP LOCKED;
 IF NOT FOUND THEN RETURN; END IF;
 IF (SELECT count(*) FROM ops.job WHERE workspace_id=p_workspace_id
     AND status='running' AND locked_until>clock_timestamp())>=capacity THEN RETURN; END IF;

 SELECT j.* INTO target FROM ops.job j
  WHERE j.id=p_job_id AND j.workspace_id=p_workspace_id
   AND j.job_type='sales_competency.review' AND j.aggregate_type='sales_competency_review'
   AND j.aggregate_id=p_review_id AND jsonb_typeof(j.payload)='object'
   AND j.payload->>'workspace_id'=p_workspace_id::text AND j.payload->>'user_id'=p_user_id::text
   AND j.payload->>'role' IN ('sales','supervisor')
   AND j.payload->>'role'=p_input_snapshot #>> '{facts,scope,role}'
   AND j.payload->>'data_scope'=p_input_snapshot #>> '{facts,scope,scope_type}'
   AND jsonb_typeof(j.payload->'team_ids')='array'
   AND j.payload->'team_ids'=p_input_snapshot #> '{facts,scope,team_ids}'
   AND j.status='queued' AND j.attempts=0 AND j.max_attempts>0
   AND j.available_at<=clock_timestamp()
   AND j.locked_by IS NULL AND j.locked_until IS NULL AND j.lease_token IS NULL
   AND j.started_at IS NULL AND j.completed_at IS NULL
   AND j.last_error_code IS NULL AND j.last_error_detail IS NULL
   AND NOT EXISTS(SELECT 1 FROM ops.job_effect e WHERE e.job_id=j.id)
   AND NOT EXISTS(SELECT 1 FROM ops.job other WHERE other.workspace_id=p_workspace_id
    AND other.job_type='sales_competency.review' AND other.aggregate_id=p_review_id AND other.id<>j.id)
  FOR UPDATE SKIP LOCKED;
 IF NOT FOUND THEN RETURN; END IF;

 SELECT r.* INTO review FROM insight.sales_competency_review r
  WHERE r.id=p_review_id AND r.workspace_id=p_workspace_id AND r.subject_user_ref_id=p_user_id
   AND r.job_id=p_job_id AND r.generation_no=p_generation_no
   AND r.input_fingerprint=p_input_fingerprint AND r.input_snapshot=p_input_snapshot
   AND r.input_snapshot #>> '{facts,subject_user_ref_id}'=p_user_id::text
   AND r.input_snapshot #>> '{facts,review_date}'=r.review_date::text
   AND r.input_snapshot #>> '{facts,scope,workspace_id}'=p_workspace_id::text
   AND r.input_snapshot #>> '{facts,scope,user_id}'=p_user_id::text
   AND r.input_snapshot #> '{facts,scope,selection,personal}'='true'::jsonb
   AND length(btrim(r.input_snapshot #>> '{facts,scope,permission_version}'))>0
   AND r.input_snapshot #>> '{score_basis,framework_version}'=r.framework_version::text
   AND r.trigger_type=p_trigger_type AND r.trigger_id IS NOT DISTINCT FROM p_trigger_id
   AND r.trigger_version IS NOT DISTINCT FROM p_trigger_version
   AND r.retry_of_review_id IS NULL AND r.status='queued' AND r.evaluation_state='legacy_unknown'
   AND r.reviewed_at IS NULL AND r.model_id IS NULL AND r.error_code IS NULL AND r.error_detail IS NULL
   AND r.overall_score IS NULL AND r.summary IS NULL AND r.dimension_scores='{}'::jsonb
   AND r.strengths='[]'::jsonb AND r.improvements='[]'::jsonb AND r.evidence='{}'::jsonb
  FOR SHARE SKIP LOCKED;
 IF NOT FOUND THEN RETURN; END IF;

 -- Preserve V224's subject serialization, including an earlier failed/queued
 -- generation and any later live lease. This query only observes predecessors.
 IF EXISTS (
  SELECT 1 FROM ops.job predecessor
  JOIN insight.sales_competency_review earlier ON earlier.id=predecessor.aggregate_id
   AND earlier.workspace_id=predecessor.workspace_id
  WHERE predecessor.workspace_id=p_workspace_id
   AND predecessor.job_type='sales_competency.review'
   AND predecessor.aggregate_type='sales_competency_review'
   AND predecessor.id<>target.id AND earlier.subject_user_ref_id=p_user_id
   AND predecessor.status IN ('queued','failed','running')
   AND ((earlier.review_date,earlier.generation_no,earlier.id,predecessor.created_at,predecessor.id)
      <(review.review_date,review.generation_no,review.id,target.created_at,target.id)
    OR (predecessor.status='running' AND predecessor.locked_until>clock_timestamp()))
 ) THEN RETURN; END IF;
 IF p_trigger_type<>'manual' THEN
  PERFORM 1 FROM activity.visit v WHERE v.id=p_trigger_id AND v.workspace_id=p_workspace_id
   AND v.recorder_user_ref_id=p_user_id AND v.version_no=p_trigger_version
   AND v.deleted_at IS NULL AND v.status IN ('confirmed','archived') FOR SHARE SKIP LOCKED;
  IF NOT FOUND THEN RETURN; END IF;
 END IF;
 IF NOT security.workspace_is_active(p_workspace_id)
  OR NOT security.workspace_job_enabled(p_workspace_id,'sales_competency.review') THEN RETURN; END IF;

 RETURN QUERY UPDATE ops.job j
  SET status='running',attempts=1,locked_by=p_worker_id,lease_token=gen_random_uuid(),
      locked_until=clock_timestamp()+make_interval(secs=>p_lock_seconds),
      started_at=clock_timestamp(),updated_at=clock_timestamp()
  WHERE j.id=target.id AND j.workspace_id=p_workspace_id
  RETURNING j.*;
 IF FOUND THEN
  UPDATE control.tenant_job_schedule SET last_claimed_at=clock_timestamp()
   WHERE workspace_id=p_workspace_id;
 END IF;
END $$;

-- Clear all initial/default grants. Deployment must explicitly grant the one
-- accepted Worker through its exact policy; no prior role grants are inherited.
DO $$ DECLARE permission record; function_owner name;
 signature text:='ops.claim_exact_competency_review(text,integer,uuid,uuid,uuid,uuid,integer,text,jsonb,text,uuid,integer)';
BEGIN
 SELECT r.rolname INTO STRICT function_owner FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
  WHERE p.oid='ops.claim_job(text,integer,text[],boolean)'::regprocedure;
 FOR permission IN
  SELECT DISTINCT a.grantee,r.rolname FROM pg_proc p
  CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
  LEFT JOIN pg_roles r ON r.oid=a.grantee WHERE p.oid=signature::regprocedure
 LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %s CASCADE',signature,
   CASE WHEN permission.grantee=0 THEN 'PUBLIC' ELSE quote_ident(permission.rolname) END);
 END LOOP;
 EXECUTE format('ALTER FUNCTION %s OWNER TO %I',signature,function_owner);
END $$;
COMMENT ON FUNCTION ops.claim_exact_competency_review(text,integer,uuid,uuid,uuid,uuid,integer,text,jsonb,text,uuid,integer) IS
 '仅领取指定冻结成长代次首次租约；绑定本人、租户、完整输入、代次及触发来源，不重试或维护其他任务';
INSERT INTO ops.schema_migration(version,description)
 VALUES('V380','Exact first-attempt frozen competency review claim; explicit Worker policy required');
COMMIT;
