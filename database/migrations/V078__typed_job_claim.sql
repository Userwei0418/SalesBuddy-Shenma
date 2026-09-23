BEGIN;
SET LOCAL check_function_bodies=on;
-- Type filtering precedes every recovery/claim. Independent worker execution slots
-- never consume attempts for a lane that is full. NULL preserves the legacy all-type
-- claim; exclude=true lets the review lane cover future/unsupported job types too.
CREATE FUNCTION ops.claim_job(p_worker_id text,p_lock_seconds integer,
 p_job_types text[],p_exclude_types boolean DEFAULT false) RETURNS SETOF ops.job
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ops AS $$
DECLARE expired record;
BEGIN
 IF p_lock_seconds IS NULL OR p_exclude_types IS NULL
  OR (p_job_types IS NOT NULL AND array_position(p_job_types,NULL) IS NOT NULL)
  OR p_lock_seconds < 3 OR p_lock_seconds > 3600 OR NULLIF(btrim(p_worker_id),'') IS NULL THEN
  RAISE EXCEPTION 'invalid worker lease settings';
 END IF;
 -- A process may stop after the result commits but before acknowledging its queue row.
 UPDATE ops.job j SET status='succeeded',completed_at=COALESCE(j.completed_at,clock_timestamp()),
   locked_by=NULL,locked_until=NULL,lease_token=NULL,updated_at=clock_timestamp()
 WHERE j.status IN ('queued','failed','running')
 AND (p_job_types IS NULL OR (j.job_type=ANY(p_job_types))<>p_exclude_types)
 AND (j.locked_until IS NULL OR j.locked_until<=clock_timestamp())
 AND (EXISTS(SELECT 1 FROM ops.job_effect e WHERE e.job_id=j.id)
   OR (j.job_type='agent.run' AND EXISTS(SELECT 1 FROM agent.run r
       WHERE r.id=j.aggregate_id AND r.status IN ('succeeded','waiting_human')))
   OR (j.job_type='visit.import' AND EXISTS(SELECT 1 FROM activity.visit_import i
       WHERE i.id=j.aggregate_id AND i.status='succeeded'))
   OR (j.job_type='sales_competency.review' AND EXISTS(SELECT 1 FROM insight.sales_competency_review r
       WHERE r.id=j.aggregate_id AND r.status='succeeded')));
 -- Bounded attempts also cover processes that disappeared without an exception handler.
 FOR expired IN
  UPDATE ops.job SET status='dead_letter',completed_at=clock_timestamp(),locked_by=NULL,
   locked_until=NULL,lease_token=NULL,last_error_code='LEASE_ATTEMPTS_EXHAUSTED',
   last_error_detail='后台任务中断且已达到重试上限，请重试或联系管理员',updated_at=clock_timestamp()
  WHERE status IN ('queued','failed','running') AND attempts>=max_attempts
    AND (p_job_types IS NULL OR (job_type=ANY(p_job_types))<>p_exclude_types)
    AND (locked_until IS NULL OR locked_until<=clock_timestamp()) RETURNING *
 LOOP
  IF expired.job_type='agent.run' THEN
   UPDATE agent.run SET status='failed',completed_at=clock_timestamp(),
    error_code='LEASE_ATTEMPTS_EXHAUSTED',error_detail=expired.last_error_detail
    WHERE id=expired.aggregate_id AND status IN ('queued','running');
  ELSIF expired.job_type='visit.import' THEN
   UPDATE activity.visit_import SET status='failed',error_message=expired.last_error_detail,updated_at=clock_timestamp()
    WHERE id=expired.aggregate_id AND status IN ('queued','processing');
  ELSIF expired.job_type='business.advice' THEN
   UPDATE insight.business_advice SET status='failed',error_code='LEASE_ATTEMPTS_EXHAUSTED',
     completed_at=clock_timestamp() WHERE id=expired.aggregate_id AND status IN ('queued','running');
  ELSIF expired.job_type='sales_competency.review' THEN
   UPDATE insight.sales_competency_review SET status='failed',error_code='LEASE_ATTEMPTS_EXHAUSTED',
    error_detail=expired.last_error_detail WHERE id=expired.aggregate_id AND status IN ('queued','running');
  END IF;
 END LOOP;
 RETURN QUERY
 UPDATE ops.job j SET status='running',attempts=j.attempts+1,locked_by=p_worker_id,
  lease_token=gen_random_uuid(),locked_until=clock_timestamp()+make_interval(secs=>p_lock_seconds),
  started_at=COALESCE(j.started_at,clock_timestamp()),updated_at=clock_timestamp()
 WHERE j.id=(SELECT candidate.id FROM ops.job candidate
  WHERE candidate.status IN ('queued','failed','running') AND candidate.attempts<candidate.max_attempts
   AND (p_job_types IS NULL OR (candidate.job_type=ANY(p_job_types))<>p_exclude_types)
   AND candidate.available_at<=clock_timestamp()
   AND (candidate.locked_until IS NULL OR candidate.locked_until<=clock_timestamp())
   AND NOT EXISTS(SELECT 1 FROM ops.job_effect e WHERE e.job_id=candidate.id)
  ORDER BY candidate.priority DESC,candidate.available_at,candidate.created_at,candidate.id
  FOR UPDATE SKIP LOCKED LIMIT 1)
 RETURNING j.*;
