BEGIN;
SET LOCAL check_function_bodies=on;

-- A collaborator may request an assessment, but only the customer's confirmed
-- sales owner executes it. This projection exposes no claim/application details
-- and does not grant the collaborator permission to write insight.risk.
CREATE FUNCTION security.customer_risk_assessment_owner(p_customer uuid) RETURNS uuid
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT own.owner_user_ref_id
 FROM crm.customer_ownership own
 JOIN crm.customer c ON c.id=own.customer_id AND c.workspace_id=own.workspace_id
 WHERE c.id=p_customer AND c.workspace_id=common.current_workspace_id()
  AND c.deleted_at IS NULL AND own.state='claimed'
  AND security.has_customer_access(c.id);
$$;

CREATE TABLE insight.customer_risk_assessment (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 customer_id uuid NOT NULL,
 actor_user_ref_id uuid NOT NULL REFERENCES platform.user_ref(id),
 actor_role_code text NOT NULL CHECK(actor_role_code IN ('sales','supervisor','manager')),
 initiated_by_user_ref_id uuid NOT NULL REFERENCES platform.user_ref(id),
 job_id uuid UNIQUE REFERENCES ops.job(id),
 trigger_type text NOT NULL CHECK(length(btrim(trigger_type)) BETWEEN 1 AND 100),
 trigger_id text NOT NULL CHECK(length(btrim(trigger_id)) BETWEEN 1 AND 200),
 idempotency_key text NOT NULL CHECK(length(btrim(idempotency_key)) BETWEEN 1 AND 300),
 identity_snapshot jsonb NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(identity_snapshot)='object'),
 fact_scope_version smallint NOT NULL DEFAULT security.current_fact_scope_version()
  CHECK(fact_scope_version>0),
 facts_fingerprint text,
 configuration_fingerprint text,
 coverage jsonb NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(coverage)='object'),
 status text NOT NULL DEFAULT 'queued'
  CHECK(status IN ('queued','running','succeeded','failed','superseded')),
 outcome text NOT NULL DEFAULT 'unknown'
  CHECK(outcome IN ('unknown','no_risk_identified','risk_found','insufficient_evidence')),
 risk_count integer NOT NULL DEFAULT 0 CHECK(risk_count>=0),
 result jsonb NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(result)='object'),
 error_code text,
 -- Inference accounting is independently committed and may report an audit gap.
 -- Keep its correlation UUID without requiring the best-effort audit row to exist.
 inference_operation_id uuid,
 data_as_of timestamptz,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 started_at timestamptz,
 completed_at timestamptz,
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(workspace_id,idempotency_key),
 FOREIGN KEY(customer_id,workspace_id) REFERENCES crm.customer(id,workspace_id),
 CHECK(outcome<>'no_risk_identified' OR risk_count=0),
 CHECK(outcome<>'risk_found' OR risk_count>0),
 CHECK((identity_snapshot->>'workspace_id'=workspace_id::text
   AND identity_snapshot->>'user_id'=actor_user_ref_id::text
   AND identity_snapshot->>'role'=actor_role_code) IS TRUE)
);
CREATE INDEX customer_risk_assessment_latest ON insight.customer_risk_assessment
 (workspace_id,customer_id,created_at DESC,id DESC);
CREATE INDEX customer_risk_assessment_success ON insight.customer_risk_assessment
 (workspace_id,customer_id,actor_user_ref_id,completed_at DESC,id DESC) WHERE status='succeeded';
CREATE INDEX customer_risk_assessment_operation ON insight.customer_risk_assessment
 (workspace_id,inference_operation_id) WHERE inference_operation_id IS NOT NULL;
COMMENT ON TABLE insight.customer_risk_assessment IS
 '客户风险评估覆盖回执；当前负责人执行、实际触发者留痕；仅证据ID/版本/摘要，不保存事实正文，成功不等于确认风险或绝对无风险';
COMMENT ON COLUMN insight.customer_risk_assessment.coverage IS
 '限定事实合同的数量、截断标记、记录ID与版本；健康读取须重新验证当前权限、事实及配置指纹';
