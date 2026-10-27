-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V222__work_reports.sql.
-- Original SHA-256: 97a72b7dc88f89edfcdb8dd648d1f8469a550fd46c456e4b0a00d5352c24e5ca. See shenma_whole_121_manifest.json.
BEGIN;

-- Independent report capability. No changes to operating_report or source RLS.
INSERT INTO config.permission_catalog(code,label,module,scopes,sensitive) VALUES
 ('work_report.read','查看工作汇报','工作汇报',ARRAY['self','assigned','teams','workspace'],false),
 ('work_report.write','创建与编辑本人工作汇报','工作汇报',ARRAY['self','assigned','teams','workspace'],false),
 ('work_report.share','确认分享本人工作汇报','工作汇报',ARRAY['self','assigned','teams','workspace'],true),
 ('agent.work_report','生成工作汇报','智能助手',ARRAY['self','assigned','teams','workspace'],false);
INSERT INTO control.permission_module(permission_code,module_code) VALUES
 ('work_report.read','core'),('work_report.write','core'),('work_report.share','core'),('agent.work_report','ai');
-- Preserve each existing built-in report scope instead of granting company-wide
-- access from role names. Custom roles are configured explicitly by their owner.
INSERT INTO config.permission_role_grant(workspace_id,role_id,permission_code,scope_code,team_ids)
 SELECT g.workspace_id,g.role_id,n.code,g.scope_code,g.team_ids
 FROM config.permission_role_grant g JOIN config.permission_role r ON r.id=g.role_id
 CROSS JOIN (VALUES('work_report.read'),('work_report.write'),('work_report.share'),('agent.work_report')) n(code)
 WHERE g.permission_code='agent.operating_report' AND r.builtin_role_code IS NOT NULL;
UPDATE config.permission_role_default d SET permissions=d.permissions||COALESCE((
 SELECT jsonb_agg((p-'permission')||jsonb_build_object('permission',n.code))
 FROM jsonb_array_elements(d.permissions) p
 CROSS JOIN (VALUES('work_report.read'),('work_report.write'),('work_report.share'),('agent.work_report')) n(code)
 WHERE p->>'permission'='agent.operating_report'),'[]'::jsonb);
UPDATE config.permission_role SET version_no=version_no+1,updated_at=clock_timestamp()
 WHERE builtin_role_code IS NOT NULL AND EXISTS(SELECT 1 FROM config.permission_role_grant g
 WHERE g.role_id=config.permission_role.id AND g.permission_code='work_report.read');
UPDATE config.authorization_scope_revision SET revision=revision+1;

CREATE FUNCTION agent.work_report_source_manifest(p_payload jsonb) RETURNS jsonb
 LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT COALESCE(jsonb_agg(jsonb_build_object('source_type',t.kind,'record_id',t.id) ORDER BY t.kind,t.id),'[]'::jsonb)
 FROM (SELECT DISTINCT s->>'source_type' kind,(s->>'record_id')::uuid id
       FROM jsonb_array_elements(COALESCE(p_payload->'sources','[]'::jsonb)) s) t;
$$;

-- Invoker function: every record is authorized with the actual current actor.
-- This returns only a boolean and never fetches source text with a definer role.
CREATE FUNCTION security.work_report_source_access(p_manifest jsonb,p_selected jsonb) RETURNS boolean
 LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
 SELECT security.authorization_has('work_report.read')
 AND jsonb_typeof(p_manifest)='array' AND jsonb_typeof(p_selected)='array'
 AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(p_selected) s
   WHERE s NOT IN ('visit','opportunity','task','risk') OR NOT security.authorization_has(s||'.read'))
 AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(p_manifest) s WHERE NOT COALESCE(CASE s->>'source_type'
   WHEN 'visit' THEN security.authorization_visit('visit.read',(s->>'record_id')::uuid)
     AND security.authorization_visit('work_report.read',(s->>'record_id')::uuid)
     AND EXISTS(SELECT 1 FROM activity.visit v WHERE v.id=(s->>'record_id')::uuid
       AND v.workspace_id=common.current_workspace_id() AND v.deleted_at IS NULL AND v.status IN ('confirmed','archived'))
   WHEN 'opportunity' THEN security.authorization_opportunity('opportunity.read',(s->>'record_id')::uuid)
     AND security.authorization_opportunity('work_report.read',(s->>'record_id')::uuid)
     AND EXISTS(SELECT 1 FROM crm.opportunity v WHERE v.id=(s->>'record_id')::uuid
       AND v.workspace_id=common.current_workspace_id() AND v.deleted_at IS NULL)
   WHEN 'task' THEN security.authorization_task('task.read',(s->>'record_id')::uuid)
     AND security.authorization_task('work_report.read',(s->>'record_id')::uuid)
     AND EXISTS(SELECT 1 FROM workflow.task v WHERE v.id=(s->>'record_id')::uuid
       AND v.workspace_id=common.current_workspace_id() AND v.deleted_at IS NULL)
   WHEN 'risk' THEN security.authorization_risk('risk.read',(s->>'record_id')::uuid)
     AND security.authorization_risk('work_report.read',(s->>'record_id')::uuid)
     AND EXISTS(SELECT 1 FROM insight.risk v WHERE v.id=(s->>'record_id')::uuid
       AND v.workspace_id=common.current_workspace_id() AND v.deleted_at IS NULL)
   ELSE false END,false));
$$;

CREATE FUNCTION security.work_report_audience_matches(p_audience jsonb) RETURNS boolean
 LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
 SELECT COALESCE(CASE p_audience->>'kind'
   WHEN 'company' THEN true
   WHEN 'members' THEN (p_audience->'member_ids') ? common.current_user_ref_id()::text
   WHEN 'team' THEN EXISTS(SELECT 1 FROM platform.team_membership tm JOIN platform.team t
     ON t.id=tm.team_id AND t.workspace_id=tm.workspace_id
     WHERE tm.workspace_id=common.current_workspace_id() AND tm.user_ref_id=common.current_user_ref_id()
       AND tm.team_id=(p_audience->>'team_id')::uuid AND t.status='active' AND t.deleted_at IS NULL
       AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
       AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to)
   ELSE false END,false);
