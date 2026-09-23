BEGIN;
-- Preserve V105 ACLs while adding display fields and immediate dependent refresh.
CREATE OR REPLACE FUNCTION ops.feishu_source(p_connection uuid,p_kind text,p_id uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ws uuid; source_table text; allowed text[]; raw jsonb; result jsonb;
BEGIN
 SELECT workspace_id INTO ws FROM config.feishu_connection WHERE id=p_connection;
 IF ws IS NULL THEN RAISE insufficient_privilege; END IF;
 CASE p_kind
 WHEN 'customer' THEN source_table:='crm.customer'; allowed:=ARRAY['name','industry_code','customer_type_code','level_code','main_business','demand_summary','customer_budget','next_action','owner_user_ref_id','owner_team_id','data_kind','lifecycle_status'];
 WHEN 'opportunity' THEN source_table:='crm.opportunity'; allowed:=ARRAY['name','customer_id','stage_code','amount','currency','probability','expected_close_date','status','product_line','sales_channel','partner_id','owner_user_ref_id','owner_team_id','data_kind'];
 WHEN 'visit' THEN source_table:='activity.visit'; allowed:=ARRAY['customer_id','opportunity_id','follow_up_record','next_action','interaction_at','archived_at','recorder_user_ref_id','archived_fields','status','recorder_team_id','interaction_mode_code','contact_name_snapshot','contact_title_snapshot','visit_goal','visit_location','contact_category_snapshot','partner_name_snapshot','is_first_visit','follow_up_score'];
 WHEN 'partner' THEN source_table:='crm.partner'; allowed:=ARRAY['name','status'];
 WHEN 'task' THEN source_table:='workflow.task'; allowed:=ARRAY['title','description','status','due_at','creator_user_ref_id','creator_team_id','customer_id','opportunity_id','completion_note','completion_review_note'];
 WHEN 'demo_scene' THEN source_table:='crm.opportunity_demo_scenes'; allowed:=ARRAY['name','description','opportunity_id','created_by'];
 WHEN 'actual' THEN source_table:='crm.customer_actual'; allowed:=ARRAY['kind','amount','occurred_on','customer_id','opportunity_id','source_ref','note','confirmed_by_user_ref_id','voided_at','void_reason'];
 WHEN 'target' THEN source_table:='crm.sales_target'; allowed:=ARRAY['period_type','period_start','period_end','scope_type','user_ref_id','team_id','department_code','kind','amount'];
 WHEN 'member' THEN source_table:='platform.user_ref'; allowed:=ARRAY['display_name','account_code','status'];
 WHEN 'contact' THEN source_table:='crm.contact'; allowed:=ARRAY['name','title','relationship_role_code','customer_id','is_primary'];
 ELSE RAISE invalid_parameter_value; END CASE;
 EXECUTE format('SELECT to_jsonb(r) FROM %s r WHERE r.id=$1 AND r.workspace_id=$2',source_table) INTO raw USING p_id,ws;
 IF raw IS NULL THEN RETURN jsonb_build_object('id',p_id,'deleted',true); END IF;
 allowed:=allowed||ARRAY['id','created_at','updated_at','deleted_at','version_no'];
 SELECT jsonb_object_agg(key,value) INTO result FROM jsonb_each(raw) WHERE key=ANY(allowed);
 IF p_kind='task' THEN
  result:=result||jsonb_build_object('owner_id',(SELECT assignee_user_ref_id FROM workflow.task_assignee WHERE task_id=p_id AND workspace_id=ws AND responsibility='owner' ORDER BY assigned_at DESC LIMIT 1));
 END IF;
 IF p_kind='member' THEN
  result:=result||(WITH eligible AS (
   SELECT DISTINCT ON(t.id) t.id,t.name,m.is_primary,m.created_at,m.id AS membership_id
   FROM platform.team_membership m JOIN platform.team t ON t.id=m.team_id AND t.workspace_id=m.workspace_id
   WHERE m.workspace_id=ws AND m.user_ref_id=p_id AND clock_timestamp()>=m.valid_from AND clock_timestamp()<m.valid_to
    AND t.status='active' AND t.deleted_at IS NULL AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to
   ORDER BY t.id,m.is_primary DESC,m.created_at DESC,m.id DESC
  ) SELECT jsonb_build_object(
   'department_ids',(SELECT COALESCE(jsonb_agg(id ORDER BY name,id),'[]'::jsonb) FROM eligible),
   'department_names',(SELECT COALESCE(jsonb_agg(name ORDER BY name,id),'[]'::jsonb) FROM eligible),
   'primary_department_id',(SELECT id FROM eligible WHERE is_primary ORDER BY created_at DESC,membership_id DESC LIMIT 1),
   'primary_department_name',(SELECT name FROM eligible WHERE is_primary ORDER BY created_at DESC,membership_id DESC LIMIT 1),
   'roles',(SELECT COALESCE(jsonb_agg(DISTINCT r.role_code),'[]'::jsonb) FROM platform.role_binding r
    WHERE r.workspace_id=ws AND r.user_ref_id=p_id AND clock_timestamp()>=r.valid_from AND clock_timestamp()<r.valid_to)));
 END IF;
 IF p_kind='target' THEN
  result:=result||jsonb_build_object('team_name',(SELECT t.name FROM platform.team t
   WHERE t.workspace_id=ws AND t.id=(result->>'team_id')::uuid));
 END IF;
 -- Fail closed for explicitly referenced parents, including missing/cross-company rows.
 IF result->>'customer_id' IS NOT NULL AND NOT EXISTS(
  SELECT 1 FROM crm.customer c WHERE c.id=(result->>'customer_id')::uuid AND c.workspace_id=ws
   AND c.data_kind='production' AND c.deleted_at IS NULL) THEN
  RETURN jsonb_build_object('id',p_id,'excluded',true);
 END IF;
 IF result->>'opportunity_id' IS NOT NULL AND NOT EXISTS(
  SELECT 1 FROM crm.opportunity o JOIN crm.customer c ON c.id=o.customer_id AND c.workspace_id=o.workspace_id
  WHERE o.id=(result->>'opportunity_id')::uuid AND o.workspace_id=ws
   AND o.deleted_at IS NULL AND c.data_kind='production' AND c.deleted_at IS NULL) THEN
  RETURN jsonb_build_object('id',p_id,'excluded',true);
 END IF;
 IF COALESCE(result->>'data_kind','production')<>'production' THEN
  RETURN jsonb_build_object('id',p_id,'excluded',true);
 END IF;
 IF p_kind='customer' THEN
  result:=result||COALESCE((SELECT jsonb_build_object('potential_score',q.potential_score,
   'relationship_score',q.relationship_score,'quadrant_code',q.quadrant_code)
   FROM insight.quadrant_score q WHERE q.workspace_id=ws AND q.customer_id=p_id AND q.valid_to='infinity'
   ORDER BY q.calculated_at DESC,q.id DESC LIMIT 1),'{}'::jsonb);
  result:=result||jsonb_build_object('risk_title',(SELECT r.title FROM insight.risk r
   WHERE r.workspace_id=ws AND r.customer_id=p_id AND r.deleted_at IS NULL
   AND (r.opportunity_id IS NULL OR EXISTS(SELECT 1 FROM crm.opportunity ro WHERE ro.id=r.opportunity_id
    AND ro.workspace_id=ws AND ro.deleted_at IS NULL))
   AND r.status IN ('new','pending','in_progress','escalated')
   ORDER BY CASE r.severity_code WHEN 'critical' THEN 1 WHEN 'high' THEN 2 ELSE 3 END,r.opened_at DESC,r.id LIMIT 1),
   'sales_members',(SELECT COALESCE(jsonb_agg(u.display_name ORDER BY u.display_name,u.id),'[]'::jsonb)
   FROM crm.customer_sales_member m JOIN platform.user_ref u ON u.id=m.user_ref_id AND u.workspace_id=m.workspace_id
   WHERE m.workspace_id=ws AND m.customer_id=p_id));
 END IF;
 IF p_kind IN ('customer','opportunity') THEN
  result:=result||jsonb_build_object('fde_members',(SELECT COALESCE(jsonb_agg(names.display_name ORDER BY names.display_name,names.id),'[]'::jsonb)
   FROM (SELECT DISTINCT u.id,u.display_name FROM crm.opportunity_participant p
    JOIN crm.opportunity o ON o.id=p.opportunity_id AND o.workspace_id=p.workspace_id
    JOIN platform.user_ref u ON u.id=p.user_ref_id AND u.workspace_id=p.workspace_id
    WHERE p.workspace_id=ws AND p.participant_role='fde' AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to
    AND o.deleted_at IS NULL AND u.status='active'
    AND ((p_kind='customer' AND o.customer_id=p_id) OR (p_kind='opportunity' AND o.id=p_id))) names));
 END IF;
 IF p_kind='visit' THEN
  result:=result||jsonb_build_object('collaborators',(SELECT COALESCE(jsonb_agg(u.display_name ORDER BY u.display_name,u.id),'[]'::jsonb)
   FROM activity.visit_participant p JOIN platform.user_ref u ON u.id=p.user_ref_id AND u.workspace_id=p.workspace_id
   WHERE p.workspace_id=ws AND p.visit_id=p_id));
 END IF;
 result:=result||jsonb_build_object('company_name',(SELECT name FROM platform.workspace WHERE id=ws));
 IF p_kind IN ('customer','opportunity','visit') THEN
  result:=result||jsonb_build_object(
   'owner_name',(SELECT display_name FROM platform.user_ref WHERE workspace_id=ws AND id=COALESCE((result->>'owner_user_ref_id')::uuid,(result->>'recorder_user_ref_id')::uuid)),
   'owner_account',(SELECT account_code FROM platform.user_ref WHERE workspace_id=ws AND id=COALESCE((result->>'owner_user_ref_id')::uuid,(result->>'recorder_user_ref_id')::uuid)),
   'department_name',(SELECT name FROM platform.team WHERE workspace_id=ws AND id=COALESCE((result->>'owner_team_id')::uuid,(result->>'recorder_team_id')::uuid)));
 END IF;
 IF result->>'customer_id' IS NOT NULL THEN
  result:=result||jsonb_build_object('customer_name',(SELECT name FROM crm.customer WHERE workspace_id=ws AND id=(result->>'customer_id')::uuid));
 END IF;
 IF p_kind='visit' THEN
  result:=result||jsonb_build_object('name',COALESCE(NULLIF(result->>'visit_goal',''),left(result->>'follow_up_record',60),'客户跟进'),
   'opportunity_name',(SELECT name FROM crm.opportunity WHERE workspace_id=ws AND id=(result->>'opportunity_id')::uuid));
 END IF;
 RETURN result;
END $$;
CREATE OR REPLACE FUNCTION ops.capture_feishu_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r jsonb; prior jsonb; ws uuid; cid uuid; kind text:=TG_ARGV[0]; oid uuid; fresh boolean:=false; live boolean:=false;
BEGIN
 r:=CASE WHEN TG_OP='DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
 prior:=CASE WHEN TG_OP='UPDATE' THEN to_jsonb(OLD) ELSE '{}'::jsonb END;
 ws:=(r->>'workspace_id')::uuid;
 SELECT id,enabled AND COALESCE((settings->'notification'->>'enabled')::boolean,false) INTO cid,live FROM config.feishu_connection WHERE workspace_id=ws;
 IF cid IS NULL THEN RETURN NULL; END IF;
 IF TG_OP='UPDATE' AND r=prior THEN RETURN NULL; END IF;
 oid:=(r->>'id')::uuid;
 IF kind='visit' THEN
  IF r->>'archived_at' IS NULL THEN RETURN NULL; END IF;
  fresh:=TG_OP='INSERT' OR prior->>'archived_at' IS NULL;
 ELSIF kind='refresh' THEN
  oid:=ws;
 ELSE fresh:=TG_OP='INSERT'; END IF;
 -- Keep exits from production: the worker emits a status-only tombstone.
 IF kind IN ('member','customer','opportunity') AND (TG_OP='DELETE' OR (TG_OP='UPDATE' AND (
  kind='member' OR r->>'data_kind' IS DISTINCT FROM prior->>'data_kind'
  OR r->>'name' IS DISTINCT FROM prior->>'name' OR r->>'deleted_at' IS DISTINCT FROM prior->>'deleted_at'
  OR r->>'customer_id' IS DISTINCT FROM prior->>'customer_id'))) THEN
  INSERT INTO ops.feishu_event(connection_id,workspace_id,object_kind,object_id,historical)
  VALUES(cid,ws,'refresh',ws,true);
 END IF;
 INSERT INTO ops.feishu_event(connection_id,workspace_id,object_kind,object_id,first_formal_create,historical)
 VALUES(cid,ws,kind,oid,fresh AND TG_OP<>'DELETE',NOT live OR kind='refresh'
 OR COALESCE(r->'import_meta'->>'import_type','')='crm_history'
 OR COALESCE(prior->'import_meta'->>'import_type','')='crm_history'
 OR COALESCE(current_setting('app.feishu_historical_import',true),'')='on');
 RETURN NULL;
END $$;
COMMIT;