COMMENT ON COLUMN insight.customer_risk_assessment.fact_scope_version IS
 '沿用security.current_fact_scope_version的smallint权限合同版本';

ALTER TABLE insight.customer_risk_assessment ENABLE ROW LEVEL SECURITY;
ALTER TABLE insight.customer_risk_assessment FORCE ROW LEVEL SECURITY;
CREATE POLICY customer_risk_assessment_read ON insight.customer_risk_assessment FOR SELECT
 USING(workspace_id=common.current_workspace_id() AND security.has_customer_access(customer_id));
CREATE POLICY customer_risk_assessment_request ON insight.customer_risk_assessment FOR INSERT
 WITH CHECK(workspace_id=common.current_workspace_id()
  AND initiated_by_user_ref_id=common.current_user_ref_id()
  AND security.has_customer_access(customer_id)
  AND actor_user_ref_id=security.customer_risk_assessment_owner(customer_id)
  AND status='queued' AND outcome='unknown' AND risk_count=0
  AND EXISTS(SELECT 1 FROM platform.user_ref u JOIN platform.role_binding rb
   ON rb.user_ref_id=u.id AND rb.workspace_id=u.workspace_id
   WHERE u.id=actor_user_ref_id AND u.workspace_id=customer_risk_assessment.workspace_id
    AND u.status='active' AND u.deleted_at IS NULL AND rb.role_code=actor_role_code
    AND clock_timestamp()>=rb.valid_from AND clock_timestamp()<rb.valid_to));
CREATE POLICY customer_risk_assessment_execute ON insight.customer_risk_assessment FOR UPDATE
 USING(workspace_id=common.current_workspace_id()
  AND actor_user_ref_id=common.current_user_ref_id() AND actor_role_code=common.current_role_code()
  AND actor_user_ref_id=security.customer_risk_assessment_owner(customer_id)
  AND security.has_active_role(actor_role_code) AND security.has_customer_access(customer_id))
 WITH CHECK(workspace_id=common.current_workspace_id()
  AND actor_user_ref_id=common.current_user_ref_id() AND actor_role_code=common.current_role_code()
  AND actor_user_ref_id=security.customer_risk_assessment_owner(customer_id)
  AND security.has_active_role(actor_role_code) AND security.has_customer_access(customer_id));

-- The initiating actor and queued owner's identity are immutable. A job may be
-- linked initially or by its owner later, but cannot point at another receipt.
CREATE FUNCTION insight.guard_customer_risk_assessment() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF TG_RELID<>'insight.customer_risk_assessment'::regclass OR TG_OP NOT IN ('INSERT','UPDATE') THEN
  RAISE EXCEPTION 'invalid risk assessment trigger target' USING ERRCODE='42501';
 END IF;
 IF TG_OP='UPDATE' AND ROW(NEW.id,NEW.workspace_id,NEW.customer_id,NEW.actor_user_ref_id,
   NEW.actor_role_code,NEW.initiated_by_user_ref_id,NEW.trigger_type,NEW.trigger_id,
   NEW.idempotency_key,NEW.identity_snapshot,NEW.fact_scope_version)
  IS DISTINCT FROM ROW(OLD.id,OLD.workspace_id,OLD.customer_id,OLD.actor_user_ref_id,
   OLD.actor_role_code,OLD.initiated_by_user_ref_id,OLD.trigger_type,OLD.trigger_id,
   OLD.idempotency_key,OLD.identity_snapshot,OLD.fact_scope_version) THEN
  RAISE EXCEPTION 'risk assessment provenance is immutable' USING ERRCODE='23514';
 END IF;
 IF TG_OP='UPDATE' AND OLD.job_id IS NOT NULL AND NEW.job_id IS DISTINCT FROM OLD.job_id THEN
  RAISE EXCEPTION 'risk assessment job is immutable' USING ERRCODE='23514';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM platform.user_ref u WHERE u.id=NEW.actor_user_ref_id
   AND u.workspace_id=NEW.workspace_id)
 OR NOT EXISTS(SELECT 1 FROM platform.user_ref u WHERE u.id=NEW.initiated_by_user_ref_id
   AND u.workspace_id=NEW.workspace_id) THEN
  RAISE EXCEPTION 'risk assessment actor workspace mismatch' USING ERRCODE='23514';
 END IF;
 IF NEW.job_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM ops.job j
  WHERE j.id=NEW.job_id AND j.workspace_id=NEW.workspace_id
   AND j.job_type='customer.risk.review' AND j.aggregate_type='customer_risk_assessment'
   AND j.aggregate_id=NEW.id) THEN
  RAISE EXCEPTION 'risk assessment job mismatch' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION insight.guard_customer_risk_assessment() FROM PUBLIC;
