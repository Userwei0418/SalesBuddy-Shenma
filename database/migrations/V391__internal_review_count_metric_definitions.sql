-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V243__review_count_metric_definitions.sql.
-- Original SHA-256: 8f6bbf86ec464a4792d5c1e9c056d24f31c6d911d3b4b8e2324cb20d72b43a53. See shenma_whole_121_manifest.json.
BEGIN;
-- A definition revision is independent of the tenant's maintenance version.
-- Preserve earlier snapshots, active state, maintenance timestamps and targets.
ALTER TABLE crm.review_metric_history ADD COLUMN definition_version integer
 GENERATED ALWAYS AS ((snapshot->>'definition_version')::integer) STORED NOT NULL;
ALTER TABLE crm.review_metric_history DROP CONSTRAINT review_metric_history_pkey;
ALTER TABLE crm.review_metric_history ADD PRIMARY KEY(workspace_id,code,version_no,definition_version);
UPDATE crm.review_metric SET
 name=CASE code WHEN 'opportunity_count' THEN '当前负责的本期新增商机数' ELSE '本人记录的正式跟进数' END,
 definition=CASE code WHEN 'opportunity_count' THEN '可信业务创建日期落目标周期，按当前负责人及当前负责团队和原授权范围，对商机去重。转交后随当前负责范围变化；不表示本人创建贡献。' ELSE '已确认或已归档、实际跟进日期落目标周期的正式跟进，个人按录入人、团队按原授权录入范围和记录部门规则，按记录去重；不将参与人重复记为录入贡献。' END,
 definition_version=2
 WHERE system_defined AND code IN ('opportunity_count','visit_count');
INSERT INTO crm.review_metric_history(workspace_id,code,version_no,snapshot)
 SELECT workspace_id,code,version_no,to_jsonb(m) FROM crm.review_metric m
 WHERE system_defined AND code IN ('opportunity_count','visit_count');

CREATE OR REPLACE FUNCTION security.seed_review_metrics(p_workspace uuid) RETURNS void
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 INSERT INTO crm.review_metric(workspace_id,code,name,unit,decimal_places,definition,source_kind,
  target_kind,goal_selectable,system_defined,definition_version)
 SELECT p_workspace,code,name,unit,precision,definition,source,kind,selectable,true,
  CASE WHEN code IN ('opportunity_count','visit_count') THEN 2 ELSE 1 END FROM (VALUES
 ('collection','回款','元',2,'已确认实际回款；不包含销售预测及任务贡献。','actual','collection',true),
 ('recognized','确收','元',2,'已确认实际确收；运营实际口径不含税，不自动换算销售预测。','actual','recognized',true),
 ('acv','ACV','元',2,'沿用既有ACV目标；口径未经确认时不自动填入预测或实际值。','manual','acv',true),
 ('opportunity_count','当前负责的本期新增商机数','个',0,'可信业务创建日期落目标周期，按当前负责人及当前负责团队和原授权范围，对商机去重。转交后随当前负责范围变化；不表示本人创建贡献。','actual','opportunity_count',true),
 ('visit_count','本人记录的正式跟进数','次',0,'已确认或已归档、实际跟进日期落目标周期的正式跟进，个人按录入人、团队按原授权录入范围和记录部门规则，按记录去重；不将参与人重复记为录入贡献。','actual','visit_count',true),
 ('demo_count','演示数','次',0,'沿用既有演示数量目标；无明确证据的进展须人工确认。','manual','demo_count',true),
 ('daily_routine','日常事务','项',0,'日常任务类别，不构造财务目标或业绩。','category',NULL,false),
 ('forecast_collection','预测回款','元',2,'该季度销售预测collection_amount且collection_confidence=high；非概率加权。','forecast','collection',false),
 ('collection_ppl','回款PPL储备','元',2,'该季度销售预测collection_amount且赢单概率>=30、非丢单，直接求和不加权；目标独立。','forecast','collection_ppl',true),
 ('forecast_recognized','预测确收','元',2,'该季度销售预测recognized_amount且赢单概率>=70、非丢单；预测与目标均含税，实际确收独立且不隐式换税。','forecast','forecast_recognized',true)
 ) AS d(code,name,unit,precision,definition,source,kind,selectable)
 ON CONFLICT(workspace_id,code) DO NOTHING;
 INSERT INTO crm.review_metric_history(workspace_id,code,version_no,snapshot)
 SELECT workspace_id,code,version_no,to_jsonb(m) FROM crm.review_metric m WHERE workspace_id=p_workspace
 ON CONFLICT DO NOTHING;
$$;
REVOKE ALL ON FUNCTION security.seed_review_metrics(uuid) FROM PUBLIC;

INSERT INTO ops.schema_migration(version,description)
 VALUES('V391','Versioned current-owner opportunity and recorder-based formal visit count definitions');
COMMIT;
