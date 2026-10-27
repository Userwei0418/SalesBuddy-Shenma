BEGIN;
-- Tenant-specific terminology and owner-specific ASR receipts. No audio/credentials in this migration.
INSERT INTO config.permission_catalog(code,label,module,scopes,sensitive) VALUES
 ('asr_hotword.read','查看公司语音热词','语音热词',ARRAY['workspace'],false),
 ('asr_hotword.manage','维护公司语音热词','语音热词',ARRAY['workspace'],true);
INSERT INTO control.permission_module VALUES ('asr_hotword.read','core'),('asr_hotword.manage','core');
UPDATE config.permission_role_default SET permissions=permissions||
 '[{"permission":"asr_hotword.read","scope":"workspace","team_ids":[]},{"permission":"asr_hotword.manage","scope":"workspace","team_ids":[]}]'::jsonb
 WHERE role_code IN ('operations','administrator');
-- Backfill only the two designated company roles. Account overrides (including deny) remain untouched.
INSERT INTO config.permission_role_grant(workspace_id,role_id,permission_code,scope_code)
 SELECT r.workspace_id,r.id,p,'workspace' FROM config.permission_role r
 CROSS JOIN unnest(ARRAY['asr_hotword.read','asr_hotword.manage']) p
 WHERE r.builtin_role_code IN ('operations','administrator') ON CONFLICT DO NOTHING;
UPDATE config.permission_role SET version_no=version_no+1,updated_at=clock_timestamp()
 WHERE builtin_role_code IN ('operations','administrator');
UPDATE config.authorization_scope_revision SET revision=revision+1;

CREATE TABLE config.asr_hotword_version (
 workspace_id uuid NOT NULL REFERENCES platform.workspace(id), version integer NOT NULL CHECK(version>0),
 items jsonb NOT NULL CHECK(jsonb_typeof(items)='array'), created_by uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), reason text NOT NULL DEFAULT '', source_version integer,
 PRIMARY KEY(workspace_id,version),
 FOREIGN KEY(created_by,workspace_id) REFERENCES platform.user_ref(id,workspace_id),
 FOREIGN KEY(workspace_id,source_version) REFERENCES config.asr_hotword_version(workspace_id,version)
);
CREATE TABLE config.asr_hotword_current (
 workspace_id uuid PRIMARY KEY REFERENCES platform.workspace(id), version integer NOT NULL,
 FOREIGN KEY(workspace_id,version) REFERENCES config.asr_hotword_version(workspace_id,version)
);
CREATE TABLE config.asr_hotword_operation (
 workspace_id uuid NOT NULL, actor_id uuid NOT NULL, operation_id uuid NOT NULL,
 request_digest text NOT NULL, version integer NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(workspace_id,actor_id,operation_id),
 FOREIGN KEY(actor_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id),
 FOREIGN KEY(workspace_id,version) REFERENCES config.asr_hotword_version(workspace_id,version)
);
CREATE TRIGGER asr_version_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON config.asr_hotword_version
 FOR EACH STATEMENT EXECUTE FUNCTION ops.deny_audit_mutation();
CREATE TRIGGER asr_operation_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON config.asr_hotword_operation
 FOR EACH STATEMENT EXECUTE FUNCTION ops.deny_audit_mutation();

