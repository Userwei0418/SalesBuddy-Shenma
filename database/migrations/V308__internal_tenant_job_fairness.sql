-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V160__tenant_job_fairness.sql.
-- Original SHA-256: 94c052f675bcdc6698a4c94684885009e46df6a6e02dba065d8ec29233aa9251. See shenma_whole_121_manifest.json.
BEGIN;
-- Technical concurrency defaults, not commercial usage/billing quotas.
CREATE TABLE control.tenant_job_schedule(
 workspace_id uuid PRIMARY KEY REFERENCES platform.workspace(id),
 concurrency_limit integer NOT NULL DEFAULT 2 CHECK(concurrency_limit BETWEEN 1 AND 16),
 last_claimed_at timestamptz NOT NULL DEFAULT '-infinity'
);
INSERT INTO control.tenant_job_schedule(workspace_id) SELECT id FROM platform.workspace;
CREATE FUNCTION control.initialize_tenant_job_schedule() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 INSERT INTO control.tenant_job_schedule(workspace_id) VALUES(NEW.id);
 RETURN NEW;
END $$;
CREATE TRIGGER initialize_tenant_job_schedule AFTER INSERT ON platform.workspace
 FOR EACH ROW EXECUTE FUNCTION control.initialize_tenant_job_schedule();
