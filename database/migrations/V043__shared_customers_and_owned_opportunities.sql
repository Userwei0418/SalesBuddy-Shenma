BEGIN;
ALTER TABLE crm.customer ADD CONSTRAINT customer_id_workspace_unique UNIQUE(id,workspace_id);
ALTER TABLE platform.user_ref ADD CONSTRAINT user_id_workspace_unique UNIQUE(id,workspace_id);
CREATE TABLE crm.customer_sales_member (
 workspace_id uuid NOT NULL,
 customer_id uuid NOT NULL,
 user_ref_id uuid NOT NULL,
 added_by_user_ref_id uuid,
 joined_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(customer_id,user_ref_id),
 FOREIGN KEY(customer_id,workspace_id) REFERENCES crm.customer(id,workspace_id),
 FOREIGN KEY(user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id),
 FOREIGN KEY(added_by_user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id)
);
CREATE INDEX customer_sales_member_user ON crm.customer_sales_member(workspace_id,user_ref_id,customer_id);
COMMENT ON TABLE crm.customer_sales_member IS '客户与负责销售多对多；认领直接加入名单，不覆盖其他销售，不扩大商机可见范围';
COMMENT ON COLUMN crm.customer.owner_user_ref_id IS '兼容历史主要负责人；完整负责销售名单查询 customer_sales_member，不能据此判断唯一归属';
INSERT INTO crm.customer_sales_member(workspace_id,customer_id,user_ref_id,added_by_user_ref_id)
 SELECT c.workspace_id,c.id,c.owner_user_ref_id,c.created_by_user_ref_id FROM crm.customer c
 JOIN platform.user_ref u ON u.id=c.owner_user_ref_id AND u.workspace_id=c.workspace_id;
INSERT INTO crm.customer_sales_member(workspace_id,customer_id,user_ref_id,added_by_user_ref_id)
 SELECT DISTINCT o.workspace_id,o.customer_id,o.owner_user_ref_id,o.owner_user_ref_id FROM crm.opportunity o
 JOIN crm.customer c ON c.id=o.customer_id AND c.workspace_id=o.workspace_id
 JOIN platform.user_ref u ON u.id=o.owner_user_ref_id AND u.workspace_id=o.workspace_id
 WHERE o.deleted_at IS NULL ON CONFLICT DO NOTHING;

CREATE FUNCTION security.supervises_team(p_team_id uuid) RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT common.current_role_code()='supervisor' AND security.has_active_role('supervisor') AND EXISTS(
  SELECT 1 FROM platform.team_membership tm WHERE tm.workspace_id=common.current_workspace_id()
   AND tm.user_ref_id=common.current_user_ref_id() AND tm.team_id=p_team_id AND tm.membership_role='supervisor'
   AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to);
$$;
CREATE FUNCTION security.opportunity_owner_in_scope(p_user_id uuid,p_team_id uuid) RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT (common.current_role_code()='manager' AND security.has_active_role('manager'))
 OR (common.current_role_code()='sales' AND security.has_active_role('sales') AND p_user_id=common.current_user_ref_id())
 OR security.supervises_team(p_team_id);
$$;
CREATE OR REPLACE FUNCTION security.has_customer_access(p_customer_id uuid) RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM crm.customer c WHERE c.id=p_customer_id
 AND c.workspace_id=common.current_workspace_id() AND c.deleted_at IS NULL AND (
  (common.current_role_code()='manager' AND security.has_active_role('manager')) OR
  (security.supervises_team(c.owner_team_id) OR EXISTS(
   SELECT 1 FROM crm.customer_sales_member m JOIN platform.team_membership tm ON tm.user_ref_id=m.user_ref_id
    AND tm.workspace_id=m.workspace_id AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
   WHERE m.customer_id=c.id AND security.supervises_team(tm.team_id))) OR
  (common.current_role_code()='sales' AND security.has_active_role('sales') AND EXISTS(
   SELECT 1 FROM crm.customer_sales_member m WHERE m.customer_id=c.id AND m.workspace_id=c.workspace_id
    AND m.user_ref_id=common.current_user_ref_id()))));
$$;
CREATE FUNCTION security.can_claim_customer(p_customer_id uuid) RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT common.current_role_code()='sales' AND security.has_active_role('sales') AND EXISTS(
 SELECT 1 FROM crm.customer c JOIN platform.team_membership tm ON tm.team_id=c.owner_team_id
  AND tm.workspace_id=c.workspace_id AND tm.user_ref_id=common.current_user_ref_id()
  AND tm.membership_role='sales' AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
 WHERE c.id=p_customer_id AND c.workspace_id=common.current_workspace_id() AND c.deleted_at IS NULL);