-- Duration quotas are opt-in until the company's real allowance is configured.
CREATE TABLE config.asr_budget_policy (
 workspace_id uuid PRIMARY KEY REFERENCES platform.workspace(id),
 daily_seconds numeric CHECK(daily_seconds>0), monthly_seconds numeric CHECK(monthly_seconds>0),
 user_daily_seconds numeric CHECK(user_daily_seconds>0),
 company_concurrency integer NOT NULL DEFAULT 2 CHECK(company_concurrency BETWEEN 1 AND 4),
 user_concurrency integer NOT NULL DEFAULT 2 CHECK(user_concurrency BETWEEN 1 AND 2),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
ALTER TABLE config.asr_budget_policy ENABLE ROW LEVEL SECURITY;
ALTER TABLE config.asr_budget_policy FORCE ROW LEVEL SECURITY;
CREATE POLICY asr_budget_admin ON config.asr_budget_policy
 USING(workspace_id=common.current_workspace_id() AND security.workspace_is_active(workspace_id)
 AND security.authorization_has('access.console') AND security.authorization_has('ai.usage_rules_manage'))
 WITH CHECK(workspace_id=common.current_workspace_id() AND security.workspace_is_active(workspace_id)
 AND security.authorization_has('access.console') AND security.authorization_has('ai.usage_rules_manage'));

CREATE TABLE activity.asr_session (
 workspace_id uuid NOT NULL, actor_id uuid NOT NULL, id uuid NOT NULL,
 scene text NOT NULL CHECK(scene IN ('customer_create','visit_entry','management_task','chatbi','demo_scene','visit_import','legacy','diagnostic')),
 language text NOT NULL DEFAULT 'zh', configuration text NOT NULL, snapshot jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(workspace_id,actor_id,id),
 FOREIGN KEY(actor_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id)
);
CREATE TABLE activity.asr_attempt (
 workspace_id uuid NOT NULL, actor_id uuid NOT NULL, operation_id uuid NOT NULL, session_id uuid NOT NULL,
 unit_key text NOT NULL, stage text NOT NULL CHECK(stage IN ('preview','final','diagnostic')),
 request_digest text NOT NULL, reuse_digest text NOT NULL, provider_key text NOT NULL,
 state text NOT NULL CHECK(state IN ('sending','completed','failed','unknown','linked')),
 audio_seconds numeric NOT NULL CHECK(audio_seconds>=0 AND audio_seconds<=3600),
 linked_operation_id uuid, capacity_released_at timestamptz, response jsonb, error_code text, created_at timestamptz NOT NULL DEFAULT clock_timestamp(), finished_at timestamptz,
 PRIMARY KEY(workspace_id,actor_id,operation_id), UNIQUE(workspace_id,actor_id,session_id,stage,unit_key),
 FOREIGN KEY(workspace_id,actor_id,session_id) REFERENCES activity.asr_session(workspace_id,actor_id,id),
 FOREIGN KEY(workspace_id,actor_id,linked_operation_id) REFERENCES activity.asr_attempt(workspace_id,actor_id,operation_id)
);
CREATE INDEX asr_attempt_usage ON activity.asr_attempt(provider_key,created_at);
CREATE INDEX asr_attempt_reuse ON activity.asr_attempt(workspace_id,actor_id,reuse_digest) WHERE state='completed';
ALTER TABLE activity.visit_import ADD COLUMN source_kind text NOT NULL DEFAULT 'legacy'
 CHECK(source_kind IN ('legacy','recording','file'));
ALTER TABLE activity.visit_import ADD COLUMN asr_session_id uuid;
-- Nullable reference is populated only after the server creates/authorizes the session.
ALTER TABLE activity.visit_import ADD CONSTRAINT visit_import_asr_session_fk
 FOREIGN KEY(workspace_id,created_by_user_ref_id,asr_session_id)
 REFERENCES activity.asr_session(workspace_id,actor_id,id);

DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['asr_hotword_version','asr_hotword_current','asr_hotword_operation'] LOOP
  EXECUTE format('ALTER TABLE config.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE config.%I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY tenant_guard ON config.%I AS RESTRICTIVE FOR ALL USING(workspace_id=common.current_workspace_id() AND security.workspace_is_active(workspace_id)) WITH CHECK(workspace_id=common.current_workspace_id() AND security.workspace_is_active(workspace_id))',t);
  EXECUTE format('CREATE POLICY term_read ON config.%I FOR SELECT USING(security.authorization_has(''access.console'') AND security.authorization_has(''asr_hotword.read''))',t);
  EXECUTE format('CREATE POLICY term_insert ON config.%I FOR INSERT WITH CHECK(security.authorization_has(''access.console'') AND security.authorization_has(''asr_hotword.read'') AND security.authorization_has(''asr_hotword.manage''))',t);
 END LOOP;
END $$;
CREATE POLICY current_update ON config.asr_hotword_current FOR UPDATE
 USING(security.authorization_has('access.console') AND security.authorization_has('asr_hotword.read') AND security.authorization_has('asr_hotword.manage'))
 WITH CHECK(security.authorization_has('access.console') AND security.authorization_has('asr_hotword.read') AND security.authorization_has('asr_hotword.manage'));
CREATE POLICY operation_owner ON config.asr_hotword_operation AS RESTRICTIVE FOR ALL
 USING(actor_id=common.current_user_ref_id()) WITH CHECK(actor_id=common.current_user_ref_id());
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['asr_session','asr_attempt'] LOOP
  EXECUTE format('ALTER TABLE activity.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE activity.%I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY asr_owner ON activity.%I FOR ALL USING(workspace_id=common.current_workspace_id() AND actor_id=common.current_user_ref_id() AND security.workspace_is_active(workspace_id) AND (security.authorization_has(''visit.transcribe'') OR security.authorization_has(''ai.config_test''))) WITH CHECK(workspace_id=common.current_workspace_id() AND actor_id=common.current_user_ref_id() AND security.workspace_is_active(workspace_id) AND (security.authorization_has(''visit.transcribe'') OR security.authorization_has(''ai.config_test'')))',t);
 END LOOP;
END $$;

-- This internal projection is the only business ASR word source; no arbitrary workspace argument.
CREATE FUNCTION security.asr_hotword_snapshot() RETURNS jsonb
 LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result jsonb;
BEGIN
 IF common.current_workspace_id() IS NULL OR common.current_user_ref_id() IS NULL
 OR NOT security.workspace_is_active(common.current_workspace_id())
 OR NOT (security.authorization_has('visit.transcribe') OR security.authorization_has('ai.config_test')) THEN
  RAISE EXCEPTION 'ASR_SCOPE_DENIED' USING ERRCODE='42501'; END IF;
 SELECT jsonb_build_object('version',v.version,'items',v.items) INTO result
 FROM config.asr_hotword_current c JOIN config.asr_hotword_version v USING(workspace_id,version)
 WHERE c.workspace_id=common.current_workspace_id();
 RETURN COALESCE(result,'{"version":0,"items":[]}'::jsonb);
END $$;

-- Atomic shared capacity and durable audio-time reservation. The existing model-call
-- quota remains an additional upper bound. Unknown calls retain consumed time.
CREATE FUNCTION security.reserve_asr_attempt(p_operation uuid,p_session uuid,p_stage text,p_unit text,
 p_digest text,p_reuse text,p_provider text,p_seconds numeric) RETURNS jsonb
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE s activity.asr_session%ROWTYPE; a activity.asr_attempt%ROWTYPE; w uuid:=common.current_workspace_id();
 u uuid:=common.current_user_ref_id(); policy config.asr_budget_policy%ROWTYPE; company_seconds numeric; actor_seconds numeric; monthly_seconds numeric;
BEGIN
 IF w IS NULL OR u IS NULL OR NOT security.workspace_is_active(w)
 OR NOT (security.authorization_has('visit.transcribe') OR security.authorization_has('ai.config_test')) THEN
  RAISE EXCEPTION 'ASR_SCOPE_DENIED' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('asr-company:'||w::text,282));
 PERFORM pg_advisory_xact_lock(hashtextextended('asr-provider:'||p_provider,282));
 SELECT * INTO policy FROM config.asr_budget_policy WHERE workspace_id=w;
 SELECT * INTO s FROM activity.asr_session WHERE workspace_id=w AND actor_id=u AND id=p_session;
 IF NOT FOUND THEN RETURN jsonb_build_object('error','ASR_OPERATION_NOT_FOUND'); END IF;
 SELECT * INTO a FROM activity.asr_attempt WHERE workspace_id=w AND actor_id=u AND operation_id=p_operation;
 IF FOUND THEN
  IF a.request_digest<>p_digest THEN RETURN jsonb_build_object('error','ASR_OPERATION_CONFLICT'); END IF;
  IF a.state='linked' THEN SELECT * INTO a FROM activity.asr_attempt WHERE workspace_id=w AND actor_id=u AND operation_id=a.linked_operation_id; END IF;
  RETURN jsonb_build_object('existing',to_jsonb(a));
 END IF;
 SELECT * INTO a FROM activity.asr_attempt WHERE workspace_id=w AND actor_id=u AND session_id=p_session
 AND stage=p_stage AND unit_key=p_unit;
 IF FOUND THEN
  IF a.request_digest<>p_digest THEN RETURN jsonb_build_object('error','ASR_OPERATION_CONFLICT'); END IF;
  IF a.state='linked' THEN SELECT * INTO a FROM activity.asr_attempt WHERE workspace_id=w AND actor_id=u AND operation_id=a.linked_operation_id; END IF;
  RETURN jsonb_build_object('existing',to_jsonb(a));
 END IF;
 IF p_stage NOT IN ('preview','final','diagnostic') OR p_seconds IS NULL OR p_seconds<=0 OR p_seconds>3600
 OR (p_stage='preview' AND p_seconds>20) THEN RETURN jsonb_build_object('error','ASR_BUDGET_EXCEEDED'); END IF;
 IF (p_stage='preview' AND EXISTS(SELECT 1 FROM activity.asr_attempt WHERE workspace_id=w AND actor_id=u AND session_id=p_session AND stage='preview'))
 OR (p_stage='final' AND COALESCE((SELECT sum(audio_seconds) FROM activity.asr_attempt WHERE workspace_id=w AND actor_id=u AND session_id=p_session AND stage='final'),0)+p_seconds>3600)
 THEN RETURN jsonb_build_object('error','ASR_BUDGET_EXCEEDED'); END IF;
 SELECT * INTO a FROM activity.asr_attempt WHERE workspace_id=w AND actor_id=u AND reuse_digest=p_reuse
 AND state IN ('completed','sending','unknown') ORDER BY (state='completed') DESC,created_at LIMIT 1;
 IF FOUND THEN
  IF a.state<>'completed' THEN
   INSERT INTO activity.asr_attempt(workspace_id,actor_id,operation_id,session_id,unit_key,stage,request_digest,reuse_digest,provider_key,state,audio_seconds,linked_operation_id)
   VALUES(w,u,p_operation,p_session,p_unit,p_stage,p_digest,p_reuse,p_provider,'linked',0,a.operation_id);
   RETURN jsonb_build_object('existing',to_jsonb(a),'linked',true);
  END IF;
  INSERT INTO activity.asr_attempt(workspace_id,actor_id,operation_id,session_id,unit_key,stage,request_digest,reuse_digest,provider_key,state,audio_seconds,response,finished_at)
  VALUES(w,u,p_operation,p_session,p_unit,p_stage,p_digest,p_reuse,p_provider,'completed',0,a.response,clock_timestamp());
  SELECT * INTO a FROM activity.asr_attempt WHERE workspace_id=w AND actor_id=u AND operation_id=p_operation;
  RETURN jsonb_build_object('existing',to_jsonb(a),'reused',true);
 END IF;
 -- A process loss is not evidence of a rejected send. Never change it back to queued.
 UPDATE activity.asr_attempt SET state='unknown',error_code='ASR_RESULT_UNKNOWN',finished_at=clock_timestamp()
 WHERE provider_key=p_provider AND state='sending' AND created_at<clock_timestamp()-interval '10 minutes';
 IF (SELECT count(*) FROM activity.asr_attempt WHERE provider_key=p_provider AND state IN ('sending','unknown') AND capacity_released_at IS NULL)>=4
 OR (SELECT count(*) FROM activity.asr_attempt WHERE workspace_id=w AND state IN ('sending','unknown') AND capacity_released_at IS NULL)>=COALESCE(policy.company_concurrency,2)
 OR (SELECT count(*) FROM activity.asr_attempt WHERE workspace_id=w AND actor_id=u AND state IN ('sending','unknown') AND capacity_released_at IS NULL)>=COALESCE(policy.user_concurrency,2)
 THEN RETURN jsonb_build_object('error','ASR_CAPACITY_BUSY'); END IF;
 SELECT COALESCE(sum(audio_seconds) FILTER(WHERE created_at>=date_trunc('day',clock_timestamp())),0),
 COALESCE(sum(audio_seconds) FILTER(WHERE created_at>=date_trunc('day',clock_timestamp()) AND actor_id=u),0),
 COALESCE(sum(audio_seconds),0) INTO company_seconds,actor_seconds,monthly_seconds
 FROM activity.asr_attempt WHERE workspace_id=w AND created_at>=date_trunc('month',clock_timestamp());
 IF company_seconds+p_seconds>policy.daily_seconds OR actor_seconds+p_seconds>policy.user_daily_seconds OR monthly_seconds+p_seconds>policy.monthly_seconds
 THEN RETURN jsonb_build_object('error','ASR_BUDGET_EXCEEDED'); END IF;
 INSERT INTO activity.asr_attempt(workspace_id,actor_id,operation_id,session_id,unit_key,stage,request_digest,reuse_digest,provider_key,state,audio_seconds)
 VALUES(w,u,p_operation,p_session,p_unit,p_stage,p_digest,p_reuse,p_provider,'sending',p_seconds);
 RETURN jsonb_build_object('accepted',true);
