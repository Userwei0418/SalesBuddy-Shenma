BEGIN;
SET LOCAL check_function_bodies=on;

-- Internal capture state, not a new business audit ledger. Existing database
-- audit triggers already cover API, Mini, imports, sync and ordinary SQL writes.
CREATE TABLE ops.review_history_capture (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 complete_from timestamptz, checked_at timestamptz NOT NULL,
 capture_contract integer NOT NULL DEFAULT 1 CHECK(capture_contract=1)
);
CREATE TABLE ops.review_metric_snapshot (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 snapshot_date date NOT NULL, captured_at timestamptz NOT NULL,
 year integer NOT NULL, quarter integer NOT NULL CHECK(quarter BETWEEN 1 AND 4),
 definition_version integer NOT NULL CHECK(definition_version=1),
 scope_stamp text NOT NULL, rows jsonb NOT NULL CHECK(jsonb_typeof(rows)='array'),
 UNIQUE(workspace_id,snapshot_date,definition_version),
 CHECK(snapshot_date=(captured_at AT TIME ZONE 'Asia/Shanghai')::date),
 CHECK(year=extract(year FROM captured_at AT TIME ZONE 'Asia/Shanghai')),
 CHECK(quarter=extract(quarter FROM captured_at AT TIME ZONE 'Asia/Shanghai'))
);
ALTER TABLE ops.review_history_capture ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops.review_metric_snapshot ENABLE ROW LEVEL SECURITY;
CREATE TRIGGER review_metric_snapshot_append_only BEFORE UPDATE OR DELETE OR TRUNCATE
 ON ops.review_metric_snapshot FOR EACH STATEMENT EXECUTE FUNCTION ops.deny_audit_mutation();

CREATE FUNCTION ops.review_capture_healthy() RETURNS boolean
LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
 SELECT NOT EXISTS (
  SELECT 1 FROM (VALUES
   -- pg_trigger bits: ROW=1, INSERT=4, DELETE=8, UPDATE=16; no BEFORE/INSTEAD.
   ('crm.opportunity','business_audit','ops.audit_business_row()',29),
   ('crm.opportunity_forecast','business_audit','ops.audit_business_row()',29),
   ('crm.opportunity_project_participant','business_audit','ops.audit_business_row()',29),
   ('crm.opportunity_participant','business_audit','ops.audit_participation_row()',29),
   ('crm.opportunity_quote_reference','quote_reference_audit','ops.audit_business_row()',29),
   ('crm.opportunity_material','finance_audit','ops.audit_business_row()',29),
   ('crm.opportunity_related_partner','related_partner_changed','ops.capture_associated_partner_event()',13)
  ) required(relation_name,trigger_name,function_name,trigger_type)
  WHERE NOT EXISTS(SELECT 1 FROM pg_trigger t WHERE t.tgrelid=required.relation_name::regclass
   AND t.tgname=required.trigger_name AND t.tgfoid=required.function_name::regprocedure
   AND t.tgtype=required.trigger_type AND t.tgattr=''::int2vector
   AND t.tgenabled IN ('O','A') AND NOT t.tgisinternal AND t.tgqual IS NULL)
 );
$$;

