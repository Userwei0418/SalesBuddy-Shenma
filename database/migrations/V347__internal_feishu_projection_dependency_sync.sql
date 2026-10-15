-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V199__feishu_projection_dependency_sync.sql.
-- Original SHA-256: 926e452b34e2c3269545bc5db0f2c595be3f950622302e72f29575a47033ea45. See shenma_whole_121_manifest.json.
BEGIN;

-- Enumerate projection dependencies by explicit row tenant/keys. This helper is
-- private and never uses the request's ambient workspace or queries the queue.
CREATE FUNCTION ops.feishu_dependency_objects(p_relation text,p_rows jsonb)
RETURNS TABLE(workspace_id uuid,object_kind text,object_id uuid)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF p_relation IN ('crm.customer','crm.opportunity') THEN
  RETURN QUERY
  WITH changed AS MATERIALIZED (
   SELECT * FROM jsonb_to_recordset(p_rows) r(workspace_id uuid,id uuid,customer_id uuid)
  ), customers AS MATERIALIZED (
   SELECT c.workspace_id,c.id FROM changed c WHERE p_relation='crm.customer'
  ), opportunities AS MATERIALIZED (
   SELECT o.workspace_id,o.id,o.customer_id FROM crm.opportunity o
    JOIN customers c ON c.workspace_id=o.workspace_id AND c.id=o.customer_id
   UNION SELECT c.workspace_id,c.id,c.customer_id FROM changed c WHERE p_relation='crm.opportunity'
  ), parents AS (
   SELECT o.workspace_id,o.customer_id AS id FROM opportunities o WHERE p_relation='crm.opportunity'
   UNION SELECT r.workspace_id,r.customer_id FROM insight.risk r
    JOIN opportunities o ON o.workspace_id=r.workspace_id AND o.id=r.opportunity_id
    WHERE p_relation='crm.opportunity' AND r.customer_id IS NOT NULL
  )
  SELECT o.workspace_id,'opportunity',o.id FROM opportunities o WHERE p_relation='crm.customer'
  UNION SELECT c.workspace_id,'customer',c.id FROM crm.customer c
   JOIN parents p ON p.workspace_id=c.workspace_id AND p.id=c.id
  UNION SELECT v.workspace_id,'visit',v.id FROM activity.visit v WHERE
   EXISTS(SELECT 1 FROM customers c WHERE c.workspace_id=v.workspace_id AND c.id=v.customer_id)
   OR EXISTS(SELECT 1 FROM opportunities o WHERE o.workspace_id=v.workspace_id AND
    (o.id=v.opportunity_id OR EXISTS(SELECT 1 FROM activity.visit_opportunity x
     WHERE x.workspace_id=v.workspace_id AND x.visit_id=v.id AND x.opportunity_id=o.id)))
  UNION SELECT t.workspace_id,'task',t.id FROM workflow.task t WHERE
   EXISTS(SELECT 1 FROM customers c WHERE c.workspace_id=t.workspace_id AND c.id=t.customer_id)
   OR EXISTS(SELECT 1 FROM opportunities o WHERE o.workspace_id=t.workspace_id AND o.id=t.opportunity_id)
  UNION SELECT a.workspace_id,'actual',a.id FROM crm.customer_actual a WHERE
   EXISTS(SELECT 1 FROM customers c WHERE c.workspace_id=a.workspace_id AND c.id=a.customer_id)
   OR EXISTS(SELECT 1 FROM opportunities o WHERE o.workspace_id=a.workspace_id AND o.id=a.opportunity_id)
  UNION SELECT f.workspace_id,'forecast',f.id FROM crm.opportunity_forecast f
   JOIN opportunities o ON o.workspace_id=f.workspace_id AND o.id=f.opportunity_id
  UNION SELECT a.workspace_id,'period_actual_snapshot',a.id FROM crm.opportunity_period_actual_snapshot a
   JOIN opportunities o ON o.workspace_id=a.workspace_id AND o.id=a.opportunity_id
  UNION SELECT d.workspace_id,'demo_scene',d.id FROM crm.opportunity_demo_scenes d
   JOIN opportunities o ON o.workspace_id=d.workspace_id AND o.id=d.opportunity_id
  UNION SELECT c.workspace_id,'contact',c.id FROM crm.contact c
   JOIN customers p ON p.workspace_id=c.workspace_id AND p.id=c.customer_id;
 ELSIF p_relation IN ('platform.user_ref','platform.team','platform.team_membership','platform.role_binding') THEN
  RETURN QUERY
  WITH changed AS MATERIALIZED (
   SELECT * FROM jsonb_to_recordset(p_rows) r(workspace_id uuid,id uuid,user_ref_id uuid)
  ), teams AS MATERIALIZED (
   SELECT c.workspace_id,c.id FROM changed c WHERE p_relation='platform.team'
  ), members AS MATERIALIZED (
   SELECT c.workspace_id,c.id FROM changed c WHERE p_relation='platform.user_ref'
   UNION SELECT c.workspace_id,c.user_ref_id FROM changed c
    WHERE p_relation IN ('platform.team_membership','platform.role_binding')
   UNION SELECT m.workspace_id,m.user_ref_id FROM platform.team_membership m
    JOIN teams t ON t.workspace_id=m.workspace_id AND t.id=m.team_id
   UNION SELECT r.workspace_id,r.user_ref_id FROM platform.role_binding r
    JOIN teams t ON t.workspace_id=r.workspace_id AND t.id=r.team_id
   UNION SELECT u.workspace_id,u.id FROM platform.user_ref u JOIN teams t
    ON t.workspace_id=u.workspace_id AND t.id::text=u.attributes->>'organization_team_id'
  ), opportunities AS MATERIALIZED (
   SELECT o.workspace_id,o.id,o.customer_id FROM crm.opportunity o WHERE
    EXISTS(SELECT 1 FROM teams t WHERE t.workspace_id=o.workspace_id AND t.id=o.owner_team_id)
    OR (p_relation='platform.user_ref' AND EXISTS(SELECT 1 FROM members m
     WHERE m.workspace_id=o.workspace_id AND (m.id=o.owner_user_ref_id OR EXISTS(
      SELECT 1 FROM crm.opportunity_participant p WHERE p.workspace_id=o.workspace_id
       AND p.opportunity_id=o.id AND p.user_ref_id=m.id))))
  )
  SELECT u.workspace_id,'member',u.id FROM platform.user_ref u
   JOIN members m ON m.workspace_id=u.workspace_id AND m.id=u.id
   WHERE p_relation<>'platform.user_ref'
  UNION SELECT c.workspace_id,'customer',c.id FROM crm.customer c WHERE
   EXISTS(SELECT 1 FROM teams t WHERE t.workspace_id=c.workspace_id AND t.id=c.owner_team_id)
   OR (p_relation='platform.user_ref' AND (
    EXISTS(SELECT 1 FROM members m WHERE m.workspace_id=c.workspace_id AND (m.id=c.owner_user_ref_id OR EXISTS(
     SELECT 1 FROM crm.customer_sales_member s WHERE s.workspace_id=c.workspace_id
      AND s.customer_id=c.id AND s.user_ref_id=m.id)))
    OR EXISTS(SELECT 1 FROM opportunities o WHERE o.workspace_id=c.workspace_id AND o.customer_id=c.id)))
  UNION SELECT o.workspace_id,'opportunity',o.id FROM opportunities o
  UNION SELECT v.workspace_id,'visit',v.id FROM activity.visit v WHERE
   EXISTS(SELECT 1 FROM teams t WHERE t.workspace_id=v.workspace_id AND t.id=v.recorder_team_id)
   OR (p_relation='platform.user_ref' AND EXISTS(SELECT 1 FROM members m WHERE m.workspace_id=v.workspace_id
    AND (m.id=v.recorder_user_ref_id OR m.id=v.manager_user_ref_id OR EXISTS(
     SELECT 1 FROM activity.visit_participant p WHERE p.workspace_id=v.workspace_id
      AND p.visit_id=v.id AND p.user_ref_id=m.id))))
  UNION SELECT t.workspace_id,'task',t.id FROM workflow.task t WHERE EXISTS(
   SELECT 1 FROM members m WHERE m.workspace_id=t.workspace_id AND (
    (p_relation='platform.user_ref' AND m.id=t.creator_user_ref_id)
    OR EXISTS(SELECT 1 FROM workflow.task_assignee a WHERE a.workspace_id=t.workspace_id
     AND a.task_id=t.id AND a.assignee_user_ref_id=m.id AND a.responsibility='owner')
    OR EXISTS(SELECT 1 FROM workflow.task_candidate c WHERE c.workspace_id=t.workspace_id
     AND c.task_id=t.id AND c.user_ref_id=m.id)))
  UNION SELECT s.workspace_id,'target',s.id FROM crm.sales_target s
   JOIN teams t ON t.workspace_id=s.workspace_id AND t.id=s.team_id;
 ELSIF p_relation='crm.opportunity_participant' THEN
  RETURN QUERY WITH parents AS MATERIALIZED (
   SELECT DISTINCT o.workspace_id,o.id,o.customer_id FROM jsonb_to_recordset(p_rows)
    r(workspace_id uuid,opportunity_id uuid) JOIN crm.opportunity o
    ON o.workspace_id=r.workspace_id AND o.id=r.opportunity_id)
  SELECT p.workspace_id,'opportunity',p.id FROM parents p
  UNION SELECT c.workspace_id,'customer',c.id FROM parents p
   JOIN crm.customer c ON c.workspace_id=p.workspace_id AND c.id=p.customer_id;
 ELSIF p_relation='activity.visit_participant' THEN
  RETURN QUERY SELECT DISTINCT v.workspace_id,'visit',v.id
   FROM jsonb_to_recordset(p_rows) r(workspace_id uuid,visit_id uuid)
   JOIN activity.visit v ON v.workspace_id=r.workspace_id AND v.id=r.visit_id;
 ELSIF p_relation IN ('workflow.task_assignee','workflow.task_candidate') THEN
  RETURN QUERY SELECT DISTINCT t.workspace_id,'task',t.id
   FROM jsonb_to_recordset(p_rows) r(workspace_id uuid,task_id uuid)
   JOIN workflow.task t ON t.workspace_id=r.workspace_id AND t.id=r.task_id;
 ELSIF p_relation='crm.partner' THEN
  RETURN QUERY WITH changed AS MATERIALIZED (
   SELECT * FROM jsonb_to_recordset(p_rows) r(workspace_id uuid,id uuid))
  SELECT o.workspace_id,'opportunity',o.id FROM crm.opportunity o WHERE EXISTS(
   SELECT 1 FROM changed c WHERE c.workspace_id=o.workspace_id AND (c.id=o.partner_id OR EXISTS(
    SELECT 1 FROM crm.opportunity_related_partner r WHERE r.workspace_id=o.workspace_id
     AND r.opportunity_id=o.id AND r.partner_id=c.id)))
  UNION SELECT v.workspace_id,'visit',v.id FROM activity.visit v
   JOIN changed c ON c.workspace_id=v.workspace_id AND c.id=v.partner_id;
 ELSE
  RAISE EXCEPTION 'Unsupported Feishu dependency relation' USING ERRCODE='0A000';
 END IF;
