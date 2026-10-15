-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V186__legacy_target_current_applicant.sql.
-- Original SHA-256: 55902048444a537c8bdc77ec2632f128b5f97b132886c705522dc8233b6d970b. See shenma_whole_121_manifest.json.
BEGIN;
-- Recheck current applicant qualification; immutable historical requests stay unchanged.
-- Person target scopes use current active memberships, matching authorization_subject.
CREATE OR REPLACE FUNCTION security.review_target_change(p_request uuid, p_decision text, p_reason text)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE req crm.sales_target_change_request; target crm.sales_target;
BEGIN
 IF NOT security.authorization_has('target.approve') THEN RAISE insufficient_privilege; END IF;
 IF p_decision IS NULL OR p_decision NOT IN ('approved','rejected') OR length(COALESCE(p_reason,''))>2000
  OR (p_decision='rejected' AND NULLIF(btrim(p_reason),'') IS NULL)
 THEN RAISE EXCEPTION '驳回必须填写原因' USING ERRCODE='22023'; END IF;
 SELECT t.* INTO target FROM crm.sales_target t JOIN crm.sales_target_change_request r ON r.target_id=t.id
  WHERE r.id=p_request AND t.workspace_id=common.current_workspace_id() AND r.workspace_id=t.workspace_id;
 IF target.id IS NULL THEN RAISE no_data_found; END IF;
 PERFORM security.lock_target_period(target.scope_type,target.user_ref_id,target.team_id,target.department_code,target.period_type,target.period_start);
 SELECT * INTO target FROM crm.sales_target WHERE id=target.id FOR UPDATE;
 SELECT * INTO req FROM crm.sales_target_change_request WHERE id=p_request AND workspace_id=common.current_workspace_id() FOR UPDATE;
 IF req.status<>'pending' THEN RAISE EXCEPTION '申请已处理，请刷新' USING ERRCODE='P0001'; END IF;
 IF NOT EXISTS(SELECT 1 FROM crm.sales_target t WHERE t.id=req.target_id AND t.workspace_id=common.current_workspace_id() AND security.authorization_subject('target.approve',t.scope_type,t.user_ref_id,t.team_id) AND (t.department_code<>'fde' OR security.authorization_has('target.fde_department'))) THEN RAISE insufficient_privilege; END IF;
 IF p_decision='approved' THEN
  IF target.version_no<>req.base_version THEN RAISE EXCEPTION '生效目标已变化，请驳回后重新申请' USING ERRCODE='P0001'; END IF;
  IF NOT security.authorization_subject('target.approve',target.scope_type,target.user_ref_id,target.team_id)
   OR NOT EXISTS(SELECT 1 FROM platform.user_ref u JOIN platform.role_binding b ON b.user_ref_id=u.id AND b.workspace_id=u.workspace_id
    WHERE u.id=req.applicant_user_ref_id AND u.workspace_id=req.workspace_id AND u.status='active' AND u.deleted_at IS NULL
     AND clock_timestamp()>=b.valid_from AND clock_timestamp()<b.valid_to)
   OR NOT security.authorization_user_allows('target.submit',req.workspace_id,req.applicant_user_ref_id,
     target.user_ref_id,ARRAY[target.team_id] || ARRAY(
       SELECT tm.team_id FROM platform.team_membership tm JOIN platform.team t ON t.id=tm.team_id AND t.workspace_id=tm.workspace_id
       WHERE target.scope_type='person' AND tm.user_ref_id=target.user_ref_id AND tm.workspace_id=req.workspace_id
        AND t.status='active' AND t.deleted_at IS NULL
        AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to
        AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to),
     target.user_ref_id=req.applicant_user_ref_id)
  THEN RAISE EXCEPTION '申请对象或申请人已停用，不能批准' USING ERRCODE='P0001'; END IF;
  IF EXISTS(SELECT 1 FROM crm.sales_target_batch_request r WHERE r.workspace_id=target.workspace_id
   AND r.scope_type=target.scope_type AND r.user_ref_id IS NOT DISTINCT FROM target.user_ref_id AND r.team_id IS NOT DISTINCT FROM target.team_id
   AND r.department_code=target.department_code AND r.period_type=target.period_type AND r.period_start=target.period_start AND r.status='pending'
   AND EXISTS(SELECT 1 FROM jsonb_array_elements(r.items) i WHERE i->>'kind'=target.kind))
  THEN RAISE EXCEPTION '存在新版整单申请，请先处理冲突申请' USING ERRCODE='P0001'; END IF;
  UPDATE crm.sales_target SET amount=req.proposed_amount,updated_by_user_ref_id=common.current_user_ref_id(),
   updated_at=clock_timestamp(),version_no=version_no+1,change_reason='历史单指标申请获批'||CASE WHEN NULLIF(btrim(p_reason),'') IS NULL THEN '' ELSE '：'||btrim(p_reason) END,
   source='approved' WHERE id=target.id RETURNING * INTO target;
 END IF;
 UPDATE crm.sales_target_change_request SET status=p_decision,reviewer_user_ref_id=common.current_user_ref_id(),
  reviewed_at=clock_timestamp(),decision_reason=COALESCE(btrim(p_reason),'') WHERE id=req.id RETURNING * INTO req;
 RETURN jsonb_build_object('status',p_decision,'target',to_jsonb(target),'request',to_jsonb(req));
END $function$
;