-- Only an opaque stamp is retained. Membership time boundaries are included
-- so expiry without a write also invalidates comparison. No auth data is public.
CREATE FUNCTION ops.review_scope_stamp(p_workspace uuid) RETURNS text
LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
 SELECT md5(jsonb_build_object(
  'revision',(SELECT revision FROM config.authorization_scope_revision WHERE workspace_id=p_workspace),
  'roles',(SELECT jsonb_agg(jsonb_build_array(id,version_no) ORDER BY id)
    FROM config.permission_role WHERE workspace_id=p_workspace),
  'grants',(SELECT jsonb_agg(to_jsonb(g) ORDER BY role_id,permission_code)
    FROM config.permission_role_grant g WHERE workspace_id=p_workspace),
  'assigned_roles',(SELECT jsonb_agg(to_jsonb(a) ORDER BY user_ref_id,role_id)
    FROM config.account_permission_role a WHERE workspace_id=p_workspace),
  'overrides',(SELECT jsonb_agg(to_jsonb(a) ORDER BY user_ref_id,permission_code)
    FROM config.account_permission_override a WHERE workspace_id=p_workspace),
  'accounts',(SELECT jsonb_agg(jsonb_build_array(user_ref_id,version_no) ORDER BY user_ref_id)
    FROM config.account_authorization WHERE workspace_id=p_workspace),
  'members',(SELECT jsonb_agg(jsonb_build_array(user_ref_id,team_id,membership_role) ORDER BY user_ref_id,team_id,membership_role)
    FROM platform.team_membership WHERE workspace_id=p_workspace
      AND valid_from<=statement_timestamp() AND valid_to>statement_timestamp()),
  'bindings',(SELECT jsonb_agg(jsonb_build_array(user_ref_id,role_code) ORDER BY user_ref_id,role_code)
    FROM platform.role_binding WHERE workspace_id=p_workspace
      AND valid_from<=statement_timestamp() AND valid_to>statement_timestamp())
 )::text);
$$;

CREATE FUNCTION ops.refresh_review_history() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
DECLARE captured timestamptz:=statement_timestamp(); local_time timestamp:=captured AT TIME ZONE 'Asia/Shanghai';
 total integer;
BEGIN
 -- No caller can supply a date, workspace, payload or claimed snapshot time.
 IF NOT pg_try_advisory_xact_lock(253,1) THEN RETURN 0; END IF;
 IF NOT ops.review_capture_healthy() THEN
  INSERT INTO ops.review_history_capture(singleton,complete_from,checked_at) VALUES(true,NULL,captured)
  ON CONFLICT(singleton) DO UPDATE SET complete_from=NULL,checked_at=excluded.checked_at;
  RETURN 0;
 END IF;
 INSERT INTO ops.review_history_capture(singleton,complete_from,checked_at) VALUES(true,captured,captured)
 ON CONFLICT(singleton) DO UPDATE SET
  complete_from=COALESCE(ops.review_history_capture.complete_from,excluded.complete_from),
  checked_at=excluded.checked_at;
 -- The first successful real capture in the scheduled local window is final.
 -- Missed days stay absent; retries after midnight cannot manufacture yesterday.
 IF local_time::time < time '23:50' THEN RETURN 0; END IF;
 INSERT INTO ops.review_metric_snapshot(workspace_id,snapshot_date,captured_at,year,quarter,
  definition_version,scope_stamp,rows)
 SELECT w.id,local_time::date,captured,extract(year FROM local_time)::integer,
  extract(quarter FROM local_time)::integer,1,ops.review_scope_stamp(w.id),
  COALESCE((SELECT jsonb_agg(jsonb_build_object(
    'id',o.id,'version_no',o.version_no,'customer_id',o.customer_id,
    'owner_id',o.owner_user_ref_id,'team_id',o.owner_team_id,'status',o.status,
    'probability',o.probability::text,'currency',o.currency,
    'forecast_id',f.id,'forecast_updated_at',f.updated_at,
    'collection_amount',f.collection_amount::text,'recognized_amount',f.recognized_amount::text,
    'collection_confidence',f.collection_confidence) ORDER BY o.id)
    FROM crm.opportunity o JOIN crm.customer c ON c.id=o.customer_id AND c.workspace_id=o.workspace_id
    LEFT JOIN crm.opportunity_forecast f ON f.opportunity_id=o.id AND f.workspace_id=o.workspace_id
      AND f.year=extract(year FROM local_time)::integer AND f.quarter=extract(quarter FROM local_time)::integer
    WHERE o.workspace_id=w.id AND o.deleted_at IS NULL AND c.deleted_at IS NULL AND o.status IN ('open','won')),'[]'::jsonb)
 FROM platform.workspace w WHERE w.status='active'
 ON CONFLICT(workspace_id,snapshot_date,definition_version) DO NOTHING;
 GET DIAGNOSTICS total=ROW_COUNT;
 RETURN total;
END $$;