$$;
CREATE FUNCTION security.has_opportunity_access(p_opportunity_id uuid) RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM crm.opportunity o WHERE o.id=p_opportunity_id
 AND o.workspace_id=common.current_workspace_id() AND o.deleted_at IS NULL
 AND security.has_customer_access(o.customer_id)
 AND security.opportunity_owner_in_scope(o.owner_user_ref_id,o.owner_team_id));
$$;
ALTER TABLE crm.customer_sales_member ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm.customer_sales_member FORCE ROW LEVEL SECURITY;
CREATE POLICY member_read ON crm.customer_sales_member FOR SELECT
 USING(workspace_id=common.current_workspace_id() AND security.has_customer_access(customer_id));
CREATE OR REPLACE FUNCTION crm.attach_customer_owner() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF NEW.owner_user_ref_id IS NOT NULL THEN
  INSERT INTO crm.customer_sales_member(workspace_id,customer_id,user_ref_id,added_by_user_ref_id)
  VALUES(NEW.workspace_id,NEW.id,NEW.owner_user_ref_id,NEW.created_by_user_ref_id) ON CONFLICT DO NOTHING;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER attach_customer_owner AFTER INSERT OR UPDATE OF owner_user_ref_id ON crm.customer
 FOR EACH ROW EXECUTE FUNCTION crm.attach_customer_owner();

CREATE FUNCTION security.add_customer_sales_member(p_customer_id uuid,p_user_id uuid) RETURNS boolean
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE c crm.customer; added integer;
BEGIN
 IF NOT security.has_customer_access(p_customer_id) OR NOT (
  (common.current_role_code()='manager' AND security.has_active_role('manager')) OR
  (common.current_role_code()='supervisor' AND security.has_active_role('supervisor'))) THEN
  RAISE EXCEPTION '无权添加客户负责销售' USING ERRCODE='42501';
 END IF;
 SELECT * INTO c FROM crm.customer WHERE id=p_customer_id FOR UPDATE;
 IF NOT EXISTS(SELECT 1 FROM platform.user_ref u JOIN platform.role_binding rb ON rb.user_ref_id=u.id
  WHERE u.id=p_user_id AND u.workspace_id=c.workspace_id AND u.status='active' AND u.deleted_at IS NULL
   AND rb.workspace_id=c.workspace_id AND rb.role_code='sales' AND clock_timestamp()>=rb.valid_from
   AND clock_timestamp()<rb.valid_to AND (common.current_role_code()='manager' OR EXISTS(
    SELECT 1 FROM platform.team_membership tm WHERE tm.user_ref_id=p_user_id AND security.supervises_team(tm.team_id)
     AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to))) THEN
  RAISE EXCEPTION '请选择权限范围内的在职销售' USING ERRCODE='42501';
 END IF;
 INSERT INTO crm.customer_sales_member(workspace_id,customer_id,user_ref_id,added_by_user_ref_id)
 VALUES(c.workspace_id,c.id,p_user_id,common.current_user_ref_id()) ON CONFLICT DO NOTHING;
 GET DIAGNOSTICS added=ROW_COUNT;
 UPDATE crm.customer SET owner_user_ref_id=COALESCE(owner_user_ref_id,p_user_id),
  lifecycle_status=CASE WHEN lifecycle_status='lead' THEN 'prospect' ELSE lifecycle_status END,
  version_no=version_no+CASE WHEN added>0 THEN 1 ELSE 0 END,updated_at=clock_timestamp() WHERE id=c.id AND added>0;
 RETURN added>0;
END $$;

