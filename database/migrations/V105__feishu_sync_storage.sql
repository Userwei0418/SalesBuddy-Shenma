BEGIN;
-- Dedicated worker membership is granted explicitly at deployment, never to PUBLIC/application roles here.
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='salegent_feishu_worker') THEN
  CREATE ROLE salegent_feishu_worker NOLOGIN NOSUPERUSER NOBYPASSRLS;
 END IF;
 IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='salegent_feishu_worker'
 AND (rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolcanlogin)) THEN
  RAISE EXCEPTION 'Unsafe existing salegent_feishu_worker role';
 END IF;
END $$;
CREATE TABLE config.feishu_connection (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 revision bigint NOT NULL CHECK(revision>0),
 enabled boolean NOT NULL DEFAULT false,
 settings jsonb NOT NULL,
 validated_revision bigint,
 next_reconcile_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 validation_requested boolean NOT NULL DEFAULT false,
 last_error_code text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_by uuid NOT NULL REFERENCES platform.user_ref(id),
 UNIQUE(workspace_id), UNIQUE(id,workspace_id),
 CHECK((settings->>'workspace_id'=workspace_id::text) IS TRUE),
 CHECK((settings->>'connection_id'=id::text) IS TRUE),
 CHECK(NOT enabled OR (validated_revision IS NOT NULL AND validated_revision=revision))
);
CREATE TABLE security.feishu_credential (
 connection_id uuid PRIMARY KEY REFERENCES config.feishu_connection(id),
 workspace_id uuid NOT NULL,
 ciphertext bytea NOT NULL,
 encryption_key_id text NOT NULL,
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(connection_id,workspace_id) REFERENCES config.feishu_connection(id,workspace_id)
);
CREATE TABLE ops.feishu_event (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 sequence_no bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
 connection_id uuid NOT NULL,
 workspace_id uuid NOT NULL,
 object_kind text NOT NULL CHECK(object_kind IN ('customer','opportunity','visit','partner','task','demo_scene','actual','target','member','contact','refresh')),
 object_id uuid NOT NULL,
 first_formal_create boolean NOT NULL DEFAULT false,
 historical boolean NOT NULL DEFAULT false,
 notification_planned boolean NOT NULL DEFAULT false,
 status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','running','succeeded','failed','dead_letter')),
 attempts integer NOT NULL DEFAULT 0,
 available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 lease_token uuid,
 locked_until timestamptz,
 error_code text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 completed_at timestamptz,
 FOREIGN KEY(connection_id,workspace_id) REFERENCES config.feishu_connection(id,workspace_id)
);
CREATE INDEX feishu_event_pending ON ops.feishu_event(status,available_at,sequence_no);
CREATE INDEX feishu_event_object ON ops.feishu_event(connection_id,object_kind,object_id,sequence_no);
CREATE TABLE ops.feishu_record_map (
 connection_id uuid NOT NULL,
 workspace_id uuid NOT NULL,
 object_kind text NOT NULL,
 object_id uuid NOT NULL,
 table_id text NOT NULL,
 record_id text NOT NULL,
 projection_hash text NOT NULL,
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(connection_id,object_kind,object_id),
 UNIQUE(connection_id,table_id,record_id),
 FOREIGN KEY(connection_id,workspace_id) REFERENCES config.feishu_connection(id,workspace_id)
);
CREATE TABLE ops.feishu_delivery (
 dedupe_key text PRIMARY KEY,
 event_id uuid NOT NULL REFERENCES ops.feishu_event(id),
 connection_id uuid NOT NULL,
 workspace_id uuid NOT NULL,
 config_revision bigint NOT NULL,
 chat_id text NOT NULL,
 payload jsonb NOT NULL,
 status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','sent','unknown','failed')),
 message_id text,
 error_code text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 sent_at timestamptz,
 FOREIGN KEY(connection_id,workspace_id) REFERENCES config.feishu_connection(id,workspace_id)
);
-- Append-only audit; never serialize credential rows, ciphertext or secret values.
CREATE TABLE ops.feishu_config_audit (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL,
 connection_id uuid NOT NULL, actor_user_ref_id uuid, database_actor text NOT NULL,
 action text NOT NULL, before_snapshot jsonb, after_snapshot jsonb,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER feishu_audit_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON ops.feishu_config_audit
 FOR EACH STATEMENT EXECUTE FUNCTION ops.deny_audit_mutation();
ALTER TABLE ops.feishu_config_audit ENABLE ROW LEVEL SECURITY;
CREATE POLICY company_management ON ops.feishu_config_audit FOR SELECT USING (
 workspace_id=common.current_workspace_id() AND
 (security.has_active_role('operations') OR security.has_active_role('administrator')));
CREATE FUNCTION ops.audit_feishu_config() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE prior jsonb; current_snapshot jsonb; cid uuid; ws uuid;
BEGIN
 IF TG_TABLE_SCHEMA='security' THEN
  cid:=NEW.connection_id; ws:=NEW.workspace_id;
  current_snapshot:=jsonb_build_object('credential_rotated',true,'encryption_key_id',NEW.encryption_key_id);
 ELSE
  cid:=NEW.id; ws:=NEW.workspace_id;
  current_snapshot:=jsonb_build_object('revision',NEW.revision,'enabled',NEW.enabled,
    'settings',NEW.settings,'validation_requested',NEW.validation_requested);
  IF TG_OP='UPDATE' THEN
   prior:=jsonb_build_object('revision',OLD.revision,'enabled',OLD.enabled,
    'settings',OLD.settings,'validation_requested',OLD.validation_requested);
   IF prior=current_snapshot THEN RETURN NULL; END IF;
  END IF;
 END IF;
 INSERT INTO ops.feishu_config_audit(workspace_id,connection_id,actor_user_ref_id,database_actor,
 action,before_snapshot,after_snapshot)
 VALUES(ws,cid,common.current_user_ref_id(),session_user,
 TG_TABLE_SCHEMA||'.'||TG_TABLE_NAME||'.'||lower(TG_OP),prior,current_snapshot);
 RETURN NULL;
END $$;
CREATE TRIGGER feishu_config_audit AFTER INSERT OR UPDATE ON config.feishu_connection
 FOR EACH ROW EXECUTE FUNCTION ops.audit_feishu_config();
CREATE TRIGGER feishu_credential_audit AFTER INSERT OR UPDATE ON security.feishu_credential
 FOR EACH ROW EXECUTE FUNCTION ops.audit_feishu_config();
-- Administrators see company status but cannot directly fetch encrypted secrets.
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['config.feishu_connection','ops.feishu_event','ops.feishu_record_map','ops.feishu_delivery'] LOOP
  EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY company_management ON %s USING (workspace_id=common.current_workspace_id() AND (security.has_active_role(''operations'') OR security.has_active_role(''administrator''))) WITH CHECK (workspace_id=common.current_workspace_id() AND (security.has_active_role(''operations'') OR security.has_active_role(''administrator'')))',t);
  EXECUTE format('CREATE POLICY sync_worker ON %s TO salegent_feishu_worker USING (true) WITH CHECK (true)',t);
 END LOOP;
END $$;
REVOKE ALL ON security.feishu_credential FROM PUBLIC;
GRANT USAGE ON SCHEMA config,ops,security,platform,crm,activity,workflow,common TO salegent_feishu_worker;
GRANT SELECT ON config.feishu_connection,security.feishu_credential TO salegent_feishu_worker;
GRANT UPDATE(validated_revision,validation_requested,last_error_code,next_reconcile_at) ON config.feishu_connection TO salegent_feishu_worker;
GRANT SELECT,INSERT,UPDATE ON ops.feishu_event,ops.feishu_record_map,ops.feishu_delivery TO salegent_feishu_worker;
GRANT USAGE ON SEQUENCE ops.feishu_event_sequence_no_seq TO salegent_feishu_worker;
CREATE FUNCTION security.set_feishu_credential(p_connection uuid,p_cipher bytea,p_key text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF NOT (security.has_active_role('operations') OR security.has_active_role('administrator'))
 OR NOT EXISTS(SELECT 1 FROM config.feishu_connection WHERE id=p_connection AND workspace_id=common.current_workspace_id())
 THEN RAISE insufficient_privilege; END IF;
 INSERT INTO security.feishu_credential(connection_id,workspace_id,ciphertext,encryption_key_id)
 VALUES(p_connection,common.current_workspace_id(),p_cipher,p_key)
 ON CONFLICT(connection_id) DO UPDATE SET ciphertext=excluded.ciphertext,encryption_key_id=excluded.encryption_key_id,updated_at=clock_timestamp();
 UPDATE config.feishu_connection SET enabled=false,validated_revision=NULL WHERE id=p_connection;
END $$;
CREATE FUNCTION security.has_feishu_credential(p_connection uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM security.feishu_credential c WHERE c.connection_id=p_connection AND c.workspace_id=common.current_workspace_id()
 AND (security.has_active_role('operations') OR security.has_active_role('administrator')));
$$;
CREATE FUNCTION ops.capture_feishu_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r jsonb; prior jsonb; ws uuid; cid uuid; kind text:=TG_ARGV[0]; oid uuid; fresh boolean:=false; live boolean:=false;
BEGIN
 r:=CASE WHEN TG_OP='DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
 prior:=CASE WHEN TG_OP='UPDATE' THEN to_jsonb(OLD) ELSE '{}'::jsonb END;
 ws:=(r->>'workspace_id')::uuid;
 SELECT id,enabled AND COALESCE((settings->'notification'->>'enabled')::boolean,false) INTO cid,live FROM config.feishu_connection WHERE workspace_id=ws;
 IF cid IS NULL THEN RETURN NULL; END IF;
 IF TG_OP='UPDATE' AND r=prior THEN RETURN NULL; END IF;
 oid:=(r->>'id')::uuid;
 IF kind='visit' THEN
  IF r->>'archived_at' IS NULL THEN RETURN NULL; END IF;
  fresh:=TG_OP='INSERT' OR prior->>'archived_at' IS NULL;
 ELSIF kind='refresh' THEN
  oid:=ws;
 ELSE fresh:=TG_OP='INSERT'; END IF;
 -- Keep exits from production: the worker emits a status-only tombstone.
 IF TG_OP='UPDATE' AND (kind='member' OR (kind IN ('customer','opportunity') AND (r->>'data_kind' IS DISTINCT FROM prior->>'data_kind' OR r->>'name' IS DISTINCT FROM prior->>'name'))) THEN
  INSERT INTO ops.feishu_event(connection_id,workspace_id,object_kind,object_id,historical)
  VALUES(cid,ws,'refresh',ws,true);
 END IF;
 INSERT INTO ops.feishu_event(connection_id,workspace_id,object_kind,object_id,first_formal_create,historical)
 VALUES(cid,ws,kind,oid,fresh AND TG_OP<>'DELETE',NOT live OR kind='refresh'
 OR COALESCE(r->'import_meta'->>'import_type','')='crm_history'
 OR COALESCE(prior->'import_meta'->>'import_type','')='crm_history'
 OR COALESCE(current_setting('app.feishu_historical_import',true),'')='on');
 RETURN NULL;
END $$;
-- Capture all write paths, including SQL business functions and approved imports.
DO $$ DECLARE item text[]; BEGIN
 FOREACH item SLICE 1 IN ARRAY ARRAY[
  ['crm.customer','customer'],['crm.opportunity','opportunity'],['activity.visit','visit'],
  ['crm.partner','partner'],['workflow.task','task'],['crm.opportunity_demo_scenes','demo_scene'],
  ['crm.customer_actual','actual'],['crm.sales_target','target'],['platform.user_ref','member'],['crm.contact','contact'],
  ['platform.team','refresh'],['platform.team_membership','refresh'],['platform.role_binding','refresh'],
  ['crm.customer_sales_member','refresh'],['workflow.task_assignee','refresh'],
  ['crm.opportunity_participant','refresh'],['activity.visit_participant','refresh'],
  ['insight.quadrant_score','refresh'],['insight.risk','refresh']
 ] LOOP
  EXECUTE format('CREATE TRIGGER feishu_capture AFTER INSERT OR UPDATE OR DELETE ON %s FOR EACH ROW EXECUTE FUNCTION ops.capture_feishu_event(%L)',item[1],item[2]);
 END LOOP;
END $$;
-- Explicit grants only to existing application writers; secret table remains private.
DO $$ DECLARE r record; BEGIN
 FOR r IN SELECT DISTINCT grantee FROM information_schema.role_table_grants WHERE table_schema='crm' AND table_name='customer'
 AND privilege_type='INSERT' AND grantee NOT IN ('PUBLIC','salegent_feishu_worker') LOOP
  EXECUTE format('GRANT SELECT,INSERT,UPDATE ON config.feishu_connection TO %I',r.grantee);
  EXECUTE format('GRANT SELECT ON ops.feishu_event,ops.feishu_record_map,ops.feishu_delivery,ops.feishu_config_audit TO %I',r.grantee);
 END LOOP;
END $$;
-- Worker-only bounded source projections; never returns full rows or credential tables.
CREATE FUNCTION ops.feishu_source(p_connection uuid,p_kind text,p_id uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ws uuid; source_table text; allowed text[]; raw jsonb; result jsonb;
BEGIN
 SELECT workspace_id INTO ws FROM config.feishu_connection WHERE id=p_connection;
 IF ws IS NULL THEN RAISE insufficient_privilege; END IF;
 CASE p_kind
 WHEN 'customer' THEN source_table:='crm.customer'; allowed:=ARRAY['name','industry_code','customer_type_code','level_code','main_business','demand_summary','customer_budget','next_action','owner_user_ref_id','owner_team_id','data_kind','lifecycle_status'];
 WHEN 'opportunity' THEN source_table:='crm.opportunity'; allowed:=ARRAY['name','customer_id','stage_code','amount','currency','probability','expected_close_date','status','product_line','sales_channel','partner_id','owner_user_ref_id','owner_team_id','data_kind'];
 WHEN 'visit' THEN source_table:='activity.visit'; allowed:=ARRAY['customer_id','opportunity_id','follow_up_record','next_action','interaction_at','archived_at','recorder_user_ref_id','archived_fields','status','recorder_team_id','interaction_mode_code','contact_name_snapshot','contact_title_snapshot','visit_goal','visit_location','contact_category_snapshot','partner_name_snapshot','is_first_visit','follow_up_score'];
 WHEN 'partner' THEN source_table:='crm.partner'; allowed:=ARRAY['name','status'];
 WHEN 'task' THEN source_table:='workflow.task'; allowed:=ARRAY['title','description','status','due_at','creator_user_ref_id','creator_team_id','customer_id','opportunity_id','completion_note','completion_review_note'];
 WHEN 'demo_scene' THEN source_table:='crm.opportunity_demo_scenes'; allowed:=ARRAY['name','description','opportunity_id','created_by'];
 WHEN 'actual' THEN source_table:='crm.customer_actual'; allowed:=ARRAY['kind','amount','occurred_on','customer_id','opportunity_id','source_ref','note','confirmed_by_user_ref_id','voided_at','void_reason'];
 WHEN 'target' THEN source_table:='crm.sales_target'; allowed:=ARRAY['period_type','period_start','period_end','scope_type','user_ref_id','team_id','department_code','kind','amount'];
 WHEN 'member' THEN source_table:='platform.user_ref'; allowed:=ARRAY['display_name','account_code','status'];
 WHEN 'contact' THEN source_table:='crm.contact'; allowed:=ARRAY['name','title','relationship_role_code','customer_id','is_primary'];
 ELSE RAISE invalid_parameter_value; END CASE;
 EXECUTE format('SELECT to_jsonb(r) FROM %s r WHERE r.id=$1 AND r.workspace_id=$2',source_table) INTO raw USING p_id,ws;
 IF raw IS NULL THEN RETURN jsonb_build_object('id',p_id,'deleted',true); END IF;
 allowed:=allowed||ARRAY['id','created_at','updated_at','deleted_at','version_no'];
 SELECT jsonb_object_agg(key,value) INTO result FROM jsonb_each(raw) WHERE key=ANY(allowed);
 IF p_kind='task' THEN
  result:=result||jsonb_build_object('owner_id',(SELECT assignee_user_ref_id FROM workflow.task_assignee WHERE task_id=p_id AND workspace_id=ws AND responsibility='owner' ORDER BY assigned_at DESC LIMIT 1));
 END IF;
 IF p_kind='member' THEN
  result:=result||jsonb_build_object('department_ids',(SELECT COALESCE(jsonb_agg(DISTINCT m.team_id),'[]'::jsonb) FROM platform.team_membership m WHERE m.workspace_id=ws AND m.user_ref_id=p_id AND clock_timestamp()>=m.valid_from AND clock_timestamp()<m.valid_to),
  'primary_department_id',(SELECT m.team_id FROM platform.team_membership m WHERE m.workspace_id=ws AND m.user_ref_id=p_id AND m.is_primary AND clock_timestamp()>=m.valid_from AND clock_timestamp()<m.valid_to ORDER BY m.created_at DESC LIMIT 1),
  'roles',(SELECT COALESCE(jsonb_agg(DISTINCT r.role_code),'[]'::jsonb) FROM platform.role_binding r WHERE r.workspace_id=ws AND r.user_ref_id=p_id AND clock_timestamp()>=r.valid_from AND clock_timestamp()<r.valid_to));
 END IF;
 -- Fail closed for explicitly referenced parents, including missing/cross-company rows.
 IF result->>'customer_id' IS NOT NULL AND NOT EXISTS(
  SELECT 1 FROM crm.customer c WHERE c.id=(result->>'customer_id')::uuid AND c.workspace_id=ws
   AND c.data_kind='production' AND c.deleted_at IS NULL) THEN
  RETURN jsonb_build_object('id',p_id,'excluded',true);
 END IF;
 IF result->>'opportunity_id' IS NOT NULL AND NOT EXISTS(
  SELECT 1 FROM crm.opportunity o JOIN crm.customer c ON c.id=o.customer_id AND c.workspace_id=o.workspace_id
  WHERE o.id=(result->>'opportunity_id')::uuid AND o.workspace_id=ws
   AND o.deleted_at IS NULL AND c.data_kind='production' AND c.deleted_at IS NULL) THEN
  RETURN jsonb_build_object('id',p_id,'excluded',true);
 END IF;
 IF COALESCE(result->>'data_kind','production')<>'production' THEN
  RETURN jsonb_build_object('id',p_id,'excluded',true);
 END IF;
 IF p_kind='customer' THEN
  result:=result||COALESCE((SELECT jsonb_build_object('potential_score',q.potential_score,
   'relationship_score',q.relationship_score,'quadrant_code',q.quadrant_code)
   FROM insight.quadrant_score q WHERE q.workspace_id=ws AND q.customer_id=p_id AND q.valid_to='infinity'
   ORDER BY q.calculated_at DESC,q.id DESC LIMIT 1),'{}'::jsonb);
  result:=result||jsonb_build_object('risk_title',(SELECT r.title FROM insight.risk r
   WHERE r.workspace_id=ws AND r.customer_id=p_id AND r.deleted_at IS NULL
   AND (r.opportunity_id IS NULL OR EXISTS(SELECT 1 FROM crm.opportunity ro WHERE ro.id=r.opportunity_id
    AND ro.workspace_id=ws AND ro.deleted_at IS NULL))
   AND r.status IN ('new','pending','in_progress','escalated')
   ORDER BY CASE r.severity_code WHEN 'critical' THEN 1 WHEN 'high' THEN 2 ELSE 3 END,r.opened_at DESC,r.id LIMIT 1),
   'sales_members',(SELECT COALESCE(jsonb_agg(u.display_name ORDER BY u.display_name,u.id),'[]'::jsonb)
   FROM crm.customer_sales_member m JOIN platform.user_ref u ON u.id=m.user_ref_id AND u.workspace_id=m.workspace_id
   WHERE m.workspace_id=ws AND m.customer_id=p_id));
 END IF;
 IF p_kind IN ('customer','opportunity') THEN
  result:=result||jsonb_build_object('fde_members',(SELECT COALESCE(jsonb_agg(names.display_name ORDER BY names.display_name,names.id),'[]'::jsonb)
   FROM (SELECT DISTINCT u.id,u.display_name FROM crm.opportunity_participant p
    JOIN crm.opportunity o ON o.id=p.opportunity_id AND o.workspace_id=p.workspace_id
    JOIN platform.user_ref u ON u.id=p.user_ref_id AND u.workspace_id=p.workspace_id
    WHERE p.workspace_id=ws AND p.participant_role='fde' AND clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to
    AND o.deleted_at IS NULL AND u.status='active'
    AND ((p_kind='customer' AND o.customer_id=p_id) OR (p_kind='opportunity' AND o.id=p_id))) names));
 END IF;
 IF p_kind='visit' THEN
  result:=result||jsonb_build_object('collaborators',(SELECT COALESCE(jsonb_agg(u.display_name ORDER BY u.display_name,u.id),'[]'::jsonb)
   FROM activity.visit_participant p JOIN platform.user_ref u ON u.id=p.user_ref_id AND u.workspace_id=p.workspace_id
   WHERE p.workspace_id=ws AND p.visit_id=p_id));
 END IF;
 result:=result||jsonb_build_object('company_name',(SELECT name FROM platform.workspace WHERE id=ws));
 IF p_kind IN ('customer','opportunity','visit') THEN
  result:=result||jsonb_build_object(
   'owner_name',(SELECT display_name FROM platform.user_ref WHERE workspace_id=ws AND id=COALESCE((result->>'owner_user_ref_id')::uuid,(result->>'recorder_user_ref_id')::uuid)),
   'owner_account',(SELECT account_code FROM platform.user_ref WHERE workspace_id=ws AND id=COALESCE((result->>'owner_user_ref_id')::uuid,(result->>'recorder_user_ref_id')::uuid)),
   'department_name',(SELECT name FROM platform.team WHERE workspace_id=ws AND id=COALESCE((result->>'owner_team_id')::uuid,(result->>'recorder_team_id')::uuid)));
 END IF;
 IF result->>'customer_id' IS NOT NULL THEN
  result:=result||jsonb_build_object('customer_name',(SELECT name FROM crm.customer WHERE workspace_id=ws AND id=(result->>'customer_id')::uuid));
 END IF;
 IF p_kind='visit' THEN
  result:=result||jsonb_build_object('name',COALESCE(NULLIF(result->>'visit_goal',''),left(result->>'follow_up_record',60),'客户跟进'),
   'opportunity_name',(SELECT name FROM crm.opportunity WHERE workspace_id=ws AND id=(result->>'opportunity_id')::uuid));
 END IF;
 RETURN result;
END $$;
CREATE FUNCTION ops.feishu_reconcile(p_connection uuid) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ws uuid; item text[]; total bigint:=0; n bigint;
BEGIN
 SELECT workspace_id INTO ws FROM config.feishu_connection WHERE id=p_connection AND enabled;
 IF ws IS NULL THEN RETURN 0; END IF;
 FOREACH item SLICE 1 IN ARRAY ARRAY[['crm.customer','customer'],['crm.opportunity','opportunity'],['activity.visit','visit'],['crm.partner','partner'],['workflow.task','task'],['crm.opportunity_demo_scenes','demo_scene'],['crm.customer_actual','actual'],['crm.sales_target','target'],['platform.user_ref','member'],['crm.contact','contact']] LOOP
  IF NOT EXISTS(SELECT 1 FROM config.feishu_connection c WHERE c.id=p_connection AND (c.settings->'mappings'->item[2]->>'enabled')::boolean IS TRUE) THEN CONTINUE; END IF;
  EXECUTE format('INSERT INTO ops.feishu_event(connection_id,workspace_id,object_kind,object_id,historical) SELECT $1,$2,$3,id,true FROM %s s WHERE workspace_id=$2 AND NOT EXISTS(SELECT 1 FROM ops.feishu_event e WHERE e.connection_id=$1 AND e.object_kind=$3 AND e.object_id=s.id AND e.status IN (''pending'',''running'',''failed''))',item[1]) USING p_connection,ws,item[2];
  GET DIAGNOSTICS n=ROW_COUNT; total:=total+n;
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION ops.feishu_reconcile(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.feishu_reconcile(uuid) TO salegent_feishu_worker;
REVOKE ALL ON FUNCTION ops.feishu_source(uuid,text,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.feishu_source(uuid,text,uuid) TO salegent_feishu_worker;
-- Explicit, paused target migration; old remote records remain untouched.
CREATE FUNCTION security.prepare_feishu_migration(p_connection uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ws uuid; live boolean;
BEGIN
 SELECT workspace_id,enabled INTO ws,live FROM config.feishu_connection
 WHERE id=p_connection AND workspace_id=common.current_workspace_id() FOR UPDATE;
 IF ws IS NULL OR NOT (security.has_active_role('operations') OR security.has_active_role('administrator')) THEN
  RAISE insufficient_privilege;
 END IF;
 IF live OR EXISTS(SELECT 1 FROM ops.feishu_event WHERE connection_id=p_connection AND status='running') THEN
  RAISE EXCEPTION 'Pause synchronization and wait for running operations before migrating' USING ERRCODE='22023';
 END IF;
 IF EXISTS(SELECT 1 FROM ops.feishu_delivery WHERE connection_id=p_connection AND status='unknown') THEN
  RAISE EXCEPTION 'Resolve unknown notification results before migrating' USING ERRCODE='22023';
 END IF;
 UPDATE ops.feishu_event SET status='dead_letter',error_code='TARGET_MIGRATED',
 completed_at=clock_timestamp(),notification_planned=true
 WHERE connection_id=p_connection AND status IN ('pending','failed','dead_letter');
 UPDATE ops.feishu_delivery SET status='failed',error_code='TARGET_MIGRATED'
 WHERE connection_id=p_connection AND status='pending';
 DELETE FROM ops.feishu_record_map WHERE connection_id=p_connection;
 INSERT INTO ops.feishu_config_audit(workspace_id,connection_id,actor_user_ref_id,database_actor,action,after_snapshot)
 VALUES(ws,p_connection,common.current_user_ref_id(),session_user,'target_migration',
 jsonb_build_object('old_remote_records_preserved',true,'backfill_notification',false));
END $$;
CREATE FUNCTION security.feishu_initialize(p_connection uuid) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ws uuid; item text[]; n bigint; total bigint:=0;
BEGIN
 SELECT workspace_id INTO ws FROM config.feishu_connection WHERE id=p_connection AND workspace_id=common.current_workspace_id();
 IF ws IS NULL OR NOT (security.has_active_role('operations') OR security.has_active_role('administrator')) THEN RAISE insufficient_privilege; END IF;
 FOREACH item SLICE 1 IN ARRAY ARRAY[['crm.customer','customer'],['crm.opportunity','opportunity'],['activity.visit','visit'],['crm.partner','partner'],['workflow.task','task'],['crm.opportunity_demo_scenes','demo_scene'],['crm.customer_actual','actual'],['crm.sales_target','target'],['platform.user_ref','member'],['crm.contact','contact']] LOOP
  EXECUTE format('INSERT INTO ops.feishu_event(connection_id,workspace_id,object_kind,object_id,historical) SELECT $1,$2,$3,id,true FROM %s s WHERE workspace_id=$2 AND NOT EXISTS(SELECT 1 FROM ops.feishu_event e WHERE e.connection_id=$1 AND e.object_kind=$3 AND e.object_id=s.id AND e.status IN (''pending'',''running'',''failed''))',item[1]) USING p_connection,ws,item[2];
  GET DIAGNOSTICS n=ROW_COUNT; total:=total+n;
 END LOOP;
 INSERT INTO ops.feishu_config_audit(workspace_id,connection_id,actor_user_ref_id,database_actor,action,after_snapshot)
 VALUES(ws,p_connection,common.current_user_ref_id(),session_user,'initialize',jsonb_build_object('queued',total));
 RETURN total;
END $$;
-- Recovery is explicit, company-scoped, paused and audited. Unknown sends are
-- never replayed: an operator verifies the group, then marks delivered or suppressed.
CREATE FUNCTION security.feishu_recover(p_connection uuid,p_event uuid,p_action text,p_key text,p_note text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ws uuid; live boolean; event_row ops.feishu_event%ROWTYPE; previous text;
BEGIN
 SELECT workspace_id,enabled INTO ws,live FROM config.feishu_connection
 WHERE id=p_connection AND workspace_id=common.current_workspace_id() FOR UPDATE;
 IF ws IS NULL OR NOT (security.has_active_role('operations') OR security.has_active_role('administrator')) THEN
  RAISE insufficient_privilege;
 END IF;
 IF live OR EXISTS(SELECT 1 FROM ops.feishu_event WHERE connection_id=p_connection AND status='running') THEN
  RAISE EXCEPTION 'Pause and wait for running operations before recovery' USING ERRCODE='22023';
 END IF;
 IF p_note IS NULL OR length(trim(p_note)) NOT BETWEEN 1 AND 1000 THEN
  RAISE EXCEPTION 'Recovery note is required' USING ERRCODE='22023';
 END IF;
 SELECT * INTO event_row FROM ops.feishu_event WHERE id=p_event AND connection_id=p_connection FOR UPDATE;
 IF NOT FOUND THEN RAISE insufficient_privilege; END IF;
 IF p_action='retry' THEN
  IF event_row.status NOT IN ('failed','dead_letter') OR event_row.error_code='TARGET_MIGRATED'
    OR EXISTS(SELECT 1 FROM ops.feishu_delivery WHERE event_id=p_event AND status='unknown') THEN
   RAISE EXCEPTION 'Event is not eligible for retry' USING ERRCODE='22023';
  END IF;
  UPDATE ops.feishu_event SET status='pending',attempts=0,error_code=NULL,completed_at=NULL,
   lease_token=NULL,locked_until=NULL,available_at=clock_timestamp() WHERE id=p_event;
 ELSIF p_action IN ('confirm_sent','suppress') THEN
  SELECT status INTO previous FROM ops.feishu_delivery
   WHERE event_id=p_event AND connection_id=p_connection AND dedupe_key=p_key FOR UPDATE;
  IF previous IS DISTINCT FROM 'unknown' THEN
   RAISE EXCEPTION 'Notification is not awaiting verification' USING ERRCODE='22023';
  END IF;
  UPDATE ops.feishu_delivery SET status=CASE WHEN p_action='confirm_sent' THEN 'sent' ELSE 'failed' END,
   error_code=CASE WHEN p_action='suppress' THEN 'OPERATOR_SUPPRESSED' ELSE NULL END,
   sent_at=CASE WHEN p_action='confirm_sent' THEN clock_timestamp() ELSE sent_at END WHERE dedupe_key=p_key;
 ELSE RAISE EXCEPTION 'Unknown recovery action' USING ERRCODE='22023'; END IF;
 INSERT INTO ops.feishu_config_audit(workspace_id,connection_id,actor_user_ref_id,database_actor,
  action,before_snapshot,after_snapshot)
 VALUES(ws,p_connection,common.current_user_ref_id(),session_user,'recovery',
  jsonb_build_object('event_status',event_row.status,'notification_status',previous),
  jsonb_build_object('event_id',p_event,'action',p_action,'delivery_key',p_key,'note',trim(p_note)));
END $$;
-- SECURITY DEFINER functions inherited through PUBLIC otherwise bypass table ACLs.
-- Preserve existing non-worker roles' effective execution rights explicitly before
-- revoking PUBLIC. Future application roles need explicit grants; worker only gets
-- its two bounded projection/reconciliation entry points.
DO $$ DECLARE f record; r record; signature text;
BEGIN
 FOR f IN SELECT p.oid,p.pronamespace,n.nspname,p.proname
  FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
  WHERE p.prosecdef AND p.prokind='f' AND n.nspname NOT IN ('pg_catalog','information_schema')
   AND p.oid<>'security.has_active_role(text)'::regprocedure
 LOOP
  signature:=f.oid::regprocedure::text;
  FOR r IN SELECT rolname FROM pg_roles WHERE NOT pg_has_role(oid,'salegent_feishu_worker','member')
   AND rolname NOT LIKE 'pg\_%' ESCAPE '\'
   AND has_schema_privilege(oid,f.pronamespace,'USAGE')
   AND has_function_privilege(oid,f.oid,'EXECUTE')
  LOOP EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO %I',signature,r.rolname); END LOOP;
  EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC',signature);
 END LOOP;
END $$;
COMMENT ON TABLE config.feishu_connection IS '公司飞书单向同步连接；默认关闭，验证版本匹配才可启用';
COMMENT ON TABLE ops.feishu_event IS '事务内只保存对象身份，不复制敏感业务原文；专用Worker消费';
COMMIT;
