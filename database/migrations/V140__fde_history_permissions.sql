BEGIN;
CREATE FUNCTION security.authorization_history_feature() RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT CASE WHEN current_setting('app.authorized_feature',true)=ANY(ARRAY[
  'profile.fde_read','profile.fde_activity','overview.read','agent.chatbi','agent.operating_report'])
 THEN current_setting('app.authorized_feature',true) ELSE 'profile.fde_read' END;
$$;
CREATE OR REPLACE FUNCTION security.fde_recorded_visit_history(p_start timestamp with time zone, p_end timestamp with time zone, p_user uuid, p_opportunity uuid, p_quarters integer[])
 RETURNS TABLE(visit_id uuid, user_ref_id uuid, customer_id uuid, customer_name text, opportunity_id uuid, opportunity_name text, interaction_at timestamp with time zone, recorder_name text, team_id_at_event uuid)
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
 SELECT v.id,v.recorder_user_ref_id,v.customer_id,COALESCE(v.archived_fields->>'customer_name',c.name),
 v.opportunity_id,COALESCE(v.archived_fields->>'opportunity_name',o.name),v.interaction_at,u.display_name,
 v.recording_team_id_snapshot
 FROM activity.visit v
 LEFT JOIN crm.customer c ON c.id=v.customer_id AND c.workspace_id=v.workspace_id
 LEFT JOIN crm.opportunity o ON o.id=v.opportunity_id AND o.workspace_id=v.workspace_id
 LEFT JOIN platform.user_ref u ON u.id=v.recorder_user_ref_id AND u.workspace_id=v.workspace_id
 WHERE v.workspace_id=common.current_workspace_id()
 AND v.deleted_at IS NULL AND v.status='archived'
 AND v.recording_role_code_snapshot IN ('fde','fde_lead')
 AND v.created_by_user_ref_id=v.recorder_user_ref_id AND v.confirmed_by_user_ref_id=v.recorder_user_ref_id
 AND (p_start IS NULL OR v.interaction_at>=p_start)
 AND (p_end IS NULL OR v.interaction_at<p_end)
 AND (p_user IS NULL OR v.recorder_user_ref_id=p_user)
 AND (p_opportunity IS NULL OR v.opportunity_id=p_opportunity)
 AND (p_quarters IS NULL OR extract(quarter FROM timezone('Asia/Shanghai',v.interaction_at))::int=ANY(p_quarters))
 AND security.authorization_allows(security.authorization_history_feature(),v.workspace_id,
  v.recorder_user_ref_id,ARRAY[v.recording_team_id_snapshot],v.recorder_user_ref_id=common.current_user_ref_id());
$function$;
CREATE OR REPLACE FUNCTION security.fde_participation_history()
 RETURNS TABLE(visit_id uuid, user_ref_id uuid, customer_id uuid, customer_name text, opportunity_id uuid, opportunity_name text, interaction_at timestamp with time zone, recorder_name text, team_id_at_event uuid)
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
 SELECT v.id,p.user_ref_id,v.customer_id,COALESCE(v.archived_fields->>'customer_name',c.name),
 v.opportunity_id,COALESCE(v.archived_fields->>'opportunity_name',o.name),v.interaction_at,u.display_name,p.team_id_at_event
 FROM activity.visit_participant p JOIN activity.visit v ON v.id=p.visit_id AND v.workspace_id=p.workspace_id
 LEFT JOIN crm.customer c ON c.id=v.customer_id AND c.workspace_id=v.workspace_id
 LEFT JOIN crm.opportunity o ON o.id=v.opportunity_id AND o.workspace_id=v.workspace_id
 LEFT JOIN platform.user_ref u ON u.id=v.recorder_user_ref_id AND u.workspace_id=v.workspace_id
 WHERE p.workspace_id=common.current_workspace_id() AND p.participant_role='fde'
 AND v.deleted_at IS NULL AND v.status IN ('confirmed','archived')
 AND security.authorization_allows(security.authorization_history_feature(),p.workspace_id,
  p.user_ref_id,ARRAY[p.team_id_at_event],p.user_ref_id=common.current_user_ref_id());
