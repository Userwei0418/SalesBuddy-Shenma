BEGIN;
-- Resolve user-visible labels once for both the directory projection and the
-- immutable publication snapshot. Imported Chinese labels remain authoritative;
-- unknown codes use readable placeholders. No grants or queue changes.
-- CREATE OR REPLACE retains the existing function OID, owner and ACL.
CREATE OR REPLACE FUNCTION ops.feishu_source(p_connection uuid,p_kind text,p_id uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=off AS $$
DECLARE ws uuid; result jsonb; p knowledge.distribution_publication; boundary_revision integer;
 notification_id uuid; notification_revision integer;
BEGIN
 IF p_kind<>'knowledge' THEN RETURN ops.feishu_source_before_knowledge(p_connection,p_kind,p_id); END IF;
 SELECT workspace_id INTO ws FROM config.feishu_connection WHERE id=p_connection;
 IF ws IS NULL OR NOT security.workspace_is_active(ws) OR NOT security.workspace_module_enabled(ws,'feishu') THEN
  RAISE insufficient_privilege; END IF;
 SELECT * INTO p FROM knowledge.distribution_publication
  WHERE workspace_id=ws AND material_id=p_id ORDER BY revision DESC LIMIT 1;
 -- Group visibility must be company-wide with no personal deny. A public usage
 -- tag alone never widens a private material's actual ACL.
 IF p.id IS NULL OR NOT security.kd_publication_live(p.id) OR NOT EXISTS(
  SELECT 1 FROM knowledge.material_acl a WHERE a.workspace_id=ws AND a.material_id=p_id
   AND cardinality(a.denied_user_ids)=0 AND EXISTS(SELECT 1 FROM jsonb_array_elements(a.grants) g
    WHERE g->>'subject_kind'='company' AND g->'actions' ? 'read')) THEN
  RETURN jsonb_build_object('id',p_id,'excluded',true);
 END IF;
 -- enabled is mutable when a later publication supersedes a row. Its immutable
 -- publish audit records whether that revision was actually enabled or withdrawn.
 -- Missing audit evidence is a boundary too: never infer notification authority.
 SELECT max(h.revision) INTO boundary_revision FROM knowledge.distribution_publication h
 WHERE h.workspace_id=ws AND h.material_id=p_id AND h.revision<=p.revision
 AND (h.version_id<>p.version_id OR NOT EXISTS(SELECT 1 FROM knowledge.audit a
  WHERE a.workspace_id=ws AND a.material_id=p_id AND a.operation='distribution.publish'
   AND a.detail->>'publication_id'=h.id::text AND a.detail->'enabled'='true'::jsonb));
 SELECT h.id,h.revision INTO notification_id,notification_revision FROM knowledge.distribution_publication h
 WHERE h.workspace_id=ws AND h.material_id=p_id AND h.version_id=p.version_id
  AND h.revision>COALESCE(boundary_revision,0) AND h.revision<=p.revision
 ORDER BY h.revision LIMIT 1;
 SELECT jsonb_build_object('id',m.id,'content_name',v.contribution->>'content_name','created_at',m.created_at,'updated_at',p.published_at,
  'publication_id',p.id,'publication_revision',p.revision,'version_id',v.id,'version_no',v.version_no,
  'notification_publication_id',notification_id,'notification_publication_revision',notification_revision,
  'description',p.description,
  'product',COALESCE(
   CASE WHEN v.contribution->>'kind'='legacy_import' THEN NULLIF(v.contribution#>>'{source,product_label}','') END,
   (SELECT NULLIF(option->>'label','') FROM knowledge.catalog c
    CROSS JOIN LATERAL jsonb_array_elements(c.value->'products') option
    WHERE c.workspace_id=ws AND option->>'code'=v.contribution#>>'{selection,product_code}'),
   '未配置产品'),
  'category',COALESCE(
   CASE WHEN v.contribution->>'kind'='legacy_import' THEN NULLIF(v.contribution#>>'{source,category_label}','') END,
   CASE v.contribution#>>'{selection,content_category}'
    WHEN 'overall_solution' THEN '总方案' WHEN 'product_intro' THEN '产品介绍'
    WHEN 'industry_solution' THEN '行业方案' WHEN 'customer_solution' THEN '客户方案'
    WHEN 'sales_support' THEN '销售支持' WHEN 'training_assessment' THEN '培训考核'
    WHEN 'conference_speech' THEN '会议演讲'
    ELSE '未分类' END),
  'content_format',(SELECT string_agg(label,'、' ORDER BY label) FROM (
   SELECT DISTINCT CASE f.media_type
    WHEN 'application/pdf' THEN 'PDF文档' WHEN 'text/plain' THEN '文本文档'
    WHEN 'text/markdown' THEN 'Markdown文档' WHEN 'text/csv' THEN 'CSV表格'
    WHEN 'application/msword' THEN 'Word文档'
    WHEN 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' THEN 'Word文档'
    WHEN 'application/vnd.ms-powerpoint' THEN 'PPT演示文稿'
    WHEN 'application/vnd.openxmlformats-officedocument.presentationml.presentation' THEN 'PPT演示文稿'
    WHEN 'application/vnd.ms-excel' THEN 'Excel表格'
    WHEN 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' THEN 'Excel表格'
    WHEN 'image/png' THEN '图片' WHEN 'image/jpeg' THEN '图片' WHEN 'image/webp' THEN '图片' WHEN 'image/gif' THEN '图片'
    WHEN 'video/mp4' THEN '视频' WHEN 'video/webm' THEN '视频' WHEN 'video/quicktime' THEN '视频'
    WHEN 'audio/mpeg' THEN '音频' WHEN 'audio/wav' THEN '音频' WHEN 'audio/ogg' THEN '音频'
    ELSE '其他文件' END AS label
   FROM knowledge.version_file vf JOIN knowledge.file f ON f.workspace_id=vf.workspace_id AND f.id=vf.file_id
   WHERE vf.workspace_id=ws AND vf.version_id=v.id) formats),
  'provider_name',u.display_name,'published_at',p.published_at,'publication_status','已发布',
  'visits',COALESCE(stats.visits,0),'unique_visitors_estimate',COALESCE(stats.visitors,0),
  'previews',COALESCE(stats.previews,0),'download_requests',COALESCE(stats.downloads,0),
  'questionnaire_submissions',(SELECT count(*) FROM knowledge.distribution_response r
    JOIN knowledge.distribution_share ds ON ds.id=r.share_id AND ds.workspace_id=ws
    JOIN knowledge.distribution_publication dp ON dp.id=ds.publication_id AND dp.workspace_id=ws
    WHERE r.workspace_id=ws AND dp.material_id=p_id AND dp.questionnaire->>'enabled'='true')) INTO result
 FROM knowledge.material m JOIN knowledge.version v ON v.workspace_id=m.workspace_id AND v.material_id=m.id AND v.id=p.version_id
 LEFT JOIN platform.user_ref u ON u.workspace_id=m.workspace_id AND u.id=v.contributor_user_id
 LEFT JOIN LATERAL(SELECT count(*) FILTER(WHERE e.kind='visit') visits,
   count(DISTINCT e.browser_hash) FILTER(WHERE e.kind='visit' AND e.browser_hash<>'') visitors,
   count(*) FILTER(WHERE e.kind='preview') previews,count(*) FILTER(WHERE e.kind='download') downloads
   FROM knowledge.distribution_event e JOIN knowledge.distribution_share ds ON ds.id=e.share_id AND ds.workspace_id=ws
   JOIN knowledge.distribution_publication dp ON dp.id=ds.publication_id AND dp.workspace_id=ws
   WHERE e.workspace_id=ws AND dp.material_id=p_id) stats ON true
 WHERE m.workspace_id=ws AND m.id=p_id;
 RETURN COALESCE(result,jsonb_build_object('id',p_id,'excluded',true));
END $$;

INSERT INTO ops.schema_migration(version,description) VALUES('V424','Render knowledge Feishu product and category labels');
COMMIT;
