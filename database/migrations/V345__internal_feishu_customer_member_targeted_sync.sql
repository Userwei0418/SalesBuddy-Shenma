-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V197__feishu_customer_member_targeted_sync.sql.
-- Original SHA-256: 278c6f16514da6dc7936f4e9183f959b4557c578bc53aad023efa0338cb3c5ef. See shenma_whole_121_manifest.json.
BEGIN;

-- A membership row has a composite identity, not the parent's customer ID.
-- The generic capture('refresh') expanded every owner restoration into a
-- company-wide reconcile. Keep all other capture triggers/functions unchanged.
CREATE FUNCTION ops.capture_feishu_customer_member_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
 current_row jsonb;
 prior_row jsonb;
 parent record;
 origin text;
BEGIN
 IF TG_TABLE_SCHEMA<>'crm' OR TG_TABLE_NAME<>'customer_sales_member' THEN
  RAISE EXCEPTION 'Unsupported Feishu customer membership trigger relation'
   USING ERRCODE='0A000';
 END IF;
 current_row:=CASE WHEN TG_OP='DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
 prior_row:=CASE WHEN TG_OP='UPDATE' THEN to_jsonb(OLD) ELSE '{}'::jsonb END;
 IF TG_OP='UPDATE' AND current_row=prior_row THEN RETURN NULL; END IF;

 -- Membership changes are never a first customer creation or a group notice.
 -- Historical imports stay in the paced queue; ordinary membership edits stay
 -- in the business queue even when their parent was originally imported.
 origin:=CASE WHEN COALESCE(current_setting('app.feishu_historical_import',true),'')='on'
  THEN 'reconcile' ELSE 'business' END;

 -- Moving a membership refreshes both parents once, using each row's own
 -- tenant, not the caller's ambient tenant context. Missing parents need no
 -- projection (including a future cascading delete); the customer trigger owns
 -- deletion/tombstone events. Existing demo/deleted parents still go through
 -- the worker's normal excluded/deleted projection, never through a new path.
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
REVOKE ALL ON FUNCTION ops.capture_feishu_customer_member_event() FROM PUBLIC;

DROP TRIGGER feishu_capture ON crm.customer_sales_member;
CREATE TRIGGER feishu_capture AFTER INSERT OR DELETE OR UPDATE ON crm.customer_sales_member
 FOR EACH ROW EXECUTE FUNCTION ops.capture_feishu_customer_member_event();

INSERT INTO ops.schema_migration(version,description)
 VALUES('V345','Sync only affected customers for customer sales membership changes');
COMMIT;