$function$;
CREATE OR REPLACE FUNCTION security.fde_profile_history(p_user uuid, p_days integer DEFAULT 30, p_limit integer DEFAULT 30)
 RETURNS TABLE(run_id uuid, reviewed_at timestamp with time zone, dimensions jsonb, advice jsonb)
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
 IF NOT security.authorization_subject('profile.fde_read','person',p_user,NULL)
 THEN RAISE insufficient_privilege; END IF;
 RETURN QUERY
 SELECT r.id,r.completed_at,r.business_context->'profile_snapshot'->'dimensions',
 CASE WHEN NOT EXISTS (
   SELECT 1 FROM jsonb_array_elements(COALESCE(r.business_context->'profile_snapshot'->'coaching_inputs'->'sources','[]'::jsonb)) src
   WHERE NOT CASE src->>'source_type'
     WHEN 'visit' THEN EXISTS(SELECT 1 FROM activity.visit v
       WHERE 'visit:'||v.id::text=src->>'source_ref' AND v.workspace_id=common.current_workspace_id()
       AND v.deleted_at IS NULL AND security.authorization_visit('profile.fde_read',v.id))
     WHEN 'task' THEN EXISTS(SELECT 1 FROM workflow.task t
       WHERE 'task:'||t.id::text=src->>'source_ref' AND t.workspace_id=common.current_workspace_id()
       AND t.deleted_at IS NULL AND security.authorization_task('profile.fde_read',t.id))
     ELSE false END
 ) THEN COALESCE(m.structured_content->'action_plan','[]'::jsonb) ELSE '[]'::jsonb END
 FROM agent.run r JOIN agent.conversation c ON c.id=r.conversation_id AND c.workspace_id=r.workspace_id
 LEFT JOIN LATERAL(SELECT structured_content FROM agent.message am WHERE am.source_run_id=r.id
   AND am.sender_type='assistant' ORDER BY am.created_at DESC,am.id DESC LIMIT 1) m ON true
 WHERE r.workspace_id=common.current_workspace_id() AND c.user_ref_id=p_user
   AND r.identity_context->>'user_id'=p_user::text AND r.identity_context->>'role' IN ('fde','fde_lead')
   AND r.business_context->>'surface'='fde_profile' AND r.business_context->>'profile_days'=p_days::text
   AND r.status='succeeded' AND jsonb_array_length(r.business_context->'profile_snapshot'->'dimensions')=6
 ORDER BY r.completed_at DESC,r.id DESC LIMIT LEAST(GREATEST(p_limit,1),30);
END $function$;
CREATE OR REPLACE FUNCTION security.fde_demo_statistics(p_users uuid[], p_opportunities uuid[], p_start timestamp with time zone, p_end timestamp with time zone, p_quarters integer[] DEFAULT NULL::integer[])
 RETURNS jsonb
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE answer jsonb;
BEGIN
 IF NOT security.authorization_has(security.authorization_history_feature()) OR EXISTS(SELECT 1 FROM unnest(p_users) uid
   WHERE NOT security.authorization_subject(security.authorization_history_feature(),'person',uid,NULL))
   OR EXISTS(SELECT 1 FROM unnest(p_opportunities) oid WHERE NOT security.authorization_opportunity_direct(security.authorization_history_feature(),oid))
 THEN RAISE insufficient_privilege; END IF;
 WITH scenes AS MATERIALIZED (
   SELECT opportunity_id,created_by FROM crm.opportunity_demo_scenes
   WHERE workspace_id=common.current_workspace_id() AND deleted_at IS NULL AND created_at>=p_start AND created_at<p_end
     AND (created_by=ANY(p_users) OR opportunity_id=ANY(p_opportunities))
     AND (p_quarters IS NULL OR extract(quarter FROM timezone('Asia/Shanghai',created_at))::int=ANY(p_quarters))
 ), people AS (SELECT created_by::text AS id,count(*) AS total FROM scenes
   WHERE created_by=ANY(p_users) GROUP BY created_by)
 SELECT jsonb_build_object('project_count',count(*) FILTER(WHERE opportunity_id=ANY(p_opportunities)),
   'own_count',count(*) FILTER(WHERE created_by=ANY(p_users)),
   'by_person',COALESCE((SELECT jsonb_object_agg(id,total) FROM people),'{}'::jsonb)) INTO answer FROM scenes;
 RETURN answer;