END $$;

CREATE FUNCTION ops.capture_feishu_dependencies() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE relation_name text:=TG_TABLE_SCHEMA||'.'||TG_TABLE_NAME; r jsonb; prior jsonb;
 origin text; watched text[]; dependencies record;
BEGIN
 IF relation_name NOT IN ('crm.customer','crm.opportunity','crm.partner','platform.user_ref','platform.team',
  'platform.team_membership','platform.role_binding','workflow.task_assignee','workflow.task_candidate',
  'crm.opportunity_participant','activity.visit_participant') THEN
  RAISE EXCEPTION 'Unsupported Feishu dependency trigger relation' USING ERRCODE='0A000';
 END IF;
 r:=CASE WHEN TG_OP='DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
 prior:=CASE WHEN TG_OP='UPDATE' THEN to_jsonb(OLD) ELSE '{}'::jsonb END;
 IF TG_OP='UPDATE' THEN
  IF (r-ARRAY['updated_at','version_no'])=(prior-ARRAY['updated_at','version_no']) THEN RETURN NULL; END IF;
  watched:=CASE relation_name
   WHEN 'platform.user_ref' THEN ARRAY['id','workspace_id','display_name','account_code','status','deleted_at']
   WHEN 'crm.customer' THEN ARRAY['id','workspace_id','name','data_kind','deleted_at']
   WHEN 'crm.opportunity' THEN ARRAY['id','workspace_id','name','data_kind','deleted_at','customer_id']
   WHEN 'crm.partner' THEN ARRAY['id','workspace_id','name','deleted_at'] END;
  IF watched IS NOT NULL AND NOT EXISTS(SELECT 1 FROM unnest(watched) k WHERE r->k IS DISTINCT FROM prior->k)
   THEN RETURN NULL; END IF;
 END IF;
 origin:=CASE WHEN COALESCE(current_setting('app.feishu_historical_import',true),'')='on'
  OR (TG_OP='INSERT' AND COALESCE(r->'import_meta'->>'import_type','')='crm_history')
  THEN 'reconcile' ELSE 'business' END;
 FOR dependencies IN
  SELECT DISTINCT c.id AS connection_id,d.workspace_id,d.object_kind,d.object_id
   FROM ops.feishu_dependency_objects(relation_name,jsonb_build_array(r,prior)) d
   JOIN config.feishu_connection c ON c.workspace_id=d.workspace_id
 LOOP
  INSERT INTO ops.feishu_event(connection_id,workspace_id,object_kind,object_id,
   first_formal_create,historical,queue_origin)
  VALUES(dependencies.connection_id,dependencies.workspace_id,dependencies.object_kind,
   dependencies.object_id,false,true,origin);
 END LOOP;
 -- BEFORE DELETE retains references before cascades; an aborted business
 -- transaction rolls back these events too. AFTER triggers return no row.
 IF TG_WHEN='BEFORE' THEN RETURN OLD; END IF;
 RETURN NULL;