CREATE INDEX job_workspace_live_lease_idx ON ops.job(workspace_id,locked_until) WHERE status='running';
CREATE FUNCTION control.job_settings(p_session text,p_workspace uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result jsonb;
BEGIN
 PERFORM control.require_actor(p_session);
 SELECT jsonb_build_object('workspace_id',w.id,'version_no',w.version_no,'concurrency_limit',s.concurrency_limit,
  'running',(SELECT count(*) FROM ops.job WHERE workspace_id=w.id AND status='running' AND locked_until>clock_timestamp()),
  'waiting',(SELECT count(*) FROM ops.job WHERE workspace_id=w.id AND status IN ('queued','failed')),
  'dead_letter',(SELECT count(*) FROM ops.job WHERE workspace_id=w.id AND status='dead_letter'))
 INTO result FROM platform.workspace w JOIN control.tenant_job_schedule s ON s.workspace_id=w.id
 WHERE w.id=p_workspace AND w.deleted_at IS NULL;
 IF result IS NULL THEN RAISE EXCEPTION 'TENANT_NOT_FOUND' USING ERRCODE='P7404'; END IF;
 RETURN result;
END $$;
CREATE FUNCTION control.save_job_settings(p_session text,p_workspace uuid,p_version integer,p_limit integer,p_reason text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE actor uuid:=control.require_sensitive(p_session); version integer; before_value jsonb;
BEGIN
 SELECT version_no INTO version FROM platform.workspace WHERE id=p_workspace AND deleted_at IS NULL FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'TENANT_NOT_FOUND' USING ERRCODE='P7404'; END IF;
 IF version IS DISTINCT FROM p_version THEN RAISE EXCEPTION 'VERSION_CONFLICT' USING ERRCODE='P7409'; END IF;
 IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 16 OR COALESCE(length(btrim(p_reason)),0) NOT BETWEEN 1 AND 500 THEN
  RAISE EXCEPTION 'INVALID_JOB_SETTINGS' USING ERRCODE='P7422'; END IF;
 before_value:=control.job_settings(p_session,p_workspace);
 UPDATE control.tenant_job_schedule SET concurrency_limit=p_limit WHERE workspace_id=p_workspace;
 UPDATE platform.workspace SET version_no=version_no+1,updated_at=clock_timestamp() WHERE id=p_workspace;
 INSERT INTO control.audit(operator_id,workspace_id,action,reason,before_state,after_state)
  VALUES(actor,p_workspace,'tenant.job_settings',btrim(p_reason),before_value,control.job_settings(p_session,p_workspace));
 RETURN control.job_settings(p_session,p_workspace);
END $$;
REVOKE ALL ON control.tenant_job_schedule FROM PUBLIC,salegent_feishu_worker;
REVOKE ALL ON FUNCTION control.initialize_tenant_job_schedule(),control.job_settings(text,uuid),
 control.save_job_settings(text,uuid,integer,integer,text) FROM PUBLIC,salegent_feishu_worker;
CREATE OR REPLACE FUNCTION ops.claim_job(p_worker_id text,p_lock_seconds integer,
 p_job_types text[],p_exclude_types boolean DEFAULT false) RETURNS SETOF ops.job
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ops AS $$
DECLARE expired record; tenant record; capacity integer;
BEGIN
 IF p_lock_seconds IS NULL OR p_exclude_types IS NULL
  OR (p_job_types IS NOT NULL AND array_position(p_job_types,NULL) IS NOT NULL)
  OR p_lock_seconds < 3 OR p_lock_seconds > 3600 OR NULLIF(btrim(p_worker_id),'') IS NULL THEN
  RAISE EXCEPTION 'invalid worker lease settings';
 END IF;
 -- Company rotation is separate from priority inside a company. Acquire
 -- workspace then scheduler locks, matching lifecycle/configuration lock order.
 FOR tenant IN SELECT w.id FROM control.tenant_job_schedule s JOIN platform.workspace w ON w.id=s.workspace_id
  WHERE w.status='active' AND w.deleted_at IS NULL AND EXISTS(
   SELECT 1 FROM ops.job j WHERE j.workspace_id=w.id AND j.status IN ('queued','failed','running')
   AND (p_job_types IS NULL OR (j.job_type=ANY(p_job_types))<>p_exclude_types)
   AND (j.locked_until IS NULL OR j.locked_until<=clock_timestamp())
   AND security.workspace_job_enabled(w.id,j.job_type))
  ORDER BY s.last_claimed_at,w.id
 LOOP
  PERFORM 1 FROM platform.workspace WHERE id=tenant.id AND status='active' AND deleted_at IS NULL FOR SHARE SKIP LOCKED;
  IF NOT FOUND THEN CONTINUE; END IF;
  SELECT concurrency_limit INTO capacity FROM control.tenant_job_schedule WHERE workspace_id=tenant.id FOR UPDATE SKIP LOCKED;
  IF NOT FOUND THEN CONTINUE; END IF;
 -- A process may stop after the result commits but before acknowledging its queue row.
 UPDATE ops.job j SET status='succeeded',completed_at=COALESCE(j.completed_at,clock_timestamp()),
   locked_by=NULL,locked_until=NULL,lease_token=NULL,updated_at=clock_timestamp()
 WHERE j.workspace_id=tenant.id AND security.workspace_is_active(j.workspace_id) AND security.workspace_job_enabled(j.workspace_id,j.job_type) AND j.status IN ('queued','failed','running')
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
  WHERE workspace_id=tenant.id AND security.workspace_is_active(workspace_id) AND security.workspace_job_enabled(workspace_id,job_type) AND status IN ('queued','failed','running') AND attempts>=max_attempts
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
 -- A fresh SQL statement after the scheduler lock observes committed claims.
 -- Count leases across all job types, including other worker processes/lanes.
 IF (SELECT count(*) FROM ops.job WHERE workspace_id=tenant.id AND status='running'
     AND locked_until>clock_timestamp())>=capacity THEN CONTINUE; END IF;
 RETURN QUERY
 UPDATE ops.job j SET status='running',attempts=j.attempts+1,locked_by=p_worker_id,
  lease_token=gen_random_uuid(),locked_until=clock_timestamp()+make_interval(secs=>p_lock_seconds),
  started_at=COALESCE(j.started_at,clock_timestamp()),updated_at=clock_timestamp()
 WHERE j.id=(SELECT candidate.id FROM ops.job candidate
  WHERE candidate.workspace_id=tenant.id AND security.workspace_is_active(candidate.workspace_id) AND security.workspace_job_enabled(candidate.workspace_id,candidate.job_type) AND candidate.status IN ('queued','failed','running') AND candidate.attempts<candidate.max_attempts
   AND (p_job_types IS NULL OR (candidate.job_type=ANY(p_job_types))<>p_exclude_types)
   AND candidate.available_at<=clock_timestamp()
   AND (candidate.locked_until IS NULL OR candidate.locked_until<=clock_timestamp())
   AND NOT EXISTS(SELECT 1 FROM ops.job_effect e WHERE e.job_id=candidate.id)
  ORDER BY candidate.priority DESC,candidate.available_at,candidate.created_at,candidate.id
  FOR UPDATE SKIP LOCKED LIMIT 1)
 RETURNING j.*;
 IF FOUND THEN
  UPDATE control.tenant_job_schedule SET last_claimed_at=clock_timestamp() WHERE workspace_id=tenant.id;
  RETURN;
 END IF;
 END LOOP;
END $$;
ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_internal_v159;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; r record;
BEGIN
 total:=security.reconcile_runtime_grants_internal_v159();
 FOR r IN SELECT rolname FROM pg_roles WHERE NOT rolsuper AND has_schema_privilege(oid,'security','USAGE')
 AND has_function_privilege(oid,'security.profile_customer_owner(uuid)','EXECUTE') LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION security.reconcile_runtime_grants_internal_v159() FROM %I',r.rolname);
 END LOOP;
 FOR r IN SELECT rolname FROM pg_roles WHERE NOT rolsuper AND has_schema_privilege(oid,'control','USAGE')
 AND has_function_privilege(oid,'control.tenants(text)','EXECUTE') LOOP
  EXECUTE format('GRANT EXECUTE ON FUNCTION control.job_settings(text,uuid),control.save_job_settings(text,uuid,integer,integer,text) TO %I',r.rolname);
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_internal_v159() FROM PUBLIC,salegent_feishu_worker;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description) VALUES('V308','Fair tenant job rotation and bounded active leases');
COMMIT;