CREATE TRIGGER customer_risk_assessment_guard BEFORE INSERT OR UPDATE ON insight.customer_risk_assessment
 FOR EACH ROW EXECUTE FUNCTION insight.guard_customer_risk_assessment();
CREATE TRIGGER customer_risk_assessment_touch BEFORE UPDATE ON insight.customer_risk_assessment
 FOR EACH ROW EXECUTE FUNCTION common.touch_runtime_updated_at();

-- One queue-derived failure projection covers ordinary failures, malformed actor
-- rejection and leases exhausted during reclaim. It never trusts payload IDs or
-- adopts a request actor, and never turns queue success into a healthy result.
CREATE FUNCTION ops.sync_customer_risk_assessment_job() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF TG_RELID<>'ops.job'::regclass OR TG_OP<>'UPDATE' THEN
  RAISE EXCEPTION 'invalid risk assessment queue trigger target' USING ERRCODE='42501';
 END IF;
 IF NEW.job_type<>'customer.risk.review' OR NEW.aggregate_type<>'customer_risk_assessment'
  OR NEW.aggregate_id IS NULL OR NEW.status NOT IN ('failed','dead_letter','cancelled') THEN
  RETURN NULL;
 END IF;
 UPDATE insight.customer_risk_assessment a
 SET status=CASE WHEN NEW.status='failed' AND NEW.attempts<NEW.max_attempts THEN 'queued' ELSE 'failed' END,
  error_code=COALESCE(NEW.last_error_code,CASE WHEN NEW.status='cancelled' THEN 'JOB_CANCELLED' ELSE 'JOB_FAILED' END),
  completed_at=CASE WHEN NEW.status='failed' AND NEW.attempts<NEW.max_attempts THEN NULL ELSE clock_timestamp() END,
  updated_at=clock_timestamp()
 WHERE a.id=NEW.aggregate_id AND a.workspace_id=NEW.workspace_id AND a.job_id=NEW.id
  AND a.status IN ('queued','running')
  AND NOT EXISTS(SELECT 1 FROM ops.job_effect e WHERE e.job_id=NEW.id AND e.workspace_id=NEW.workspace_id);
 RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION ops.sync_customer_risk_assessment_job() FROM PUBLIC;
CREATE TRIGGER customer_risk_assessment_job_terminal AFTER UPDATE OF status ON ops.job
 FOR EACH ROW WHEN(OLD.status IS DISTINCT FROM NEW.status)
 EXECUTE FUNCTION ops.sync_customer_risk_assessment_job();