END $$;

-- One canonical implementation for upgraded workers and one-off/older consumers.
CREATE OR REPLACE FUNCTION ops.claim_job(p_worker_id text,p_lock_seconds integer) RETURNS SETOF ops.job
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT * FROM ops.claim_job(p_worker_id,p_lock_seconds,NULL::text[],false);
$$;
-- A malformed actor cannot safely enter business RLS as a made-up administrator.
-- This queue-only authority accepts just the current lease, derives every target
-- from the locked job row and closes pending projections in the same transaction.
CREATE FUNCTION ops.reject_invalid_job_actor(p_job_id uuid,p_lease_token uuid) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ops
 SET app.workspace_id='' SET app.user_ref_id='' SET app.role_code=''
 SET app.team_ids='' SET app.job_id='' AS $$
DECLARE claimed ops.job%ROWTYPE; aggregate_status text; result_status text;
 failure_detail constant text := '后台任务身份快照无效，已停止执行，请由管理员核对后重新发起';
BEGIN
 SELECT * INTO claimed FROM ops.job
 WHERE id=p_job_id AND lease_token=p_lease_token AND status='running'
   AND locked_until>clock_timestamp() FOR UPDATE;
 IF NOT FOUND THEN RETURN NULL; END IF;
 -- SET LOCAL values are restored on function exit by the function's SET clauses.
 -- Audit attribution is the job itself, never a payload-supplied person or role.
 PERFORM set_config('app.workspace_id',claimed.workspace_id::text,true),
         set_config('app.job_id',claimed.id::text,true);
 IF claimed.job_type='visit.import' THEN
  SELECT status INTO aggregate_status FROM activity.visit_import
   WHERE id=claimed.aggregate_id AND workspace_id=claimed.workspace_id FOR UPDATE;
 ELSIF claimed.job_type='agent.run' THEN
  SELECT status INTO aggregate_status FROM agent.run
   WHERE id=claimed.aggregate_id AND workspace_id=claimed.workspace_id FOR UPDATE;
 ELSIF claimed.job_type='business.advice' THEN
  SELECT status INTO aggregate_status FROM insight.business_advice
   WHERE id=claimed.aggregate_id AND workspace_id=claimed.workspace_id FOR UPDATE;
 ELSIF claimed.job_type='sales_competency.review' THEN
  SELECT status INTO aggregate_status FROM insight.sales_competency_review
   WHERE id=claimed.aggregate_id AND workspace_id=claimed.workspace_id FOR UPDATE;
 END IF;
 IF EXISTS(SELECT 1 FROM ops.job_effect WHERE job_id=claimed.id AND workspace_id=claimed.workspace_id)
   OR aggregate_status IN ('succeeded','waiting_human','superseded') THEN
  result_status := 'succeeded';
 ELSE
  result_status := 'dead_letter';
  IF claimed.job_type='visit.import' THEN
   UPDATE activity.visit_import SET status='failed',error_message=failure_detail,updated_at=clock_timestamp()
    WHERE id=claimed.aggregate_id AND workspace_id=claimed.workspace_id AND status IN ('queued','processing');
  ELSIF claimed.job_type='agent.run' THEN
   UPDATE agent.run SET status='failed',error_code='INVALID_JOB_ACTOR',error_detail=failure_detail,
    completed_at=clock_timestamp(),updated_at=clock_timestamp()
    WHERE id=claimed.aggregate_id AND workspace_id=claimed.workspace_id AND status IN ('queued','running');
  ELSIF claimed.job_type='business.advice' THEN
   UPDATE insight.business_advice SET status='failed',error_code='INVALID_JOB_ACTOR',
    completed_at=clock_timestamp(),updated_at=clock_timestamp()
    WHERE id=claimed.aggregate_id AND workspace_id=claimed.workspace_id AND status IN ('queued','running');
  ELSIF claimed.job_type='sales_competency.review' THEN
   UPDATE insight.sales_competency_review SET status='failed',error_code='INVALID_JOB_ACTOR',
    error_detail=failure_detail,updated_at=clock_timestamp()
    WHERE id=claimed.aggregate_id AND workspace_id=claimed.workspace_id AND status IN ('queued','running');
  END IF;
  -- battle_map.review has no pending business projection: its previous confirmed
  -- quadrant remains intact; unsupported types also terminate only their job row.
 END IF;
 UPDATE ops.job SET status=result_status,completed_at=clock_timestamp(),updated_at=clock_timestamp(),
  locked_by=NULL,locked_until=NULL,lease_token=NULL,
  last_error_code=CASE WHEN result_status='dead_letter' THEN 'INVALID_JOB_ACTOR' END,
  last_error_detail=CASE WHEN result_status='dead_letter' THEN failure_detail END
 WHERE id=claimed.id AND lease_token=p_lease_token AND status='running' AND locked_until>clock_timestamp();
 IF NOT FOUND THEN RAISE no_data_found USING MESSAGE='job lease expired during identity rejection'; END IF;
 RETURN result_status;
