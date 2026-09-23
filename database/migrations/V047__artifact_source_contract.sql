-- Raw transcription belongs to its uploader; generated analysis inherits the source run.
-- These are two artifact sources, not two competing authorization implementations.
DROP POLICY artifact_fact_scope_boundary ON agent.artifact;
CREATE POLICY artifact_fact_scope_boundary ON agent.artifact AS RESTRICTIVE FOR SELECT
 USING((artifact_type='audio_transcript' AND run_id IS NULL)
   OR EXISTS(SELECT 1 FROM agent.run r WHERE r.id=run_id));
ALTER TABLE agent.artifact ADD CONSTRAINT artifact_requires_source
 CHECK((artifact_type='audio_transcript' AND run_id IS NULL) OR run_id IS NOT NULL) NOT VALID;
COMMENT ON CONSTRAINT artifact_requires_source ON agent.artifact IS
 '新模型分析必须有运行来源；用户上传的原始语音转写按上传人权限独立保留';
INSERT INTO ops.schema_migration(version,description)
 VALUES('V047','明确上传人原始转写与生成分析的来源及读取边界');
