BEGIN;
-- Extend the existing source of truth; existing annual money targets retain IDs and values.
ALTER TABLE crm.sales_target ADD COLUMN period_type text NOT NULL DEFAULT 'year';
ALTER TABLE crm.sales_target ADD COLUMN period_start date;
ALTER TABLE crm.sales_target ADD COLUMN period_end date;
UPDATE crm.sales_target SET period_start=make_date(target_year,1,1),period_end=make_date(target_year,12,31);
ALTER TABLE crm.sales_target ALTER COLUMN period_start SET NOT NULL;
ALTER TABLE crm.sales_target ALTER COLUMN period_end SET NOT NULL;
ALTER TABLE crm.sales_target DROP CONSTRAINT sales_target_kind_check;
ALTER TABLE crm.sales_target ADD CONSTRAINT sales_target_kind_check CHECK(kind IN
 ('collection','recognized','acv','opportunity_count','visit_count','demo_count'));
ALTER TABLE crm.sales_target ADD CONSTRAINT target_period_check CHECK(period_type IN ('week','month','quarter','year')
 AND period_end>=period_start AND target_year=extract(year FROM period_start)::integer);
ALTER TABLE crm.sales_target ADD CONSTRAINT target_count_integer CHECK(kind NOT IN
 ('opportunity_count','visit_count','demo_count') OR amount=trunc(amount));
DO $$ DECLARE c record; BEGIN
 FOR c IN SELECT conname FROM pg_constraint WHERE conrelid='crm.sales_target'::regclass AND contype='u'
 LOOP EXECUTE format('ALTER TABLE crm.sales_target DROP CONSTRAINT %I',c.conname); END LOOP;
END $$;
ALTER TABLE crm.sales_target ADD CONSTRAINT target_scope_period_unique UNIQUE NULLS NOT DISTINCT
 (workspace_id,period_type,period_start,scope_type,user_ref_id,team_id,kind);
COMMENT ON TABLE crm.sales_target IS '已生效目标唯一主表；金额单位元，数量为整数；运营配置或本人首次自填生效，后续本人修改需审批；不按比例推算其他周期目标';

CREATE TABLE crm.sales_target_change_request (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 target_id uuid NOT NULL REFERENCES crm.sales_target(id),
 applicant_user_ref_id uuid NOT NULL,
 base_version integer NOT NULL CHECK(base_version>0),
 previous_amount numeric(18,2) NOT NULL,
 proposed_amount numeric(18,2) NOT NULL CHECK(proposed_amount>0),
 status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected','cancelled')),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 reviewed_at timestamptz,
 reviewer_user_ref_id uuid,
 decision_reason text NOT NULL DEFAULT '',
 FOREIGN KEY(applicant_user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id),
 FOREIGN KEY(reviewer_user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id)
);
CREATE UNIQUE INDEX target_one_pending ON crm.sales_target_change_request(target_id) WHERE status='pending';
CREATE INDEX target_request_inbox ON crm.sales_target_change_request(workspace_id,status,created_at DESC);
COMMENT ON TABLE crm.sales_target_change_request IS '目标修改申请；pending不影响生效目标。审批前校验主目标版本，历史申请永不删除';

CREATE FUNCTION security.target_scope_read(p_scope text,p_user uuid,p_team uuid) RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT CASE
 WHEN p_scope='department' AND p_user IS NULL AND p_team IS NULL
 THEN security.management_actor() OR (common.current_role_code()='manager' AND security.has_active_role('manager'))
 WHEN p_scope='person' AND p_team IS NULL THEN EXISTS(SELECT 1 FROM platform.user_ref u
 WHERE u.id=p_user AND u.workspace_id=common.current_workspace_id() AND u.status='active' AND u.deleted_at IS NULL
 AND (security.management_actor() OR (p_user=common.current_user_ref_id() AND security.has_active_role(common.current_role_code()))
 OR security.profile_scope_allowed('person',p_user,NULL) OR (security.is_fde_actor() AND security.fde_manages_user(p_user))))
 WHEN p_scope='team' AND p_user IS NULL THEN EXISTS(SELECT 1 FROM platform.team t
 WHERE t.id=p_team AND t.workspace_id=common.current_workspace_id() AND t.status='active' AND t.deleted_at IS NULL
 AND (security.management_actor() OR security.profile_scope_allowed('team',NULL,p_team)
 OR (common.current_role_code()='fde_lead' AND security.fde_user_is_active(common.current_user_ref_id(),'fde_lead',p_team))))
 ELSE false END;
