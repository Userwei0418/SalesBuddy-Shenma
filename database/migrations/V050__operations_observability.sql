BEGIN;
ALTER TABLE ops.audit_log ADD COLUMN module_code text;
ALTER TABLE ops.audit_log ADD COLUMN object_label text;
ALTER TABLE ops.audit_log ADD COLUMN changed_fields text[] NOT NULL DEFAULT '{}';
COMMENT ON TABLE ops.audit_log IS '业务审计：事务内变更与请求操作记录；应用不可修改、删除或清空';
CREATE INDEX audit_lookup ON ops.audit_log(workspace_id,occurred_at DESC,id DESC);
CREATE INDEX audit_actor_lookup ON ops.audit_log(workspace_id,actor_user_ref_id,occurred_at DESC);
CREATE FUNCTION security.audit_redact(p_value jsonb) RETURNS jsonb
 LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE result jsonb; entry record;
BEGIN
 IF p_value IS NULL THEN RETURN NULL; END IF;
 IF jsonb_typeof(p_value)='object' THEN
  result='{}'::jsonb;
  FOR entry IN SELECT * FROM jsonb_each(p_value) LOOP
   result=result||jsonb_build_object(entry.key,CASE WHEN entry.key ~* '(password|secret|token|authorization|api.?key|ciphertext|credential|mobile|phone|email)'
    THEN to_jsonb('[REDACTED]'::text) ELSE security.audit_redact(entry.value) END);
  END LOOP;
  RETURN result;
 ELSIF jsonb_typeof(p_value)='array' THEN
  RETURN COALESCE((SELECT jsonb_agg(security.audit_redact(value)) FROM jsonb_array_elements(p_value)),'[]'::jsonb);
 END IF;
 RETURN p_value;
END $$;
CREATE FUNCTION ops.audit_business_row() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE prior jsonb; current_value jsonb; object_value jsonb; object_key uuid; changed text[];
BEGIN
 IF TG_OP<>'INSERT' THEN prior=to_jsonb(OLD); END IF;
 IF TG_OP<>'DELETE' THEN current_value=to_jsonb(NEW); END IF;
 IF prior IS NOT DISTINCT FROM current_value THEN RETURN NULL; END IF;
 object_value=COALESCE(current_value,prior);
 object_key=COALESCE(object_value->>'id',object_value->>'customer_id',object_value->>'user_ref_id')::uuid;
 SELECT array_agg(key ORDER BY key) INTO changed FROM jsonb_object_keys(COALESCE(prior,'{}')||COALESCE(current_value,'{}')) key
 WHERE prior->key IS DISTINCT FROM current_value->key AND key NOT IN ('updated_at','created_at','version_no');
 INSERT INTO ops.audit_log(workspace_id,actor_user_ref_id,actor_role_code,action_code,module_code,object_type,object_id,object_label,
 request_id,client_ip,user_agent,before_snapshot,after_snapshot,changed_fields)
 VALUES((object_value->>'workspace_id')::uuid,common.current_user_ref_id(),COALESCE(common.current_role_code(),'system'),
 TG_TABLE_SCHEMA||'.'||TG_TABLE_NAME||'.'||lower(TG_OP),TG_TABLE_SCHEMA,TG_TABLE_NAME,object_key,
 COALESCE(object_value->>'name',object_value->>'display_name',object_value->>'title',TG_TABLE_NAME),
 NULLIF(current_setting('app.request_id',true),'')::uuid,NULLIF(current_setting('app.client_ip',true),'')::inet,
 left(current_setting('app.user_agent',true),500),security.audit_redact(prior),security.audit_redact(current_value),COALESCE(changed,'{}'));
 RETURN NULL;
END $$;
DO $$ DECLARE tab record; BEGIN
 FOR tab IN SELECT table_schema,table_name FROM information_schema.tables WHERE table_type='BASE TABLE' AND (
 (table_schema='crm' AND table_name IN ('customer','contact','opportunity','customer_sales_member','customer_ownership','customer_claim_request',
  'customer_ownership_event','customer_actual','opportunity_forecast','sales_target','business_change','customer_assignment','customer_product')) OR
 (table_schema='activity' AND table_name IN ('visit','visit_field_value','visit_contact','action_item','visit_import')) OR
 (table_schema='workflow' AND table_name IN ('task','task_assignee','task_event')) OR
 (table_schema='platform' AND table_name IN ('user_ref','role_binding','team','team_membership','password_credential','identity_binding')) OR
 (table_schema='config' AND table_name IN ('agent_runtime_config','agent_runtime_release')))
 LOOP EXECUTE format('CREATE TRIGGER business_audit AFTER INSERT OR UPDATE OR DELETE ON %I.%I FOR EACH ROW EXECUTE FUNCTION ops.audit_business_row()',tab.table_schema,tab.table_name); END LOOP;