CREATE FUNCTION security.review_metric_baseline(p_kind text,p_subjects uuid[],p_date date,
 p_year integer,p_quarter integer,p_definition integer) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
DECLARE snapshot ops.review_metric_snapshot; chosen jsonb; reason text;
BEGIN
 IF p_kind IS NULL OR p_kind NOT IN ('team','person') OR p_subjects IS NULL OR cardinality(p_subjects)=0
  OR cardinality(p_subjects)>100 OR array_position(p_subjects,NULL) IS NOT NULL
  OR NOT security.authorization_has('work_report.read')
  OR EXISTS(SELECT 1 FROM unnest(p_subjects) subject WHERE NOT (
    security.authorization_subject('work_report.read',p_kind,CASE WHEN p_kind='person' THEN subject END,CASE WHEN p_kind='team' THEN subject END)
    AND security.authorization_subject('opportunity.read',p_kind,CASE WHEN p_kind='person' THEN subject END,CASE WHEN p_kind='team' THEN subject END)
    AND security.authorization_subject('customer.read',p_kind,CASE WHEN p_kind='person' THEN subject END,CASE WHEN p_kind='team' THEN subject END)))
 THEN RETURN jsonb_build_object('status','incomparable','reason_code','source_scope_not_fully_authorized','reason','来源读取权限不覆盖所选统计范围'); END IF;
 SELECT * INTO snapshot FROM ops.review_metric_snapshot
 WHERE workspace_id=common.current_workspace_id() AND snapshot_date=p_date AND definition_version=p_definition;
 IF NOT FOUND THEN RETURN jsonb_build_object('status','no_baseline','reason_code','previous_day_snapshot_missing','reason','昨日没有成功的定时快照，不能补造日变化'); END IF;
 IF snapshot.year<>p_year OR snapshot.quarter<>p_quarter THEN reason:='baseline_period_mismatch';
 ELSIF snapshot.scope_stamp IS DISTINCT FROM ops.review_scope_stamp(common.current_workspace_id()) THEN reason:='authorization_scope_changed';
 END IF;
 IF reason IS NOT NULL THEN RETURN jsonb_build_object('status','incomparable','reason_code',reason,
  'reason',CASE WHEN reason='baseline_period_mismatch' THEN '昨日快照与当前指标不在同一季度' ELSE '授权或成员范围已变化，昨日快照不可直接比较' END,
  'captured_at',snapshot.captured_at,'snapshot_date',snapshot.snapshot_date); END IF;
 SELECT COALESCE(jsonb_agg(row ORDER BY row->>'id'),'[]'::jsonb) INTO chosen
 FROM jsonb_array_elements(snapshot.rows) row WHERE
  (row->>CASE WHEN p_kind='team' THEN 'team_id' ELSE 'owner_id' END)::uuid=ANY(p_subjects);
 -- Reauthorize every old source; never expose old amounts after permission loss.
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(chosen) row WHERE NOT COALESCE(
  security.authorization_opportunity('opportunity.read',(row->>'id')::uuid)
  AND security.authorization_opportunity('work_report.read',(row->>'id')::uuid)
  AND security.authorization_customer('customer.read',(row->>'customer_id')::uuid)
  AND security.authorization_customer('work_report.read',(row->>'customer_id')::uuid)
  AND EXISTS(SELECT 1 FROM crm.opportunity o JOIN crm.customer c ON c.id=o.customer_id AND c.workspace_id=o.workspace_id
    WHERE o.workspace_id=common.current_workspace_id() AND o.id=(row->>'id')::uuid
      AND o.deleted_at IS NULL AND c.deleted_at IS NULL),false))
 THEN RETURN jsonb_build_object('status','incomparable','reason_code','baseline_source_access_changed',
  'reason','昨日快照中有来源已不可读取，无法计算日变化'); END IF;
 RETURN jsonb_build_object('status','available','rows',chosen,'snapshot_id',snapshot.id,
  'captured_at',snapshot.captured_at,'snapshot_date',snapshot.snapshot_date,'year',snapshot.year,
  'quarter',snapshot.quarter,'definition_version',snapshot.definition_version);
