BEGIN;

-- This access type is deliberately not an employee or a sales role. The
-- account therefore cannot satisfy business user_ref foreign keys, appear in
-- personnel directories, or become an owner/assignee/notification recipient.
CREATE TABLE security.demo_readonly_identity (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 account_code text NOT NULL CHECK(account_code=upper(btrim(account_code)) AND length(account_code) BETWEEN 3 AND 64),
 password_hash text NOT NULL CHECK(length(password_hash) BETWEEN 32 AND 512),
 enabled boolean NOT NULL DEFAULT true,
 permission_version integer NOT NULL DEFAULT 1 CHECK(permission_version>0),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(workspace_id,account_code), UNIQUE(id,workspace_id)
);
CREATE TABLE security.demo_readonly_session (
 id uuid PRIMARY KEY,
 workspace_id uuid NOT NULL,
 identity_id uuid NOT NULL,
 permission_version integer NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 expires_at timestamptz NOT NULL,
 revoked_at timestamptz,
 FOREIGN KEY(identity_id,workspace_id) REFERENCES security.demo_readonly_identity(id,workspace_id),
 CHECK(expires_at>created_at AND expires_at<=created_at+interval '30 minutes')
);
CREATE INDEX demo_readonly_session_identity ON security.demo_readonly_session(identity_id) WHERE revoked_at IS NULL;

-- Business rows are all visible inside the fixed demo company (user's latest
-- instruction). Free text remains explicitly human-reviewed, never copied from
-- AI summaries or internal notes. A changed source row invalidates its text.
CREATE TABLE security.demo_readonly_text (
 workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 resource text NOT NULL CHECK(resource IN ('customers','opportunities','follow-ups','tasks')),
 resource_id uuid NOT NULL,
 source_updated_at timestamptz NOT NULL,
 display_text text NOT NULL CHECK(length(display_text)<=10000),
 reviewed_by text NOT NULL CHECK(length(btrim(reviewed_by)) BETWEEN 1 AND 200),
 reviewed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(workspace_id,resource,resource_id)
);
CREATE TABLE security.demo_readonly_catalog (
 workspace_id uuid PRIMARY KEY REFERENCES platform.workspace(id),
 version bigint NOT NULL DEFAULT 1,
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE security.demo_readonly_admission (
 identity_id uuid NOT NULL REFERENCES security.demo_readonly_identity(id),
 session_id uuid NOT NULL,
 window_start timestamptz NOT NULL,
 requests integer NOT NULL,
 PRIMARY KEY(identity_id,session_id)
);

CREATE FUNCTION security.demo_readonly_workspace() RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT id FROM platform.workspace WHERE external_workspace_id='demo-sales-workspace'
 AND status='active' AND deleted_at IS NULL;
$$;

CREATE FUNCTION security.demo_readonly_guard_identity() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM platform.workspace WHERE id=NEW.workspace_id AND external_workspace_id='demo-sales-workspace' AND deleted_at IS NULL) THEN
  RAISE EXCEPTION 'DEMO_IDENTITY_COMPANY_INVALID' USING ERRCODE='23514';
 END IF;
 IF EXISTS(SELECT 1 FROM platform.user_ref u WHERE u.workspace_id=NEW.workspace_id
  AND upper(u.account_code)=NEW.account_code AND u.deleted_at IS NULL) THEN
  RAISE EXCEPTION 'DEMO_IDENTITY_ACCOUNT_CONFLICT' USING ERRCODE='23505';
 END IF;
 IF TG_OP='UPDATE' THEN
  IF NEW.id<>OLD.id OR NEW.workspace_id<>OLD.workspace_id OR NEW.account_code<>OLD.account_code THEN
   RAISE EXCEPTION 'DEMO_IDENTITY_BINDING_IMMUTABLE' USING ERRCODE='23514';
  END IF;
  IF NEW.password_hash IS DISTINCT FROM OLD.password_hash OR NEW.enabled IS DISTINCT FROM OLD.enabled THEN
   NEW.permission_version:=OLD.permission_version+1;
  ELSIF NEW.permission_version<OLD.permission_version THEN
   RAISE EXCEPTION 'DEMO_IDENTITY_VERSION_INVALID' USING ERRCODE='23514';
  END IF;
  IF NEW.permission_version<>OLD.permission_version THEN
   UPDATE security.demo_readonly_session SET revoked_at=clock_timestamp()
    WHERE identity_id=OLD.id AND revoked_at IS NULL;
  END IF;
  NEW.updated_at:=clock_timestamp();
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER demo_identity_guard BEFORE INSERT OR UPDATE ON security.demo_readonly_identity
 FOR EACH ROW EXECUTE FUNCTION security.demo_readonly_guard_identity();