CREATE FUNCTION security.claim_customer(p_customer_id uuid) RETURNS jsonb
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE c crm.customer; added integer;
BEGIN
 IF NOT security.can_claim_customer(p_customer_id) THEN
  RAISE EXCEPTION '仅可认领所在部门的客户' USING ERRCODE='42501';
 END IF;
 SELECT * INTO c FROM crm.customer WHERE id=p_customer_id FOR UPDATE;
 INSERT INTO crm.customer_sales_member(workspace_id,customer_id,user_ref_id,added_by_user_ref_id)
 VALUES(c.workspace_id,c.id,common.current_user_ref_id(),common.current_user_ref_id()) ON CONFLICT DO NOTHING;
 GET DIAGNOSTICS added=ROW_COUNT;
 -- Preserve the first owner as a compatibility label; membership is authoritative.
 UPDATE crm.customer SET owner_user_ref_id=COALESCE(owner_user_ref_id,common.current_user_ref_id()),
  lifecycle_status=CASE WHEN lifecycle_status='lead' THEN 'prospect' ELSE lifecycle_status END,
  updated_at=clock_timestamp(),version_no=version_no+CASE WHEN added>0 THEN 1 ELSE 0 END WHERE id=c.id AND added>0;
 RETURN jsonb_build_object('customer_id',c.id::text,'customer_name',c.name,'claimed',true,'added',added>0);
END $$;
CREATE FUNCTION security.customer_claim_pool(p_query text,p_limit integer) RETURNS SETOF jsonb
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('id',c.id::text,'name',c.name,'industry_code',c.industry_code,
  'level_code',c.level_code,'customer_type_code',c.customer_type_code,'team_name',t.name,
  'owner_name',members.names,'sales_members',members.people,
  'claimed',EXISTS(SELECT 1 FROM crm.customer_sales_member m WHERE m.customer_id=c.id AND m.user_ref_id=common.current_user_ref_id()))
 FROM crm.customer c LEFT JOIN platform.team t ON t.id=c.owner_team_id
 LEFT JOIN LATERAL(SELECT string_agg(u.display_name,'、' ORDER BY u.display_name) AS names,
  jsonb_agg(jsonb_build_object('id',u.id::text,'name',u.display_name) ORDER BY u.display_name) AS people
  FROM crm.customer_sales_member m JOIN platform.user_ref u ON u.id=m.user_ref_id AND u.workspace_id=m.workspace_id
  WHERE m.customer_id=c.id) members ON true
 WHERE security.can_claim_customer(c.id) AND (NULLIF(btrim(p_query),'') IS NULL
  OR c.name ILIKE '%'||p_query||'%' OR members.names ILIKE '%'||p_query||'%' OR t.name ILIKE '%'||p_query||'%')
 ORDER BY c.name,c.id LIMIT LEAST(GREATEST(p_limit,1),1000);
$$;
DO $$ DECLARE r record; BEGIN
 FOR r IN SELECT DISTINCT grantee FROM information_schema.role_table_grants
 WHERE table_schema='crm' AND table_name='customer' AND privilege_type='SELECT' AND grantee<>'PUBLIC'
 LOOP EXECUTE format('GRANT SELECT ON crm.customer_sales_member TO %I',r.grantee); END LOOP;
END $$;
REVOKE ALL ON crm.customer_sales_member FROM PUBLIC;

DROP POLICY customer_scope ON crm.customer;
CREATE POLICY customer_scope ON crm.customer USING(security.has_customer_access(id)) WITH CHECK(
 workspace_id=common.current_workspace_id() AND (security.has_active_role('manager') OR security.has_active_role('supervisor')
 OR owner_user_ref_id=common.current_user_ref_id() OR security.has_customer_access(id)));
DROP POLICY customer_object_scope ON crm.opportunity;
CREATE POLICY opportunity_owner_scope ON crm.opportunity USING(
 workspace_id=common.current_workspace_id() AND deleted_at IS NULL AND security.has_customer_access(customer_id)
 AND security.opportunity_owner_in_scope(owner_user_ref_id,owner_team_id)) WITH CHECK(
 workspace_id=common.current_workspace_id() AND security.has_customer_access(customer_id)
 AND security.opportunity_owner_in_scope(owner_user_ref_id,owner_team_id));
-- Restrictive policies intersect every existing policy, including historical broad customer-based rules.
CREATE POLICY visit_opportunity_boundary ON activity.visit AS RESTRICTIVE
 USING(opportunity_id IS NULL OR security.has_opportunity_access(opportunity_id))
 WITH CHECK(opportunity_id IS NULL OR security.has_opportunity_access(opportunity_id));
CREATE POLICY action_opportunity_boundary ON activity.action_item AS RESTRICTIVE
 USING(opportunity_id IS NULL OR security.has_opportunity_access(opportunity_id))
 WITH CHECK(opportunity_id IS NULL OR security.has_opportunity_access(opportunity_id));