END $$;

CREATE FUNCTION security.review_day_update_status(p_kind text,p_subjects uuid[],p_start timestamptz,p_end timestamptz)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
DECLARE started timestamptz; healthy boolean; authorized boolean; updated jsonb; reason text;
BEGIN
 IF p_kind IS NULL OR p_kind NOT IN ('team','person') OR p_subjects IS NULL OR cardinality(p_subjects)=0
  OR cardinality(p_subjects)>100 OR array_position(p_subjects,NULL) IS NOT NULL
  OR p_start IS NULL OR p_end IS NULL OR p_end<p_start OR p_end-p_start>interval '1 day'
  OR p_end>statement_timestamp()
  OR NOT security.authorization_has('work_report.read') THEN
  RAISE EXCEPTION 'Invalid Review day scope' USING ERRCODE='42501'; END IF;
 authorized:=NOT EXISTS(SELECT 1 FROM unnest(p_subjects) subject WHERE NOT (
  security.authorization_subject('work_report.read',p_kind,CASE WHEN p_kind='person' THEN subject END,CASE WHEN p_kind='team' THEN subject END)
  AND security.authorization_subject('opportunity.read',p_kind,CASE WHEN p_kind='person' THEN subject END,CASE WHEN p_kind='team' THEN subject END)
  AND security.authorization_subject('customer.read',p_kind,CASE WHEN p_kind='person' THEN subject END,CASE WHEN p_kind='team' THEN subject END)));
 SELECT complete_from INTO started FROM ops.review_history_capture WHERE singleton;
 healthy:=ops.review_capture_healthy();
 reason:=CASE WHEN NOT authorized THEN 'source_scope_not_fully_authorized'
  WHEN NOT healthy THEN 'capture_trigger_unavailable'
  WHEN started IS NULL THEN 'capture_not_activated'
  WHEN p_start<started THEN 'before_complete_capture' END;
 SELECT COALESCE(jsonb_agg(id ORDER BY id),'[]'::jsonb) INTO updated FROM (
  SELECT DISTINCT o.id FROM ops.audit_log a
  JOIN crm.opportunity o ON o.workspace_id=a.workspace_id AND o.id=CASE
   WHEN a.action_code LIKE 'crm.opportunity_related_partner.%' OR a.object_type='opportunity' THEN a.object_id
   ELSE (COALESCE(a.after_snapshot,a.before_snapshot)->>'opportunity_id')::uuid END
  JOIN crm.customer c ON c.id=o.customer_id AND c.workspace_id=o.workspace_id
  WHERE a.workspace_id=common.current_workspace_id() AND a.occurred_at>=p_start AND a.occurred_at<p_end
   AND a.action_code IN ('crm.opportunity.insert','crm.opportunity.update',
    'crm.opportunity_forecast.insert','crm.opportunity_forecast.update','crm.opportunity_forecast.delete',
    'crm.opportunity_related_partner.insert','crm.opportunity_related_partner.delete',
    'crm.opportunity_project_participant.insert','crm.opportunity_project_participant.update','crm.opportunity_project_participant.delete',
    'crm.opportunity_material.insert','crm.opportunity_material.update','crm.opportunity_material.delete',
    'crm.opportunity_participant.insert','crm.opportunity_participant.update','crm.opportunity_participant.delete',
    'crm.opportunity_quote_reference.insert','crm.opportunity_quote_reference.update','crm.opportunity_quote_reference.delete')
   AND EXISTS(SELECT 1 FROM unnest(a.changed_fields) field WHERE field NOT IN
    ('id','workspace_id','created_at','updated_at','version_no','created_by_user_ref_id',
     'updated_by_user_ref_id','import_meta','external_opportunity_id'))
   AND o.deleted_at IS NULL AND c.deleted_at IS NULL
   AND (CASE WHEN p_kind='team' THEN o.owner_team_id ELSE o.owner_user_ref_id END)=ANY(p_subjects)
   AND security.authorization_opportunity('opportunity.read',o.id)
   AND security.authorization_opportunity('work_report.read',o.id)
   AND security.authorization_customer('customer.read',c.id)
   AND security.authorization_customer('work_report.read',c.id)
 ) ids;
 RETURN jsonb_build_object('updated_ids',updated,'history_coverage',jsonb_build_object(
  'status',CASE WHEN reason IS NULL THEN 'complete' WHEN jsonb_array_length(updated)>0 THEN 'partial' ELSE 'unavailable' END,
  'complete_from',started,'reason_codes',CASE WHEN reason IS NULL THEN '[]'::jsonb ELSE jsonb_build_array(reason) END,
  'message',CASE WHEN reason IS NULL THEN '商机资料、季度预测、关联伙伴、参与同事、报价与物料的实际写入均由数据库审计覆盖'
    ELSE '所选日的商机变更捕获覆盖不足，不能证明没有更新' END));
