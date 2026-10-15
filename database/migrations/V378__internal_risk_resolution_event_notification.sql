-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V230__risk_resolution_event_notification.sql.
-- Original SHA-256: 665cd7d6e0960ec52d023824ef69d6ac9ba4ab761c87e3a8d42baa4227f5e4cb. See shenma_whole_121_manifest.json.
BEGIN;

-- Only a newly inserted, authorized resolution event can produce this notice.
-- The API never supplies a recipient or notification body and cannot execute
-- this trigger function directly. General notification RLS stays unchanged.
CREATE FUNCTION security.notify_risk_resolution_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r insight.risk;
BEGIN
 SELECT * INTO r FROM insight.risk WHERE id=NEW.risk_id
  AND workspace_id=NEW.workspace_id AND deleted_at IS NULL;
 IF NOT FOUND OR NEW.workspace_id IS DISTINCT FROM common.current_workspace_id()
  OR NEW.actor_user_ref_id IS DISTINCT FROM common.current_user_ref_id()
  OR NEW.from_status IS NULL OR NEW.from_status='resolved'
  OR NEW.to_status IS DISTINCT FROM 'resolved' OR r.status<>'resolved'
  OR r.resolved_at IS NULL
  OR (NEW.payload->>'resolution_note') IS DISTINCT FROM r.resolution_note
  OR security.authorization_risk('risk.read',r.id) IS NOT TRUE
  OR security.authorization_risk('risk.resolve',r.id) IS NOT TRUE
 THEN RAISE insufficient_privilege; END IF;

 INSERT INTO workflow.notification(workspace_id,recipient_user_ref_id,template_code,title,body,
  object_type,object_id,status,dedupe_key,payload)
 SELECT r.workspace_id,u.id,'risk_resolved','客户风险已解除',r.title,'risk',r.id,'pending',
  'risk-resolved:'||r.id::text||':'||u.id::text,
  jsonb_build_object('risk_id',r.id::text,'resolved_by_user_ref_id',NEW.actor_user_ref_id::text,
                    'resolution_note',r.resolution_note)
 FROM platform.user_ref u WHERE u.id=r.owner_user_ref_id AND u.workspace_id=r.workspace_id
  AND u.id<>NEW.actor_user_ref_id AND u.status='active' AND u.deleted_at IS NULL
 ON CONFLICT (workspace_id,recipient_user_ref_id,dedupe_key) WHERE dedupe_key IS NOT NULL DO NOTHING;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION security.notify_risk_resolution_event() FROM PUBLIC,salegent_feishu_worker;
CREATE TRIGGER risk_resolution_event_notification AFTER INSERT ON insight.risk_event
 FOR EACH ROW WHEN (NEW.event_type='resolved')
 EXECUTE FUNCTION security.notify_risk_resolution_event();

INSERT INTO ops.schema_migration(version,description)
 VALUES('V378','Notify the risk owner from the authorized resolution event');
COMMIT;
