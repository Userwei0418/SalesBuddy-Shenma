-- Analysis is a versioned projection of authorized facts, with explicit result provenance.
-- V1 precedes shared-customer / personal-opportunity isolation; V2 is the current contract.
CREATE FUNCTION security.current_fact_scope_version() RETURNS smallint
 LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$ SELECT 2::smallint $$;
CREATE FUNCTION security.is_current_fact_scope(p_version smallint) RETURNS boolean
 LANGUAGE sql STABLE PARALLEL SAFE AS $$
 SELECT p_version=security.current_fact_scope_version();
$$;
CREATE FUNCTION security.has_analysis_scope(p_identity jsonb,p_version smallint) RETURNS boolean
 LANGUAGE sql STABLE AS $$
 SELECT security.is_current_fact_scope(p_version)
 AND p_identity->>'workspace_id'=common.current_workspace_id()::text
 AND p_identity->>'user_id'=common.current_user_ref_id()::text
 AND p_identity->>'role'=common.current_role_code()
 AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(COALESCE(p_identity->'team_ids','[]')) prior_team
   WHERE NOT prior_team=ANY(COALESCE(string_to_array(current_setting('app.team_ids',true),','),ARRAY[]::text[])));
$$;

ALTER TABLE agent.run ADD COLUMN fact_scope_version smallint NOT NULL
 DEFAULT security.current_fact_scope_version() CHECK(fact_scope_version>0);
UPDATE agent.run SET fact_scope_version=1
 WHERE created_at < (SELECT applied_at FROM ops.schema_migration WHERE version='V043')
 AND intent_code NOT IN ('visit_entry','customer_create','management_task');
CREATE POLICY run_fact_scope_boundary ON agent.run AS RESTRICTIVE FOR SELECT
 USING(security.has_analysis_scope(identity_context,fact_scope_version));
COMMENT ON COLUMN agent.run.fact_scope_version IS '事实权限契约版本；旧版本报告保留审计，当前会话不可复用';

ALTER TABLE agent.run ADD CONSTRAINT run_message_provenance_key UNIQUE(workspace_id,conversation_id,id);
ALTER TABLE agent.message ADD COLUMN source_run_id uuid;
UPDATE agent.message m SET source_run_id=r.id FROM agent.run r
 WHERE m.workspace_id=r.workspace_id AND m.conversation_id=r.conversation_id
 AND m.structured_content->>'run_id'=r.id::text;
ALTER TABLE agent.message ADD CONSTRAINT message_source_run_fk
 FOREIGN KEY(workspace_id,conversation_id,source_run_id) REFERENCES agent.run(workspace_id,conversation_id,id);
-- Unattributed historical messages remain for audit; new assistant output must name its run.
ALTER TABLE agent.message ADD CONSTRAINT assistant_message_requires_source_run
 CHECK(sender_type<>'assistant' OR source_run_id IS NOT NULL) NOT VALID;
CREATE INDEX message_source_run_idx ON agent.message(source_run_id,created_at DESC) WHERE source_run_id IS NOT NULL;
CREATE POLICY message_fact_scope_boundary ON agent.message AS RESTRICTIVE FOR SELECT
 USING(sender_type<>'assistant' OR EXISTS(SELECT 1 FROM agent.run r WHERE r.id=source_run_id));
CREATE POLICY artifact_fact_scope_boundary ON agent.artifact AS RESTRICTIVE FOR SELECT
 USING(EXISTS(SELECT 1 FROM agent.run r WHERE r.id=run_id));
COMMENT ON COLUMN agent.message.source_run_id IS '正式结果来源；会话消息与报告共同继承运行记录的权限及版本';

-- Fold the one-time V045 upgrade marker into the same runtime version contract.
ALTER TABLE insight.quadrant_score ADD COLUMN fact_scope_version smallint NOT NULL
 DEFAULT security.current_fact_scope_version() CHECK(fact_scope_version>0);
UPDATE insight.quadrant_score SET fact_scope_version=1 WHERE NOT scope_verified;
DROP POLICY quadrant_verified_scope ON insight.quadrant_score;
ALTER TABLE insight.quadrant_score DROP COLUMN scope_verified;
CREATE POLICY quadrant_fact_scope_boundary ON insight.quadrant_score AS RESTRICTIVE FOR SELECT
 USING(security.is_current_fact_scope(fact_scope_version));
ALTER TABLE insight.recommendation ADD COLUMN fact_scope_version smallint NOT NULL
 DEFAULT security.current_fact_scope_version() CHECK(fact_scope_version>0);
UPDATE insight.recommendation SET fact_scope_version=1 WHERE NOT scope_verified;
DROP POLICY recommendation_verified_scope ON insight.recommendation;
ALTER TABLE insight.recommendation DROP COLUMN scope_verified;
CREATE POLICY recommendation_fact_scope_boundary ON insight.recommendation AS RESTRICTIVE FOR SELECT
 USING(security.is_current_fact_scope(fact_scope_version));
INSERT INTO ops.schema_migration(version,description)
 VALUES('V046','统一分析事实权限版本与消息来源外键，报告和会话共享读取规则');