CREATE OR REPLACE FUNCTION security.review_target_batch(p_request uuid, p_decision text, p_reason text)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE req crm.sales_target_batch_request; target crm.sales_target; item jsonb; effective_items jsonb:='[]'::jsonb;
BEGIN
 IF NOT security.authorization_has('target.approve') THEN RAISE insufficient_privilege; END IF;
 IF p_decision IS NULL OR p_decision NOT IN ('approved','rejected') OR length(COALESCE(p_reason,''))>2000
  OR (p_decision='rejected' AND NULLIF(btrim(p_reason),'') IS NULL)
 THEN RAISE EXCEPTION '审批结论无效，驳回必须填写原因' USING ERRCODE='22023'; END IF;
 SELECT * INTO req FROM crm.sales_target_batch_request WHERE id=p_request AND workspace_id=common.current_workspace_id();
 IF req.id IS NULL THEN RAISE no_data_found; END IF;
 PERFORM security.lock_target_period(req.scope_type,req.user_ref_id,req.team_id,req.department_code,req.period_type,req.period_start);
 SELECT * INTO req FROM crm.sales_target_batch_request WHERE id=p_request AND workspace_id=common.current_workspace_id() FOR UPDATE;
 IF req.status<>'pending' THEN RAISE EXCEPTION '申请已处理，请刷新' USING ERRCODE='P0001'; END IF;
 IF NOT security.authorization_subject('target.approve',req.scope_type,req.user_ref_id,req.team_id) OR (req.department_code='fde' AND NOT security.authorization_has('target.fde_department')) THEN RAISE insufficient_privilege; END IF;
 IF p_decision='approved' THEN
  IF NOT security.authorization_subject('target.approve',req.scope_type,req.user_ref_id,req.team_id)
   OR NOT EXISTS(SELECT 1 FROM platform.user_ref u JOIN platform.role_binding b ON b.user_ref_id=u.id AND b.workspace_id=u.workspace_id
    WHERE u.id=req.applicant_user_ref_id AND u.workspace_id=req.workspace_id AND u.status='active' AND u.deleted_at IS NULL
     AND clock_timestamp()>=b.valid_from AND clock_timestamp()<b.valid_to)
   OR NOT security.authorization_user_allows('target.submit',req.workspace_id,req.applicant_user_ref_id,
     req.user_ref_id,ARRAY[req.team_id] || ARRAY(
       SELECT tm.team_id FROM platform.team_membership tm JOIN platform.team t ON t.id=tm.team_id AND t.workspace_id=tm.workspace_id
       WHERE req.scope_type='person' AND tm.user_ref_id=req.user_ref_id AND tm.workspace_id=req.workspace_id
        AND t.status='active' AND t.deleted_at IS NULL
        AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to
        AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to),
     req.user_ref_id=req.applicant_user_ref_id)
  THEN RAISE EXCEPTION '申请对象或申请人已停用，不能批准' USING ERRCODE='P0001'; END IF;
  -- Validate every baseline before applying any item; null baseline means still absent.
  FOR item IN SELECT value FROM jsonb_array_elements(req.items) ORDER BY value->>'kind' LOOP
   SELECT * INTO target FROM crm.sales_target WHERE workspace_id=req.workspace_id AND scope_type=req.scope_type
    AND user_ref_id IS NOT DISTINCT FROM req.user_ref_id AND team_id IS NOT DISTINCT FROM req.team_id
    AND department_code=req.department_code AND period_type=req.period_type AND period_start=req.period_start AND kind=item->>'kind' FOR UPDATE;
   IF target.id IS DISTINCT FROM (item->>'target_id')::uuid OR target.version_no IS DISTINCT FROM (item->>'base_version')::integer
   THEN RAISE EXCEPTION '生效目标已变化，请驳回后重新申请' USING ERRCODE='P0001'; END IF;
  END LOOP;
  FOR item IN SELECT value FROM jsonb_array_elements(req.items) ORDER BY value->>'kind' LOOP
   IF item->>'target_id' IS NULL THEN
    INSERT INTO crm.sales_target(workspace_id,target_year,scope_type,user_ref_id,team_id,department_code,kind,amount,
     updated_by_user_ref_id,period_type,period_start,period_end,change_reason,source)
    VALUES(req.workspace_id,extract(year FROM req.period_start)::integer,req.scope_type,req.user_ref_id,req.team_id,req.department_code,
     item->>'kind',(item->>'proposed_amount')::numeric,common.current_user_ref_id(),req.period_type,req.period_start,req.period_end,req.reason,'approved');
   ELSIF item->'previous_amount'<>item->'proposed_amount' THEN
    UPDATE crm.sales_target SET amount=(item->>'proposed_amount')::numeric,version_no=version_no+1,updated_at=clock_timestamp(),
     updated_by_user_ref_id=common.current_user_ref_id(),change_reason=req.reason,source='approved' WHERE id=(item->>'target_id')::uuid;
   END IF;
  END LOOP;
 END IF;
 SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY t.kind),'[]'::jsonb) INTO effective_items FROM crm.sales_target t
  WHERE t.workspace_id=req.workspace_id AND t.scope_type=req.scope_type AND t.user_ref_id IS NOT DISTINCT FROM req.user_ref_id
   AND t.team_id IS NOT DISTINCT FROM req.team_id AND t.department_code=req.department_code AND t.period_type=req.period_type
   AND t.period_start=req.period_start AND EXISTS(SELECT 1 FROM jsonb_array_elements(req.items) i WHERE i->>'kind'=t.kind);
 UPDATE crm.sales_target_batch_request SET status=p_decision,reviewed_at=clock_timestamp(),
  reviewer_user_ref_id=common.current_user_ref_id(),decision_reason=COALESCE(btrim(p_reason),'') WHERE id=req.id RETURNING * INTO req;
 RETURN jsonb_build_object('status',p_decision,'items',effective_items,'request',to_jsonb(req));
END $function$
;

INSERT INTO ops.schema_migration(version,description) VALUES('V334','Current applicant authorization and person-team scope for target approvals');
COMMIT;