END $$;
CREATE FUNCTION ops.deny_audit_mutation() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN RAISE EXCEPTION '审计及归属历史只读，不可修改或删除' USING ERRCODE='42501'; END $$;
CREATE TRIGGER audit_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON ops.audit_log FOR EACH STATEMENT EXECUTE FUNCTION ops.deny_audit_mutation();
CREATE TRIGGER ownership_history_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON crm.customer_ownership_event FOR EACH STATEMENT EXECUTE FUNCTION ops.deny_audit_mutation();
CREATE POLICY audit_management_read ON ops.audit_log AS RESTRICTIVE FOR SELECT
 USING(workspace_id=common.current_workspace_id() AND security.management_actor());
CREATE TABLE ops.system_event (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid,actor_user_ref_id uuid,request_id uuid,
 level text NOT NULL CHECK(level IN ('INFO','WARN','ERROR')),service_module text NOT NULL,event_type text NOT NULL,
 detail text NOT NULL,error_stack text,occurred_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX system_event_lookup ON ops.system_event(occurred_at DESC,workspace_id,level);
ALTER TABLE ops.system_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops.system_event FORCE ROW LEVEL SECURITY;
CREATE POLICY system_event_read ON ops.system_event FOR SELECT USING(security.management_actor() AND
 (workspace_id=common.current_workspace_id() OR (workspace_id IS NULL AND common.current_role_code()='administrator')));
CREATE FUNCTION ops.record_system_event(p_workspace uuid,p_actor uuid,p_request uuid,p_level text,p_module text,p_event text,p_detail text,p_stack text) RETURNS void
 LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 INSERT INTO ops.system_event(workspace_id,actor_user_ref_id,request_id,level,service_module,event_type,detail,error_stack)
 VALUES(p_workspace,p_actor,p_request,p_level,left(p_module,100),left(p_event,100),left(p_detail,8000),left(p_stack,32000));
$$;
COMMENT ON TABLE ops.system_event IS '运维日志：已脱敏异常及服务事件；90天在线保留，可导出；业务审计永久保留';
-- Native provider attempts: legacy logical records remain distinguishable.
ALTER TABLE agent.model_invocation ALTER COLUMN run_id DROP NOT NULL;
ALTER TABLE agent.model_invocation ADD COLUMN record_kind text NOT NULL DEFAULT 'legacy_logical' CHECK(record_kind IN ('legacy_logical','provider_attempt'));
ALTER TABLE agent.model_invocation ALTER COLUMN record_kind SET DEFAULT 'provider_attempt';
ALTER TABLE agent.model_invocation ADD COLUMN actor_user_ref_id uuid;
ALTER TABLE agent.model_invocation ADD COLUMN actor_role_code text;
ALTER TABLE agent.model_invocation ADD COLUMN operation_code text;
ALTER TABLE agent.model_invocation ADD COLUMN operation_id uuid;
ALTER TABLE agent.model_invocation ADD COLUMN request_id uuid;
ALTER TABLE agent.model_invocation ADD COLUMN audio_seconds numeric;
UPDATE agent.model_invocation i SET actor_user_ref_id=(r.identity_context->>'user_id')::uuid,
 actor_role_code=r.identity_context->>'role',operation_code=r.intent_code,operation_id=r.id FROM agent.run r WHERE r.id=i.run_id;
CREATE INDEX invocation_usage_lookup ON agent.model_invocation(workspace_id,started_at DESC,actor_role_code);
CREATE FUNCTION security.ai_invocation_report(p_start timestamptz,p_end timestamptz,p_role text,p_user uuid)
 RETURNS TABLE(id uuid,actor_user_ref_id uuid,actor_name text,actor_role_code text,operation_code text,operation_id uuid,
 model_id text,endpoint_code text,status text,attempt_no integer,input_tokens integer,output_tokens integer,
 audio_seconds numeric,latency_ms integer,http_status integer,error_code text,started_at timestamptz,completed_at timestamptz,
 record_kind text,request_summary jsonb,request_id uuid,provider_code text)
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT i.id,i.actor_user_ref_id,u.display_name,i.actor_role_code,i.operation_code,i.operation_id,i.model_id,i.endpoint_code,
 i.status,i.attempt_no,i.input_tokens,i.output_tokens,i.audio_seconds,i.latency_ms,i.http_status,i.error_code,i.started_at,i.completed_at,
 i.record_kind,i.request_snapshot,i.request_id,i.provider_code FROM agent.model_invocation i LEFT JOIN platform.user_ref u ON u.id=i.actor_user_ref_id
 WHERE security.management_actor() AND i.workspace_id=common.current_workspace_id() AND i.started_at>=p_start AND i.started_at<p_end
 AND (p_role IS NULL OR i.actor_role_code=p_role) AND (p_user IS NULL OR i.actor_user_ref_id=p_user);
$$;
CREATE TABLE ops.ai_usage_rule (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 name text NOT NULL,role_code text CHECK(role_code IN ('sales','supervisor','manager','operations','administrator')),
 period text NOT NULL CHECK(period IN ('day','week','month')),calls_limit integer CHECK(calls_limit>0),tokens_limit bigint CHECK(tokens_limit>0),
 enabled boolean NOT NULL DEFAULT true,version_no integer NOT NULL DEFAULT 1,created_by_user_ref_id uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK(calls_limit IS NOT NULL OR tokens_limit IS NOT NULL)
);
CREATE TABLE ops.ai_usage_alert (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),workspace_id uuid NOT NULL,rule_id uuid NOT NULL REFERENCES ops.ai_usage_rule(id),
 rule_version integer NOT NULL,period_start timestamptz NOT NULL,calls_count bigint NOT NULL,known_tokens bigint NOT NULL,
 unknown_usage_calls bigint NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(rule_id,rule_version,period_start)
);
ALTER TABLE ops.ai_usage_rule ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops.ai_usage_rule FORCE ROW LEVEL SECURITY;
ALTER TABLE ops.ai_usage_alert ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops.ai_usage_alert FORCE ROW LEVEL SECURITY;
CREATE POLICY usage_rule_management ON ops.ai_usage_rule USING(workspace_id=common.current_workspace_id() AND security.management_actor()) WITH CHECK(workspace_id=common.current_workspace_id() AND security.management_actor());
CREATE POLICY usage_alert_management ON ops.ai_usage_alert FOR SELECT USING(workspace_id=common.current_workspace_id() AND security.management_actor());
CREATE TRIGGER usage_rule_audit AFTER INSERT OR UPDATE OR DELETE ON ops.ai_usage_rule FOR EACH ROW EXECUTE FUNCTION ops.audit_business_row();
CREATE FUNCTION ops.evaluate_ai_usage_alerts() RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE rule ops.ai_usage_rule; started timestamptz; calls bigint; tokens bigint; unknown_calls bigint; alert uuid; added integer=0;
BEGIN
 FOR rule IN SELECT * FROM ops.ai_usage_rule WHERE enabled LOOP
  started=date_trunc(rule.period,timezone('Asia/Shanghai',clock_timestamp())) AT TIME ZONE 'Asia/Shanghai';
  SELECT count(*),COALESCE(sum(COALESCE(input_tokens,0)+COALESCE(output_tokens,0)),0),
   count(*) FILTER(WHERE endpoint_code LIKE '%chat%' AND (input_tokens IS NULL OR output_tokens IS NULL))
  INTO calls,tokens,unknown_calls FROM agent.model_invocation WHERE workspace_id=rule.workspace_id AND started_at>=started
   AND record_kind='provider_attempt' AND (rule.role_code IS NULL OR actor_role_code=rule.role_code);
  IF calls>=rule.calls_limit OR tokens>=rule.tokens_limit THEN
   alert=NULL;
   INSERT INTO ops.ai_usage_alert(workspace_id,rule_id,rule_version,period_start,calls_count,known_tokens,unknown_usage_calls)
   VALUES(rule.workspace_id,rule.id,rule.version_no,started,calls,tokens,unknown_calls) ON CONFLICT DO NOTHING RETURNING id INTO alert;
   IF alert IS NOT NULL THEN
    added=added+1;
    INSERT INTO workflow.notification(workspace_id,recipient_user_ref_id,template_code,title,body,object_type,object_id,dedupe_key,payload)
    SELECT DISTINCT rule.workspace_id,r.user_ref_id,'ai_usage_alert','AI 用量达到提醒阈值',rule.name||'：已调用 '||calls||' 次，已知 Token '||tokens,
    'ai_usage_rule',rule.id,'ai-usage:'||alert::text||':'||r.user_ref_id::text,
    jsonb_build_object('alert_id',alert::text,'calls',calls,'known_tokens',tokens,'unknown_usage_calls',unknown_calls)
    FROM platform.role_binding r JOIN platform.user_ref u ON u.id=r.user_ref_id AND u.status='active' AND u.deleted_at IS NULL
    WHERE r.workspace_id=rule.workspace_id AND r.role_code IN ('operations','administrator') AND clock_timestamp()>=r.valid_from AND clock_timestamp()<r.valid_to
    ON CONFLICT DO NOTHING;
   END IF;
  END IF;
 END LOOP;
 RETURN added;
END $$;
CREATE FUNCTION ops.prune_system_events() RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE removed bigint;
BEGIN
 DELETE FROM ops.system_event WHERE occurred_at<clock_timestamp()-interval '90 days';
 GET DIAGNOSTICS removed=ROW_COUNT;
 RETURN removed;
END $$;
CREATE POLICY own_provider_attempt ON agent.model_invocation
 USING(workspace_id=common.current_workspace_id() AND actor_user_ref_id=common.current_user_ref_id() AND record_kind='provider_attempt')
 WITH CHECK(workspace_id=common.current_workspace_id() AND actor_user_ref_id=common.current_user_ref_id() AND actor_role_code=common.current_role_code() AND record_kind='provider_attempt');
COMMIT;
