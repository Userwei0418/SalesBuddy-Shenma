-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V192__feishu_manual_recovery_force_check.sql.
-- Original SHA-256: f826ea03ad4999312e824511271098edea3b9297325141ae2a047696c94f489c. See shenma_whole_121_manifest.json.
-- Explicit operator recovery must recheck Feishu even when a prior source
-- fingerprint matches. A retry resets attempts, so claim alone only reaches 1.
-- Preserve the V132 function signature, owner, ACL, tenant authorization,
-- pause/unknown-delivery/target-migration guards and recovery audit.
-- No business records or existing queued events are changed by this migration.

CREATE OR REPLACE FUNCTION security.feishu_recover(p_connection uuid, p_event uuid, p_action text, p_key text, p_note text)
 RETURNS void
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE ws uuid; live boolean; event_row ops.feishu_event%ROWTYPE; previous text;
BEGIN
 SELECT workspace_id,enabled INTO ws,live FROM config.feishu_connection
 WHERE id=p_connection AND workspace_id=common.current_workspace_id() FOR UPDATE;
 IF ws IS NULL OR NOT security.authorization_has('feishu.recover') THEN
  RAISE insufficient_privilege;
 END IF;
 IF live OR EXISTS(SELECT 1 FROM ops.feishu_event WHERE connection_id=p_connection AND status='running') THEN
  RAISE EXCEPTION 'Pause and wait for running operations before recovery' USING ERRCODE='22023';
 END IF;
 IF p_note IS NULL OR length(trim(p_note)) NOT BETWEEN 1 AND 1000 THEN
  RAISE EXCEPTION 'Recovery note is required' USING ERRCODE='22023';
 END IF;
 SELECT * INTO event_row FROM ops.feishu_event WHERE id=p_event AND connection_id=p_connection FOR UPDATE;
 IF NOT FOUND THEN RAISE insufficient_privilege; END IF;
 IF p_action='retry' THEN
  IF event_row.status NOT IN ('failed','dead_letter') OR event_row.error_code='TARGET_MIGRATED'
    OR EXISTS(SELECT 1 FROM ops.feishu_delivery WHERE event_id=p_event AND status='unknown') THEN
   RAISE EXCEPTION 'Event is not eligible for retry' USING ERRCODE='22023';
  END IF;
  UPDATE ops.feishu_event SET status='pending',attempts=0,force_remote_check=true,error_code=NULL,completed_at=NULL,
   lease_token=NULL,locked_until=NULL,available_at=clock_timestamp() WHERE id=p_event;
 ELSIF p_action IN ('confirm_sent','suppress') THEN
  SELECT status INTO previous FROM ops.feishu_delivery
   WHERE event_id=p_event AND connection_id=p_connection AND dedupe_key=p_key FOR UPDATE;
  IF previous IS DISTINCT FROM 'unknown' THEN
   RAISE EXCEPTION 'Notification is not awaiting verification' USING ERRCODE='22023';
  END IF;
  UPDATE ops.feishu_delivery SET status=CASE WHEN p_action='confirm_sent' THEN 'sent' ELSE 'failed' END,
   error_code=CASE WHEN p_action='suppress' THEN 'OPERATOR_SUPPRESSED' ELSE NULL END,
   sent_at=CASE WHEN p_action='confirm_sent' THEN clock_timestamp() ELSE sent_at END WHERE dedupe_key=p_key;
 ELSE RAISE EXCEPTION 'Unknown recovery action' USING ERRCODE='22023'; END IF;
 INSERT INTO ops.feishu_config_audit(workspace_id,connection_id,actor_user_ref_id,database_actor,
  action,before_snapshot,after_snapshot)
 VALUES(ws,p_connection,common.current_user_ref_id(),session_user,'recovery',
  jsonb_build_object('event_status',event_row.status,'notification_status',previous),
  jsonb_build_object('event_id',p_event,'action',p_action,'delivery_key',p_key,'note',trim(p_note)));
END $function$
;