CREATE POLICY change_opportunity_boundary ON crm.business_change AS RESTRICTIVE
 USING(opportunity_id IS NULL OR security.has_opportunity_access(opportunity_id))
 WITH CHECK(opportunity_id IS NULL OR security.has_opportunity_access(opportunity_id));
CREATE POLICY actual_opportunity_boundary ON crm.customer_actual AS RESTRICTIVE
 USING(opportunity_id IS NULL OR security.has_opportunity_access(opportunity_id))
 WITH CHECK(opportunity_id IS NULL OR security.has_opportunity_access(opportunity_id));
CREATE POLICY risk_sales_boundary ON insight.risk AS RESTRICTIVE
 USING((common.current_role_code()<>'sales' OR owner_user_ref_id=common.current_user_ref_id())
 AND (opportunity_id IS NULL OR security.has_opportunity_access(opportunity_id)))
 WITH CHECK((common.current_role_code()<>'sales' OR owner_user_ref_id=common.current_user_ref_id())
 AND (opportunity_id IS NULL OR security.has_opportunity_access(opportunity_id)));
CREATE POLICY task_opportunity_boundary ON workflow.task AS RESTRICTIVE
 USING(opportunity_id IS NULL OR security.has_opportunity_access(opportunity_id))
 WITH CHECK(opportunity_id IS NULL OR security.has_opportunity_access(opportunity_id));

-- Customer-wide cached AI conclusions cannot expose a colleague's opportunity facts to a new claimant.
ALTER TABLE insight.quadrant_score ADD COLUMN subject_user_ref_id uuid REFERENCES platform.user_ref(id);
UPDATE insight.quadrant_score q SET subject_user_ref_id=c.owner_user_ref_id FROM crm.customer c WHERE c.id=q.customer_id;
DROP INDEX insight.uq_quadrant_current;
CREATE UNIQUE INDEX uq_quadrant_current ON insight.quadrant_score(customer_id,subject_user_ref_id) NULLS NOT DISTINCT
 WHERE valid_to='infinity';
CREATE POLICY quadrant_sales_boundary ON insight.quadrant_score AS RESTRICTIVE
 USING(common.current_role_code()<>'sales' OR subject_user_ref_id=common.current_user_ref_id())
 WITH CHECK(subject_user_ref_id=common.current_user_ref_id());
COMMENT ON COLUMN insight.quadrant_score.subject_user_ref_id IS '评分依据该账号可见事实；一线仅看本人评估，经理按权限读取最近评估';
CREATE OR REPLACE VIEW crm.v_customer_current_quadrant WITH(security_invoker=true) AS
 SELECT c.id,c.workspace_id,c.name,c.industry_code,c.customer_type_code,c.lifecycle_status,c.level_code,
  c.owner_user_ref_id,c.owner_team_id,qs.potential_score,qs.relationship_score,qs.quadrant_code,
  qs.calculated_at AS quadrant_calculated_at
 FROM crm.customer c LEFT JOIN LATERAL(SELECT q.* FROM insight.quadrant_score q WHERE q.customer_id=c.id
  AND q.workspace_id=c.workspace_id AND q.valid_to='infinity' ORDER BY q.calculated_at DESC,q.id DESC LIMIT 1) qs ON true
 WHERE c.deleted_at IS NULL;
ALTER TABLE insight.recommendation ADD COLUMN subject_user_ref_id uuid REFERENCES platform.user_ref(id);
UPDATE insight.recommendation r SET subject_user_ref_id=c.owner_user_ref_id FROM crm.customer c WHERE c.id=r.customer_id;
CREATE POLICY recommendation_sales_boundary ON insight.recommendation AS RESTRICTIVE
 USING((common.current_role_code()<>'sales' OR subject_user_ref_id=common.current_user_ref_id())
 AND (opportunity_id IS NULL OR security.has_opportunity_access(opportunity_id)))
 WITH CHECK(subject_user_ref_id=common.current_user_ref_id()
 AND (opportunity_id IS NULL OR security.has_opportunity_access(opportunity_id)));

CREATE POLICY notification_opportunity_boundary ON workflow.notification AS RESTRICTIVE FOR SELECT
 USING((object_type IS DISTINCT FROM 'opportunity' OR security.has_opportunity_access(object_id))
 AND (NULLIF(payload->>'opportunity_id','') IS NULL OR EXISTS(SELECT 1 FROM crm.opportunity o
  WHERE o.id::text=payload->>'opportunity_id')));