$$;
CREATE FUNCTION security.target_scope_write(p_scope text,p_user uuid,p_team uuid) RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.target_scope_read(p_scope,p_user,p_team) AND
 (security.management_actor() OR (p_scope='person' AND p_user=common.current_user_ref_id()
 AND common.current_role_code() IN ('sales','supervisor','manager','fde','fde_lead')
 AND security.has_active_role(common.current_role_code())));
$$;
DROP POLICY sales_target_read ON crm.sales_target;
DROP POLICY sales_target_insert ON crm.sales_target;
DROP POLICY sales_target_update ON crm.sales_target;
CREATE POLICY sales_target_read ON crm.sales_target FOR SELECT USING
 (workspace_id=common.current_workspace_id() AND security.target_scope_read(scope_type,user_ref_id,team_id));
-- All target writes pass the state machine functions, not an unrestricted upsert.
ALTER TABLE crm.sales_target_change_request ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm.sales_target_change_request FORCE ROW LEVEL SECURITY;
CREATE POLICY target_request_read ON crm.sales_target_change_request FOR SELECT USING
 (workspace_id=common.current_workspace_id() AND (security.management_actor() OR applicant_user_ref_id=common.current_user_ref_id()));
CREATE TRIGGER target_request_audit AFTER INSERT OR UPDATE ON crm.sales_target_change_request
 FOR EACH ROW EXECUTE FUNCTION ops.audit_business_row();
CREATE FUNCTION crm.protect_target_request() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF OLD.status<>'pending' OR NEW.status='pending' OR
 (NEW.id,NEW.workspace_id,NEW.target_id,NEW.applicant_user_ref_id,NEW.base_version,NEW.previous_amount,NEW.proposed_amount,NEW.created_at)
 IS DISTINCT FROM (OLD.id,OLD.workspace_id,OLD.target_id,OLD.applicant_user_ref_id,OLD.base_version,OLD.previous_amount,OLD.proposed_amount,OLD.created_at)
 THEN RAISE EXCEPTION '已提交目标申请不可覆盖，请新建申请' USING ERRCODE='22023'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER target_request_immutable BEFORE UPDATE ON crm.sales_target_change_request FOR EACH ROW EXECUTE FUNCTION crm.protect_target_request();
CREATE TRIGGER target_request_no_delete BEFORE DELETE OR TRUNCATE ON crm.sales_target_change_request FOR EACH STATEMENT EXECUTE FUNCTION ops.deny_audit_mutation();

