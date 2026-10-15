-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V195__task_group_notifications.sql.
-- Original SHA-256: e36231fe5be493db929241ae0234f66f41e8e2d917be1997727c21f09109a3d1. See shenma_whole_121_manifest.json.
BEGIN;
-- Explicitly enabled future task dispatches may notify existing department groups.
-- Preserve the two existing function OIDs, owners and ACLs; no new grants.
-- Business tasks, company settings and deliveries remain unchanged. Existing
-- unplanned task-create queue entries predate opt-in capture and must not alert.
UPDATE ops.feishu_event SET historical=true
 WHERE object_kind='task' AND first_formal_create AND NOT notification_planned
  AND NOT historical;
CREATE OR REPLACE FUNCTION ops.feishu_source(p_connection uuid,p_kind text,p_id uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result jsonb; extra jsonb; ws uuid; recipients jsonb; old_workspace text;
BEGIN
 result:=ops.feishu_source_before_task_import(p_connection,p_kind,p_id);
 IF p_kind<>'task' OR result->>'excluded'='true' OR result->>'deleted'='true' THEN RETURN result; END IF;
 SELECT workspace_id INTO ws FROM config.feishu_connection WHERE id=p_connection;
 SELECT jsonb_build_object('source_code',t.source_code,'association_kind',t.association_kind,
  'accepted_at',t.accepted_at,'completion_submitted_at',t.completion_submitted_at,'completed_at',t.completed_at,
  'verification_status',t.verification_status,'owner_name',owner.display_name,'owner_account',owner.account_code,
  'batch_id',b.id,'batch_name',b.batch_name,'meeting_date',b.meeting_date,'dispatched_at',b.confirmed_at,
  'original_owner_id',i.original_assignee_user_ref_id,'original_owner_name',i.confirmed_item#>>'{original_assignee,name}',
  'original_dispatcher_id',t.creator_user_ref_id,'original_dispatcher_name',creator.display_name)
 INTO extra FROM workflow.task t
 LEFT JOIN platform.user_ref owner ON owner.id=(result->>'owner_id')::uuid AND owner.workspace_id=t.workspace_id
 LEFT JOIN platform.user_ref creator ON creator.id=t.creator_user_ref_id AND creator.workspace_id=t.workspace_id
 LEFT JOIN workflow.task_import_item i ON i.task_id=t.id AND i.workspace_id=t.workspace_id
 LEFT JOIN workflow.task_import_batch b ON b.id=i.batch_id AND b.workspace_id=i.workspace_id AND b.status='confirmed'
 WHERE t.id=p_id AND t.workspace_id=ws;
 -- The existing eligibility function is workspace-scoped. Set its context only
 -- for this projection; never leave a tenant context on the worker connection.
 old_workspace:=current_setting('app.workspace_id',true);
 BEGIN
  PERFORM set_config('app.workspace_id',ws::text,true);
  WITH recipients AS MATERIALIZED (
   SELECT DISTINCT u.id,u.display_name FROM workflow.task t
   JOIN workflow.task_assignee a ON a.task_id=t.id AND a.workspace_id=t.workspace_id AND a.responsibility='owner'
   JOIN platform.user_ref u ON u.id=a.assignee_user_ref_id AND u.workspace_id=t.workspace_id
   WHERE t.id=p_id AND t.workspace_id=ws
    AND security.task_recipient_eligible(t.id,u.id,a.assignee_role,a.assignee_team_id)
   UNION
   SELECT DISTINCT u.id,u.display_name FROM workflow.task t
   JOIN workflow.task_candidate c ON c.task_id=t.id AND c.workspace_id=t.workspace_id AND c.decision='pending'
   JOIN platform.user_ref u ON u.id=c.user_ref_id AND u.workspace_id=t.workspace_id
   WHERE t.id=p_id AND t.workspace_id=ws AND t.status='pending_confirm' AND t.target_position IS NOT NULL
    AND NOT EXISTS(SELECT 1 FROM workflow.task_assignee a WHERE a.task_id=t.id AND a.workspace_id=ws AND a.responsibility='owner')
    AND security.task_recipient_eligible(t.id,u.id,c.role_code,c.team_id)
  ), departments AS (
   SELECT DISTINCT tm.team_id FROM recipients r
   JOIN platform.team_membership tm ON tm.user_ref_id=r.id AND tm.workspace_id=ws
   JOIN platform.team d ON d.id=tm.team_id AND d.workspace_id=ws
   WHERE d.status='active' AND d.deleted_at IS NULL
    AND clock_timestamp()>=d.valid_from AND clock_timestamp()<d.valid_to
    AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
  )
  SELECT jsonb_build_object(
   'notification_recipient_ids',(SELECT COALESCE(jsonb_agg(id ORDER BY id),'[]'::jsonb) FROM recipients),
   'notification_recipient_names',(SELECT COALESCE(jsonb_agg(display_name ORDER BY id),'[]'::jsonb) FROM recipients),
   'notification_department_ids',(SELECT COALESCE(jsonb_agg(team_id ORDER BY team_id),'[]'::jsonb) FROM departments),
   'target_position',(SELECT target_position FROM workflow.task WHERE id=p_id AND workspace_id=ws)
  ) INTO recipients;
  PERFORM set_config('app.workspace_id',COALESCE(old_workspace,''),true);
 EXCEPTION WHEN OTHERS THEN
  PERFORM set_config('app.workspace_id',COALESCE(old_workspace,''),true);
  RAISE;
 END;
 RETURN result||COALESCE(extra,'{}'::jsonb)||recipients;
END $$;


CREATE OR REPLACE FUNCTION ops.capture_feishu_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r jsonb; prior jsonb; ws uuid; cid uuid; kind text:=TG_ARGV[0]; oid uuid; fresh boolean:=false; live boolean:=false; origin text;
BEGIN
 r:=CASE WHEN TG_OP='DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
 prior:=CASE WHEN TG_OP='UPDATE' THEN to_jsonb(OLD) ELSE '{}'::jsonb END;
 ws:=(r->>'workspace_id')::uuid;
 SELECT id,enabled AND COALESCE((settings->'notification'->>'enabled')::boolean,false) AND (kind<>'task' OR (
    COALESCE((settings#>>'{mappings,task,enabled}')::boolean,false)
    AND COALESCE(settings#>'{notification,on_create}','[]'::jsonb) ? 'task'
   )) INTO cid,live FROM config.feishu_connection WHERE workspace_id=ws;
 IF cid IS NULL THEN RETURN NULL; END IF;
 IF TG_OP='UPDATE' AND r=prior THEN RETURN NULL; END IF;
 oid:=(r->>'id')::uuid;
 -- Only a verified administrator console confirmation opts a weekly dispatch
 -- into group notices. Direct member assignment still synchronizes without chat.
 IF kind='task' THEN
  live:=COALESCE(live,false)
    AND COALESCE(r->>'source_code','')='meeting_import'
    AND COALESCE(common.current_role_code(),'')='administrator'
    AND COALESCE(current_setting('app.task_group_notification_origin',true),'')='admin_weekly_dispatch';
 END IF;
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

INSERT INTO ops.schema_migration(version,description) VALUES('V343','Task dispatch group notification eligibility and current recipient routing');
COMMIT;