END $$;
CREATE TABLE config.asr_capacity_resolution (
 workspace_id uuid NOT NULL, operation_id uuid NOT NULL, owner_id uuid NOT NULL, resolved_by uuid NOT NULL,
 evidence text NOT NULL CHECK(length(evidence) BETWEEN 10 AND 500), resolved_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(workspace_id,owner_id,operation_id),
 FOREIGN KEY(workspace_id,owner_id,operation_id) REFERENCES activity.asr_attempt(workspace_id,actor_id,operation_id),
 FOREIGN KEY(resolved_by,workspace_id) REFERENCES platform.user_ref(id,workspace_id)
);
ALTER TABLE config.asr_capacity_resolution ENABLE ROW LEVEL SECURITY;
ALTER TABLE config.asr_capacity_resolution FORCE ROW LEVEL SECURITY;
CREATE POLICY asr_resolution_read ON config.asr_capacity_resolution FOR SELECT
 USING(workspace_id=common.current_workspace_id() AND security.workspace_is_active(workspace_id)
 AND security.authorization_has('access.console') AND security.authorization_has('ai.usage_rules_manage'));
CREATE TRIGGER asr_resolution_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON config.asr_capacity_resolution
 FOR EACH STATEMENT EXECUTE FUNCTION ops.deny_audit_mutation();
