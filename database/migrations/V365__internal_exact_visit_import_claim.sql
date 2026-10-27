-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V217__exact_visit_import_claim.sql.
-- Original SHA-256: 918b311f3c2d80963fd6966e9f740ba37b27877ccd8ea94ff664340c67cf47a3. See shenma_whole_121_manifest.json.
BEGIN;

-- This is an exact first-attempt claim, never an alternate queue sweeper.
-- Existing broad claim/recovery functions and their ACLs are untouched.
CREATE FUNCTION ops.claim_exact_visit_import(
 p_worker_id text,p_lock_seconds integer,p_job_id uuid,p_workspace_id uuid,
 p_import_id uuid,p_user_id uuid,p_content_sha256 text)
RETURNS SETOF ops.job
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE capacity integer; target record;
BEGIN
 IF p_lock_seconds IS NULL OR p_lock_seconds NOT BETWEEN 3 AND 3600
  OR COALESCE(length(btrim(p_worker_id)),0) NOT BETWEEN 1 AND 200
  OR p_job_id IS NULL OR p_workspace_id IS NULL OR p_import_id IS NULL OR p_user_id IS NULL
  OR '00000000-0000-0000-0000-000000000000'::uuid IN (p_job_id,p_workspace_id,p_import_id,p_user_id)
  OR p_content_sha256 IS NULL OR p_content_sha256 !~ '^[0-9a-f]{64}$' THEN
  RAISE EXCEPTION 'INVALID_EXACT_IMPORT_CLAIM' USING ERRCODE='22023';
 END IF;

 -- Same lifecycle/configuration lock order as V160. SKIP LOCKED returns no
 -- claim instead of waiting on an unrelated administrative transaction.
 PERFORM 1 FROM platform.workspace WHERE id=p_workspace_id AND status='active'
  AND deleted_at IS NULL AND security.workspace_is_active(id)
  AND security.workspace_job_enabled(id,'visit.import') FOR SHARE SKIP LOCKED;
 IF NOT FOUND THEN RETURN; END IF;
 SELECT concurrency_limit INTO capacity FROM control.tenant_job_schedule
  WHERE workspace_id=p_workspace_id FOR UPDATE SKIP LOCKED;
 IF NOT FOUND THEN RETURN; END IF;
 -- A new statement after the scheduler lock observes committed claims from
 -- every lane. Counting leases is read-only; no other job is recovered/updated.
 IF (SELECT count(*) FROM ops.job WHERE workspace_id=p_workspace_id
     AND status='running' AND locked_until>clock_timestamp())>=capacity THEN RETURN; END IF;

 SELECT j.* INTO target FROM ops.job j
  WHERE j.id=p_job_id AND j.workspace_id=p_workspace_id
   AND j.job_type='visit.import' AND j.aggregate_type='visit_import' AND j.aggregate_id=p_import_id
   AND jsonb_typeof(j.payload)='object'
   AND j.payload->>'workspace_id'=p_workspace_id::text AND j.payload->>'user_id'=p_user_id::text
   AND j.status='queued' AND j.attempts=0 AND j.max_attempts>0
   AND j.available_at<=clock_timestamp()
   AND j.locked_by IS NULL AND j.locked_until IS NULL AND j.lease_token IS NULL
   AND j.started_at IS NULL AND j.completed_at IS NULL
   AND j.last_error_code IS NULL AND j.last_error_detail IS NULL
   AND NOT EXISTS(SELECT 1 FROM ops.job_effect e WHERE e.job_id=j.id)
  FOR UPDATE SKIP LOCKED;
 IF NOT FOUND THEN RETURN; END IF;

 -- Hold the exact source while assigning its lease. A competing retry/upload
 -- transaction may lock import before job, so never wait for this final lock.
 PERFORM 1 FROM activity.visit_import i
  WHERE i.id=p_import_id AND i.workspace_id=p_workspace_id
   AND i.created_by_user_ref_id=p_user_id AND i.content_sha256=p_content_sha256
   AND i.status='queued' FOR SHARE SKIP LOCKED;
 IF NOT FOUND THEN RETURN; END IF;
 IF NOT security.workspace_is_active(p_workspace_id)
  OR NOT security.workspace_job_enabled(p_workspace_id,'visit.import') THEN RETURN; END IF;

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

-- Clear every initial/default EXECUTE grant, including grant options. Only a
-- direct restricted worker grantee of the existing typed claim is eligible.
-- Import INSERT distinguishes the API from worker; platform/Feishu and broad
-- inherited roles never acquire this capability. No role is created or widened.
DO $$ DECLARE permission record; function_owner name;
 signature text:='ops.claim_exact_visit_import(text,integer,uuid,uuid,uuid,uuid,text)';
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
 FOR permission IN
  SELECT DISTINCT r.rolname FROM pg_proc p
  CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
  JOIN pg_roles r ON r.oid=a.grantee
  WHERE p.oid='ops.claim_job(text,integer,text[],boolean)'::regprocedure
   AND a.privilege_type='EXECUTE' AND a.grantee<>p.proowner
   AND r.rolcanlogin AND NOT r.rolsuper AND NOT r.rolbypassrls
   AND NOT r.rolcreaterole AND NOT r.rolcreatedb AND NOT r.rolreplication
   AND NOT EXISTS(SELECT 1 FROM pg_auth_members m WHERE m.member=r.oid)
   AND NOT pg_has_role(r.oid,'salegent_feishu_worker','member')
   AND has_schema_privilege(r.oid,'ops','USAGE')
   AND has_table_privilege(r.oid,'ops.job','UPDATE')
   AND has_table_privilege(r.oid,'activity.visit_import','UPDATE')
   AND NOT has_any_column_privilege(r.oid,'activity.visit_import','INSERT')
   AND NOT has_function_privilege(r.oid,'control.tenants(text)','EXECUTE')
 LOOP
  EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO %I',signature,permission.rolname);
 END LOOP;
END $$;
COMMENT ON FUNCTION ops.claim_exact_visit_import(text,integer,uuid,uuid,uuid,uuid,text) IS
 '仅领取指定新上传访谈任务的首次租约；校验租户、上传人及内容SHA，不扫描维护其它任务';
INSERT INTO ops.schema_migration(version,description)
 VALUES('V365','Exact first-attempt visit import claim for restricted workers');
COMMIT;
