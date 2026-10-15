-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V188__feishu_opportunity_update_notifications.sql.
-- Original SHA-256: ef26234f753ba6f0010c280b0bbf78139856c6c67e1b4ffbee7710c4f89d2d47. See shenma_whole_121_manifest.json.
-- Only newly committed, human-confirmed opportunity changes may notify.
-- Existing synchronization events remain snapshot-free and are never backfilled.
ALTER TABLE ops.feishu_event ADD COLUMN notification_snapshot jsonb;
ALTER TABLE ops.feishu_event ADD CONSTRAINT feishu_event_notification_snapshot_check CHECK (
 notification_snapshot IS NULL OR (
  jsonb_typeof(notification_snapshot)='object'
  AND notification_snapshot->>'schema_version'='1'
  AND notification_snapshot->>'change_id'=id::text
  AND notification_snapshot->>'workspace_id'=workspace_id::text
  AND notification_snapshot->>'opportunity_id'=object_id::text
  AND notification_snapshot ?& ARRAY['opportunity_name','customer_name','owner_name',
   'department_id','actor_name','changed_at','currency','changes']
  AND object_kind='opportunity' AND NOT first_formal_create AND NOT historical
  AND queue_origin='business'
 ) IS TRUE
);

CREATE FUNCTION ops.protect_feishu_notification_snapshot() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF NEW.notification_snapshot IS DISTINCT FROM OLD.notification_snapshot THEN
  RAISE EXCEPTION 'Feishu notification snapshots are immutable' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION ops.protect_feishu_notification_snapshot() FROM PUBLIC;
CREATE TRIGGER feishu_notification_snapshot_immutable
 BEFORE UPDATE OF notification_snapshot ON ops.feishu_event
 FOR EACH ROW EXECUTE FUNCTION ops.protect_feishu_notification_snapshot();

CREATE FUNCTION ops.capture_feishu_opportunity_change() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE connection_id uuid; clean_changes jsonb; snapshot jsonb;
BEGIN
 -- A create returns version 1 from INSERT; save_opportunity records that same
 -- version after related writes. Updates use the aggregate's incremented version.
 IF NEW.opportunity_id IS NULL OR NEW.version_no<=1
  OR NEW.workspace_id IS DISTINCT FROM common.current_workspace_id()
  OR NEW.actor_user_ref_id IS DISTINCT FROM common.current_user_ref_id()
  OR COALESCE(current_setting('app.job_id',true),'')<>''
  OR COALESCE(current_setting('app.feishu_historical_import',true),'')='on'
  OR NOT security.workspace_is_active(NEW.workspace_id)
  OR NOT security.workspace_module_enabled(NEW.workspace_id,'feishu')
 THEN RETURN NULL; END IF;
 IF jsonb_typeof(NEW.changes) IS DISTINCT FROM 'array' THEN RETURN NULL; END IF;

 -- Keep the existing human-readable units and labels. Never copy arbitrary
 -- payload keys or nested objects into an external notification snapshot.
 SELECT jsonb_agg(jsonb_build_object('label',item->'label',
   'before',item->'before','after',item->'after') ORDER BY position)
 INTO clean_changes
 FROM jsonb_array_elements(NEW.changes) WITH ORDINALITY AS change(item,position)
 WHERE jsonb_typeof(item)='object'
  AND jsonb_typeof(item->'label')='string' AND NULLIF(btrim(item->>'label'),'') IS NOT NULL
  AND item ?& ARRAY['before','after']
  AND jsonb_typeof(item->'before') IN ('string','number','boolean','null')
  AND jsonb_typeof(item->'after') IN ('string','number','boolean','null')
  AND item->'before' IS DISTINCT FROM item->'after';
 IF clean_changes IS NULL THEN RETURN NULL; END IF;

 SELECT f.id INTO connection_id FROM config.feishu_connection f
 WHERE f.workspace_id=NEW.workspace_id AND f.enabled
  AND f.settings->'notification'->>'enabled'='true'
  AND f.settings->'mappings'->'opportunity'->>'enabled'='true'
  AND (CASE WHEN f.settings->'notification' ? 'on_update'
       THEN f.settings->'notification'->'on_update'
       ELSE '["opportunity"]'::jsonb END) @> '["opportunity"]'::jsonb;
 IF connection_id IS NULL THEN RETURN NULL; END IF;

 -- Every lookup is tenant-bound. Opportunities inherit production eligibility
 -- from their customer; provenance in import_meta is deliberately not a gate:
 -- an imported historical opportunity can subsequently receive a human edit.
 SELECT jsonb_build_object(
  'schema_version',1,'change_id',NEW.id,'workspace_id',NEW.workspace_id,
  'opportunity_id',o.id,'customer_id',c.id,'version_no',NEW.version_no,
  'opportunity_name',o.name,'customer_name',c.name,
  'owner_user_ref_id',owner.id,'owner_name',owner.display_name,
  'department_id',t.id,'department_name',t.name,
  'actor_user_ref_id',actor.id,'actor_name',actor.display_name,
  'changed_at',NEW.created_at,'currency',btrim(o.currency),'changes',clean_changes)
 INTO snapshot
 FROM crm.opportunity o
 JOIN crm.customer c ON c.id=o.customer_id AND c.workspace_id=o.workspace_id
 JOIN platform.user_ref actor ON actor.id=NEW.actor_user_ref_id AND actor.workspace_id=o.workspace_id
 LEFT JOIN platform.user_ref owner ON owner.id=o.owner_user_ref_id AND owner.workspace_id=o.workspace_id
 LEFT JOIN platform.team t ON t.id=o.owner_team_id AND t.workspace_id=o.workspace_id
 WHERE o.id=NEW.opportunity_id AND o.workspace_id=NEW.workspace_id
  AND c.id=NEW.customer_id AND c.data_kind='production'
  AND o.deleted_at IS NULL AND c.deleted_at IS NULL
  AND actor.status='active' AND actor.deleted_at IS NULL;
 IF snapshot IS NULL THEN RETURN NULL; END IF;

 -- The change UUID is also the notification event identity. Retried source
 -- capture cannot create a second card event for the same committed change.
 INSERT INTO ops.feishu_event(id,connection_id,workspace_id,object_kind,object_id,
  first_formal_create,historical,queue_origin,notification_snapshot)
 VALUES(NEW.id,connection_id,NEW.workspace_id,'opportunity',NEW.opportunity_id,
  false,false,'business',snapshot)
 ON CONFLICT(id) DO NOTHING;
 RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION ops.capture_feishu_opportunity_change() FROM PUBLIC;
CREATE TRIGGER feishu_capture_opportunity_change AFTER INSERT ON crm.business_change
 FOR EACH ROW EXECUTE FUNCTION ops.capture_feishu_opportunity_change();

COMMENT ON COLUMN ops.feishu_event.notification_snapshot IS
 'V188 human opportunity updates only: immutable minimal actor/time/identity/diff snapshot; NULL legacy events never gain update notifications';
COMMENT ON FUNCTION ops.capture_feishu_opportunity_change() IS
 'Trigger-only outbox capture of new human opportunity changes, independent of generic table synchronization and AI touches';
COMMENT ON TABLE ops.feishu_event IS
 'Transactional synchronization identity; V188 human opportunity updates additionally freeze only approved notification fields, consumed by the dedicated worker';