END $function$;
CREATE OR REPLACE FUNCTION security.fde_peer_rankings(p_start date, p_end date, p_quarters integer[] DEFAULT NULL::integer[])
 RETURNS jsonb
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE answer jsonb;
BEGIN
 IF NOT security.authorization_has('dashboard.ranking') THEN RAISE insufficient_privilege; END IF;
 IF p_start IS NULL OR p_end IS NULL OR p_start>p_end OR
   (p_quarters IS NOT NULL AND EXISTS(SELECT 1 FROM unnest(p_quarters) q WHERE q NOT BETWEEN 1 AND 4))
 THEN RAISE EXCEPTION 'INVALID_FDE_RANKING_RANGE' USING ERRCODE='22023'; END IF;
 WITH members AS MATERIALIZED (
   SELECT DISTINCT ON(u.id) u.id,u.display_name AS name,t.name AS team_name,'fde'::text AS role
   FROM platform.user_ref u JOIN platform.role_binding b ON b.user_ref_id=u.id AND b.workspace_id=u.workspace_id
   JOIN platform.team_membership tm ON tm.user_ref_id=u.id AND tm.workspace_id=u.workspace_id
   JOIN platform.team t ON t.id=tm.team_id AND t.workspace_id=u.workspace_id
   WHERE u.workspace_id=common.current_workspace_id() AND b.role_code='fde' AND tm.membership_role='fde'
     AND security.fde_user_is_active(u.id,'fde',tm.team_id)
     AND security.authorization_subject('dashboard.ranking','person',u.id,NULL)
     AND (b.team_id IS NULL OR b.team_id=tm.team_id)
     AND clock_timestamp()>=b.valid_from AND clock_timestamp()<b.valid_to
     AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
   ORDER BY u.id,tm.is_primary DESC,tm.valid_from DESC
 ), visits AS (
   SELECT v.recorder_user_ref_id AS user_id,count(*) AS followup_count,
     count(DISTINCT v.opportunity_id) AS opportunity_count
   FROM activity.visit v JOIN members m ON m.id=v.recorder_user_ref_id
   WHERE v.workspace_id=common.current_workspace_id() AND v.deleted_at IS NULL AND v.status='archived'
     AND security.authorization_allows('dashboard.ranking',v.workspace_id,v.recorder_user_ref_id,ARRAY[v.recording_team_id_snapshot],false)
     AND v.recording_role_code_snapshot='fde' AND v.created_by_user_ref_id=v.recorder_user_ref_id
     AND v.confirmed_by_user_ref_id=v.recorder_user_ref_id
     AND timezone('Asia/Shanghai',v.interaction_at)::date BETWEEN p_start AND p_end
     AND (p_quarters IS NULL OR extract(quarter FROM timezone('Asia/Shanghai',v.interaction_at))::int=ANY(p_quarters))
   GROUP BY v.recorder_user_ref_id
 ), relations AS MATERIALIZED (
   SELECT DISTINCT p.user_ref_id AS user_id,o.id AS opportunity_id
   FROM crm.opportunity_participant p JOIN members m ON m.id=p.user_ref_id
   JOIN crm.opportunity o ON o.id=p.opportunity_id AND o.workspace_id=p.workspace_id AND o.deleted_at IS NULL
   WHERE p.workspace_id=common.current_workspace_id() AND p.participant_role='fde' AND security.authorization_opportunity_direct('dashboard.ranking',o.id)
     AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to
 ), project_counts AS (SELECT user_id,count(*) AS opportunity_count FROM relations GROUP BY user_id),
 ledger AS (
   SELECT r.user_id,sum(a.amount) AS recognized_amount FROM relations r
   JOIN crm.customer_actual a ON a.opportunity_id=r.opportunity_id AND a.workspace_id=common.current_workspace_id()
   WHERE a.voided_at IS NULL AND a.kind='recognized' AND a.occurred_on BETWEEN p_start AND p_end
     AND a.occurred_on<=timezone('Asia/Shanghai',clock_timestamp())::date
     AND (p_quarters IS NULL OR extract(quarter FROM a.occurred_on)::int=ANY(p_quarters)) GROUP BY r.user_id
 ), demos AS (
   SELECT d.created_by AS user_id,count(*) AS demo_scene_count
   FROM crm.opportunity_demo_scenes d JOIN members m ON m.id=d.created_by
   WHERE d.workspace_id=common.current_workspace_id() AND d.deleted_at IS NULL
     AND timezone('Asia/Shanghai',d.created_at)::date BETWEEN p_start AND p_end
     AND (p_quarters IS NULL OR extract(quarter FROM timezone('Asia/Shanghai',d.created_at))::int=ANY(p_quarters))
   GROUP BY d.created_by
 ), facts AS (
   SELECT m.id::text AS user_id,m.name,m.team_name,m.role,COALESCE(v.followup_count,0) AS followup_count,
     COALESCE(l.recognized_amount,0) AS recognized_amount,COALESCE(d.demo_scene_count,0) AS demo_scene_count,
     COALESCE(v.opportunity_count,0) AS opportunity_count,COALESCE(p.opportunity_count,0) AS current_opportunity_count
   FROM members m LEFT JOIN visits v ON v.user_id=m.id LEFT JOIN ledger l ON l.user_id=m.id
   LEFT JOIN demos d ON d.user_id=m.id LEFT JOIN project_counts p ON p.user_id=m.id
 ) SELECT jsonb_build_object('data_source','database','scope','all_fde','complete',true,
   'start',p_start,'end',p_end,'total',count(*),'items',COALESCE(jsonb_agg(to_jsonb(f) ORDER BY f.name,f.user_id),'[]'::jsonb),
   'amount_basis','参与项目已确认确收；同项目可展示在多名协助人的个人行，不可累加为公司业绩') INTO answer FROM facts f;
 RETURN answer;
