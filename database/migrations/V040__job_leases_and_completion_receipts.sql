BEGIN;
ALTER TABLE ops.job ADD COLUMN lease_token uuid;
COMMENT ON COLUMN ops.job.lease_token IS '每次领取生成的新令牌；所有心跳、结果和终态更新必须匹配当前令牌';
CREATE TABLE ops.job_effect (
  job_id uuid PRIMARY KEY REFERENCES ops.job(id) ON DELETE CASCADE,
  workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
  lease_token uuid NOT NULL,
  completed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
COMMENT ON TABLE ops.job_effect IS '与最终业务结果同事务写入的完成回执，防止结果提交后进程中断导致重复执行';
ALTER TABLE ops.job_effect ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops.job_effect FORCE ROW LEVEL SECURITY;
CREATE POLICY job_effect_workspace ON ops.job_effect
 USING(workspace_id=common.current_workspace_id()) WITH CHECK(workspace_id=common.current_workspace_id());
DO $$ DECLARE r record; BEGIN
 FOR r IN SELECT DISTINCT grantee FROM information_schema.role_table_grants
 WHERE table_schema='ops' AND table_name='job' AND privilege_type='UPDATE' AND grantee<>'PUBLIC'
 LOOP EXECUTE format('GRANT SELECT,INSERT ON ops.job_effect TO %I',r.grantee); END LOOP;
END $$;

CREATE OR REPLACE FUNCTION ops.claim_job(p_worker_id text,p_lock_seconds integer) RETURNS SETOF ops.job
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ops AS $$
DECLARE expired record;
BEGIN
 IF p_lock_seconds < 3 OR p_lock_seconds > 3600 OR NULLIF(btrim(p_worker_id),'') IS NULL THEN
  RAISE EXCEPTION 'invalid worker lease settings';
 END IF;
 -- A process may stop after the result commits but before acknowledging its queue row.
 UPDATE ops.job j SET status='succeeded',completed_at=COALESCE(j.completed_at,clock_timestamp()),
   locked_by=NULL,locked_until=NULL,lease_token=NULL,updated_at=clock_timestamp()
 WHERE j.status IN ('queued','failed','running')
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
    AND (locked_until IS NULL OR locked_until<=clock_timestamp()) RETURNING *
 LOOP
  IF expired.job_type='agent.run' THEN
   UPDATE agent.run SET status='failed',completed_at=clock_timestamp(),
    error_code='LEASE_ATTEMPTS_EXHAUSTED',error_detail=expired.last_error_detail
    WHERE id=expired.aggregate_id AND status IN ('queued','running');
  ELSIF expired.job_type='visit.import' THEN
   UPDATE activity.visit_import SET status='failed',error_message=expired.last_error_detail,updated_at=clock_timestamp()
    WHERE id=expired.aggregate_id AND status IN ('queued','processing');
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
   AND candidate.available_at<=clock_timestamp()
   AND (candidate.locked_until IS NULL OR candidate.locked_until<=clock_timestamp())
   AND NOT EXISTS(SELECT 1 FROM ops.job_effect e WHERE e.job_id=candidate.id)
  ORDER BY candidate.priority DESC,candidate.available_at,candidate.created_at
  FOR UPDATE SKIP LOCKED LIMIT 1)
 RETURNING j.*;
END $$;
INSERT INTO ops.schema_migration(version,description) VALUES('V040','作业租约令牌、过期回收与事务完成回执');
COMMIT;