CREATE FUNCTION security.save_period_target(p_scope text,p_user uuid,p_team uuid,p_period text,p_start date,p_end date,
 p_kind text,p_amount numeric,p_version integer DEFAULT NULL) RETURNS jsonb
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE target crm.sales_target; req crm.sales_target_change_request; expected_end date;
BEGIN
 IF NOT security.target_scope_write(p_scope,p_user,p_team) THEN RAISE insufficient_privilege; END IF;
 IF p_period NOT IN ('week','month','quarter','year') OR p_kind NOT IN
 ('recognized','collection','acv','opportunity_count','visit_count','demo_count') OR p_amount<=0
 OR p_amount IS NULL OR p_start IS NULL OR p_end IS NULL OR p_amount>=10000000000000000
 OR (p_kind IN ('opportunity_count','visit_count','demo_count') AND p_amount<>trunc(p_amount))
 THEN RAISE EXCEPTION '目标周期、指标或数值无效' USING ERRCODE='22023'; END IF;
 expected_end=CASE p_period WHEN 'week' THEN p_start+6 WHEN 'month' THEN (p_start+interval '1 month'-interval '1 day')::date
 WHEN 'quarter' THEN (p_start+interval '3 months'-interval '1 day')::date ELSE (p_start+interval '1 year'-interval '1 day')::date END;
 IF date_trunc(p_period,p_start::timestamp)::date<>p_start OR expected_end<>p_end THEN
 RAISE EXCEPTION '目标须使用完整自然周、月、季度或年' USING ERRCODE='22023'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(concat_ws(':',common.current_workspace_id(),p_scope,p_user,p_team,p_period,p_start,p_kind),0));
 SELECT * INTO target FROM crm.sales_target WHERE workspace_id=common.current_workspace_id()
 AND scope_type=p_scope AND user_ref_id IS NOT DISTINCT FROM p_user AND team_id IS NOT DISTINCT FROM p_team
 AND period_type=p_period AND period_start=p_start AND kind=p_kind FOR UPDATE;
 IF NOT FOUND THEN
  IF p_version IS NOT NULL THEN RAISE EXCEPTION '目标状态已变化，请刷新' USING ERRCODE='P0001'; END IF;
  INSERT INTO crm.sales_target(workspace_id,target_year,scope_type,user_ref_id,team_id,kind,amount,updated_by_user_ref_id,period_type,period_start,period_end)
  VALUES(common.current_workspace_id(),extract(year FROM p_start)::integer,p_scope,p_user,p_team,p_kind,p_amount,
  common.current_user_ref_id(),p_period,p_start,p_end) RETURNING * INTO target;
  RETURN jsonb_build_object('status','effective','target',to_jsonb(target),'request',NULL);
 END IF;
 IF p_version IS NOT NULL AND target.version_no<>p_version THEN RAISE EXCEPTION '目标已更新，请刷新后再提交' USING ERRCODE='P0001'; END IF;
 IF target.amount=p_amount THEN RETURN jsonb_build_object('status','unchanged','target',to_jsonb(target),'request',NULL); END IF;
 SELECT * INTO req FROM crm.sales_target_change_request WHERE target_id=target.id AND status='pending' FOR UPDATE;
 IF security.management_actor() THEN
  UPDATE crm.sales_target SET amount=p_amount,updated_by_user_ref_id=common.current_user_ref_id(),
  updated_at=clock_timestamp(),version_no=version_no+1 WHERE id=target.id RETURNING * INTO target;
  UPDATE crm.sales_target_change_request SET status='cancelled',reviewed_at=clock_timestamp(),
  reviewer_user_ref_id=common.current_user_ref_id(),decision_reason='目标已由运营调整，请核对后重新申请' WHERE target_id=target.id AND status='pending';
  RETURN jsonb_build_object('status','effective','target',to_jsonb(target),'request',NULL);
 END IF;
 IF req.id IS NOT NULL THEN
  IF req.proposed_amount=p_amount AND req.base_version=target.version_no THEN
   RETURN jsonb_build_object('status','pending','target',to_jsonb(target),'request',to_jsonb(req));
  END IF;
  RAISE EXCEPTION '已有待审批的目标修改申请，请等待运营处理' USING ERRCODE='P0001';
 END IF;
 INSERT INTO crm.sales_target_change_request(workspace_id,target_id,applicant_user_ref_id,base_version,previous_amount,proposed_amount)
 VALUES(target.workspace_id,target.id,common.current_user_ref_id(),target.version_no,target.amount,p_amount) RETURNING * INTO req;
 RETURN jsonb_build_object('status','pending','target',to_jsonb(target),'request',to_jsonb(req));
END $$;

CREATE FUNCTION security.review_target_change(p_request uuid,p_decision text,p_reason text) RETURNS jsonb
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE req crm.sales_target_change_request; target crm.sales_target; target_key uuid;
BEGIN
 IF NOT security.management_actor() THEN RAISE insufficient_privilege; END IF;
 IF p_decision NOT IN ('approved','rejected') OR (p_decision='rejected' AND NULLIF(btrim(p_reason),'') IS NULL)
 THEN RAISE EXCEPTION '驳回必须填写原因' USING ERRCODE='22023'; END IF;
 SELECT target_id INTO target_key FROM crm.sales_target_change_request WHERE id=p_request AND workspace_id=common.current_workspace_id();
 IF target_key IS NULL THEN RAISE no_data_found; END IF;
 SELECT * INTO target FROM crm.sales_target WHERE id=target_key FOR UPDATE;
 SELECT * INTO req FROM crm.sales_target_change_request WHERE id=p_request FOR UPDATE;
 IF req.status<>'pending' THEN RAISE EXCEPTION '申请已处理，请刷新' USING ERRCODE='P0001'; END IF;
 IF p_decision='approved' THEN
  IF target.version_no<>req.base_version THEN RAISE EXCEPTION '生效目标已变化，请驳回后重新申请' USING ERRCODE='P0001'; END IF;
  IF NOT EXISTS(SELECT 1 FROM platform.user_ref u WHERE u.id=req.applicant_user_ref_id
   AND u.workspace_id=req.workspace_id AND u.status='active' AND u.deleted_at IS NULL)
  THEN RAISE EXCEPTION '申请人已停用，不能批准' USING ERRCODE='P0001'; END IF;
  UPDATE crm.sales_target SET amount=req.proposed_amount,updated_by_user_ref_id=common.current_user_ref_id(),
  updated_at=clock_timestamp(),version_no=version_no+1 WHERE id=target.id RETURNING * INTO target;
 END IF;
 UPDATE crm.sales_target_change_request SET status=p_decision,reviewer_user_ref_id=common.current_user_ref_id(),
 reviewed_at=clock_timestamp(),decision_reason=COALESCE(btrim(p_reason),'') WHERE id=req.id RETURNING * INTO req;
 RETURN jsonb_build_object('status',p_decision,'target',to_jsonb(target),'request',to_jsonb(req));
