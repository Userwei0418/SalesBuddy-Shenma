BEGIN;
-- Additive scheduling metadata; historical remains the notification guard unchanged.
ALTER TABLE ops.feishu_event ADD COLUMN queue_origin text NOT NULL DEFAULT 'business'
 CHECK(queue_origin IN ('business','reconcile'));
-- Old events cannot be reliably reclassified. Keep their FIFO/background ordering;
-- newly captured business changes will promote their same-object predecessors.
UPDATE ops.feishu_event SET queue_origin='reconcile' WHERE historical;
COMMENT ON COLUMN ops.feishu_event.queue_origin IS 'Scheduling origin only; never grants notification eligibility';

CREATE OR REPLACE FUNCTION ops.feishu_reconcile(p_connection uuid) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ws uuid; item text[]; total bigint:=0; n bigint;
BEGIN
 SELECT workspace_id INTO ws FROM config.feishu_connection WHERE id=p_connection AND enabled;
 IF ws IS NULL THEN RETURN 0; END IF;
 FOREACH item SLICE 1 IN ARRAY ARRAY[['crm.customer','customer'],['crm.opportunity','opportunity'],['activity.visit','visit'],['crm.partner','partner'],['workflow.task','task'],['crm.opportunity_demo_scenes','demo_scene'],['crm.customer_actual','actual'],['crm.sales_target','target'],['platform.user_ref','member'],['crm.contact','contact']] LOOP
  IF NOT EXISTS(SELECT 1 FROM config.feishu_connection c WHERE c.id=p_connection AND (c.settings->'mappings'->item[2]->>'enabled')::boolean IS TRUE) THEN CONTINUE; END IF;
  EXECUTE format('INSERT INTO ops.feishu_event(connection_id,workspace_id,object_kind,object_id,historical,queue_origin) SELECT $1,$2,$3,id,true,''reconcile'' FROM %s s WHERE workspace_id=$2 AND NOT EXISTS(SELECT 1 FROM ops.feishu_event e WHERE e.connection_id=$1 AND e.object_kind=$3 AND e.object_id=s.id AND e.status IN (''pending'',''running'',''failed''))',item[1]) USING p_connection,ws,item[2];
  GET DIAGNOSTICS n=ROW_COUNT; total:=total+n;
 END LOOP;
 RETURN total;
END $$;

CREATE OR REPLACE FUNCTION security.feishu_initialize(p_connection uuid) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ws uuid; item text[]; n bigint; total bigint:=0;
BEGIN
 SELECT workspace_id INTO ws FROM config.feishu_connection WHERE id=p_connection AND workspace_id=common.current_workspace_id();
 IF ws IS NULL OR NOT (security.has_active_role('operations') OR security.has_active_role('administrator')) THEN RAISE insufficient_privilege; END IF;
 FOREACH item SLICE 1 IN ARRAY ARRAY[['crm.customer','customer'],['crm.opportunity','opportunity'],['activity.visit','visit'],['crm.partner','partner'],['workflow.task','task'],['crm.opportunity_demo_scenes','demo_scene'],['crm.customer_actual','actual'],['crm.sales_target','target'],['platform.user_ref','member'],['crm.contact','contact']] LOOP
  EXECUTE format('INSERT INTO ops.feishu_event(connection_id,workspace_id,object_kind,object_id,historical,queue_origin) SELECT $1,$2,$3,id,true,''reconcile'' FROM %s s WHERE workspace_id=$2 AND NOT EXISTS(SELECT 1 FROM ops.feishu_event e WHERE e.connection_id=$1 AND e.object_kind=$3 AND e.object_id=s.id AND e.status IN (''pending'',''running'',''failed''))',item[1]) USING p_connection,ws,item[2];
  GET DIAGNOSTICS n=ROW_COUNT; total:=total+n;
 END LOOP;
 INSERT INTO ops.feishu_config_audit(workspace_id,connection_id,actor_user_ref_id,database_actor,action,after_snapshot)
 VALUES(ws,p_connection,common.current_user_ref_id(),session_user,'initialize',jsonb_build_object('queued',total));
 RETURN total;
END $$;

CREATE OR REPLACE FUNCTION ops.capture_feishu_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r jsonb; prior jsonb; ws uuid; cid uuid; kind text:=TG_ARGV[0]; oid uuid; fresh boolean:=false; live boolean:=false; origin text;
BEGIN
 r:=CASE WHEN TG_OP='DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
 prior:=CASE WHEN TG_OP='UPDATE' THEN to_jsonb(OLD) ELSE '{}'::jsonb END;
 ws:=(r->>'workspace_id')::uuid;
 SELECT id,enabled AND COALESCE((settings->'notification'->>'enabled')::boolean,false) INTO cid,live FROM config.feishu_connection WHERE workspace_id=ws;
 IF cid IS NULL THEN RETURN NULL; END IF;
 IF TG_OP='UPDATE' AND r=prior THEN RETURN NULL; END IF;
 oid:=(r->>'id')::uuid;
 -- Scheduling source is independent of notification eligibility and old import metadata.
 origin:=CASE WHEN COALESCE(current_setting('app.feishu_historical_import',true),'')='on'
   OR (TG_OP='INSERT' AND COALESCE(r->'import_meta'->>'import_type','')='crm_history')
   THEN 'reconcile' ELSE 'business' END;
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
  INSERT INTO ops.feishu_event(connection_id,workspace_id,object_kind,object_id,historical,queue_origin)
  VALUES(cid,ws,'refresh',ws,true,origin);
 END IF;
 INSERT INTO ops.feishu_event(connection_id,workspace_id,object_kind,object_id,first_formal_create,historical,queue_origin)
 VALUES(cid,ws,kind,oid,fresh AND TG_OP<>'DELETE',NOT live OR kind='refresh'
 OR COALESCE(r->'import_meta'->>'import_type','')='crm_history'
 OR COALESCE(prior->'import_meta'->>'import_type','')='crm_history'
 OR COALESCE(current_setting('app.feishu_historical_import',true),'')='on',origin);
 RETURN NULL;
END $$;
COMMIT;