END $$;

-- Preserve direct object capture and all create-notification conditions.
-- Regular changes no longer enqueue company refresh. The explicit initialize
-- and reconcile functions are unchanged.
CREATE OR REPLACE FUNCTION ops.capture_feishu_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r jsonb; prior jsonb; ws uuid; cid uuid; kind text:=TG_ARGV[0]; oid uuid;
 fresh boolean:=false; live boolean:=false; origin text; previous_connection uuid;
BEGIN
 IF NOT ((TG_TABLE_SCHEMA||'.'||TG_TABLE_NAME,kind) IN (
  ('crm.customer','customer'),('crm.opportunity','opportunity'),('activity.visit','visit'),
  ('crm.partner','partner'),('workflow.task','task'),('crm.opportunity_demo_scenes','demo_scene'),
  ('crm.customer_actual','actual'),('crm.sales_target','target'),('platform.user_ref','member'),
  ('crm.contact','contact'),('crm.opportunity_forecast','forecast'))) THEN
  RAISE EXCEPTION 'Unsupported Feishu direct capture relation' USING ERRCODE='0A000';
 END IF;
 r:=CASE WHEN TG_OP='DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
 prior:=CASE WHEN TG_OP='UPDATE' THEN to_jsonb(OLD) ELSE '{}'::jsonb END;
 IF TG_OP='UPDATE' AND r=prior THEN RETURN NULL; END IF;
 ws:=(r->>'workspace_id')::uuid; oid:=(r->>'id')::uuid;
 origin:=CASE WHEN COALESCE(current_setting('app.feishu_historical_import',true),'')='on'
  OR (TG_OP='INSERT' AND COALESCE(r->'import_meta'->>'import_type','')='crm_history')
  THEN 'reconcile' ELSE 'business' END;
 -- A controlled identity/tenant move must also remove the former projection.
 IF TG_OP='UPDATE' AND (r->>'workspace_id',r->>'id') IS DISTINCT FROM (prior->>'workspace_id',prior->>'id') THEN
  SELECT id INTO previous_connection FROM config.feishu_connection WHERE workspace_id=(prior->>'workspace_id')::uuid;
  IF previous_connection IS NOT NULL AND (kind<>'visit' OR prior->>'archived_at' IS NOT NULL) THEN
   INSERT INTO ops.feishu_event(connection_id,workspace_id,object_kind,object_id,first_formal_create,historical,queue_origin)
    VALUES(previous_connection,(prior->>'workspace_id')::uuid,kind,(prior->>'id')::uuid,false,true,origin);
  END IF;
 END IF;
 SELECT id,enabled AND COALESCE((settings->'notification'->>'enabled')::boolean,false) AND (kind<>'task' OR (
  COALESCE((settings#>>'{mappings,task,enabled}')::boolean,false)
  AND COALESCE(settings#>'{notification,on_create}','[]'::jsonb) ? 'task'))
  INTO cid,live FROM config.feishu_connection WHERE workspace_id=ws;
 IF cid IS NULL THEN RETURN NULL; END IF;
 IF kind='task' THEN
  live:=COALESCE(live,false) AND COALESCE(r->>'source_code','')='meeting_import'
   AND COALESCE(common.current_role_code(),'')='administrator'
   AND COALESCE(current_setting('app.task_group_notification_origin',true),'')='admin_weekly_dispatch';
 END IF;
 IF kind='visit' THEN
  IF r->>'archived_at' IS NULL THEN RETURN NULL; END IF;
  fresh:=TG_OP='INSERT' OR prior->>'archived_at' IS NULL;
 ELSE fresh:=TG_OP='INSERT'; END IF;
 INSERT INTO ops.feishu_event(connection_id,workspace_id,object_kind,object_id,first_formal_create,historical,queue_origin)
 VALUES(cid,ws,kind,oid,fresh AND TG_OP<>'DELETE',NOT live
  OR COALESCE(r->'import_meta'->>'import_type','')='crm_history'
  OR COALESCE(prior->'import_meta'->>'import_type','')='crm_history'
  OR COALESCE(current_setting('app.feishu_historical_import',true),'')='on',origin);
 RETURN NULL;
END $$;

-- Relation changes affect only their explicit old/new parents. Team deletion
-- needs BEFORE capture; membership/role rows can still vanish by cascade later.
DO $$ DECLARE rel text; BEGIN
 FOREACH rel IN ARRAY ARRAY['platform.team_membership','platform.role_binding',
  'workflow.task_assignee','crm.opportunity_participant','activity.visit_participant'] LOOP
  EXECUTE format('DROP TRIGGER feishu_capture ON %s',rel);
  EXECUTE format('CREATE TRIGGER feishu_capture AFTER INSERT OR UPDATE OR DELETE ON %s
   FOR EACH ROW EXECUTE FUNCTION ops.capture_feishu_dependencies()',rel);
 END LOOP;
 DROP TRIGGER feishu_capture ON platform.team;
 CREATE TRIGGER feishu_capture AFTER INSERT OR UPDATE ON platform.team
  FOR EACH ROW EXECUTE FUNCTION ops.capture_feishu_dependencies();
 CREATE TRIGGER feishu_dependents_delete BEFORE DELETE ON platform.team
  FOR EACH ROW EXECUTE FUNCTION ops.capture_feishu_dependencies();
 CREATE TRIGGER feishu_capture AFTER INSERT OR UPDATE OR DELETE ON workflow.task_candidate
  FOR EACH ROW EXECUTE FUNCTION ops.capture_feishu_dependencies();
 FOREACH rel IN ARRAY ARRAY['platform.user_ref','crm.customer','crm.opportunity','crm.partner'] LOOP
  EXECUTE format('CREATE TRIGGER feishu_dependents_update AFTER UPDATE ON %s
   FOR EACH ROW EXECUTE FUNCTION ops.capture_feishu_dependencies()',rel);
  EXECUTE format('CREATE TRIGGER feishu_dependents_delete BEFORE DELETE ON %s
   FOR EACH ROW EXECUTE FUNCTION ops.capture_feishu_dependencies()',rel);
 END LOOP;
END $$;

REVOKE ALL ON FUNCTION ops.feishu_dependency_objects(text,jsonb),ops.capture_feishu_dependencies() FROM PUBLIC;
DO $$ DECLARE granted record; BEGIN
 FOR granted IN SELECT DISTINCT p.oid::regprocedure AS signature,roles.rolname
  FROM pg_proc p CROSS JOIN LATERAL aclexplode(p.proacl) acl JOIN pg_roles roles ON roles.oid=acl.grantee
  WHERE p.oid IN ('ops.feishu_dependency_objects(text,jsonb)'::regprocedure,
   'ops.capture_feishu_dependencies()'::regprocedure) AND acl.grantee<>p.proowner
 LOOP EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I',granted.signature,granted.rolname); END LOOP;
END $$;
INSERT INTO ops.schema_migration(version,description)
 VALUES('V347','Capture bounded Feishu projection dependencies without automatic company refresh');
COMMIT;