-- Enrich the existing management-only, content-free audit projection. Preserve
-- its implementation and ACL rather than copying the large provider query.
ALTER FUNCTION security.agent_operation_rows(timestamptz,timestamptz) RENAME TO agent_operation_rows_v066;
CREATE FUNCTION security.agent_operation_rows(p_start timestamptz,p_end timestamptz)
 RETURNS TABLE(operation_id uuid,case_id uuid,started_at timestamptz,record jsonb)
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT previous.operation_id,COALESCE(a.id,previous.case_id),previous.started_at,
  previous.record || CASE WHEN a.id IS NULL THEN '{}'::jsonb ELSE jsonb_build_object(
   'case_id',a.id,'assessment_id',a.id,'customer_id',a.customer_id,'customer_name',c.name,
   'business_status',a.status,'business_completed_at',a.completed_at,
   'risk_assessment',jsonb_build_object('id',a.id,'status',a.status,'outcome',a.outcome,
    'risk_count',a.risk_count,'initiated_by',a.initiated_by_user_ref_id,
    'fact_scope_version',a.fact_scope_version,'data_as_of',a.data_as_of,'error_code',a.error_code)) END
 FROM security.agent_operation_rows_v066(p_start,p_end) previous
 LEFT JOIN LATERAL (
  SELECT receipt.* FROM insight.customer_risk_assessment receipt
  WHERE receipt.workspace_id=common.current_workspace_id()
   AND (receipt.inference_operation_id=previous.operation_id
    OR receipt.job_id::text=previous.record->>'job_id')
  ORDER BY receipt.created_at DESC,receipt.id DESC LIMIT 1
 ) a ON true
 LEFT JOIN crm.customer c ON c.id=a.customer_id AND c.workspace_id=a.workspace_id
 WHERE security.management_actor();
$$;

-- Preserve runtime ACLs on upgrades and roles introduced after initial install.
ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_v091;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
 LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE permission record; reconciled integer;
BEGIN
 reconciled:=security.reconcile_runtime_grants_v091();
 FOR permission IN SELECT DISTINCT r.rolname,a.privilege_type
  FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  CROSS JOIN LATERAL aclexplode(c.relacl) a JOIN pg_roles r ON r.oid=a.grantee
  WHERE n.nspname='ops' AND c.relname='job' AND a.grantee<>c.relowner
   AND a.privilege_type IN ('SELECT','INSERT','UPDATE')
 LOOP
  EXECUTE format('GRANT USAGE ON SCHEMA insight TO %I',permission.rolname);
  EXECUTE format('GRANT %s ON insight.customer_risk_assessment TO %I',permission.privilege_type,permission.rolname);
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.customer_risk_assessment_owner(uuid) TO %I',permission.rolname);
  reconciled:=reconciled+1;
 END LOOP;
 RETURN reconciled;
END $$;

-- New definer/wrapper functions inherit their predecessor's exact ACL and owner,
-- including customer installations with restrictive default function grants.
DO $$ DECLARE pair record; permission record; function_owner name; BEGIN
 FOR pair IN SELECT * FROM (VALUES
  ('security.customer_risk_assessment_owner(uuid)','security.profile_customer_owner(uuid)'),
  ('security.agent_operation_rows(timestamptz,timestamptz)','security.agent_operation_rows_v066(timestamptz,timestamptz)'),
  ('security.reconcile_runtime_grants()','security.reconcile_runtime_grants_v091()')
 ) signatures(target,source) LOOP
  SELECT r.rolname INTO function_owner FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
   WHERE p.oid=pair.source::regprocedure;
  FOR permission IN SELECT DISTINCT a.grantee,r.rolname FROM pg_proc p
   CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
   LEFT JOIN pg_roles r ON r.oid=a.grantee WHERE p.oid=pair.target::regprocedure
  LOOP EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %s CASCADE',pair.target,
   CASE WHEN permission.grantee=0 THEN 'PUBLIC' ELSE quote_ident(permission.rolname) END); END LOOP;
  EXECUTE format('ALTER FUNCTION %s OWNER TO %I',pair.target,function_owner);
  FOR permission IN SELECT a.grantee,a.is_grantable,r.rolname FROM pg_proc p
   CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
   LEFT JOIN pg_roles r ON r.oid=a.grantee
   WHERE p.oid=pair.source::regprocedure AND a.privilege_type='EXECUTE'
  LOOP EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO %s%s',pair.target,
   CASE WHEN permission.grantee=0 THEN 'PUBLIC' ELSE quote_ident(permission.rolname) END,
   CASE WHEN permission.is_grantable THEN ' WITH GRANT OPTION' ELSE '' END); END LOOP;
 END LOOP;
END $$;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description)
 VALUES('V098','客户负责人风险评估覆盖回执、协作触发边界与任务失败闭环');
COMMIT;