END $$;

-- No table or generic audit read permission is added to any runtime role.
DO $acl$
DECLARE grant_row record;
BEGIN
 FOR grant_row IN SELECT c.oid,c.relowner,a.grantee,r.rolname FROM pg_class c
 CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a
 LEFT JOIN pg_roles r ON r.oid=a.grantee
 WHERE c.oid IN ('ops.review_history_capture'::regclass,'ops.review_metric_snapshot'::regclass)
 AND a.grantee<>c.relowner LOOP
 EXECUTE format('REVOKE ALL ON TABLE %s FROM %s CASCADE',grant_row.oid::regclass,
 CASE WHEN grant_row.grantee=0 THEN 'PUBLIC' ELSE quote_ident(grant_row.rolname) END);
 END LOOP;
 FOR grant_row IN SELECT p.oid,a.grantee,r.rolname FROM pg_proc p
 CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
 LEFT JOIN pg_roles r ON r.oid=a.grantee
 WHERE p.oid IN ('ops.review_capture_healthy()'::regprocedure,'ops.review_scope_stamp(uuid)'::regprocedure,
 'ops.refresh_review_history()'::regprocedure,'security.review_metric_baseline(text,uuid[],date,integer,integer,integer)'::regprocedure,
 'security.review_day_update_status(text,uuid[],timestamp with time zone,timestamp with time zone)'::regprocedure)
 AND a.grantee<>p.proowner LOOP
 EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %s CASCADE',grant_row.oid::regprocedure,
 CASE WHEN grant_row.grantee=0 THEN 'PUBLIC' ELSE quote_ident(grant_row.rolname) END);
 END LOOP;
END $acl$;
ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_before_review_history;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; runtime record;
BEGIN
 total:=security.reconcile_runtime_grants_before_review_history();
 FOR runtime IN SELECT r.rolname FROM pg_roles r WHERE NOT r.rolsuper AND NOT r.rolbypassrls
  AND r.rolname NOT LIKE 'pg\_%' ESCAPE '\' AND NOT pg_has_role(r.oid,'salegent_feishu_worker','member')
 LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_before_review_history() FROM %I',runtime.rolname);
  IF has_function_privilege(runtime.rolname,'security.work_report_source_access(jsonb,jsonb)','EXECUTE')
   AND has_table_privilege(runtime.rolname,'crm.opportunity','SELECT') THEN
   EXECUTE format('GRANT EXECUTE ON FUNCTION security.review_metric_baseline(text,uuid[],date,integer,integer,integer),security.review_day_update_status(text,uuid[],timestamp with time zone,timestamp with time zone) TO %I',runtime.rolname);
  END IF;
  IF has_function_privilege(runtime.rolname,'ops.claim_job(text,integer,text[],boolean)','EXECUTE')
   AND has_function_privilege(runtime.rolname,'security.work_report_source_access(jsonb,jsonb)','EXECUTE') THEN
   EXECUTE format('GRANT EXECUTE ON FUNCTION ops.refresh_review_history() TO %I',runtime.rolname);
  END IF;
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_before_review_history() FROM PUBLIC;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description) VALUES('V401','Review audited update presence and actual daily scheduled metric snapshots');
COMMIT;
