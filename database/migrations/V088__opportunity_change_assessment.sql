-- Update only the existing recipients of one human-confirmed opportunity change.
-- Historical cards have no pending marker and are never retroactively re-assessed.
CREATE FUNCTION workflow.complete_opportunity_change_review(p_event uuid,p_review jsonb) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE v_workspace uuid; v_count integer;
BEGIN
 IF p_review->>'status' IS DISTINCT FROM 'completed'
    OR COALESCE(p_review->>'color','') NOT IN ('green','yellow','red','gray')
    OR COALESCE(p_review->>'source','') NOT IN ('agent_platform','rules')
    OR length(COALESCE(p_review->>'summary','')) NOT BETWEEN 1 AND 240 THEN
  RAISE EXCEPTION 'invalid opportunity change assessment' USING ERRCODE='22023';
 END IF;
 SELECT workspace_id INTO v_workspace FROM crm.business_change
 WHERE id=p_event AND workspace_id=common.current_workspace_id()
   AND actor_user_ref_id=common.current_user_ref_id() AND opportunity_id IS NOT NULL
   AND security.has_opportunity_access(opportunity_id);
 IF v_workspace IS NULL THEN RAISE EXCEPTION 'change assessment outside current scope' USING ERRCODE='42501'; END IF;
 UPDATE workflow.notification SET payload=payload || jsonb_build_object('change_review',p_review),
   title=CASE p_review->>'color' WHEN 'green' THEN '商机变化向好' WHEN 'yellow' THEN '商机变化需关注'
          WHEN 'red' THEN '商机变化转差' ELSE '商机信息已更新' END
 WHERE workspace_id=v_workspace AND template_code='business_changed'
   AND payload->>'event_id'=p_event::text AND payload->'change_review'->>'status'='pending';
 GET DIAGNOSTICS v_count=ROW_COUNT;
 RETURN v_count;
END $$;
REVOKE ALL ON FUNCTION workflow.complete_opportunity_change_review(uuid,jsonb) FROM PUBLIC;
DO $$ DECLARE r record; BEGIN
 FOR r IN SELECT DISTINCT grantee FROM information_schema.role_table_grants
 WHERE table_schema='agent' AND table_name='inference_operation' AND privilege_type='INSERT' AND grantee<>'PUBLIC'
 LOOP EXECUTE format('GRANT EXECUTE ON FUNCTION workflow.complete_opportunity_change_review(uuid,jsonb) TO %I',r.grantee); END LOOP;
END $$;