END $$;
DO $$ DECLARE r record; BEGIN
 FOR r IN SELECT DISTINCT grantee FROM information_schema.role_table_grants WHERE table_schema='crm'
 AND table_name='sales_target' AND privilege_type='SELECT' AND grantee<>'PUBLIC'
 LOOP EXECUTE format('GRANT SELECT ON crm.sales_target_change_request TO %I',r.grantee); END LOOP;
END $$;
-- Keep grants reproducible for runtime roles provisioned after an empty deployment.
CREATE OR REPLACE FUNCTION security.reconcile_runtime_grants() RETURNS integer
 LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE permission record; reconciled integer:=0;
BEGIN
 FOR permission IN
  SELECT DISTINCT r.rolname,a.privilege_type FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  CROSS JOIN LATERAL aclexplode(c.relacl) a JOIN pg_roles r ON r.oid=a.grantee
  WHERE n.nspname='activity' AND c.relname='visit' AND a.grantee<>c.relowner
   AND a.privilege_type IN ('SELECT','INSERT','UPDATE')
 LOOP
  EXECUTE format('GRANT %s ON activity.visit_participant TO %I',permission.privilege_type,permission.rolname);
  IF permission.privilege_type='SELECT' THEN EXECUTE format('GRANT SELECT ON activity.v_visit_collaborators TO %I',permission.rolname); END IF;
  reconciled:=reconciled+1;
 END LOOP;
 FOR permission IN
  SELECT r.rolname,CASE WHEN n.nspname='activity' THEN 'activity.visit_participant' ELSE 'workflow.task_assignee' END AS target_table
  FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  CROSS JOIN LATERAL aclexplode(c.relacl) a JOIN pg_roles r ON r.oid=a.grantee
  WHERE ((n.nspname='activity' AND c.relname='visit') OR (n.nspname='workflow' AND c.relname='task_assignee'))
   AND a.grantee<>c.relowner AND a.privilege_type IN ('INSERT','UPDATE')
  GROUP BY r.rolname,n.nspname HAVING count(DISTINCT a.privilege_type)=2
 LOOP
  EXECUTE format('GRANT DELETE ON %s TO %I',permission.target_table,permission.rolname);
  reconciled:=reconciled+1;
 END LOOP;
 FOR permission IN
  SELECT DISTINCT r.rolname,a.privilege_type FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  CROSS JOIN LATERAL aclexplode(c.relacl) a JOIN pg_roles r ON r.oid=a.grantee
  WHERE n.nspname='crm' AND c.relname='opportunity' AND a.grantee<>c.relowner
   AND a.privilege_type IN ('SELECT','INSERT','UPDATE')
 LOOP
  EXECUTE format('GRANT %s ON crm.opportunity_demo_scenes TO %I',permission.privilege_type,permission.rolname);
  reconciled:=reconciled+1;
 END LOOP;
 FOR permission IN
  SELECT DISTINCT r.rolname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  CROSS JOIN LATERAL aclexplode(c.relacl) a JOIN pg_roles r ON r.oid=a.grantee
  WHERE n.nspname='crm' AND c.relname='sales_target' AND a.grantee<>c.relowner AND a.privilege_type='SELECT'
 LOOP
  EXECUTE format('GRANT SELECT ON crm.sales_target_change_request TO %I',permission.rolname);
  reconciled:=reconciled+1;
 END LOOP;
 RETURN reconciled;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants() FROM PUBLIC;
SELECT security.reconcile_runtime_grants();
COMMIT;
