-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V219__history_ownership_authorization.sql.
-- Original SHA-256: 8b234ce4f1c5418f7d7f12cc56d2d0146979a0fa993487def271318e7f1c9967. See shenma_whole_121_manifest.json.
BEGIN;
SET LOCAL check_function_bodies=on;

-- Preserve the original function identity/owner/ACL; grant no new EXECUTE.
CREATE OR REPLACE FUNCTION security.restore_historical_customer_ownership(p_record uuid,p_user uuid,p_team uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r ops.crm_import_record; c crm.customer; o crm.customer_ownership; prior_setting text;
BEGIN
 IF security.management_actor() IS NOT TRUE
  OR current_setting('app.feishu_historical_import',true) IS DISTINCT FROM 'on'
  OR NOT EXISTS(SELECT 1 FROM platform.user_ref u
    WHERE u.id=common.current_user_ref_id() AND u.workspace_id=common.current_workspace_id()
     AND u.status='active' AND u.deleted_at IS NULL)
 THEN RAISE insufficient_privilege; END IF;
 -- Rebuild from current database grants: neither an import flag nor a caller
 -- supplied authorization_context can grant historical ownership restoration.
 PERFORM security.authorization_refresh();
 IF security.authorization_has('history.import') IS NOT TRUE
 THEN RAISE insufficient_privilege; END IF;
 SELECT x.* INTO r FROM ops.crm_import_record x JOIN ops.crm_import_batch b ON b.id=x.batch_id AND b.workspace_id=x.workspace_id
 WHERE x.id=p_record AND x.workspace_id=common.current_workspace_id() AND x.object_kind='customer'
  AND x.status='approved' AND b.status IN ('approved','applying') FOR UPDATE OF x;
 IF NOT FOUND OR jsonb_typeof(r.normalized->'source_owner_names') IS DISTINCT FROM 'array'
  OR jsonb_array_length(r.normalized->'source_owner_names')<>1
  OR NULLIF(btrim(r.normalized->'source_owner_names'->>0),'') IS NULL THEN RAISE insufficient_privilege; END IF;
 IF NOT EXISTS(SELECT 1 FROM platform.user_ref u JOIN platform.team_membership tm ON tm.user_ref_id=u.id AND tm.workspace_id=u.workspace_id
  JOIN platform.team t ON t.id=tm.team_id AND t.workspace_id=tm.workspace_id
  WHERE u.id=p_user AND u.workspace_id=r.workspace_id AND u.status='active' AND u.deleted_at IS NULL
   AND upper(u.account_code)=upper(r.normalized->>'current_owner_account') AND tm.team_id=p_team
   AND tm.membership_role IN ('sales','supervisor','manager')
   AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
   AND t.status='active' AND t.deleted_at IS NULL AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to
   AND EXISTS(SELECT 1 FROM platform.role_binding rb WHERE rb.workspace_id=u.workspace_id AND rb.user_ref_id=u.id
    AND (rb.team_id IS NULL OR rb.team_id=tm.team_id)
    AND rb.role_code=tm.membership_role
    AND clock_timestamp()>=rb.valid_from AND clock_timestamp()<rb.valid_to)) THEN RAISE insufficient_privilege; END IF;
 SELECT * INTO c FROM crm.customer WHERE id=r.target_id AND workspace_id=r.workspace_id AND deleted_at IS NULL FOR UPDATE;
 IF NOT FOUND THEN RAISE insufficient_privilege; END IF;
 SELECT * INTO o FROM crm.customer_ownership WHERE customer_id=c.id AND workspace_id=r.workspace_id FOR UPDATE;
 IF NOT FOUND THEN RAISE insufficient_privilege; END IF;
 IF o.state<>'unclaimed' OR o.owner_user_ref_id IS NOT NULL OR c.owner_user_ref_id IS NOT NULL THEN
  RETURN jsonb_build_object('status','preserved_existing_ownership','customer_id',c.id);
 END IF;
 IF EXISTS(SELECT 1 FROM crm.customer_claim_request WHERE customer_id=c.id AND workspace_id=c.workspace_id AND status='pending') THEN
  RAISE EXCEPTION 'pending customer claim requires explicit reconciliation' USING ERRCODE='40001';
 END IF;
 IF c.import_meta->>'import_record_id' IS DISTINCT FROM r.id::text
  AND (r.expected_target_version IS NULL OR c.version_no<>r.expected_target_version) THEN
  RAISE EXCEPTION 'customer changed after history ownership review' USING ERRCODE='40001';
 END IF;
 UPDATE crm.customer_ownership SET state='claimed',owner_user_ref_id=p_user,version_no=version_no+1,updated_at=clock_timestamp()
  WHERE customer_id=c.id AND workspace_id=c.workspace_id;
 prior_setting:=current_setting('app.ownership_transition',true);
 PERFORM set_config('app.ownership_transition','on',true);
 UPDATE crm.customer SET owner_user_ref_id=p_user,owner_team_id=p_team WHERE id=c.id AND workspace_id=c.workspace_id;
 PERFORM set_config('app.ownership_transition',COALESCE(prior_setting,''),true);
 INSERT INTO crm.customer_ownership_event(workspace_id,customer_id,event_type,owner_user_ref_id,actor_user_ref_id,reason,import_record_id)
 VALUES(c.workspace_id,c.id,'history_restored',p_user,common.current_user_ref_id(),'历史源唯一在职销售归属恢复；非认领审批',r.id);
 RETURN jsonb_build_object('status','history_restored','customer_id',c.id,'owner_user_ref_id',p_user,'owner_team_id',p_team);
END $$;

INSERT INTO ops.schema_migration(version,description)
 VALUES('V367','Require current history authorization at direct ownership restoration entry');
COMMIT;