CREATE FUNCTION security.asr_unknown_attempts() RETURNS jsonb
 LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF NOT security.workspace_is_active(common.current_workspace_id()) OR NOT security.authorization_has('access.console')
 OR NOT security.authorization_has('ai.usage_rules_manage') THEN RAISE EXCEPTION 'ASR_SCOPE_DENIED' USING ERRCODE='42501'; END IF;
 RETURN COALESCE((SELECT jsonb_agg(jsonb_build_object('operation_id',a.operation_id,'actor_id',a.actor_id,'error_code',a.error_code,'capacity_released_at',a.capacity_released_at,
 'created_at',a.created_at,'audio_seconds',a.audio_seconds,'state',a.state)) FROM activity.asr_attempt a
 WHERE a.workspace_id=common.current_workspace_id() AND a.capacity_released_at IS NULL
 AND (a.state='unknown' OR (a.state='sending' AND a.created_at<clock_timestamp()-interval '10 minutes'))),'[]'::jsonb);
END $$;
CREATE FUNCTION security.release_asr_capacity(p_operation uuid,p_owner uuid,p_evidence text) RETURNS boolean
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a activity.asr_attempt%ROWTYPE;
BEGIN
 IF NOT security.workspace_is_active(common.current_workspace_id()) OR NOT security.authorization_has('access.console')
 OR NOT security.authorization_has('ai.usage_rules_manage') THEN RAISE EXCEPTION 'ASR_SCOPE_DENIED' USING ERRCODE='42501'; END IF;
 IF length(btrim(p_evidence))<10 OR length(p_evidence)>500 THEN RAISE EXCEPTION 'ASR_RESOLUTION_EVIDENCE_REQUIRED' USING ERRCODE='22023'; END IF;
 SELECT * INTO a FROM activity.asr_attempt WHERE workspace_id=common.current_workspace_id()
 AND actor_id=p_owner AND operation_id=p_operation FOR UPDATE;
 IF FOUND AND a.capacity_released_at IS NOT NULL THEN RETURN true; END IF;
 IF NOT FOUND OR NOT (a.state='unknown' OR (a.state='sending' AND a.created_at<clock_timestamp()-interval '10 minutes')) THEN RETURN false; END IF;
 INSERT INTO config.asr_capacity_resolution(workspace_id,operation_id,owner_id,resolved_by,evidence)
 VALUES(a.workspace_id,p_operation,p_owner,common.current_user_ref_id(),p_evidence) ON CONFLICT DO NOTHING;
 UPDATE activity.asr_attempt SET capacity_released_at=clock_timestamp(),state='unknown',error_code='ASR_RESULT_UNKNOWN'
 WHERE workspace_id=a.workspace_id AND actor_id=p_owner AND operation_id=p_operation;
 RETURN true;
END $$;
REVOKE ALL ON FUNCTION security.asr_unknown_attempts(),security.release_asr_capacity(uuid,uuid,text) FROM PUBLIC;
REVOKE ALL ON config.asr_capacity_resolution FROM PUBLIC,salegent_feishu_worker;
REVOKE ALL ON FUNCTION security.asr_hotword_snapshot(),security.reserve_asr_attempt(uuid,uuid,text,text,text,text,text,numeric) FROM PUBLIC;
REVOKE ALL ON config.asr_hotword_version,config.asr_hotword_current,config.asr_hotword_operation,
 activity.asr_session,activity.asr_attempt FROM PUBLIC,salegent_feishu_worker;
REVOKE ALL ON config.asr_budget_policy FROM PUBLIC,salegent_feishu_worker;
-- Runtime roles receive only their explicit installation-contract grants.
INSERT INTO ops.schema_migration(version,description)
 VALUES('V430','Company-scoped ASR terminology, snapshots and durable attempt receipts');
COMMIT;