$$;

CREATE TABLE agent.work_report_scheme (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 owner_user_ref_id uuid NOT NULL,name text NOT NULL CHECK(length(btrim(name)) BETWEEN 1 AND 240),
 visibility text NOT NULL CHECK(visibility IN ('private','team','company')),team_id uuid,
 config jsonb NOT NULL CHECK(jsonb_typeof(config)='object'),version_no integer NOT NULL DEFAULT 1 CHECK(version_no>0),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(id,workspace_id),CHECK((visibility='team')=(team_id IS NOT NULL)),
 FOREIGN KEY(owner_user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id),
 FOREIGN KEY(team_id,workspace_id) REFERENCES platform.team(id,workspace_id)
);
CREATE TABLE agent.work_report_scheme_revision (
 scheme_id uuid NOT NULL,workspace_id uuid NOT NULL,version_no integer NOT NULL CHECK(version_no>0),
 name text NOT NULL,config jsonb NOT NULL,config_hash text NOT NULL CHECK(config_hash~'^[0-9a-f]{64}$'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),PRIMARY KEY(scheme_id,version_no),
 FOREIGN KEY(scheme_id,workspace_id) REFERENCES agent.work_report_scheme(id,workspace_id)
);
CREATE TABLE agent.work_report_snapshot (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 owner_user_ref_id uuid NOT NULL,selection jsonb NOT NULL,period jsonb NOT NULL,scope jsonb NOT NULL,
 status text NOT NULL DEFAULT 'preparing' CHECK(status IN ('preparing','ready','failed')),
 snapshot_hash text CHECK(snapshot_hash~'^[0-9a-f]{64}$'),payload jsonb,error jsonb,
 source_manifest jsonb GENERATED ALWAYS AS (agent.work_report_source_manifest(payload)) STORED,
 job_id uuid REFERENCES ops.job(id),created_at timestamptz NOT NULL DEFAULT clock_timestamp(),data_as_of timestamptz,
 UNIQUE(id,workspace_id),UNIQUE(id,workspace_id,snapshot_hash),
 FOREIGN KEY(owner_user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id),
 CHECK((status='ready')=(payload IS NOT NULL AND snapshot_hash IS NOT NULL AND data_as_of IS NOT NULL)),
 CHECK((status='failed')=(error IS NOT NULL)),CHECK(jsonb_typeof(selection->'sources')='array')
);
CREATE TABLE agent.work_report_document (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 owner_user_ref_id uuid NOT NULL,snapshot_id uuid NOT NULL,snapshot_hash text NOT NULL,
 context jsonb NOT NULL,source_manifest jsonb NOT NULL,selected_sources jsonb NOT NULL,
 latest_revision_id uuid NOT NULL,shared_revision_id uuid,audience jsonb,
 version_no integer NOT NULL DEFAULT 1 CHECK(version_no>0),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(id,workspace_id),FOREIGN KEY(owner_user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id),
 FOREIGN KEY(snapshot_id,workspace_id,snapshot_hash) REFERENCES agent.work_report_snapshot(id,workspace_id,snapshot_hash),
 CHECK((shared_revision_id IS NULL)=(audience IS NULL))
);
CREATE TABLE agent.work_report_run (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 owner_user_ref_id uuid NOT NULL,snapshot_id uuid NOT NULL,request jsonb NOT NULL,generation_input jsonb NOT NULL,
 status text NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','succeeded','failed','cancelled')),
 version_no integer NOT NULL DEFAULT 1 CHECK(version_no>0),job_id uuid REFERENCES ops.job(id),
 document_id uuid,revision_id uuid,result jsonb,error jsonb,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),started_at timestamptz,finished_at timestamptz,
 UNIQUE(id,workspace_id),FOREIGN KEY(owner_user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id),
 FOREIGN KEY(snapshot_id,workspace_id) REFERENCES agent.work_report_snapshot(id,workspace_id),
 FOREIGN KEY(document_id,workspace_id) REFERENCES agent.work_report_document(id,workspace_id)
);
CREATE TABLE agent.work_report_revision (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),workspace_id uuid NOT NULL,document_id uuid NOT NULL,
 revision_no integer NOT NULL CHECK(revision_no>0),origin text NOT NULL CHECK(origin IN ('generated','basic_summary','manual','rewrite')),
 content jsonb NOT NULL CHECK(jsonb_typeof(content)='object'),scheme jsonb NOT NULL
 CHECK(jsonb_typeof(scheme)='object' AND scheme ?& ARRAY['scheme_id','version_no','name','config_hash']
 AND (scheme-ARRAY['scheme_id','version_no','name','config_hash'])='{}'::jsonb
 AND scheme->>'config_hash'~'^[0-9a-f]{64}$'),
 created_by_user_ref_id uuid NOT NULL,run_id uuid,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(document_id,revision_no),UNIQUE(id,document_id,workspace_id),
 FOREIGN KEY(document_id,workspace_id) REFERENCES agent.work_report_document(id,workspace_id) DEFERRABLE INITIALLY DEFERRED,
 FOREIGN KEY(created_by_user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id),
 FOREIGN KEY(run_id,workspace_id) REFERENCES agent.work_report_run(id,workspace_id)
);
ALTER TABLE agent.work_report_document ADD CONSTRAINT work_report_latest_revision
 FOREIGN KEY(latest_revision_id,id,workspace_id) REFERENCES agent.work_report_revision(id,document_id,workspace_id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE agent.work_report_document ADD CONSTRAINT work_report_shared_revision
 FOREIGN KEY(shared_revision_id,id,workspace_id) REFERENCES agent.work_report_revision(id,document_id,workspace_id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE agent.work_report_run ADD CONSTRAINT work_report_run_revision
 FOREIGN KEY(revision_id,document_id,workspace_id) REFERENCES agent.work_report_revision(id,document_id,workspace_id) DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE agent.work_report_event (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 actor_user_ref_id uuid NOT NULL,object_type text NOT NULL CHECK(object_type IN ('scheme','snapshot','run','document')),
 object_id uuid NOT NULL,action text NOT NULL CHECK(length(action) BETWEEN 1 AND 80),metadata jsonb NOT NULL DEFAULT '{}',
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(actor_user_ref_id,workspace_id) REFERENCES platform.user_ref(id,workspace_id),
 CHECK(jsonb_typeof(metadata)='object' AND (metadata-ARRAY['version_no','revision_id','snapshot_hash','status','request_id','content_hash'])='{}'::jsonb)
);
CREATE INDEX work_report_scheme_owner ON agent.work_report_scheme(workspace_id,owner_user_ref_id,updated_at DESC,id);
CREATE INDEX work_report_document_owner ON agent.work_report_document(workspace_id,owner_user_ref_id,updated_at DESC,id);
CREATE INDEX work_report_run_owner ON agent.work_report_run(workspace_id,owner_user_ref_id,created_at DESC,id);

DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['work_report_scheme','work_report_scheme_revision','work_report_snapshot',
   'work_report_document','work_report_run','work_report_revision','work_report_event'] LOOP
  EXECUTE format('ALTER TABLE agent.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE agent.%I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY active_tenant_guard ON agent.%I AS RESTRICTIVE FOR ALL TO PUBLIC
    USING (workspace_id=common.current_workspace_id() AND security.workspace_is_active(workspace_id))
    WITH CHECK (workspace_id=common.current_workspace_id() AND security.workspace_is_active(workspace_id))',t);
  EXECUTE format('REVOKE ALL ON agent.%I FROM PUBLIC,salegent_feishu_worker',t);
 END LOOP;
END $$;

CREATE POLICY report_scheme_read ON agent.work_report_scheme FOR SELECT USING(
 security.authorization_has('work_report.read') AND (owner_user_ref_id=common.current_user_ref_id()
 OR visibility='company' OR (visibility='team' AND security.work_report_audience_matches(jsonb_build_object('kind','team','team_id',team_id)))));
CREATE POLICY report_scheme_insert ON agent.work_report_scheme FOR INSERT WITH CHECK(
 owner_user_ref_id=common.current_user_ref_id() AND security.authorization_has('work_report.write')
 AND (visibility='private' OR security.authorization_has('work_report.share')));
CREATE POLICY report_scheme_update ON agent.work_report_scheme FOR UPDATE USING(
 owner_user_ref_id=common.current_user_ref_id() AND security.authorization_has('work_report.write')) WITH CHECK(
 owner_user_ref_id=common.current_user_ref_id() AND (visibility='private' OR security.authorization_has('work_report.share')));
CREATE POLICY report_scheme_revision_read ON agent.work_report_scheme_revision FOR SELECT USING(EXISTS(
 SELECT 1 FROM agent.work_report_scheme s WHERE s.id=scheme_id AND s.workspace_id=work_report_scheme_revision.workspace_id));
CREATE POLICY report_scheme_revision_insert ON agent.work_report_scheme_revision FOR INSERT WITH CHECK(EXISTS(
 SELECT 1 FROM agent.work_report_scheme s WHERE s.id=scheme_id AND s.workspace_id=work_report_scheme_revision.workspace_id
 AND s.owner_user_ref_id=common.current_user_ref_id() AND security.authorization_has('work_report.write')));

-- Snapshots and generation inputs remain private even after a report is shared:
-- raw supplements and writing references are not implicitly published.
CREATE POLICY report_snapshot_read ON agent.work_report_snapshot FOR SELECT USING(
 owner_user_ref_id=common.current_user_ref_id()
 AND security.work_report_source_access(source_manifest,selection->'sources'));
CREATE POLICY report_snapshot_insert ON agent.work_report_snapshot FOR INSERT WITH CHECK(
 owner_user_ref_id=common.current_user_ref_id() AND status='preparing' AND payload IS NULL
 AND security.authorization_has('work_report.write'));
CREATE POLICY report_snapshot_update ON agent.work_report_snapshot FOR UPDATE USING(
 owner_user_ref_id=common.current_user_ref_id() AND status='preparing' AND security.authorization_has('work_report.write'))
 WITH CHECK(owner_user_ref_id=common.current_user_ref_id()
 AND security.work_report_source_access(source_manifest,selection->'sources'));
CREATE POLICY report_document_read ON agent.work_report_document FOR SELECT USING(
 (owner_user_ref_id=common.current_user_ref_id() OR (shared_revision_id IS NOT NULL AND security.work_report_audience_matches(audience)))
 AND security.work_report_source_access(source_manifest,selected_sources));
CREATE POLICY report_document_insert ON agent.work_report_document FOR INSERT WITH CHECK(
 owner_user_ref_id=common.current_user_ref_id() AND shared_revision_id IS NULL AND security.authorization_has('work_report.write')
 AND security.work_report_source_access(source_manifest,selected_sources));
CREATE POLICY report_document_update ON agent.work_report_document FOR UPDATE USING(
 owner_user_ref_id=common.current_user_ref_id() AND security.authorization_has('work_report.write'))
 WITH CHECK(owner_user_ref_id=common.current_user_ref_id()
 AND security.work_report_source_access(source_manifest,selected_sources));
CREATE POLICY report_revision_read ON agent.work_report_revision FOR SELECT USING(EXISTS(
 SELECT 1 FROM agent.work_report_document d WHERE d.id=document_id AND d.workspace_id=work_report_revision.workspace_id
 AND (d.owner_user_ref_id=common.current_user_ref_id() OR d.shared_revision_id=work_report_revision.id)));
CREATE POLICY report_revision_insert ON agent.work_report_revision FOR INSERT WITH CHECK(
 created_by_user_ref_id=common.current_user_ref_id() AND security.authorization_has('work_report.write') AND EXISTS(
 SELECT 1 FROM agent.work_report_document d WHERE d.id=document_id AND d.workspace_id=work_report_revision.workspace_id
 AND d.owner_user_ref_id=common.current_user_ref_id()));
CREATE POLICY report_run_read ON agent.work_report_run FOR SELECT USING(
 owner_user_ref_id=common.current_user_ref_id() AND EXISTS(SELECT 1 FROM agent.work_report_snapshot s
 WHERE s.id=snapshot_id AND s.workspace_id=work_report_run.workspace_id));
CREATE POLICY report_run_insert ON agent.work_report_run FOR INSERT WITH CHECK(
 owner_user_ref_id=common.current_user_ref_id() AND status='queued'
 AND security.authorization_has('work_report.write') AND security.authorization_has('agent.work_report')
 AND EXISTS(SELECT 1 FROM agent.work_report_snapshot s WHERE s.id=snapshot_id
 AND s.workspace_id=work_report_run.workspace_id AND s.status='ready'));
CREATE POLICY report_run_update ON agent.work_report_run FOR UPDATE USING(
 owner_user_ref_id=common.current_user_ref_id() AND security.authorization_has('work_report.write'))
 WITH CHECK(owner_user_ref_id=common.current_user_ref_id());
CREATE POLICY report_event_read ON agent.work_report_event FOR SELECT USING(actor_user_ref_id=common.current_user_ref_id());
CREATE POLICY report_event_insert ON agent.work_report_event FOR INSERT WITH CHECK(actor_user_ref_id=common.current_user_ref_id());

CREATE FUNCTION agent.protect_work_report() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE s agent.work_report_snapshot;
BEGIN
 IF TG_TABLE_NAME='work_report_document' AND TG_OP='INSERT' THEN
  SELECT * INTO s FROM agent.work_report_snapshot WHERE id=NEW.snapshot_id AND workspace_id=NEW.workspace_id AND status='ready';
  IF s.id IS NULL OR s.owner_user_ref_id<>NEW.owner_user_ref_id OR s.snapshot_hash<>NEW.snapshot_hash THEN
   RAISE insufficient_privilege; END IF;
  NEW.source_manifest=s.source_manifest; NEW.selected_sources=s.selection->'sources';
  NEW.context=jsonb_build_object('period',s.period,'scope',s.scope,'coverage',s.payload->'coverage','metrics',s.payload->'metrics');
  RETURN NEW;
 END IF;
 IF (NEW.id,NEW.workspace_id,NEW.owner_user_ref_id,NEW.created_at)
  IS DISTINCT FROM (OLD.id,OLD.workspace_id,OLD.owner_user_ref_id,OLD.created_at) THEN
  RAISE EXCEPTION '报告标识、租户和作者不可修改' USING ERRCODE='22023'; END IF;
 IF TG_TABLE_NAME='work_report_snapshot' THEN
  IF OLD.status<>'preparing' OR (NEW.selection,NEW.period,NEW.scope) IS DISTINCT FROM (OLD.selection,OLD.period,OLD.scope) THEN
   RAISE EXCEPTION '事实快照不可覆盖' USING ERRCODE='22023'; END IF;
 ELSIF TG_TABLE_NAME='work_report_document' THEN
  IF (NEW.snapshot_id,NEW.snapshot_hash,NEW.context,NEW.source_manifest,NEW.selected_sources)
   IS DISTINCT FROM (OLD.snapshot_id,OLD.snapshot_hash,OLD.context,OLD.source_manifest,OLD.selected_sources) THEN
   RAISE EXCEPTION '报告的事实快照不可替换' USING ERRCODE='22023'; END IF;
  IF (NEW.shared_revision_id,NEW.audience) IS DISTINCT FROM (OLD.shared_revision_id,OLD.audience)
   AND NOT security.authorization_has('work_report.share') THEN RAISE insufficient_privilege; END IF;
  NEW.version_no=OLD.version_no+1; NEW.updated_at=clock_timestamp();
 ELSIF TG_TABLE_NAME='work_report_run' THEN
  IF (NEW.snapshot_id,NEW.request,NEW.generation_input) IS DISTINCT FROM (OLD.snapshot_id,OLD.request,OLD.generation_input)
   OR OLD.status IN ('succeeded','failed','cancelled') THEN
   RAISE EXCEPTION '作业输入与终态不可覆盖' USING ERRCODE='22023'; END IF;
  NEW.version_no=OLD.version_no+1;
 ELSE
  NEW.version_no=OLD.version_no+1; NEW.updated_at=clock_timestamp();
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER report_scheme_protect BEFORE UPDATE ON agent.work_report_scheme FOR EACH ROW EXECUTE FUNCTION agent.protect_work_report();
CREATE TRIGGER report_snapshot_protect BEFORE UPDATE ON agent.work_report_snapshot FOR EACH ROW EXECUTE FUNCTION agent.protect_work_report();
CREATE TRIGGER report_document_protect BEFORE INSERT OR UPDATE ON agent.work_report_document FOR EACH ROW EXECUTE FUNCTION agent.protect_work_report();
CREATE TRIGGER report_run_protect BEFORE UPDATE ON agent.work_report_run FOR EACH ROW EXECUTE FUNCTION agent.protect_work_report();

REVOKE ALL ON FUNCTION agent.work_report_source_manifest(jsonb),security.work_report_source_access(jsonb,jsonb),
 security.work_report_audience_matches(jsonb),agent.protect_work_report() FROM PUBLIC,salegent_feishu_worker;
-- New report objects must not inherit ambient installation defaults. Clear
-- only their initial ACLs; every existing table/function privilege is kept.
DO $$ DECLARE object record; permission record;
BEGIN
 FOR object IN SELECT c.oid,n.nspname,c.relname FROM pg_class c
  JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='agent'
  AND c.relname=ANY(ARRAY['work_report_scheme','work_report_scheme_revision',
   'work_report_snapshot','work_report_document','work_report_run','work_report_revision','work_report_event'])
 LOOP
  FOR permission IN SELECT DISTINCT a.grantee,r.rolname FROM pg_class c
   CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a
   LEFT JOIN pg_roles r ON r.oid=a.grantee WHERE c.oid=object.oid AND a.grantee<>c.relowner
  LOOP
   EXECUTE format('REVOKE ALL ON TABLE %I.%I FROM %s CASCADE',object.nspname,object.relname,
    CASE WHEN permission.grantee=0 THEN 'PUBLIC' ELSE quote_ident(permission.rolname) END);
  END LOOP;
 END LOOP;
 FOR object IN SELECT p.oid,p.oid::regprocedure AS signature FROM pg_proc p WHERE p.oid=ANY(ARRAY[
  'agent.work_report_source_manifest(jsonb)'::regprocedure,'security.work_report_source_access(jsonb,jsonb)'::regprocedure,
  'security.work_report_audience_matches(jsonb)'::regprocedure,'agent.protect_work_report()'::regprocedure])
 LOOP
  FOR permission IN SELECT DISTINCT a.grantee,r.rolname FROM pg_proc p
   CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
   LEFT JOIN pg_roles r ON r.oid=a.grantee WHERE p.oid=object.oid AND a.grantee<>p.proowner
  LOOP
   EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %s CASCADE',object.signature,
    CASE WHEN permission.grantee=0 THEN 'PUBLIC' ELSE quote_ident(permission.rolname) END);
  END LOOP;
 END LOOP;
END $$;

-- Runtime roles are bound explicitly by the installation policy. Do not infer
-- worker/API identity from unrelated business-table privileges. The reviewed
-- LOCAL grant_delta installs exactly the new report capabilities, with no
-- changes to any existing privilege, role membership or source-table ACL.

-- Preserve existing fairness, lease fencing and ACLs. Only report terminal projections are added.
CREATE OR REPLACE FUNCTION ops.claim_job(p_worker_id text,p_lock_seconds integer,
 p_job_types text[],p_exclude_types boolean DEFAULT false) RETURNS SETOF ops.job
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ops AS $$
DECLARE expired record; tenant record; capacity integer;
BEGIN
 IF p_lock_seconds IS NULL OR p_exclude_types IS NULL
  OR (p_job_types IS NOT NULL AND array_position(p_job_types,NULL) IS NOT NULL)
  OR p_lock_seconds < 3 OR p_lock_seconds > 3600 OR NULLIF(btrim(p_worker_id),'') IS NULL THEN
  RAISE EXCEPTION 'invalid worker lease settings';
 END IF;
 -- Company rotation is separate from priority inside a company. Acquire
 -- workspace then scheduler locks, matching lifecycle/configuration lock order.
 FOR tenant IN SELECT w.id FROM control.tenant_job_schedule s JOIN platform.workspace w ON w.id=s.workspace_id
  WHERE w.status='active' AND w.deleted_at IS NULL AND EXISTS(
   SELECT 1 FROM ops.job j WHERE j.workspace_id=w.id AND j.status IN ('queued','failed','running')
   AND (p_job_types IS NULL OR (j.job_type=ANY(p_job_types))<>p_exclude_types)
   AND (j.locked_until IS NULL OR j.locked_until<=clock_timestamp())
   AND security.workspace_job_enabled(w.id,j.job_type))
  ORDER BY s.last_claimed_at,w.id
 LOOP
  PERFORM 1 FROM platform.workspace WHERE id=tenant.id AND status='active' AND deleted_at IS NULL FOR SHARE SKIP LOCKED;
  IF NOT FOUND THEN CONTINUE; END IF;
  SELECT concurrency_limit INTO capacity FROM control.tenant_job_schedule WHERE workspace_id=tenant.id FOR UPDATE SKIP LOCKED;
  IF NOT FOUND THEN CONTINUE; END IF;
 -- A process may stop after the result commits but before acknowledging its queue row.
 UPDATE ops.job j SET status='succeeded',completed_at=COALESCE(j.completed_at,clock_timestamp()),
   locked_by=NULL,locked_until=NULL,lease_token=NULL,updated_at=clock_timestamp()
 WHERE j.workspace_id=tenant.id AND security.workspace_is_active(j.workspace_id) AND security.workspace_job_enabled(j.workspace_id,j.job_type) AND j.status IN ('queued','failed','running')
 AND (p_job_types IS NULL OR (j.job_type=ANY(p_job_types))<>p_exclude_types)
 AND (j.locked_until IS NULL OR j.locked_until<=clock_timestamp())
 AND (EXISTS(SELECT 1 FROM ops.job_effect e WHERE e.job_id=j.id)
   OR (j.job_type='agent.run' AND EXISTS(SELECT 1 FROM agent.run r
       WHERE r.id=j.aggregate_id AND r.status IN ('succeeded','waiting_human')))
   OR (j.job_type='visit.import' AND EXISTS(SELECT 1 FROM activity.visit_import i
       WHERE i.id=j.aggregate_id AND i.status='succeeded'))
   OR (j.job_type='sales_competency.review' AND EXISTS(SELECT 1 FROM insight.sales_competency_review r
       WHERE r.id=j.aggregate_id AND r.status='succeeded')));
 -- Bounded attempts also cover processes that disappeared without an exception handler.
 FOR expired IN
  UPDATE ops.job SET status='dead_letter',completed_at=clock_timestamp(),locked_by=NULL,
   locked_until=NULL,lease_token=NULL,last_error_code='LEASE_ATTEMPTS_EXHAUSTED',
   last_error_detail='后台任务中断且已达到重试上限，请重试或联系管理员',updated_at=clock_timestamp()
  WHERE workspace_id=tenant.id AND security.workspace_is_active(workspace_id) AND security.workspace_job_enabled(workspace_id,job_type) AND status IN ('queued','failed','running') AND attempts>=max_attempts
    AND (p_job_types IS NULL OR (job_type=ANY(p_job_types))<>p_exclude_types)
    AND (locked_until IS NULL OR locked_until<=clock_timestamp()) RETURNING *
 LOOP
  IF expired.job_type='agent.run' THEN
   UPDATE agent.run SET status='failed',completed_at=clock_timestamp(),
    error_code='LEASE_ATTEMPTS_EXHAUSTED',error_detail=expired.last_error_detail
    WHERE id=expired.aggregate_id AND status IN ('queued','running');
  ELSIF expired.job_type='visit.import' THEN
   UPDATE activity.visit_import SET status='failed',error_message=expired.last_error_detail,updated_at=clock_timestamp()
    WHERE id=expired.aggregate_id AND status IN ('queued','processing');
  ELSIF expired.job_type='business.advice' THEN
   UPDATE insight.business_advice SET status='failed',error_code='LEASE_ATTEMPTS_EXHAUSTED',
     completed_at=clock_timestamp() WHERE id=expired.aggregate_id AND status IN ('queued','running');
  ELSIF expired.job_type='work_report.snapshot' THEN
   UPDATE agent.work_report_snapshot SET status='failed',error=jsonb_build_object(
     'code','WORK_REPORT_LEASE_EXHAUSTED','detail','材料准备中断，请重新获取材料','retryable',false)
    WHERE id=expired.aggregate_id AND workspace_id=expired.workspace_id AND job_id=expired.id AND status='preparing';
  ELSIF expired.job_type='work_report.generate' THEN
   UPDATE agent.work_report_run SET status='failed',finished_at=clock_timestamp(),error=jsonb_build_object(
     'code','WORK_REPORT_LEASE_EXHAUSTED','detail','报告生成中断，请重新生成','retryable',false)
    WHERE id=expired.aggregate_id AND workspace_id=expired.workspace_id AND job_id=expired.id AND status IN ('queued','running');
  ELSIF expired.job_type='sales_competency.review' THEN
   UPDATE insight.sales_competency_review SET status='failed',error_code='LEASE_ATTEMPTS_EXHAUSTED',
    error_detail=expired.last_error_detail WHERE id=expired.aggregate_id AND status IN ('queued','running');
  END IF;
 END LOOP;
 -- A fresh SQL statement after the scheduler lock observes committed claims.
 -- Count leases across all job types, including other worker processes/lanes.
 IF (SELECT count(*) FROM ops.job WHERE workspace_id=tenant.id AND status='running'
     AND locked_until>clock_timestamp())>=capacity THEN CONTINUE; END IF;
 RETURN QUERY
 UPDATE ops.job j SET status='running',attempts=j.attempts+1,locked_by=p_worker_id,
  lease_token=gen_random_uuid(),locked_until=clock_timestamp()+make_interval(secs=>p_lock_seconds),
  started_at=COALESCE(j.started_at,clock_timestamp()),updated_at=clock_timestamp()
 WHERE j.id=(SELECT candidate.id FROM ops.job candidate
  WHERE candidate.workspace_id=tenant.id AND security.workspace_is_active(candidate.workspace_id) AND security.workspace_job_enabled(candidate.workspace_id,candidate.job_type) AND candidate.status IN ('queued','failed','running') AND candidate.attempts<candidate.max_attempts
   AND (p_job_types IS NULL OR (candidate.job_type=ANY(p_job_types))<>p_exclude_types)
   AND candidate.available_at<=clock_timestamp()
   AND (candidate.locked_until IS NULL OR candidate.locked_until<=clock_timestamp())
   AND NOT EXISTS(SELECT 1 FROM ops.job_effect e WHERE e.job_id=candidate.id)
  ORDER BY candidate.priority DESC,candidate.available_at,candidate.created_at,candidate.id
  FOR UPDATE SKIP LOCKED LIMIT 1)
 RETURNING j.*;
 IF FOUND THEN
  UPDATE control.tenant_job_schedule SET last_claimed_at=clock_timestamp() WHERE workspace_id=tenant.id;
  RETURN;
 END IF;
 END LOOP;
END $$;

CREATE OR REPLACE FUNCTION ops.reject_invalid_job_actor(p_job_id uuid,p_lease_token uuid) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,ops
 SET app.workspace_id='' SET app.user_ref_id='' SET app.role_code=''
 SET app.team_ids='' SET app.job_id='' AS $$
DECLARE claimed ops.job%ROWTYPE; aggregate_status text; result_status text;
 failure_detail constant text := '后台任务身份快照无效，已停止执行，请由管理员核对后重新发起';
BEGIN
 SELECT * INTO claimed FROM ops.job
 WHERE id=p_job_id AND lease_token=p_lease_token AND status='running'
   AND locked_until>clock_timestamp() FOR UPDATE;
 IF NOT FOUND THEN RETURN NULL; END IF;
 -- SET LOCAL values are restored on function exit by the function's SET clauses.
 -- Audit attribution is the job itself, never a payload-supplied person or role.
 PERFORM set_config('app.workspace_id',claimed.workspace_id::text,true),
         set_config('app.job_id',claimed.id::text,true);
 IF claimed.job_type='visit.import' THEN
  SELECT status INTO aggregate_status FROM activity.visit_import
   WHERE id=claimed.aggregate_id AND workspace_id=claimed.workspace_id FOR UPDATE;
 ELSIF claimed.job_type='agent.run' THEN
  SELECT status INTO aggregate_status FROM agent.run
   WHERE id=claimed.aggregate_id AND workspace_id=claimed.workspace_id FOR UPDATE;
 ELSIF claimed.job_type='business.advice' THEN
  SELECT status INTO aggregate_status FROM insight.business_advice
   WHERE id=claimed.aggregate_id AND workspace_id=claimed.workspace_id FOR UPDATE;
 ELSIF claimed.job_type='work_report.snapshot' THEN
  SELECT status INTO aggregate_status FROM agent.work_report_snapshot
   WHERE id=claimed.aggregate_id AND workspace_id=claimed.workspace_id AND job_id=claimed.id FOR UPDATE;
 ELSIF claimed.job_type='work_report.generate' THEN
  SELECT status INTO aggregate_status FROM agent.work_report_run
   WHERE id=claimed.aggregate_id AND workspace_id=claimed.workspace_id AND job_id=claimed.id FOR UPDATE;
 ELSIF claimed.job_type='sales_competency.review' THEN
  SELECT status INTO aggregate_status FROM insight.sales_competency_review
   WHERE id=claimed.aggregate_id AND workspace_id=claimed.workspace_id FOR UPDATE;
 END IF;
 IF EXISTS(SELECT 1 FROM ops.job_effect WHERE job_id=claimed.id AND workspace_id=claimed.workspace_id)
   OR aggregate_status IN ('succeeded','waiting_human','superseded') THEN
  result_status := 'succeeded';
 ELSE
  result_status := 'dead_letter';
  IF claimed.job_type='visit.import' THEN
   UPDATE activity.visit_import SET status='failed',error_message=failure_detail,updated_at=clock_timestamp()
    WHERE id=claimed.aggregate_id AND workspace_id=claimed.workspace_id AND status IN ('queued','processing');
  ELSIF claimed.job_type='agent.run' THEN
   UPDATE agent.run SET status='failed',error_code='INVALID_JOB_ACTOR',error_detail=failure_detail,
    completed_at=clock_timestamp(),updated_at=clock_timestamp()
    WHERE id=claimed.aggregate_id AND workspace_id=claimed.workspace_id AND status IN ('queued','running');
  ELSIF claimed.job_type='business.advice' THEN
   UPDATE insight.business_advice SET status='failed',error_code='INVALID_JOB_ACTOR',
    completed_at=clock_timestamp(),updated_at=clock_timestamp()
    WHERE id=claimed.aggregate_id AND workspace_id=claimed.workspace_id AND status IN ('queued','running');
  ELSIF claimed.job_type='work_report.snapshot' THEN
   UPDATE agent.work_report_snapshot SET status='failed',error=jsonb_build_object(
     'code','WORK_REPORT_INVALID_JOB_ACTOR','detail',failure_detail,'retryable',false)
    WHERE id=claimed.aggregate_id AND workspace_id=claimed.workspace_id AND job_id=claimed.id AND status='preparing';
  ELSIF claimed.job_type='work_report.generate' THEN
   UPDATE agent.work_report_run SET status='failed',finished_at=clock_timestamp(),error=jsonb_build_object(
     'code','WORK_REPORT_INVALID_JOB_ACTOR','detail',failure_detail,'retryable',false)
    WHERE id=claimed.aggregate_id AND workspace_id=claimed.workspace_id AND job_id=claimed.id AND status IN ('queued','running');
  ELSIF claimed.job_type='sales_competency.review' THEN
   UPDATE insight.sales_competency_review SET status='failed',error_code='INVALID_JOB_ACTOR',
    error_detail=failure_detail,updated_at=clock_timestamp()
    WHERE id=claimed.aggregate_id AND workspace_id=claimed.workspace_id AND status IN ('queued','running');
  END IF;
  -- battle_map.review has no pending business projection: its previous confirmed
  -- quadrant remains intact; unsupported types also terminate only their job row.
 END IF;
 UPDATE ops.job SET status=result_status,completed_at=clock_timestamp(),updated_at=clock_timestamp(),
  locked_by=NULL,locked_until=NULL,lease_token=NULL,
  last_error_code=CASE WHEN result_status='dead_letter' THEN 'INVALID_JOB_ACTOR' END,
  last_error_detail=CASE WHEN result_status='dead_letter' THEN failure_detail END
 WHERE id=claimed.id AND lease_token=p_lease_token AND status='running' AND locked_until>clock_timestamp();
 IF NOT FOUND THEN RAISE no_data_found USING MESSAGE='job lease expired during identity rejection'; END IF;
 RETURN result_status;
EXCEPTION WHEN no_data_found THEN
 -- Roll back pending-projection updates if the lease expired while waiting for a
 -- row lock. Returning NULL tells the worker it must not acknowledge this attempt.
 RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION security.workspace_job_enabled(p_workspace uuid,p_type text) RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT security.workspace_module_enabled(p_workspace,CASE WHEN p_type='visit.import' THEN 'visit'
 WHEN p_type IN ('agent.run','sales_competency.review','battle_map.review','customer.risk.review',
 'business.advice','opportunity.change.review','work_report.generate') THEN 'ai' ELSE 'core' END);
$$;
-- Exact first lease for a reviewed report job. No sweeper, recovery, retries or
-- writes to other queue rows. Fingerprints use PostgreSQL jsonb text bytes;
-- the caller records these from a read-only preflight, never client input.
CREATE FUNCTION ops.claim_exact_work_report(
 p_worker_id text,p_lock_seconds integer,p_job_id uuid,p_workspace_id uuid,
 p_aggregate_id uuid,p_user_id uuid,p_job_type text,p_payload_sha256 text,
 p_input_sha256 text,p_version integer,p_not_before timestamptz,p_deadline timestamptz)
RETURNS SETOF ops.job
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE capacity integer; target record;
BEGIN
 IF p_lock_seconds IS NULL OR p_lock_seconds NOT BETWEEN 3 AND 120
  OR COALESCE(length(btrim(p_worker_id)),0) NOT BETWEEN 1 AND 200
  OR p_job_id IS NULL OR p_workspace_id IS NULL OR p_aggregate_id IS NULL OR p_user_id IS NULL
  OR '00000000-0000-0000-0000-000000000000'::uuid IN (p_job_id,p_workspace_id,p_aggregate_id,p_user_id)
  OR p_job_type IS NULL OR p_job_type NOT IN ('work_report.snapshot','work_report.generate')
  OR p_payload_sha256 IS NULL OR p_payload_sha256 !~ '^[0-9a-f]{64}$'
  OR p_input_sha256 IS NULL OR p_input_sha256 !~ '^[0-9a-f]{64}$'
  OR p_version IS NULL OR p_version<>1
  OR p_not_before IS NULL OR p_deadline IS NULL
  OR p_deadline<=p_not_before OR p_deadline>p_not_before+interval '30 minutes'
  OR clock_timestamp()<p_not_before OR clock_timestamp()>=p_deadline THEN
  RAISE EXCEPTION 'INVALID_EXACT_REPORT_CLAIM' USING ERRCODE='22023';
 END IF;
 PERFORM 1 FROM platform.workspace WHERE id=p_workspace_id AND status='active'
  AND deleted_at IS NULL AND security.workspace_is_active(id)
  AND security.workspace_job_enabled(id,p_job_type) FOR SHARE SKIP LOCKED;
 IF NOT FOUND THEN RETURN; END IF;
 SELECT concurrency_limit INTO capacity FROM control.tenant_job_schedule
  WHERE workspace_id=p_workspace_id FOR UPDATE SKIP LOCKED;
 IF NOT FOUND THEN RETURN; END IF;
 IF (SELECT count(*) FROM ops.job WHERE workspace_id=p_workspace_id
     AND status='running' AND locked_until>clock_timestamp())>=capacity THEN RETURN; END IF;
 SELECT j.* INTO target FROM ops.job j
  WHERE j.id=p_job_id AND j.workspace_id=p_workspace_id
   AND j.job_type=p_job_type AND j.aggregate_type='work_report' AND j.aggregate_id=p_aggregate_id
   AND jsonb_typeof(j.payload)='object'
   AND j.payload->>'workspace_id'=p_workspace_id::text AND j.payload->>'user_id'=p_user_id::text
   AND encode(sha256(convert_to(j.payload::text,'UTF8')),'hex')=p_payload_sha256
   AND j.created_at>=p_not_before AND j.created_at<p_deadline
   AND j.status='queued' AND j.attempts=0 AND j.max_attempts>0
   AND j.available_at<=clock_timestamp()
   AND j.locked_by IS NULL AND j.locked_until IS NULL AND j.lease_token IS NULL
   AND j.started_at IS NULL AND j.completed_at IS NULL
   AND j.last_error_code IS NULL AND j.last_error_detail IS NULL
   AND NOT EXISTS(SELECT 1 FROM ops.job_effect e WHERE e.job_id=j.id)
  FOR UPDATE SKIP LOCKED;
 IF NOT FOUND THEN RETURN; END IF;
 IF p_job_type='work_report.snapshot' THEN
  PERFORM 1 FROM agent.work_report_snapshot s WHERE s.id=p_aggregate_id
   AND s.workspace_id=p_workspace_id AND s.owner_user_ref_id=p_user_id AND s.job_id=p_job_id
   AND s.created_at>=p_not_before AND s.created_at<p_deadline AND s.status='preparing'
   AND s.payload IS NULL AND s.error IS NULL AND s.data_as_of IS NULL
   AND encode(sha256(convert_to(jsonb_build_object('selection',s.selection,
       'period',s.period,'scope',s.scope)::text,'UTF8')),'hex')=p_input_sha256
   FOR SHARE SKIP LOCKED;
 ELSE
  PERFORM 1 FROM agent.work_report_run r WHERE r.id=p_aggregate_id
   AND r.workspace_id=p_workspace_id AND r.owner_user_ref_id=p_user_id AND r.job_id=p_job_id
   AND r.created_at>=p_not_before AND r.created_at<p_deadline
   AND r.status='queued' AND r.version_no=p_version
   AND r.started_at IS NULL AND r.finished_at IS NULL AND r.result IS NULL AND r.error IS NULL
   AND r.revision_id IS NULL
   AND encode(sha256(convert_to(r.generation_input::text,'UTF8')),'hex')=p_input_sha256
   AND EXISTS(SELECT 1 FROM agent.work_report_snapshot s WHERE s.id=r.snapshot_id
       AND s.workspace_id=p_workspace_id AND s.owner_user_ref_id=p_user_id AND s.status='ready')
   FOR SHARE SKIP LOCKED;
 END IF;
 IF NOT FOUND THEN RETURN; END IF;
 IF clock_timestamp()>=p_deadline OR NOT security.workspace_is_active(p_workspace_id)
  OR NOT security.workspace_job_enabled(p_workspace_id,p_job_type) THEN RETURN; END IF;
 RETURN QUERY UPDATE ops.job j SET status='running',attempts=1,locked_by=p_worker_id,
  lease_token=gen_random_uuid(),locked_until=LEAST(p_deadline,clock_timestamp()+make_interval(secs=>p_lock_seconds)),
  started_at=clock_timestamp(),updated_at=clock_timestamp()
  WHERE j.id=target.id AND j.workspace_id=p_workspace_id RETURNING j.*;
 IF FOUND THEN
  UPDATE control.tenant_job_schedule SET last_claimed_at=clock_timestamp() WHERE workspace_id=p_workspace_id;
 END IF;
END $$;
-- Revoke ambient/default ACLs as well as PUBLIC. Grant only through the reviewed
-- explicit runtime policy; owning role follows the existing scheduler function.
DO $$ DECLARE permission record; function_owner name;
 signature text:='ops.claim_exact_work_report(text,integer,uuid,uuid,uuid,uuid,text,text,text,integer,timestamptz,timestamptz)';
BEGIN
 SELECT r.rolname INTO STRICT function_owner FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner
  WHERE p.oid='ops.claim_job(text,integer,text[],boolean)'::regprocedure;
 FOR permission IN SELECT DISTINCT a.grantee,r.rolname FROM pg_proc p
  CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
  LEFT JOIN pg_roles r ON r.oid=a.grantee WHERE p.oid=signature::regprocedure AND a.grantee<>p.proowner
 LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %s CASCADE',signature,
   CASE WHEN permission.grantee=0 THEN 'PUBLIC' ELSE quote_ident(permission.rolname) END);
 END LOOP;
 EXECUTE format('ALTER FUNCTION %s OWNER TO %I',signature,function_owner);
END $$;

INSERT INTO ops.schema_migration(version,description) VALUES('V370','Independent work report snapshots, revisions and explicit sharing');
COMMIT;
