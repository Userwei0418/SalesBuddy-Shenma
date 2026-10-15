-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V171__platform_runtime_overview.sql.
-- Original SHA-256: d7fcc7fa3aa7ce5f689164eb2db834e9303bc59446e02004ee304dfb0fbe8f22. See shenma_whole_121_manifest.json.
BEGIN;
-- Bounded, metadata-only operational reads. No payloads, object identifiers,
-- provider responses, filenames, URLs, credentials or business records escape.
CREATE INDEX job_runtime_overview ON ops.job(workspace_id,status,available_at)
 WHERE status IN ('queued','failed','running','dead_letter');
CREATE INDEX feishu_runtime_overview ON ops.feishu_event(workspace_id,status,available_at)
 WHERE status IN ('pending','failed','running','dead_letter');
CREATE INDEX feishu_unknown_overview ON ops.feishu_delivery(workspace_id) WHERE status='unknown';
CREATE INDEX call_pending_overview ON control.call_attempt(workspace_id,state,created_at)
 WHERE state IN ('reserved','dispatched','unknown');
CREATE FUNCTION control.runtime_overview(p_session text,p_after uuid,p_wait_minutes integer) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE rows jsonb; sampled timestamptz:=statement_timestamp(); cutoff timestamptz;
BEGIN
 PERFORM control.require_actor(p_session);
 IF p_wait_minutes IS NULL OR p_wait_minutes NOT BETWEEN 1 AND 1440 THEN
  RAISE EXCEPTION 'INVALID_RUNTIME_WINDOW' USING ERRCODE='P7422'; END IF;
 cutoff:=sampled-make_interval(mins=>p_wait_minutes);
 WITH selected AS MATERIALIZED (
  SELECT w.id,w.name,w.external_workspace_id AS company_code,w.status
  FROM platform.workspace w WHERE w.deleted_at IS NULL AND (p_after IS NULL OR w.id>p_after)
  ORDER BY w.id LIMIT 21
 ), page AS (SELECT * FROM selected ORDER BY id LIMIT 20)
 SELECT jsonb_build_object('sampled_at',sampled,'wait_minutes',p_wait_minutes,
  'next_cursor',CASE WHEN (SELECT count(*) FROM selected)>20 THEN
   (SELECT id FROM page ORDER BY id DESC LIMIT 1) END,
  'items',COALESCE(jsonb_agg(jsonb_build_object(
   'workspace_id',w.id,'name',w.name,'company_code',w.company_code,'status',w.status,
   'jobs',j.counts,'sync',s.counts,'originals',COALESCE(o.counts,jsonb_build_object(
    'available',false,'limit_bytes',NULL,'stored_bytes',NULL,'reserved_bytes',NULL,
    'over_limit',false,'pending',NULL,'overdue',NULL)),'calls',c.counts
  ) ORDER BY w.id),'[]')) INTO rows
 FROM page w
 CROSS JOIN LATERAL (
  SELECT jsonb_build_object(
   'running',count(*) FILTER(WHERE status='running' AND locked_until>sampled),
   'expired_leases',count(*) FILTER(WHERE status='running' AND (locked_until IS NULL OR locked_until<=sampled)),
   'waiting',count(*) FILTER(WHERE status IN ('queued','failed')),
   'overdue',count(*) FILTER(WHERE status IN ('queued','failed') AND available_at<cutoff
     AND w.status='active' AND security.workspace_job_enabled(w.id,job_type)),
   'dead_letter',count(*) FILTER(WHERE status='dead_letter')) AS counts
  FROM ops.job WHERE workspace_id=w.id AND status IN ('queued','failed','running','dead_letter')
 ) j
 CROSS JOIN LATERAL (
  SELECT jsonb_build_object(
   'enabled',COALESCE(bool_or(f.enabled AND security.workspace_module_enabled(w.id,'feishu')),false),
   'waiting',count(e.id) FILTER(WHERE e.status IN ('pending','failed')),
   'overdue',count(e.id) FILTER(WHERE e.status IN ('pending','failed') AND e.available_at<cutoff
     AND w.status='active' AND f.enabled AND security.workspace_module_enabled(w.id,'feishu')),
   'expired_leases',count(e.id) FILTER(WHERE e.status='running' AND (e.locked_until IS NULL OR e.locked_until<=sampled)),
   'dead_letter',count(e.id) FILTER(WHERE e.status='dead_letter'),
   'unknown_deliveries',(SELECT count(*) FROM ops.feishu_delivery WHERE workspace_id=w.id AND status='unknown')) AS counts
  FROM config.feishu_connection f LEFT JOIN ops.feishu_event e
   ON e.workspace_id=f.workspace_id AND e.connection_id=f.id AND e.status IN ('pending','failed','running','dead_letter')
  WHERE f.workspace_id=w.id
 ) s
 LEFT JOIN LATERAL (
  SELECT jsonb_build_object('available',true,'limit_bytes',q.limit_bytes,'stored_bytes',q.stored_bytes,'reserved_bytes',q.reserved_bytes,
   'over_limit',q.limit_bytes IS NOT NULL AND q.stored_bytes+q.reserved_bytes>q.limit_bytes,
   'pending',count(a.import_id),'overdue',count(a.import_id) FILTER(WHERE a.created_at<cutoff)) AS counts
  FROM control.tenant_storage_quota q LEFT JOIN control.storage_allocation a ON a.workspace_id=q.workspace_id
   AND a.state IN ('reserved','external_pending','discarding')
  WHERE q.workspace_id=w.id GROUP BY q.workspace_id
 ) o ON true
 CROSS JOIN LATERAL (
  SELECT jsonb_build_object('pending',count(*) FILTER(WHERE state IN ('reserved','dispatched')),
   'overdue',count(*) FILTER(WHERE state IN ('reserved','dispatched') AND created_at<cutoff),
   'unknown',count(*) FILTER(WHERE state='unknown'),
   'failed_last_24h',(SELECT count(*) FROM agent.model_invocation WHERE workspace_id=w.id
    AND started_at>=sampled-interval '24 hours' AND started_at<=sampled
    AND record_kind='provider_attempt' AND status='failed')) AS counts
  FROM control.call_attempt WHERE workspace_id=w.id AND state IN ('reserved','dispatched','unknown')
 ) c;
 -- Recheck expiration after queries. require_actor locks enabled/session rows;
 -- revocation cannot race a successful snapshot under that lock.
 PERFORM control.require_actor(p_session);
 RETURN rows;
END $$;
REVOKE ALL ON FUNCTION control.runtime_overview(text,uuid,integer) FROM PUBLIC,salegent_feishu_worker;
ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_before_runtime_overview;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; r record;
BEGIN
 total:=security.reconcile_runtime_grants_before_runtime_overview();
 FOR r IN SELECT rolname FROM pg_roles WHERE NOT rolsuper AND has_schema_privilege(oid,'control','USAGE')
 AND has_function_privilege(oid,'control.tenants(text)','EXECUTE') LOOP
  EXECUTE format('GRANT EXECUTE ON FUNCTION control.runtime_overview(text,uuid,integer) TO %I',r.rolname);
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_before_runtime_overview()
 FROM PUBLIC,salegent_feishu_worker;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description) VALUES('V319','Bounded platform runtime counters without business content');
COMMIT;
