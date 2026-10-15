-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V198__feishu_customer_insight_targeted_sync.sql.
-- Original SHA-256: 500909a158632816d6dee0ab204378f70708af1e9909b1b00500ab35dcfdda79. See shenma_whole_121_manifest.json.
BEGIN;

-- Customer scores and risk titles are customer projection inputs. A single
-- insight change must not expand into an entire company's reconcile queue.
CREATE FUNCTION ops.capture_feishu_customer_insight_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
 current_row jsonb;
 prior_row jsonb;
 parent record;
 origin text;
BEGIN
 IF TG_TABLE_SCHEMA<>'insight' OR TG_TABLE_NAME NOT IN ('quadrant_score','risk') THEN
  RAISE EXCEPTION 'Unsupported Feishu customer insight trigger relation'
   USING ERRCODE='0A000';
 END IF;
 current_row:=CASE WHEN TG_OP='DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
 prior_row:=CASE WHEN TG_OP='UPDATE' THEN to_jsonb(OLD) ELSE '{}'::jsonb END;
 -- risk's existing BEFORE trigger touches audit metadata even for SET x=x.
 -- Keep that audit behavior, but do not enqueue a projection for audit alone.
 IF TG_OP='UPDATE' AND (current_row-ARRAY['updated_at','version_no'])=
  (prior_row-ARRAY['updated_at','version_no']) THEN RETURN NULL; END IF;
 origin:=CASE WHEN COALESCE(current_setting('app.feishu_historical_import',true),'')='on'
  OR (TG_OP='INSERT' AND COALESCE(current_row->'import_meta'->>'import_type','')='crm_history')
  THEN 'reconcile' ELSE 'business' END;

 -- Use each row's explicit parent and tenant. risk.customer_id may be NULL;
 -- the existing projection does not infer it from risk.opportunity_id.
 -- Do not skip pending events: an earlier worker may have read an old snapshot.
 FOR parent IN
  SELECT DISTINCT connection.id AS connection_id,c.workspace_id,c.id AS customer_id
  FROM jsonb_to_recordset(jsonb_build_array(current_row,prior_row))
   AS target(workspace_id uuid,customer_id uuid)
  JOIN crm.customer c ON c.id=target.customer_id AND c.workspace_id=target.workspace_id
  JOIN config.feishu_connection connection ON connection.workspace_id=c.workspace_id
 LOOP
  INSERT INTO ops.feishu_event(connection_id,workspace_id,object_kind,object_id,
   first_formal_create,historical,queue_origin)
  VALUES(parent.connection_id,parent.workspace_id,'customer',parent.customer_id,
   false,true,origin);
 END LOOP;
 RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION ops.capture_feishu_customer_insight_event() FROM PUBLIC;
-- Deployment-owner default privileges must not expose a new DEFINER helper.
-- Only this new function's ACL is changed; existing/default grants are intact.
DO $$ DECLARE granted record; BEGIN
 FOR granted IN SELECT DISTINCT roles.rolname
  FROM pg_proc p CROSS JOIN LATERAL aclexplode(p.proacl) acl
  JOIN pg_roles roles ON roles.oid=acl.grantee
  WHERE p.oid='ops.capture_feishu_customer_insight_event()'::regprocedure
   AND acl.grantee<>p.proowner
 LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION ops.capture_feishu_customer_insight_event() FROM %I',granted.rolname);
 END LOOP;
END $$;

DROP TRIGGER feishu_capture ON insight.quadrant_score;
CREATE TRIGGER feishu_capture AFTER INSERT OR DELETE OR UPDATE ON insight.quadrant_score
 FOR EACH ROW EXECUTE FUNCTION ops.capture_feishu_customer_insight_event();
DROP TRIGGER feishu_capture ON insight.risk;
CREATE TRIGGER feishu_capture AFTER INSERT OR DELETE OR UPDATE ON insight.risk
 FOR EACH ROW EXECUTE FUNCTION ops.capture_feishu_customer_insight_event();

INSERT INTO ops.schema_migration(version,description)
 VALUES('V346','Sync only affected customer projections for score and risk changes');
COMMIT;
