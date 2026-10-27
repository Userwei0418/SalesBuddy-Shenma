BEGIN;

-- A local connection ending is not evidence that a supplier job has ended.
-- Release local dispatch capacity without changing unknown outcome or usage.
ALTER TABLE activity.asr_attempt ADD COLUMN transport_closed_at timestamptz;
ALTER TABLE config.asr_capacity_resolution ADD COLUMN resolution_kind text NOT NULL
 DEFAULT 'provider_finished' CHECK(resolution_kind IN ('provider_finished','local_transport_closed'));
COMMENT ON COLUMN activity.asr_attempt.capacity_released_at IS
 'Local dispatch capacity only; does not certify supplier completion or authorize resend';

CREATE FUNCTION security.close_asr_transport(p_operation uuid) RETURNS boolean
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a activity.asr_attempt%ROWTYPE; w uuid:=common.current_workspace_id(); u uuid:=common.current_user_ref_id();
BEGIN
 IF w IS NULL OR u IS NULL OR NOT security.workspace_is_active(w)
 OR NOT (security.authorization_has('visit.transcribe') OR security.authorization_has('ai.config_test')) THEN
  RAISE EXCEPTION 'ASR_SCOPE_DENIED' USING ERRCODE='42501'; END IF;
 SELECT * INTO a FROM activity.asr_attempt WHERE workspace_id=w AND actor_id=u
 AND operation_id=p_operation FOR UPDATE;
 IF NOT FOUND OR a.state='linked' THEN RETURN false; END IF;
 IF a.transport_closed_at IS NOT NULL THEN RETURN true; END IF;
 -- Invoked only after the server-side client.close() returned successfully.
 -- A missing outcome receipt remains unknown, never assumed failed/completed.
 IF a.state IN ('sending','unknown') AND a.capacity_released_at IS NULL THEN
  INSERT INTO config.asr_capacity_resolution(workspace_id,operation_id,owner_id,resolved_by,evidence,resolution_kind)
  VALUES(w,p_operation,u,u,'Server ASR client close completed; supplier outcome remains unknown','local_transport_closed')
  ON CONFLICT DO NOTHING;
 END IF;
 UPDATE activity.asr_attempt SET transport_closed_at=clock_timestamp(),
  capacity_released_at=COALESCE(capacity_released_at,clock_timestamp()),
  state=CASE WHEN state='sending' THEN 'unknown' ELSE state END,
  error_code=CASE WHEN state='sending' THEN 'ASR_RESULT_UNKNOWN' ELSE error_code END
 WHERE workspace_id=w AND actor_id=u AND operation_id=p_operation;
 RETURN true;
END $$;

CREATE FUNCTION security.release_asr_capacity(p_operation uuid,p_owner uuid,p_evidence text,p_kind text) RETURNS boolean
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE a activity.asr_attempt%ROWTYPE;
BEGIN
 IF NOT security.workspace_is_active(common.current_workspace_id()) OR NOT security.authorization_has('access.console')
 OR NOT security.authorization_has('ai.usage_rules_manage') THEN RAISE EXCEPTION 'ASR_SCOPE_DENIED' USING ERRCODE='42501'; END IF;
 IF p_kind IS NULL OR p_kind NOT IN ('provider_finished','local_transport_closed') OR p_evidence IS NULL
 OR length(btrim(p_evidence))<10 OR length(p_evidence)>500 THEN
  RAISE EXCEPTION 'ASR_RESOLUTION_EVIDENCE_REQUIRED' USING ERRCODE='22023'; END IF;
 SELECT * INTO a FROM activity.asr_attempt WHERE workspace_id=common.current_workspace_id()
 AND actor_id=p_owner AND operation_id=p_operation FOR UPDATE;
 IF FOUND AND a.capacity_released_at IS NOT NULL THEN RETURN true; END IF;
 IF NOT FOUND THEN RETURN false; END IF;
 -- No age-based automatic release. An operator must confirm the old process
 -- and connection ended. Recent sending requests remain ineligible, even if
 -- the caller claims a local close; stale sends use the existing 10-minute
 -- unknown boundary so a dead process does not depend on another ASR request.
 IF NOT (a.state='unknown' OR (a.state='sending'
  AND a.created_at<clock_timestamp()-interval '10 minutes')) THEN RETURN false; END IF;
 INSERT INTO config.asr_capacity_resolution(workspace_id,operation_id,owner_id,resolved_by,evidence,resolution_kind)
 VALUES(a.workspace_id,p_operation,p_owner,common.current_user_ref_id(),p_evidence,p_kind) ON CONFLICT DO NOTHING;
 UPDATE activity.asr_attempt SET capacity_released_at=clock_timestamp(),state='unknown',error_code='ASR_RESULT_UNKNOWN',
  transport_closed_at=CASE WHEN p_kind='local_transport_closed' THEN COALESCE(transport_closed_at,clock_timestamp()) ELSE transport_closed_at END
 WHERE workspace_id=a.workspace_id AND actor_id=p_owner AND operation_id=p_operation;
 RETURN true;
END $$;

-- Old clients continue to assert supplier completion; they cannot silently use
-- the distinct local-transport basis introduced by this migration.
CREATE OR REPLACE FUNCTION security.release_asr_capacity(p_operation uuid,p_owner uuid,p_evidence text) RETURNS boolean
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 RETURN security.release_asr_capacity(p_operation,p_owner,p_evidence,'provider_finished');
END $$;

CREATE OR REPLACE FUNCTION security.asr_unknown_attempts() RETURNS jsonb
 LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF NOT security.workspace_is_active(common.current_workspace_id()) OR NOT security.authorization_has('access.console')
 OR NOT security.authorization_has('ai.usage_rules_manage') THEN RAISE EXCEPTION 'ASR_SCOPE_DENIED' USING ERRCODE='42501'; END IF;
 RETURN COALESCE((SELECT jsonb_agg(jsonb_build_object('operation_id',a.operation_id,'actor_id',a.actor_id,
 'error_code',a.error_code,'capacity_released_at',a.capacity_released_at,'transport_closed_at',a.transport_closed_at,
 'resolution_kind',a.resolution_kind,'capacity_scope','local_dispatch',
 'created_at',a.created_at,'audio_seconds',a.audio_seconds,'state',a.state)) FROM (
 SELECT x.*,r.resolution_kind FROM activity.asr_attempt x LEFT JOIN config.asr_capacity_resolution r
 ON (r.workspace_id,r.owner_id,r.operation_id)=(x.workspace_id,x.actor_id,x.operation_id)
 WHERE x.workspace_id=common.current_workspace_id()
 AND (x.state='unknown' OR (x.state='sending' AND x.created_at<clock_timestamp()-interval '10 minutes'))
 ORDER BY (x.capacity_released_at IS NULL) DESC,x.created_at DESC LIMIT 100) a),'[]'::jsonb);
END $$;
REVOKE ALL ON FUNCTION security.close_asr_transport(uuid),security.release_asr_capacity(uuid,uuid,text,text) FROM PUBLIC;
INSERT INTO ops.schema_migration(version,description)
 VALUES('V432','Separate ASR local transport capacity from unknown provider outcome');
COMMIT;
