-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V225__fde_profile_review_details.sql.
-- Original SHA-256: a07c7fb6be24b3c1bc949f1f7d3e230a7aa07315741b5356c62a60a6c279d51d. See shenma_whole_121_manifest.json.
BEGIN;
-- New narrow functions preserve V140 callers and never expose a raw agent.run.
CREATE FUNCTION security.fde_profile_review_detail(p_run uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r record; snap jsonb; src jsonb; item jsonb; ref text; sid uuid; ver jsonb;
  sources jsonb:='[]'; actions jsonb:='[]'; allowed text[]:=ARRAY[]::text[]; visible boolean;
BEGIN
 SELECT ar.id,ar.completed_at,ar.business_context->'profile_snapshot' AS snapshot,
   c.user_ref_id,u.display_name,u.account_code,m.structured_content->'action_plan' AS advice
 INTO r FROM agent.run ar
 JOIN agent.conversation c ON c.id=ar.conversation_id AND c.workspace_id=ar.workspace_id
 JOIN platform.user_ref u ON u.id=c.user_ref_id AND u.workspace_id=ar.workspace_id
 LEFT JOIN LATERAL(SELECT am.structured_content FROM agent.message am
   WHERE am.source_run_id=ar.id AND am.workspace_id=ar.workspace_id AND am.sender_type='assistant'
   ORDER BY am.created_at DESC,am.id DESC LIMIT 1) m ON true
 WHERE ar.id=p_run AND ar.workspace_id=common.current_workspace_id() AND ar.status='succeeded'
   AND ar.business_context->>'mode'='operating_report' AND ar.business_context->>'surface'='fde_profile'
   AND ar.identity_context->>'user_id'=c.user_ref_id::text
   AND ar.identity_context->>'role' IN ('fde','fde_lead')
   AND jsonb_array_length(ar.business_context->'profile_snapshot'->'dimensions')=6
   AND security.authorization_subject('profile.fde_read','person',c.user_ref_id,NULL);
 IF NOT FOUND THEN RETURN NULL; END IF;
 snap:=r.snapshot;
 FOR src IN SELECT value FROM jsonb_array_elements(COALESCE(snap->'coaching_inputs'->'sources','[]')) LOOP
   ref:=src->>'source_ref'; sid:=NULL; ver:=NULL;
   IF src->>'source_type'='visit' THEN
     SELECT v.id INTO sid FROM activity.visit v WHERE 'visit:'||v.id::text=ref
       AND v.workspace_id=common.current_workspace_id() AND v.deleted_at IS NULL
       AND security.authorization_visit('visit.read',v.id);
   ELSIF src->>'source_type'='task' THEN
     SELECT t.id INTO sid FROM workflow.task t WHERE 'task:'||t.id::text=ref
       AND t.workspace_id=common.current_workspace_id() AND t.deleted_at IS NULL
       AND security.authorization_task('task.read',t.id);
   ELSE CONTINUE;
   END IF;
   visible:=sid IS NOT NULL;
   IF visible THEN
     allowed:=array_append(allowed,ref);
     SELECT value->'version' INTO ver FROM jsonb_array_elements(COALESCE(snap->'coaching_versions','[]'))
       WHERE value->>'type'=src->>'source_type' AND value->>'id'=sid::text LIMIT 1;
   END IF;
   sources:=sources||jsonb_build_array(jsonb_build_object(
     'source_type',src->>'source_type','source_id',sid,'source_version',CASE WHEN visible THEN ver END,
     'source_ref',CASE WHEN visible THEN ref END,'readable',visible,
     'detail',CASE WHEN visible THEN CASE src->>'source_type' WHEN 'visit'
       THEN concat_ws(E'\n',src->>'communication',src->>'next_action') ELSE src->>'title' END END));
 END LOOP;
 FOR item IN SELECT value FROM jsonb_array_elements(COALESCE(r.advice,'[]')) LOOP
   visible:=false;
   IF jsonb_typeof(item->'source_refs')='array' THEN
     visible:=jsonb_array_length(item->'source_refs') BETWEEN 1 AND 3 AND NOT EXISTS(
       SELECT 1 FROM jsonb_array_elements_text(item->'source_refs') sr WHERE NOT sr=ANY(allowed));
   END IF;
   actions:=actions||jsonb_build_array(jsonb_build_object(
     'title',CASE WHEN visible THEN item->>'title' ELSE '当前无法查看该建议依据' END,
     'detail',CASE WHEN visible THEN item->>'detail' ELSE '当前无法查看该建议依据' END,
     'source_refs',CASE WHEN visible THEN item->'source_refs' ELSE '[]'::jsonb END));
 END LOOP;
 RETURN jsonb_build_object('run_id',r.id,'reviewed_at',r.completed_at,'subject_id',r.user_ref_id,
   'subject_name',r.display_name,'account_code',r.account_code,'advice',actions,'evidence',sources,
   'profile',jsonb_build_object('dimensions',snap->'dimensions','period',snap->'period',
     'sample_count',snap->'sample_count','data_as_of',snap->'data_as_of','score_basis',snap->'score_basis',
     'evidence_coverage',snap->'evidence_coverage','coaching_coverage',snap->'coaching_coverage'));
END $$;
CREATE FUNCTION security.fde_profile_history_v2(p_user uuid,p_days integer DEFAULT 30,p_limit integer DEFAULT 30)
RETURNS TABLE(review jsonb) LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF NOT security.authorization_subject('profile.fde_read','person',p_user,NULL) THEN
   RAISE insufficient_privilege; END IF;
 RETURN QUERY SELECT security.fde_profile_review_detail(ar.id) FROM agent.run ar
 JOIN agent.conversation c ON c.id=ar.conversation_id AND c.workspace_id=ar.workspace_id
 WHERE ar.workspace_id=common.current_workspace_id() AND c.user_ref_id=p_user
   AND ar.business_context->>'surface'='fde_profile' AND ar.business_context->>'mode'='operating_report'
   AND ar.business_context->>'profile_days'=p_days::text
   AND ar.identity_context->>'user_id'=p_user::text AND ar.identity_context->>'role' IN ('fde','fde_lead')
   AND ar.status='succeeded' AND jsonb_array_length(ar.business_context->'profile_snapshot'->'dimensions')=6
 ORDER BY ar.completed_at DESC,ar.id DESC LIMIT LEAST(GREATEST(p_limit,1),30);
END $$;
REVOKE ALL ON FUNCTION security.fde_profile_review_detail(uuid),security.fde_profile_history_v2(uuid,integer,integer)
 FROM PUBLIC,salegent_feishu_worker;
-- Match existing authorized profile API readers; no new table grants or broad Agent history permission.
ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_pre_fde_details;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r record; total integer; BEGIN
 total:=security.reconcile_runtime_grants_pre_fde_details();
 FOR r IN SELECT rolname FROM pg_roles
 WHERE rolname NOT LIKE 'pg\_%' ESCAPE '\'
   AND NOT pg_has_role(oid,'salegent_feishu_worker','member')
   AND has_schema_privilege(oid,'security','USAGE')
   AND has_function_privilege(oid,'security.fde_profile_history(uuid,integer,integer)','EXECUTE') LOOP
   EXECUTE format('GRANT EXECUTE ON FUNCTION security.fde_profile_review_detail(uuid),security.fde_profile_history_v2(uuid,integer,integer) TO %I',r.rolname);
   total:=total+1;
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_pre_fde_details()
 FROM PUBLIC,salegent_feishu_worker;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description) VALUES('V373','Frozen FDE profile history and source-authorized review details');
COMMIT;
