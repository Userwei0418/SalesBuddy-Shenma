-- Old customer-wide analysis may contain another salesperson's opportunity facts.
-- Keep audit history, expire its current projection, and rebuild through the real worker.
ALTER TABLE insight.quadrant_score ADD COLUMN scope_verified boolean NOT NULL DEFAULT true;
UPDATE insight.quadrant_score q SET scope_verified=false,
 valid_to=LEAST(valid_to,clock_timestamp())
 WHERE q.calculated_at < (SELECT applied_at FROM ops.schema_migration WHERE version='V043')
 AND EXISTS(SELECT 1 FROM crm.opportunity o WHERE o.customer_id=q.customer_id
   AND o.deleted_at IS NULL AND o.owner_user_ref_id IS DISTINCT FROM q.subject_user_ref_id);
CREATE POLICY quadrant_verified_scope ON insight.quadrant_score AS RESTRICTIVE FOR SELECT
 USING(common.current_role_code()<>'sales' OR scope_verified);

ALTER TABLE insight.recommendation ADD COLUMN scope_verified boolean NOT NULL DEFAULT true;
UPDATE insight.recommendation r SET scope_verified=false,expires_at=clock_timestamp()
 WHERE r.generated_at < (SELECT applied_at FROM ops.schema_migration WHERE version='V043')
 AND EXISTS(SELECT 1 FROM crm.opportunity o WHERE o.customer_id=r.customer_id
   AND o.deleted_at IS NULL AND o.owner_user_ref_id IS DISTINCT FROM r.subject_user_ref_id);
CREATE POLICY recommendation_verified_scope ON insight.recommendation AS RESTRICTIVE FOR SELECT
 USING(common.current_role_code()<>'sales' OR scope_verified);

-- Formal field differences stay intact; only unscoped historical AI commentary is retired.
UPDATE workflow.notification n SET payload=payload-'ai_review'
 WHERE n.template_code='business_changed' AND n.payload ? 'ai_review'
 AND n.created_at < (SELECT applied_at FROM ops.schema_migration WHERE version='V043')
 AND EXISTS(SELECT 1 FROM crm.opportunity o WHERE o.customer_id::text=n.payload->>'customer_id'
   AND o.deleted_at IS NULL AND o.owner_user_ref_id IS DISTINCT FROM n.recipient_user_ref_id);

-- Rebuild absent/current-invalid personal maps, without changing any human business facts.
INSERT INTO ops.job(workspace_id,job_type,aggregate_type,aggregate_id,payload,priority,correlation_id)
SELECT m.workspace_id,'battle_map.review','customer',m.customer_id,
 jsonb_build_object('workspace_id',m.workspace_id,'user_id',m.user_ref_id,'role','sales','data_scope','self',
 'team_ids',COALESCE((SELECT jsonb_agg(tm.team_id) FROM platform.team_membership tm
   WHERE tm.user_ref_id=m.user_ref_id AND tm.workspace_id=m.workspace_id
   AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to),'[]'::jsonb),
 'trigger_type','permission.scope_refreshed','trigger_id',m.customer_id),50,m.customer_id
FROM crm.customer_sales_member m JOIN platform.user_ref u ON u.id=m.user_ref_id AND u.workspace_id=m.workspace_id
JOIN crm.customer c ON c.id=m.customer_id AND c.deleted_at IS NULL
WHERE u.deleted_at IS NULL AND u.status='active'
 AND EXISTS(SELECT 1 FROM platform.role_binding rb WHERE rb.user_ref_id=u.id AND rb.workspace_id=u.workspace_id
   AND rb.role_code='sales' AND clock_timestamp()>=rb.valid_from AND clock_timestamp()<rb.valid_to)
 AND NOT EXISTS(SELECT 1 FROM insight.quadrant_score q WHERE q.customer_id=m.customer_id
   AND q.subject_user_ref_id=m.user_ref_id AND q.scope_verified AND q.valid_to='infinity');
INSERT INTO ops.schema_migration(version,description)
 VALUES('V045','旧客户级分析按个人商机权限失效并重建，保留历史审计');
