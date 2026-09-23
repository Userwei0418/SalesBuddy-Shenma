BEGIN;
-- Sync metadata only; preserve business rows, queued work and notification receipts.
ALTER TABLE ops.feishu_record_map ADD COLUMN source_fingerprint text;
ALTER TABLE ops.feishu_event ADD COLUMN force_remote_check boolean NOT NULL DEFAULT false;
COMMENT ON COLUMN ops.feishu_record_map.source_fingerprint IS
 'Versioned source, mapping and linked-record fingerprint; written only after acknowledged sync';
COMMENT ON COLUMN ops.feishu_event.force_remote_check IS
 'Explicit operator repair bypasses unchanged-source optimization';

-- Commit-delivered wakeups contain no tenant identifiers or business data.
CREATE FUNCTION ops.wake_feishu_worker() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 PERFORM pg_notify('sales_feishu_work','');
 RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION ops.wake_feishu_worker() FROM PUBLIC;
CREATE TRIGGER feishu_queue_wakeup AFTER INSERT ON ops.feishu_event
 FOR EACH STATEMENT EXECUTE FUNCTION ops.wake_feishu_worker();
CREATE TRIGGER feishu_config_wakeup AFTER INSERT OR UPDATE ON config.feishu_connection
 FOR EACH STATEMENT EXECUTE FUNCTION ops.wake_feishu_worker();

-- Preserve existing function ownership, ACL and tenant authorization.
CREATE OR REPLACE FUNCTION security.feishu_initialize(p_connection uuid) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ws uuid; item text[]; n bigint; total bigint:=0;
BEGIN
 SELECT workspace_id INTO ws FROM config.feishu_connection WHERE id=p_connection AND workspace_id=common.current_workspace_id();
 IF ws IS NULL OR NOT (security.has_active_role('operations') OR security.has_active_role('administrator')) THEN RAISE insufficient_privilege; END IF;
 UPDATE ops.feishu_event SET force_remote_check=true WHERE connection_id=p_connection
 AND workspace_id=ws AND status IN ('pending','failed');
 -- Running work may already have read its flags; enqueue a forced successor instead.
 FOREACH item SLICE 1 IN ARRAY ARRAY[['crm.customer','customer'],['crm.opportunity','opportunity'],['activity.visit','visit'],['crm.partner','partner'],['workflow.task','task'],['crm.opportunity_demo_scenes','demo_scene'],['crm.customer_actual','actual'],['crm.sales_target','target'],['platform.user_ref','member'],['crm.contact','contact'],['crm.opportunity_forecast','forecast'],['crm.opportunity_period_actual_snapshot','period_actual_snapshot']] LOOP
  EXECUTE format('INSERT INTO ops.feishu_event(connection_id,workspace_id,object_kind,object_id,historical,queue_origin,force_remote_check) SELECT $1,$2,$3,id,true,''reconcile'',true FROM %s s WHERE workspace_id=$2 AND NOT EXISTS(SELECT 1 FROM ops.feishu_event e WHERE e.connection_id=$1 AND e.object_kind=$3 AND e.object_id=s.id AND e.status IN (''pending'',''failed''))',item[1]) USING p_connection,ws,item[2];
  GET DIAGNOSTICS n=ROW_COUNT; total:=total+n;
 END LOOP;
 INSERT INTO ops.feishu_config_audit(workspace_id,connection_id,actor_user_ref_id,database_actor,action,after_snapshot)
 VALUES(ws,p_connection,common.current_user_ref_id(),session_user,'initialize',jsonb_build_object('queued',total));
 RETURN total;
END $$;

COMMIT;