EXCEPTION WHEN no_data_found THEN
 -- Roll back pending-projection updates if the lease expired while waiting for a
 -- row lock. Returning NULL tells the worker it must not acknowledge this attempt.
 RETURN NULL;
END $$;
-- Match the existing queue function's owner and effective EXECUTE ACL, including installations
-- which revoked PUBLIC. Do not widen a customer's runtime/deployer permissions.
DO $$ DECLARE permission record; function_owner name; target_signature text; BEGIN
 SELECT r.rolname INTO function_owner FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
 WHERE p.oid='ops.claim_job(text,integer)'::regprocedure;
 FOREACH target_signature IN ARRAY ARRAY[
  'ops.claim_job(text,integer,text[],boolean)','ops.reject_invalid_job_actor(uuid,uuid)'
 ] LOOP
 -- CREATE FUNCTION may receive role grants from the deployer's default privileges.
 -- Remove its entire initial ACL, including PUBLIC and grant options, before copying
 -- the old overload. Revoking only PUBLIC would retain unrelated default grantees.
 FOR permission IN
  SELECT DISTINCT a.grantee,r.rolname
  FROM pg_proc p CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
  LEFT JOIN pg_roles r ON r.oid=a.grantee
  WHERE p.oid=target_signature::regprocedure
 LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %s CASCADE',target_signature,
   CASE WHEN permission.grantee=0 THEN 'PUBLIC' ELSE quote_ident(permission.rolname) END);
 END LOOP;
 EXECUTE format('ALTER FUNCTION %s OWNER TO %I',target_signature,function_owner);
 FOR permission IN
  SELECT a.grantee,a.is_grantable,r.rolname
  FROM pg_proc p CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
  LEFT JOIN pg_roles r ON r.oid=a.grantee
  WHERE p.oid='ops.claim_job(text,integer)'::regprocedure AND a.privilege_type='EXECUTE'
 LOOP
  EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO %s%s',target_signature,
   CASE WHEN permission.grantee=0 THEN 'PUBLIC' ELSE quote_ident(permission.rolname) END,
   CASE WHEN permission.is_grantable THEN ' WITH GRANT OPTION' ELSE '' END);
 END LOOP;
 END LOOP;
END $$;
COMMENT ON FUNCTION ops.claim_job(text,integer,text[],boolean) IS
 '按任务类型在数据库领取前隔离执行容量；租约、完成回执和重试规则保持一致';
COMMENT ON FUNCTION ops.reject_invalid_job_actor(uuid,uuid) IS
 '仅当前任务租约可将无效身份任务和同工作区未完成业务投影同步终止，不采用载荷中的身份或目标';
INSERT INTO ops.schema_migration(version,description)
 VALUES('V078','Worker按类型领取及有界执行容量');
COMMIT;