END $function$;
CREATE OR REPLACE FUNCTION security.fde_dashboard_rankings(p_start date, p_end date, p_quarters integer[] DEFAULT NULL::integer[], p_member uuid DEFAULT NULL::uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE answer jsonb; subject_role text;
BEGIN
 IF NOT security.authorization_has('dashboard.ranking') THEN RAISE insufficient_privilege; END IF;
 IF p_start IS NULL OR p_end IS NULL OR p_start>p_end OR
   (p_quarters IS NOT NULL AND EXISTS(SELECT 1 FROM unnest(p_quarters) q WHERE q NOT BETWEEN 1 AND 4))
 THEN RAISE EXCEPTION 'INVALID_FDE_RANKING_RANGE' USING ERRCODE='22023'; END IF;

 IF p_member IS NULL AND NOT EXISTS(SELECT 1 FROM platform.team t WHERE t.workspace_id=common.current_workspace_id() AND security.authorization_subject('profile.fde_read','team',NULL,t.id)) THEN RAISE insufficient_privilege; END IF;
 IF p_member IS NOT NULL AND NOT security.authorization_subject('profile.fde_read','person',p_member,NULL)
   AND NOT EXISTS(SELECT 1 FROM security.fde_recorded_visit_history() h WHERE h.user_ref_id=p_member)
 THEN RAISE insufficient_privilege; END IF;
 SELECT b.role_code INTO subject_role FROM platform.role_binding b
 WHERE b.workspace_id=common.current_workspace_id() AND b.user_ref_id=p_member
   AND b.role_code IN ('fde','fde_lead') AND security.fde_user_is_active(p_member,b.role_code)
   AND clock_timestamp()>=b.valid_from AND clock_timestamp()<b.valid_to
 ORDER BY CASE b.role_code WHEN 'fde_lead' THEN 0 ELSE 1 END,b.id LIMIT 1;
 WITH members AS MATERIALIZED (
   SELECT DISTINCT ON(u.id) u.id,u.display_name AS name,t.id AS team_id,t.name AS team_name,b.role_code AS role
   FROM platform.user_ref u JOIN platform.role_binding b ON b.user_ref_id=u.id AND b.workspace_id=u.workspace_id
   JOIN platform.team_membership tm ON tm.user_ref_id=u.id AND tm.workspace_id=u.workspace_id
   JOIN platform.team t ON t.id=tm.team_id AND t.workspace_id=u.workspace_id
   WHERE u.workspace_id=common.current_workspace_id() AND b.role_code IN ('fde','fde_lead') AND tm.membership_role=b.role_code
     AND security.fde_user_is_active(u.id,b.role_code,tm.team_id)
     AND security.authorization_subject('dashboard.ranking','person',u.id,NULL)
     AND (b.team_id IS NULL OR b.team_id=tm.team_id)
     AND clock_timestamp()>=b.valid_from AND clock_timestamp()<b.valid_to
     AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
   ORDER BY u.id,CASE b.role_code WHEN 'fde_lead' THEN 0 ELSE 1 END,tm.is_primary DESC,tm.valid_from DESC,tm.id
 ), recorded_visits AS MATERIALIZED (
   SELECT v.recorder_user_ref_id,v.recording_team_id_snapshot,v.opportunity_id
   FROM activity.visit v
   WHERE v.workspace_id=common.current_workspace_id() AND v.deleted_at IS NULL AND v.status='archived'
     AND security.authorization_allows('dashboard.ranking',v.workspace_id,v.recorder_user_ref_id,ARRAY[v.recording_team_id_snapshot],false)
     AND v.recording_role_code_snapshot IN ('fde','fde_lead') AND v.created_by_user_ref_id=v.recorder_user_ref_id
     AND v.confirmed_by_user_ref_id=v.recorder_user_ref_id
     AND timezone('Asia/Shanghai',v.interaction_at)::date BETWEEN p_start AND p_end
     AND (p_quarters IS NULL OR extract(quarter FROM timezone('Asia/Shanghai',v.interaction_at))::int=ANY(p_quarters))
 ), visits AS (
   SELECT v.recorder_user_ref_id AS user_id,count(*) AS followup_count,
     count(DISTINCT v.opportunity_id) AS opportunity_count
   FROM recorded_visits v JOIN members m ON m.id=v.recorder_user_ref_id
   GROUP BY v.recorder_user_ref_id
 ), team_visits AS (
   -- Historical team ownership is immutable, independent of the author's current membership/status.
   SELECT recording_team_id_snapshot AS team_id,count(*) AS followup_count,
     count(DISTINCT opportunity_id) AS opportunity_count FROM recorded_visits
   WHERE recording_team_id_snapshot IS NOT NULL GROUP BY recording_team_id_snapshot
 ), relations AS MATERIALIZED (
   SELECT DISTINCT p.user_ref_id AS user_id,m.team_id,o.id AS opportunity_id
   FROM crm.opportunity_participant p JOIN members m ON m.id=p.user_ref_id
   JOIN crm.opportunity o ON o.id=p.opportunity_id AND o.workspace_id=p.workspace_id AND o.deleted_at IS NULL
   WHERE p.workspace_id=common.current_workspace_id() AND p.participant_role='fde' AND security.authorization_opportunity_direct('dashboard.ranking',o.id)
     AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to
 ), project_counts AS (SELECT user_id,count(*) AS opportunity_count FROM relations GROUP BY user_id),
 ledger AS (
   SELECT r.user_id,sum(a.amount) AS recognized_amount FROM relations r
   JOIN crm.customer_actual a ON a.opportunity_id=r.opportunity_id AND a.workspace_id=common.current_workspace_id()
   WHERE a.voided_at IS NULL AND a.kind='recognized' AND a.occurred_on BETWEEN p_start AND p_end
     AND a.occurred_on<=timezone('Asia/Shanghai',clock_timestamp())::date
     AND (p_quarters IS NULL OR extract(quarter FROM a.occurred_on)::int=ANY(p_quarters)) GROUP BY r.user_id
 ), demos AS (
   SELECT d.created_by AS user_id,count(*) AS demo_scene_count
   FROM crm.opportunity_demo_scenes d JOIN members m ON m.id=d.created_by
   WHERE d.workspace_id=common.current_workspace_id() AND d.deleted_at IS NULL
     AND timezone('Asia/Shanghai',d.created_at)::date BETWEEN p_start AND p_end
     AND (p_quarters IS NULL OR extract(quarter FROM timezone('Asia/Shanghai',d.created_at))::int=ANY(p_quarters))
   GROUP BY d.created_by
 ), facts AS (
   SELECT m.id::text AS user_id,m.team_id::text AS team_id,m.name,m.team_name,m.role,COALESCE(v.followup_count,0) AS followup_count,
     COALESCE(l.recognized_amount,0) AS recognized_amount,COALESCE(d.demo_scene_count,0) AS demo_scene_count,
     COALESCE(v.opportunity_count,0) AS opportunity_count,COALESCE(p.opportunity_count,0) AS current_opportunity_count
   FROM members m LEFT JOIN visits v ON v.user_id=m.id LEFT JOIN ledger l ON l.user_id=m.id
   LEFT JOIN demos d ON d.user_id=m.id LEFT JOIN project_counts p ON p.user_id=m.id
 ), team_members AS (
   SELECT DISTINCT tm.team_id,m.id AS user_id FROM platform.team_membership tm JOIN members m ON m.id=tm.user_ref_id
   WHERE tm.workspace_id=common.current_workspace_id() AND tm.membership_role IN ('fde','fde_lead')
     AND security.fde_user_is_active(m.id,NULL,tm.team_id)
     AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
 ), team_relations AS (
   SELECT DISTINCT tm.team_id,r.opportunity_id FROM team_members tm JOIN relations r ON r.user_id=tm.user_id
 ), team_ledger AS (
   SELECT r.team_id,sum(a.amount) AS recognized_amount FROM team_relations r
   JOIN crm.customer_actual a ON a.opportunity_id=r.opportunity_id AND a.workspace_id=common.current_workspace_id()
   WHERE a.voided_at IS NULL AND a.kind='recognized' AND a.occurred_on BETWEEN p_start AND p_end
     AND a.occurred_on<=timezone('Asia/Shanghai',clock_timestamp())::date
     AND (p_quarters IS NULL OR extract(quarter FROM a.occurred_on)::int=ANY(p_quarters)) GROUP BY r.team_id
 ), ranked_teams AS (
   SELECT team_id FROM team_members UNION SELECT team_id FROM team_visits
 ), teams AS (
   SELECT t.id::text AS user_id,t.name,t.name AS team_name,'fde_team'::text AS role,
     COALESCE(max(v.followup_count),0) AS followup_count,COALESCE(sum(f.demo_scene_count),0) AS demo_scene_count,
     COALESCE(max(l.recognized_amount),0) AS recognized_amount,
     COALESCE(max(v.opportunity_count),0) AS opportunity_count
   FROM platform.team t JOIN ranked_teams rt ON rt.team_id=t.id
   LEFT JOIN team_members tm ON tm.team_id=t.id
   LEFT JOIN facts f ON f.user_id=tm.user_id::text LEFT JOIN team_ledger l ON l.team_id=t.id
   LEFT JOIN team_visits v ON v.team_id=t.id
   WHERE t.workspace_id=common.current_workspace_id() AND security.authorization_subject('dashboard.ranking','team',NULL,t.id) AND t.deleted_at IS NULL AND t.status='active'
   GROUP BY t.id,t.name
 ), selected_teams AS (
   SELECT DISTINCT tm.team_id::text AS id FROM platform.team_membership tm
   WHERE tm.workspace_id=common.current_workspace_id() AND tm.user_ref_id=common.current_user_ref_id()
     AND tm.membership_role='fde_lead' AND security.fde_user_is_active(common.current_user_ref_id(),'fde_lead',tm.team_id)
     AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
 ), result AS (
   SELECT to_jsonb(f) AS item FROM facts f WHERE p_member IS NOT NULL AND f.role=subject_role
   UNION ALL SELECT to_jsonb(t) FROM teams t WHERE p_member IS NULL
 ) SELECT jsonb_build_object('contract_version',2,'data_source','database',
   'scope',CASE WHEN p_member IS NULL THEN 'company_fde_teams' WHEN subject_role='fde_lead' THEN 'all_fde_leads' ELSE 'all_fde' END,
   'complete',true,'start',p_start,'end',p_end,'total',count(*),
   'selection',jsonb_build_object('personal',p_member IS NOT NULL,'member_id',p_member,'cohort_role',subject_role,
     'team_ids',COALESCE((SELECT jsonb_agg(id) FROM selected_teams),'[]'::jsonb)),
   'items',COALESCE(jsonb_agg(item ORDER BY item->>'name',item->>'user_id'),'[]'::jsonb),
   'amount_basis','个人按参与项目计确收；团队按参与项目并集计确收，同一项目在同一团队仅计一次') INTO answer FROM result;
 RETURN answer;
END $function$;

DO $$ DECLARE p record; BEGIN
 FOR p IN SELECT * FROM pg_policies WHERE schemaname='crm' AND tablename='opportunity_quote_reference'
  AND policyname LIKE 'fde_commercial_%' LOOP
  EXECUTE format('DROP POLICY %I ON crm.opportunity_quote_reference',p.policyname); END LOOP;
END $$;
ALTER POLICY quote_write ON crm.opportunity_quote_reference WITH CHECK(workspace_id=common.current_workspace_id()
 AND created_by_user_ref_id=common.current_user_ref_id()
 AND security.authorization_opportunity('opportunity.quote_create',opportunity_id));
ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_v139;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; r record;
BEGIN
 total:=security.reconcile_runtime_grants_v139();
 FOR r IN SELECT rolname FROM pg_roles WHERE NOT pg_has_role(oid,'salegent_feishu_worker','member')
 AND rolname NOT LIKE 'pg\_%' ESCAPE '\' AND has_schema_privilege(oid,'security','USAGE')
 AND has_function_privilege(oid,'security.profile_customer_owner(uuid)','EXECUTE') LOOP
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.authorization_history_feature() TO %I',r.rolname);
  total:=total+1;
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_v139(),
 security.authorization_history_feature() FROM PUBLIC,salegent_feishu_worker;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description) VALUES('V140','Independent FDE portrait scopes and quote permissions');
COMMIT;