CREATE OR REPLACE FUNCTION workflow.enqueue_battle_map_leader_notifications(p_workspace_id uuid, p_customer_id uuid, p_trigger_id uuid, p_title text, p_body text, p_potential_score numeric, p_relationship_score numeric, p_quadrant_code text) RETURNS integer
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'workflow', 'platform', 'crm', 'common', 'security'
    AS $$
DECLARE
  v_owner_team_id uuid;
  v_inserted integer := 0;
BEGIN
  IF p_workspace_id IS DISTINCT FROM common.current_workspace_id() THEN
    RAISE EXCEPTION 'WORKSPACE_SCOPE_VIOLATION';
  END IF;

  SELECT customer.owner_team_id
    INTO v_owner_team_id
    FROM crm.customer customer
   WHERE customer.id = p_customer_id
     AND customer.workspace_id = p_workspace_id
     AND customer.deleted_at IS NULL
     AND (
       security.has_customer_access(customer.id)
       OR security.has_active_role('manager')
       OR (
         security.has_active_role('supervisor')
         AND EXISTS (
           SELECT 1
             FROM platform.team_membership current_tm
            WHERE current_tm.user_ref_id = common.current_user_ref_id()
              AND current_tm.team_id = customer.owner_team_id
              AND clock_timestamp() >= current_tm.valid_from
              AND clock_timestamp() < current_tm.valid_to
         )
       )
     );

  IF NOT FOUND THEN
    RAISE EXCEPTION 'BATTLE_MAP_NOTIFICATION_FORBIDDEN';
  END IF;

  INSERT INTO workflow.notification (
    workspace_id, recipient_user_ref_id, channel_code, template_code,
    title, body, object_type, object_id, status, dedupe_key, payload
  )
  SELECT DISTINCT p_workspace_id, recipient.id, 'in_app', 'battle_map_updated',
         p_title, p_body, 'customer', p_customer_id, 'pending',
         'battle_map_updated:' || p_trigger_id::text || ':' || recipient.id::text,
         jsonb_build_object(
           'customer_id', p_customer_id::text,
           'trigger_type', 'visit.archived',
           'trigger_id', p_trigger_id::text,
           'potential_score', p_potential_score,
           'relationship_score', p_relationship_score,
           'quadrant_code', p_quadrant_code
         )
    FROM platform.user_ref recipient
    JOIN platform.role_binding rb ON rb.user_ref_id = recipient.id
     AND clock_timestamp() >= rb.valid_from
     AND clock_timestamp() < rb.valid_to
    LEFT JOIN platform.team_membership tm ON tm.user_ref_id = recipient.id
     AND tm.is_primary
     AND clock_timestamp() >= tm.valid_from
     AND clock_timestamp() < tm.valid_to
   WHERE recipient.workspace_id = p_workspace_id
     AND recipient.status = 'active'
     AND recipient.deleted_at IS NULL
     AND recipient.id <> common.current_user_ref_id()
     AND (
       rb.role_code = 'manager'
       OR (rb.role_code = 'supervisor' AND tm.team_id = v_owner_team_id)
     )
  ON CONFLICT (workspace_id, recipient_user_ref_id, dedupe_key)
    WHERE dedupe_key IS NOT NULL DO NOTHING;

  GET DIAGNOSTICS v_inserted = ROW_COUNT;
  RETURN v_inserted;
END;
$$;
CREATE FUNCTION security.opportunity_name_is_available(p_customer_id uuid,p_name text,p_exclude_id uuid) RETURNS boolean
 LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF NOT security.has_customer_access(p_customer_id) THEN
  RAISE EXCEPTION '客户不存在或不可见' USING ERRCODE='42501';
 END IF;
 IF p_exclude_id IS NOT NULL AND NOT security.has_opportunity_access(p_exclude_id) THEN
  RAISE EXCEPTION '商机不存在或不可见' USING ERRCODE='42501';
 END IF;
 RETURN NOT EXISTS(SELECT 1 FROM crm.opportunity o WHERE o.customer_id=p_customer_id AND o.deleted_at IS NULL
  AND (p_exclude_id IS NULL OR o.id<>p_exclude_id)
  AND crm.normalize_opportunity_name(o.name)=crm.normalize_opportunity_name(p_name));
END $$;
INSERT INTO ops.schema_migration(version,description) VALUES('V043','客户多人认领与商机个人权限边界');
COMMIT;
