BEGIN;

-- Customer ownership includes approval/legacy-resolution workflow state that is
-- intentionally private. Profile and map aggregation need only the confirmed
-- owner ID of a customer the actor may already read; do not relax ownership RLS.
CREATE FUNCTION security.profile_customer_owner(p_customer uuid) RETURNS uuid
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT o.owner_user_ref_id
 FROM crm.customer_ownership o JOIN crm.customer c ON c.id=o.customer_id AND c.workspace_id=o.workspace_id
 WHERE c.id=p_customer AND c.workspace_id=common.current_workspace_id() AND c.deleted_at IS NULL
  AND o.state='claimed'
  AND (security.management_actor() OR
   (common.current_role_code() IN ('sales','supervisor','manager') AND security.has_active_role(common.current_role_code())))
  AND security.has_customer_access(c.id);
$$;
COMMENT ON FUNCTION security.profile_customer_owner(uuid) IS
 '仅返回当前可读客户已确认认领者ID，供销售主体地图/效率聚合；未认领、待核对、越权及FDE均为空，不开放认领申请或普通业务事实';

INSERT INTO ops.schema_migration(version,description)
 VALUES('V092','销售主体客户集合使用受控的已确认归属投影');
COMMIT;
