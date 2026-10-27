BEGIN;
SET LOCAL check_function_bodies=on;

-- A roster-only grant must not open the general business_change INSERT policy.
-- The database performs the mutation and derives its history in the same call;
-- callers cannot supply an actor, customer, tenant, event version or JSON diff.
CREATE FUNCTION security.set_fde_members_with_history(
 p_opportunity uuid,p_expected_version integer,p_members uuid[]) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
 target crm.opportunity%ROWTYPE;
 actor_id uuid:=common.current_user_ref_id();
 actor_workspace uuid:=common.current_workspace_id();
 before_ids uuid[];
 desired_ids uuid[];
 member_id uuid;
 current_version integer;
 observed_at timestamptz;
BEGIN
 IF actor_id IS NULL OR actor_workspace IS NULL
  OR NOT security.workspace_is_active(actor_workspace)
  OR NOT security.can_manage_fde_members(p_opportunity)
  OR NOT security.authorization_opportunity('opportunity.read',p_opportunity)
 THEN RAISE insufficient_privilege; END IF;
 IF p_members IS NULL OR cardinality(p_members)>30 OR array_position(p_members,NULL) IS NOT NULL
 THEN RAISE EXCEPTION '请选择最多30位有效FDE成员' USING ERRCODE='22023'; END IF;

 -- Preserve V366's finance-before-aggregate lock order. Recheck permission
 -- after waiting, before changing any data or returning an existing version.
 PERFORM security.finance_write_lock();
 SELECT o.* INTO target FROM crm.opportunity o
 WHERE o.id=p_opportunity AND o.workspace_id=actor_workspace AND o.deleted_at IS NULL
 FOR UPDATE;
 IF NOT FOUND OR NOT security.can_manage_fde_members(p_opportunity)
  OR NOT security.authorization_opportunity('opportunity.read',p_opportunity)
 THEN RAISE insufficient_privilege; END IF;
 IF target.version_no IS DISTINCT FROM p_expected_version
 THEN RAISE EXCEPTION '商机已变化，请刷新后重试' USING ERRCODE='40001'; END IF;

 observed_at:=clock_timestamp();
 SELECT COALESCE(array_agg(p.user_ref_id ORDER BY p.user_ref_id),'{}'::uuid[])
 INTO before_ids FROM crm.opportunity_participant p
 WHERE p.opportunity_id=target.id AND p.workspace_id=target.workspace_id
  AND p.participant_role='fde' AND observed_at>=p.valid_from AND observed_at<p.valid_to;
 SELECT COALESCE(array_agg(value ORDER BY value),'{}'::uuid[]) INTO desired_ids
 FROM (SELECT DISTINCT unnest(p_members) AS value) members;

 -- Existing disabled members may be retained or removed, but not newly added.
 IF EXISTS(SELECT 1 FROM unnest(desired_ids) member
  WHERE NOT member=ANY(before_ids) AND NOT security.fde_user_is_active(member))
 THEN RAISE EXCEPTION 'FDE成员不存在、已停用或不属于有效FDE部门' USING ERRCODE='23514'; END IF;
 IF target.primary_fde_id IS NOT NULL AND NOT target.primary_fde_id=ANY(desired_ids)
 THEN RAISE EXCEPTION '主责FDE须保留在长期名单中，请一并更换或清空主责' USING ERRCODE='23514'; END IF;
 IF before_ids=desired_ids THEN RETURN target.version_no; END IF;

 current_version:=security.bump_fde_membership_version(target.id,p_expected_version);
 FOREACH member_id IN ARRAY desired_ids LOOP
  IF NOT member_id=ANY(before_ids) THEN
   INSERT INTO crm.opportunity_participant
    (opportunity_id,user_ref_id,workspace_id,participant_role,assigned_by_user_ref_id,source_code)
   VALUES(target.id,member_id,target.workspace_id,'fde',actor_id,'manual');
  END IF;
 END LOOP;
 FOREACH member_id IN ARRAY before_ids LOOP
  IF NOT member_id=ANY(desired_ids) THEN
   UPDATE crm.opportunity_participant p SET valid_to=clock_timestamp(),
    ended_by_user_ref_id=actor_id,end_reason='manual'
   WHERE p.opportunity_id=target.id AND p.workspace_id=target.workspace_id
    AND p.user_ref_id=member_id AND p.participant_role='fde'
    AND observed_at>=p.valid_from AND observed_at<p.valid_to;
  END IF;
 END LOOP;
 INSERT INTO crm.business_change
  (workspace_id,customer_id,opportunity_id,actor_user_ref_id,version_no,changes)
 VALUES(target.workspace_id,target.customer_id,target.id,actor_id,current_version,
  jsonb_build_array(jsonb_build_object('field','fde_members','label','长期FDE名单',
   'before',to_jsonb(before_ids),'after',to_jsonb(desired_ids))));
 RETURN current_version;
END $$;
REVOKE ALL ON FUNCTION security.set_fde_members_with_history(uuid,integer,uuid[]) FROM PUBLIC;
COMMENT ON FUNCTION security.set_fde_members_with_history(uuid,integer,uuid[]) IS
 'Atomic legacy FDE roster update and database-derived history; requires scoped fde_members, never general opportunity.update';

INSERT INTO ops.schema_migration(version,description)
 VALUES('V428','Record legacy FDE membership changes through a scoped atomic mutation');
COMMIT;