CREATE FUNCTION security.demo_readonly_guard_employee() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF NEW.deleted_at IS NULL AND EXISTS(SELECT 1 FROM security.demo_readonly_identity d
  WHERE d.workspace_id=NEW.workspace_id AND d.account_code=upper(NEW.account_code)) THEN
  RAISE EXCEPTION 'DEMO_IDENTITY_ACCOUNT_CONFLICT' USING ERRCODE='23505';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER demo_identity_employee_collision BEFORE INSERT OR UPDATE OF account_code,workspace_id,deleted_at
 ON platform.user_ref FOR EACH ROW EXECUTE FUNCTION security.demo_readonly_guard_employee();

CREATE FUNCTION security.is_demo_readonly_identity(p_workspace uuid,p_user uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT EXISTS(SELECT 1 FROM security.demo_readonly_identity WHERE workspace_id=p_workspace AND id=p_user);
$$;

-- Keep both overloads and their existing ACLs. Ambiguity is checked across both
-- identity types before either password is verified, never by trying tenants.
CREATE OR REPLACE FUNCTION security.password_login_identifier(p_workspace text,p_identifier text,p_login_type text)
 RETURNS TABLE(workspace text,account_code text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 WITH candidates AS (
  SELECT w.external_workspace_id workspace,u.account_code
  FROM platform.workspace w JOIN platform.user_ref u ON u.workspace_id=w.id
  JOIN platform.password_credential p ON p.user_ref_id=u.id AND p.workspace_id=u.workspace_id
  WHERE w.status='active' AND w.deleted_at IS NULL AND u.status='active' AND u.deleted_at IS NULL
   AND NULLIF(btrim(p_identifier),'') IS NOT NULL
   AND (NULLIF(btrim(p_workspace),'') IS NULL OR lower(w.external_workspace_id)=lower(btrim(p_workspace)))
   AND CASE p_login_type
    WHEN 'phone' THEN btrim(p_identifier) ~ '^1[3-9][0-9]{9}$' AND u.phone_number=btrim(p_identifier)
    WHEN 'account' THEN u.account_code=upper(btrim(p_identifier))
    WHEN 'auto' THEN u.account_code=upper(btrim(p_identifier)) OR u.phone_number=btrim(p_identifier)
    ELSE false END
  UNION ALL
  SELECT w.external_workspace_id,d.account_code FROM security.demo_readonly_identity d
  JOIN platform.workspace w ON w.id=d.workspace_id
  WHERE w.id=security.demo_readonly_workspace() AND p_login_type IN ('account','auto')
   AND d.account_code=upper(btrim(p_identifier))
   AND (NULLIF(btrim(p_workspace),'') IS NULL OR lower(w.external_workspace_id)=lower(btrim(p_workspace)))
 ) SELECT c.workspace,c.account_code FROM candidates c WHERE (SELECT count(*) FROM candidates)=1;
$$;
CREATE OR REPLACE FUNCTION security.password_login_identifier(p_workspace text,p_identifier text)
 RETURNS TABLE(workspace text,account_code text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT * FROM security.password_login_identifier(p_workspace,p_identifier,'auto');
$$;

CREATE FUNCTION security.demo_readonly_candidate(p_workspace text,p_identifier text,p_login_type text) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('identity_id',d.id,'workspace_id',d.workspace_id,'account_code',d.account_code,
 'password_hash',d.password_hash,'enabled',d.enabled,'permission_version',d.permission_version,
 'company',jsonb_build_object('id',w.id,'code',w.external_workspace_id,'name',w.name),
 'data_version',COALESCE(c.version,1)::text,'modules',jsonb_build_array('customers','opportunities','follow-ups','tasks'))
 FROM security.password_login_identifier(p_workspace,p_identifier,p_login_type) i
 JOIN platform.workspace w ON w.external_workspace_id=i.workspace
 JOIN security.demo_readonly_identity d ON d.workspace_id=w.id AND d.account_code=i.account_code
 LEFT JOIN security.demo_readonly_catalog c ON c.workspace_id=w.id
 WHERE w.id=security.demo_readonly_workspace();
$$;

CREATE FUNCTION security.demo_readonly_session(p_session uuid,p_workspace uuid,p_identity uuid,p_permission_version integer) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('session_kind','demo_readonly','session_id',s.id,'identity_id',d.id,
 'workspace_id',d.workspace_id,'permission_version',d.permission_version,'account_code',d.account_code,
 'company',jsonb_build_object('id',w.id,'code',w.external_workspace_id,'name',w.name),
 'data_version',COALESCE(c.version,1)::text,'modules',jsonb_build_array('customers','opportunities','follow-ups','tasks'),
 'expires_at',s.expires_at)
 FROM security.demo_readonly_session s JOIN security.demo_readonly_identity d
 ON d.id=s.identity_id AND d.workspace_id=s.workspace_id
 JOIN platform.workspace w ON w.id=d.workspace_id
 LEFT JOIN security.demo_readonly_catalog c ON c.workspace_id=w.id
 WHERE s.id=p_session AND s.workspace_id=p_workspace AND s.identity_id=p_identity
 AND s.permission_version=p_permission_version AND d.permission_version=p_permission_version
 AND d.enabled AND s.revoked_at IS NULL AND s.expires_at>clock_timestamp()
 AND w.id=security.demo_readonly_workspace();
$$;
CREATE FUNCTION security.demo_readonly_create_session(p_session uuid,p_workspace uuid,p_identity uuid,p_permission_version integer,p_expected_password_hash text,p_attempt_key text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE d security.demo_readonly_identity; issued timestamptz:=clock_timestamp();
BEGIN
 SELECT * INTO d FROM security.demo_readonly_identity WHERE id=p_identity AND workspace_id=p_workspace FOR SHARE;
 IF NOT FOUND OR NOT d.enabled OR d.permission_version<>p_permission_version
 OR d.password_hash IS DISTINCT FROM p_expected_password_hash OR p_workspace IS DISTINCT FROM security.demo_readonly_workspace()
 THEN RETURN NULL; END IF;
 INSERT INTO security.demo_readonly_session(id,workspace_id,identity_id,permission_version,created_at,expires_at)
 VALUES(p_session,p_workspace,p_identity,p_permission_version,issued,issued+interval '30 minutes');
 DELETE FROM security.login_throttle WHERE key_hash=p_attempt_key AND length(p_attempt_key)=64;
 RETURN security.demo_readonly_session(p_session,p_workspace,p_identity,p_permission_version);
END $$;
CREATE FUNCTION security.demo_readonly_revoke_session(p_session uuid,p_workspace uuid,p_identity uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 UPDATE security.demo_readonly_session SET revoked_at=COALESCE(revoked_at,clock_timestamp())
 WHERE id=p_session AND workspace_id=p_workspace AND identity_id=p_identity;
 RETURN FOUND;
END $$;

CREATE FUNCTION security.demo_readonly_admit(p_session uuid,p_workspace uuid,p_identity uuid,p_permission_version integer) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE session_count integer; identity_count integer; started timestamptz:=clock_timestamp();
BEGIN
 IF security.demo_readonly_session(p_session,p_workspace,p_identity,p_permission_version) IS NULL THEN RETURN false; END IF;
 -- Serialize a shared identity, so concurrent devices cannot race its budget.
 PERFORM 1 FROM security.demo_readonly_identity WHERE id=p_identity FOR UPDATE;
 INSERT INTO security.demo_readonly_admission(identity_id,session_id,window_start,requests)
 VALUES(p_identity,p_session,started,1)
 ON CONFLICT(identity_id,session_id) DO UPDATE SET
 requests=CASE WHEN demo_readonly_admission.window_start<started-interval '1 minute' THEN 1 ELSE demo_readonly_admission.requests+1 END,
 window_start=CASE WHEN demo_readonly_admission.window_start<started-interval '1 minute' THEN started ELSE demo_readonly_admission.window_start END
 RETURNING requests INTO session_count;
 SELECT COALESCE(sum(requests),0) INTO identity_count FROM security.demo_readonly_admission
 WHERE identity_id=p_identity AND window_start>=started-interval '1 minute';
 DELETE FROM security.demo_readonly_admission WHERE window_start<started-interval '1 day';
 RETURN session_count<=60 AND identity_count<=600;
END $$;

CREATE FUNCTION security.demo_readonly_touch_catalog() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE w uuid;
BEGIN
 w:=CASE WHEN TG_OP='DELETE' THEN OLD.workspace_id ELSE NEW.workspace_id END;
 IF w=security.demo_readonly_workspace() THEN
  INSERT INTO security.demo_readonly_catalog(workspace_id) VALUES(w)
  ON CONFLICT(workspace_id) DO UPDATE SET version=security.demo_readonly_catalog.version+1,updated_at=clock_timestamp();
 END IF;
 RETURN NULL;
END $$;
DO $$ DECLARE target text; BEGIN
 FOREACH target IN ARRAY ARRAY['crm.customer','crm.opportunity','activity.visit','workflow.task','workflow.task_execution'] LOOP
  EXECUTE format('CREATE TRIGGER demo_catalog_changed AFTER INSERT OR UPDATE OR DELETE ON %s FOR EACH ROW EXECUTE FUNCTION security.demo_readonly_touch_catalog()',target);
 END LOOP;
END $$;

-- Operator-only maintenance. These are NEVER granted to API/worker roles.
-- Existing employee accounts cause a conflict, not silent conversion or loss of
-- responsibility. Resolve those explicitly before registering the shared login.
CREATE FUNCTION security.demo_readonly_register(p_workspace uuid,p_account text,p_password_hash text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result uuid;
BEGIN
 IF p_workspace IS DISTINCT FROM security.demo_readonly_workspace() THEN
  RAISE EXCEPTION 'DEMO_IDENTITY_COMPANY_INVALID' USING ERRCODE='23514'; END IF;
 LOCK TABLE platform.user_ref IN SHARE ROW EXCLUSIVE MODE;
 INSERT INTO security.demo_readonly_identity(workspace_id,account_code,password_hash)
 VALUES(p_workspace,upper(btrim(p_account)),p_password_hash) RETURNING id INTO result;
 INSERT INTO security.demo_readonly_catalog(workspace_id) VALUES(p_workspace) ON CONFLICT DO NOTHING;
 RETURN result;
END $$;
CREATE FUNCTION security.demo_readonly_update_identity(p_identity uuid,p_password_hash text,p_enabled boolean,p_revoke_all boolean) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 UPDATE security.demo_readonly_identity SET password_hash=COALESCE(p_password_hash,password_hash),
 enabled=COALESCE(p_enabled,enabled),permission_version=permission_version+CASE WHEN p_revoke_all THEN 1 ELSE 0 END
 WHERE id=p_identity;
 RETURN FOUND;
END $$;
CREATE FUNCTION security.demo_readonly_review_text(p_workspace uuid,p_resource text,p_id uuid,p_text text,p_reviewed_by text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE modified timestamptz;
BEGIN
 IF p_workspace IS DISTINCT FROM security.demo_readonly_workspace() THEN
  RAISE EXCEPTION 'DEMO_IDENTITY_COMPANY_INVALID' USING ERRCODE='23514'; END IF;
 CASE p_resource
 WHEN 'customers' THEN SELECT updated_at INTO modified FROM crm.customer WHERE workspace_id=p_workspace AND id=p_id AND deleted_at IS NULL;
 WHEN 'opportunities' THEN SELECT updated_at INTO modified FROM crm.opportunity WHERE workspace_id=p_workspace AND id=p_id AND deleted_at IS NULL;
 WHEN 'follow-ups' THEN SELECT updated_at INTO modified FROM activity.visit WHERE workspace_id=p_workspace AND id=p_id AND deleted_at IS NULL AND status IN ('confirmed','archived');
 WHEN 'tasks' THEN SELECT updated_at INTO modified FROM workflow.task WHERE workspace_id=p_workspace AND id=p_id AND deleted_at IS NULL;
 ELSE RAISE EXCEPTION 'DEMO_RESOURCE_INVALID' USING ERRCODE='22023';
 END CASE;
 IF modified IS NULL THEN RETURN false; END IF;
 IF p_text IS NULL THEN
  DELETE FROM security.demo_readonly_text WHERE workspace_id=p_workspace AND resource=p_resource AND resource_id=p_id;
 ELSE
  INSERT INTO security.demo_readonly_text(workspace_id,resource,resource_id,source_updated_at,display_text,reviewed_by)
  VALUES(p_workspace,p_resource,p_id,modified,p_text,p_reviewed_by)
  ON CONFLICT(workspace_id,resource,resource_id) DO UPDATE SET source_updated_at=EXCLUDED.source_updated_at,
   display_text=EXCLUDED.display_text,reviewed_by=EXCLUDED.reviewed_by,reviewed_at=clock_timestamp();
 END IF;
 INSERT INTO security.demo_readonly_catalog(workspace_id) VALUES(p_workspace)
 ON CONFLICT(workspace_id) DO UPDATE SET version=security.demo_readonly_catalog.version+1,updated_at=clock_timestamp();
 RETURN true;
END $$;

-- The projection uses the caller's restricted API role and RLS, never a
-- superuser/business-role impersonation. This context is set only after the
-- independent session is verified; it cannot coexist with an ordinary actor.
CREATE FUNCTION security.demo_readonly_context(p_workspace uuid) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE context jsonb;
BEGIN
 IF NULLIF(current_setting('app.user_ref_id',true),'') IS NOT NULL
 OR NULLIF(current_setting('app.role_code',true),'') IS NOT NULL THEN RETURN false; END IF;
 context:=NULLIF(current_setting('app.demo_readonly_context',true),'')::jsonb;
 IF context IS NULL OR p_workspace IS DISTINCT FROM security.demo_readonly_workspace()
 OR p_workspace IS DISTINCT FROM (context->>'workspace_id')::uuid THEN RETURN false; END IF;
 RETURN security.demo_readonly_session((context->>'session_id')::uuid,p_workspace,
  (context->>'identity_id')::uuid,(context->>'permission_version')::integer) IS NOT NULL;
EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range THEN RETURN false;
END $$;

-- Ordinary expressions remain identical when there is no dedicated context.
-- In that context, only the separate SELECT policies can grant access. Existing
-- restrictive policies keep a validated fixed-company/session boundary.
DO $$ DECLARE p record; target text; expression text; BEGIN
 FOREACH target IN ARRAY ARRAY['crm.customer','crm.opportunity','activity.visit','workflow.task','workflow.task_execution'] LOOP
  FOR p IN SELECT polname,polpermissive,polcmd,pg_get_expr(polqual,polrelid) qual
   FROM pg_policy WHERE polrelid=target::regclass AND polcmd IN ('*','r') AND polqual IS NOT NULL LOOP
   expression:=CASE WHEN p.polpermissive THEN 'false' ELSE 'security.demo_readonly_context(workspace_id)' END;
   EXECUTE format('ALTER POLICY %I ON %s USING (CASE WHEN NULLIF(current_setting(''app.demo_readonly_context'',true),'''') IS NOT NULL THEN %s ELSE (%s) END)',p.polname,target,expression,p.qual);
  END LOOP;
  EXECUTE format('CREATE POLICY demo_fixed_company_read ON %s FOR SELECT USING(security.demo_readonly_context(workspace_id))',target);
 END LOOP;
END $$;

CREATE FUNCTION security.demo_readonly_read(p_session uuid,p_workspace uuid,p_identity uuid,p_permission_version integer,
 p_resource text,p_id uuid,p_query text,p_offset integer,p_limit integer) RETURNS jsonb
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog SET statement_timeout='5s' AS $$
DECLARE session_data jsonb; result jsonb;
BEGIN
 IF current_setting('transaction_read_only')<>'on' THEN RAISE EXCEPTION 'DEMO_READ_ONLY_REQUIRED' USING ERRCODE='42501'; END IF;
 session_data:=security.demo_readonly_session(p_session,p_workspace,p_identity,p_permission_version);
 IF session_data IS NULL THEN RETURN NULL; END IF;
 PERFORM set_config('app.user_ref_id','',true),set_config('app.role_code','',true),set_config('app.authorization_context','',true),
 set_config('app.workspace_id',p_workspace::text,true),set_config('app.demo_readonly_context',session_data::text,true);
 IF p_resource NOT IN ('overview','customers','opportunities','follow-ups','tasks') OR p_resource IS NULL
 OR p_offset IS NULL OR p_offset<0 OR p_offset>10000 OR p_limit IS NULL OR p_limit<1 OR p_limit>50
 OR length(COALESCE(p_query,''))>100 THEN RAISE EXCEPTION 'DEMO_QUERY_INVALID' USING ERRCODE='22023'; END IF;
 IF p_resource='overview' THEN
  SELECT jsonb_build_object('customer_count',(SELECT count(*) FROM crm.customer WHERE workspace_id=p_workspace AND deleted_at IS NULL),
   'opportunity_count',(SELECT count(*) FROM crm.opportunity WHERE workspace_id=p_workspace AND deleted_at IS NULL),
   'follow_up_count',(SELECT count(*) FROM activity.visit WHERE workspace_id=p_workspace AND deleted_at IS NULL AND status IN ('confirmed','archived')),
   'task_count',(SELECT count(*) FROM workflow.task WHERE workspace_id=p_workspace AND deleted_at IS NULL),
   'data_version',session_data->>'data_version') INTO result;
  RETURN result;
 END IF;
 -- Explicit DTOs are the only output. No attributes, staff, contact, raw text,
 -- model output, upload or media fields are selected.
 IF p_resource='customers' THEN
  WITH visible AS (
   SELECT c.id,c.name,c.updated_at,jsonb_build_object('id',c.id,'name',c.name,'industry',c.industry_code,
    'level',c.level_code,'stage',c.lifecycle_status,'description',x.display_text,'updated_at',c.updated_at) item
   FROM crm.customer c LEFT JOIN security.demo_readonly_text x ON x.workspace_id=c.workspace_id AND x.resource='customers'
    AND x.resource_id=c.id AND x.source_updated_at=c.updated_at
   WHERE c.workspace_id=p_workspace AND c.deleted_at IS NULL AND (p_id IS NULL OR c.id=p_id)
    AND (COALESCE(p_query,'')='' OR strpos(lower(c.name),lower(p_query))>0)
  ), page AS (SELECT item FROM visible ORDER BY updated_at DESC,id LIMIT p_limit OFFSET p_offset)
  SELECT jsonb_build_object('items',COALESCE((SELECT jsonb_agg(item) FROM page),'[]'::jsonb),'total',(SELECT count(*) FROM visible)) INTO result;
 ELSIF p_resource='opportunities' THEN
  WITH visible AS (
   SELECT o.id,o.name,o.updated_at,jsonb_build_object('id',o.id,'name',o.name,'customer',CASE WHEN c.id IS NOT NULL THEN jsonb_build_object('id',c.id,'name',c.name) END,
    'product',o.product_line,'stage',CASE WHEN o.status IN ('won','lost') THEN o.status
     WHEN o.probability=10 THEN 'identified' WHEN o.probability=30 THEN 'qualified'
     WHEN o.probability=50 THEN 'solution' WHEN o.probability=70 THEN 'proposal'
     WHEN o.probability=90 THEN 'negotiation' ELSE o.stage_code END,'amount',o.amount,'expected_close_date',o.expected_close_date,'updated_at',o.updated_at) item
   FROM crm.opportunity o LEFT JOIN crm.customer c ON c.id=o.customer_id AND c.workspace_id=o.workspace_id AND c.deleted_at IS NULL
   WHERE o.workspace_id=p_workspace AND o.deleted_at IS NULL AND (p_id IS NULL OR o.id=p_id)
    AND (COALESCE(p_query,'')='' OR strpos(lower(o.name),lower(p_query))>0)
  ), page AS (SELECT item FROM visible ORDER BY updated_at DESC,id LIMIT p_limit OFFSET p_offset)
  SELECT jsonb_build_object('items',COALESCE((SELECT jsonb_agg(item) FROM page),'[]'::jsonb),'total',(SELECT count(*) FROM visible)) INTO result;
 ELSIF p_resource='follow-ups' THEN
  WITH visible AS (
   SELECT v.id,v.updated_at,jsonb_build_object('id',v.id,
    'customer',CASE WHEN c.id IS NOT NULL THEN jsonb_build_object('id',c.id,'name',c.name) END,
    'opportunity',CASE WHEN o.id IS NOT NULL THEN jsonb_build_object('id',o.id,'name',o.name) END,
    'follow_up_date',(v.interaction_at AT TIME ZONE 'Asia/Shanghai')::date,'type',v.interaction_mode_code,'display_text',x.display_text,'updated_at',v.updated_at) item
   FROM activity.visit v LEFT JOIN crm.customer c ON c.id=v.customer_id AND c.workspace_id=v.workspace_id AND c.deleted_at IS NULL
   LEFT JOIN crm.opportunity o ON o.id=v.opportunity_id AND o.workspace_id=v.workspace_id AND o.deleted_at IS NULL
   LEFT JOIN security.demo_readonly_text x ON x.workspace_id=v.workspace_id AND x.resource='follow-ups'
    AND x.resource_id=v.id AND x.source_updated_at=v.updated_at
   WHERE v.workspace_id=p_workspace AND v.deleted_at IS NULL AND v.status IN ('confirmed','archived') AND (p_id IS NULL OR v.id=p_id)
    AND (COALESCE(p_query,'')='' OR strpos(lower(COALESCE(c.name,'')),lower(p_query))>0 OR strpos(lower(COALESCE(x.display_text,'')),lower(p_query))>0)
  ), page AS (SELECT item FROM visible ORDER BY updated_at DESC,id LIMIT p_limit OFFSET p_offset)
  SELECT jsonb_build_object('items',COALESCE((SELECT jsonb_agg(item) FROM page),'[]'::jsonb),'total',(SELECT count(*) FROM visible)) INTO result;
 ELSE
  WITH visible AS (
   SELECT t.id,t.updated_at,jsonb_build_object('id',t.id,'title',t.title,'status',t.status,'due_at',t.due_at,
    'progress_percent',CASE WHEN e.task_id IS NOT NULL THEN workflow.task_execution_progress(e.state)->'percent' END,
    'description',x.display_text,'customer',CASE WHEN c.id IS NOT NULL THEN jsonb_build_object('id',c.id,'name',c.name) END,
    'opportunity',CASE WHEN o.id IS NOT NULL THEN jsonb_build_object('id',o.id,'name',o.name) END,'updated_at',t.updated_at) item
   FROM workflow.task t LEFT JOIN crm.customer c ON c.id=t.customer_id AND c.workspace_id=t.workspace_id AND c.deleted_at IS NULL
   LEFT JOIN crm.opportunity o ON o.id=t.opportunity_id AND o.workspace_id=t.workspace_id AND o.deleted_at IS NULL
   LEFT JOIN workflow.task_execution e ON e.task_id=t.id AND e.workspace_id=t.workspace_id
   LEFT JOIN security.demo_readonly_text x ON x.workspace_id=t.workspace_id AND x.resource='tasks'
    AND x.resource_id=t.id AND x.source_updated_at=t.updated_at
   WHERE t.workspace_id=p_workspace AND t.deleted_at IS NULL AND (p_id IS NULL OR t.id=p_id)
    AND (COALESCE(p_query,'')='' OR strpos(lower(t.title),lower(p_query))>0)
  ), page AS (SELECT item FROM visible ORDER BY updated_at DESC,id LIMIT p_limit OFFSET p_offset)
  SELECT jsonb_build_object('items',COALESCE((SELECT jsonb_agg(item) FROM page),'[]'::jsonb),'total',(SELECT count(*) FROM visible)) INTO result;
 END IF;
 IF p_id IS NOT NULL THEN RETURN result->'items'->0; END IF;
 RETURN result||jsonb_build_object('offset',p_offset,'limit',p_limit,'data_version',session_data->>'data_version');
END $$;

-- Closed ACLs override PUBLIC and inherited ALTER DEFAULT PRIVILEGES grants.
DO $$ DECLARE t text; f record; a record; BEGIN
 FOREACH t IN ARRAY ARRAY['security.demo_readonly_identity','security.demo_readonly_session','security.demo_readonly_text','security.demo_readonly_catalog','security.demo_readonly_admission'] LOOP
  EXECUTE format('REVOKE ALL ON TABLE %s FROM PUBLIC',t);
  FOR a IN SELECT DISTINCT r.rolname FROM pg_class c CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) x
   JOIN pg_roles r ON r.oid=x.grantee WHERE c.oid=t::regclass AND x.grantee<>c.relowner LOOP
   EXECUTE format('REVOKE ALL ON TABLE %s FROM %I',t,a.rolname);
  END LOOP;
  EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY',t);
 END LOOP;
 FOR f IN SELECT p.oid::regprocedure signature,p.proowner FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
  WHERE n.nspname='security' AND (p.proname LIKE 'demo_readonly_%' OR p.proname='is_demo_readonly_identity') LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',f.signature);
  FOR a IN SELECT DISTINCT r.rolname FROM pg_proc p CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) x
   JOIN pg_roles r ON r.oid=x.grantee WHERE p.oid=f.signature AND x.grantee<>p.proowner LOOP
   EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I',f.signature,a.rolname);
  END LOOP;
 END LOOP;
END $$;
CREATE POLICY demo_reviewed_text_read ON security.demo_readonly_text FOR SELECT
 USING(security.demo_readonly_context(workspace_id));
-- Called by SELECT policies for existing API/worker/observer roles. This helper
-- exposes only a boolean and fails closed without the verified session context.
GRANT EXECUTE ON FUNCTION security.demo_readonly_context(uuid) TO PUBLIC;

INSERT INTO ops.schema_migration(version,description)
 VALUES('V435','Dedicated mini read-only identity with independent sessions and fixed-company field projections');
COMMIT;
