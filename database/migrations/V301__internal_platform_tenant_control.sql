-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V153__platform_tenant_control.sql.
-- Original SHA-256: 72eaed62031e57bf670d1b90d4b01778a96fdbeb794ec8e0fca5817c6661a82e. See shenma_whole_121_manifest.json.
BEGIN;
-- This schema is intentionally outside the tenant runtime's schema allowlist.
CREATE SCHEMA control;
REVOKE ALL ON SCHEMA control FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA control REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

CREATE TABLE control.operator (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 account_code text NOT NULL UNIQUE CHECK(account_code=upper(account_code) AND account_code ~ '^[A-Z0-9][A-Z0-9_.-]{2,63}$'),
 display_name text NOT NULL CHECK(length(btrim(display_name)) BETWEEN 1 AND 100),
 password_hash text NOT NULL, enabled boolean NOT NULL DEFAULT true,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE control.session (
 token_hash text PRIMARY KEY CHECK(token_hash ~ '^[a-f0-9]{64}$'),
 operator_id uuid NOT NULL REFERENCES control.operator(id),
 expires_at timestamptz NOT NULL, revoked_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE control.login_attempt (
 key_hash text PRIMARY KEY CHECK(key_hash ~ '^[a-f0-9]{64}$'),
 attempts integer NOT NULL, window_start timestamptz NOT NULL
);
CREATE TABLE control.tenant_setup (
 workspace_id uuid PRIMARY KEY REFERENCES platform.workspace(id),
 purpose text NOT NULL CHECK(purpose IN ('internal','demo','customer')),
 administrator_id uuid NOT NULL REFERENCES platform.user_ref(id),
 activation_hash text UNIQUE CHECK(activation_hash ~ '^[a-f0-9]{64}$'),
 activation_expires_at timestamptz NOT NULL,
 activated_at timestamptz, created_by uuid NOT NULL REFERENCES control.operator(id),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE control.provision_request (
 idempotency_key uuid PRIMARY KEY, operator_id uuid NOT NULL REFERENCES control.operator(id),
 request_digest text NOT NULL CHECK(request_digest ~ '^[a-f0-9]{64}$'),
 workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE control.audit (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 operator_id uuid REFERENCES control.operator(id), workspace_id uuid REFERENCES platform.workspace(id),
 action text NOT NULL, reason text NOT NULL DEFAULT '',
 before_state jsonb NOT NULL DEFAULT '{}', after_state jsonb NOT NULL DEFAULT '{}',
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
REVOKE ALL ON ALL TABLES IN SCHEMA control FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA control FROM PUBLIC;

ALTER TABLE platform.workspace DROP CONSTRAINT workspace_status_check;
ALTER TABLE platform.workspace ADD CONSTRAINT workspace_status_check
 CHECK(status IN ('provisioning','active','suspended','closed'));
-- Existing company codes are left intact. New ones are normalized by provision().
CREATE UNIQUE INDEX workspace_company_code_lower_unique
 ON platform.workspace(lower(external_workspace_id));

CREATE FUNCTION control.actor(p_session text) RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT o.id FROM control.session s JOIN control.operator o ON o.id=s.operator_id
 WHERE s.token_hash=p_session AND s.revoked_at IS NULL AND s.expires_at>clock_timestamp() AND o.enabled;
$$;
CREATE FUNCTION control.require_actor(p_session text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE actor uuid;
BEGIN
 SELECT o.id INTO actor FROM control.session s JOIN control.operator o ON o.id=s.operator_id
 WHERE s.token_hash=p_session AND s.revoked_at IS NULL AND s.expires_at>clock_timestamp() AND o.enabled
 FOR SHARE OF s,o;
 IF actor IS NULL THEN RAISE EXCEPTION 'PLATFORM_AUTH_REQUIRED' USING ERRCODE='P7401'; END IF;
 RETURN actor;
END $$;

CREATE FUNCTION control.consume_attempt(p_account_key text,p_network_key text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE k text; n integer; valid boolean:=true;
BEGIN
 -- Stable ordering avoids lock inversion when requests share only one bucket.
 FOR k IN SELECT unnest(ARRAY[p_account_key,p_network_key]) ORDER BY 1 LOOP
  INSERT INTO control.login_attempt(key_hash,attempts,window_start) VALUES(k,1,clock_timestamp())
  ON CONFLICT(key_hash) DO UPDATE SET
   attempts=CASE WHEN control.login_attempt.window_start<clock_timestamp()-interval '5 minutes' THEN 1 ELSE control.login_attempt.attempts+1 END,
   window_start=CASE WHEN control.login_attempt.window_start<clock_timestamp()-interval '5 minutes' THEN clock_timestamp() ELSE control.login_attempt.window_start END
  RETURNING attempts INTO n;
  IF n>(CASE WHEN k=p_account_key THEN 5 ELSE 50 END) THEN valid:=false; END IF;
 END LOOP;
 RETURN valid;
END $$;

CREATE FUNCTION control.login_candidate(p_account text) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('id',id,'password_hash',password_hash)
 FROM control.operator WHERE account_code=upper(btrim(p_account)) AND enabled;
$$;
CREATE FUNCTION control.issue_session(p_operator uuid,p_observed_hash text,p_session text,p_account_key text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE o control.operator%ROWTYPE;
BEGIN
 SELECT * INTO o FROM control.operator WHERE id=p_operator AND enabled AND password_hash=p_observed_hash FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION 'PLATFORM_AUTH_REQUIRED' USING ERRCODE='P7401'; END IF;
 INSERT INTO control.session(token_hash,operator_id,expires_at) VALUES(p_session,o.id,clock_timestamp()+interval '30 minutes');
 DELETE FROM control.login_attempt WHERE key_hash=p_account_key;
 INSERT INTO control.audit(operator_id,action) VALUES(o.id,'platform.login');
 RETURN jsonb_build_object('id',o.id,'account_code',o.account_code,'display_name',o.display_name);
END $$;
CREATE FUNCTION control.session_profile(p_session text) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('id',id,'account_code',account_code,'display_name',display_name)
 FROM control.operator WHERE id=control.actor(p_session);
$$;
CREATE FUNCTION control.logout(p_session text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE actor uuid:=control.actor(p_session);
BEGIN
 UPDATE control.session SET revoked_at=clock_timestamp() WHERE token_hash=p_session AND revoked_at IS NULL;
 IF actor IS NOT NULL THEN INSERT INTO control.audit(operator_id,action) VALUES(actor,'platform.logout'); END IF;
END $$;

CREATE FUNCTION control.readiness(p_workspace uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object(
  'administrator_ready',EXISTS(SELECT 1 FROM platform.user_ref u
   JOIN platform.role_binding b ON b.user_ref_id=u.id AND b.workspace_id=u.workspace_id
   JOIN platform.password_credential pc ON pc.user_ref_id=u.id AND pc.workspace_id=u.workspace_id
   WHERE u.workspace_id=p_workspace AND u.status='active' AND u.deleted_at IS NULL
    AND b.role_code='administrator' AND b.valid_from<=clock_timestamp() AND b.valid_to>clock_timestamp()),
  'activation_complete',NOT EXISTS(SELECT 1 FROM control.tenant_setup s WHERE s.workspace_id=p_workspace AND s.activated_at IS NULL),
  'permissions_ready',EXISTS(SELECT 1 FROM config.permission_role r WHERE r.workspace_id=p_workspace AND r.builtin_role_code='administrator' AND r.status='active'));
$$;
CREATE FUNCTION control.tenants(p_session text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 PERFORM control.require_actor(p_session);
 RETURN (SELECT COALESCE(jsonb_agg(jsonb_build_object('id',w.id,'company_code',w.external_workspace_id,
  'name',w.name,'status',w.status,'version_no',w.version_no,'purpose',COALESCE(s.purpose,'existing'),
  'created_at',w.created_at,'readiness',control.readiness(w.id)) ORDER BY w.created_at DESC,w.id),'[]')
  FROM platform.workspace w LEFT JOIN control.tenant_setup s ON s.workspace_id=w.id WHERE w.deleted_at IS NULL);
END $$;

CREATE FUNCTION control.provision(p_session text,p_key uuid,p_digest text,p_body jsonb,p_activation_hash text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE actor uuid:=control.require_actor(p_session); prior control.provision_request%ROWTYPE;
 ws uuid:=gen_random_uuid(); uid uuid:=gen_random_uuid(); code text:=lower(btrim(p_body->>'company_code'));
 account text:=upper(btrim(p_body->>'administrator_account'));
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('platform-provision:'||p_key::text,0));
 SELECT * INTO prior FROM control.provision_request WHERE idempotency_key=p_key;
 IF FOUND THEN
  IF prior.operator_id<>actor OR prior.request_digest<>p_digest THEN
   RAISE EXCEPTION 'IDEMPOTENCY_CONFLICT' USING ERRCODE='P7409'; END IF;
  RETURN jsonb_build_object('id',prior.workspace_id,'replayed',true);
 END IF;
 IF code IS NULL OR code !~ '^[a-z0-9][a-z0-9-]{2,47}$'
  OR account IS NULL OR account !~ '^[A-Z0-9][A-Z0-9_.-]{2,63}$'
  OR p_body->>'purpose' IS NULL OR p_body->>'purpose' NOT IN ('internal','demo','customer')
  OR COALESCE(length(btrim(p_body->>'name')),0) NOT BETWEEN 1 AND 100
  OR COALESCE(length(btrim(p_body->>'administrator_name')),0) NOT BETWEEN 1 AND 100 THEN
  RAISE EXCEPTION 'INVALID_TENANT_INPUT' USING ERRCODE='P7422'; END IF;
 INSERT INTO platform.workspace(id,external_workspace_id,name,status,attributes)
 VALUES(ws,code,btrim(p_body->>'name'),'provisioning',jsonb_build_object('purpose',p_body->>'purpose','provisioned_by','platform'));
 INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name)
 VALUES(uid,ws,'local:'||uid::text,account,btrim(p_body->>'administrator_name'));
 -- A company administrator has an organizational identity, never an invented sales appointment.
 INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code)
 VALUES(ws,uid,'administrator','workspace');
 INSERT INTO control.tenant_setup(workspace_id,purpose,administrator_id,activation_hash,activation_expires_at,created_by)
 VALUES(ws,p_body->>'purpose',uid,p_activation_hash,clock_timestamp()+interval '24 hours',actor);
 INSERT INTO control.provision_request VALUES(p_key,actor,p_digest,ws,clock_timestamp());
 INSERT INTO control.audit(operator_id,workspace_id,action,after_state)
 VALUES(actor,ws,'tenant.provision',jsonb_build_object('company_code',code,'status','provisioning','administrator_id',uid));
 RETURN jsonb_build_object('id',ws,'replayed',false);
END $$;

CREATE FUNCTION control.reissue_activation(p_session text,p_workspace uuid,p_version integer,p_hash text,p_reason text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE actor uuid:=control.require_actor(p_session); w platform.workspace%ROWTYPE;
BEGIN
 SELECT * INTO w FROM platform.workspace WHERE id=p_workspace AND deleted_at IS NULL FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'TENANT_NOT_FOUND' USING ERRCODE='P7404'; END IF;
 IF w.version_no<>p_version THEN RAISE EXCEPTION 'VERSION_CONFLICT' USING ERRCODE='P7409'; END IF;
 IF w.status<>'provisioning' OR NOT EXISTS(SELECT 1 FROM control.tenant_setup WHERE workspace_id=p_workspace AND activated_at IS NULL)
  OR COALESCE(length(btrim(p_reason)),0) NOT BETWEEN 1 AND 500 THEN
  RAISE EXCEPTION 'ACTIVATION_REISSUE_NOT_ALLOWED' USING ERRCODE='P7422'; END IF;
 UPDATE control.tenant_setup SET activation_hash=p_hash,activation_expires_at=clock_timestamp()+interval '24 hours' WHERE workspace_id=p_workspace;
 UPDATE platform.workspace SET version_no=version_no+1,updated_at=clock_timestamp() WHERE id=p_workspace;
 INSERT INTO control.audit(operator_id,workspace_id,action,reason) VALUES(actor,p_workspace,'tenant.activation_reissue',btrim(p_reason));
END $$;

CREATE FUNCTION control.activate(p_company text,p_token_hash text,p_password_hash text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE setup control.tenant_setup%ROWTYPE; ws uuid;
BEGIN
 SELECT id INTO ws FROM platform.workspace WHERE external_workspace_id=lower(btrim(p_company)) AND status='provisioning' AND deleted_at IS NULL FOR UPDATE;
 IF ws IS NULL THEN RETURN false; END IF;
 SELECT * INTO setup FROM control.tenant_setup WHERE workspace_id=ws AND activation_hash=p_token_hash
  AND activation_expires_at>clock_timestamp() AND activated_at IS NULL FOR UPDATE;
 IF NOT FOUND THEN RETURN false; END IF;
 INSERT INTO platform.password_credential(user_ref_id,workspace_id,password_hash,must_change_password)
 VALUES(setup.administrator_id,ws,p_password_hash,false);
 UPDATE control.tenant_setup SET activated_at=clock_timestamp(),activation_hash=NULL WHERE workspace_id=ws;
 UPDATE platform.workspace SET version_no=version_no+1,updated_at=clock_timestamp() WHERE id=ws;
 INSERT INTO control.audit(workspace_id,action) VALUES(ws,'tenant.administrator_activated');
 RETURN true;
END $$;

CREATE FUNCTION control.transition(p_session text,p_workspace uuid,p_version integer,p_status text,p_reason text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE actor uuid:=control.require_actor(p_session); w platform.workspace%ROWTYPE; checks jsonb;
BEGIN
 SELECT * INTO w FROM platform.workspace WHERE id=p_workspace AND deleted_at IS NULL FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'TENANT_NOT_FOUND' USING ERRCODE='P7404'; END IF;
 IF w.version_no<>p_version THEN RAISE EXCEPTION 'VERSION_CONFLICT' USING ERRCODE='P7409'; END IF;
 IF COALESCE(length(btrim(p_reason)),0) NOT BETWEEN 1 AND 500 OR p_status IS NULL OR NOT (
  (w.status='provisioning' AND p_status IN ('active','closed')) OR
  (w.status='active' AND p_status IN ('suspended','closed')) OR
  (w.status='suspended' AND p_status IN ('active','closed'))) THEN
  RAISE EXCEPTION 'INVALID_TENANT_TRANSITION' USING ERRCODE='P7422'; END IF;
 IF p_status='active' THEN
  checks:=control.readiness(p_workspace);
  IF EXISTS(SELECT 1 FROM jsonb_each_text(checks) c WHERE c.value<>'true') THEN
   RAISE EXCEPTION 'TENANT_NOT_READY' USING ERRCODE='P7409'; END IF;
 ELSE
  UPDATE platform.auth_session SET status='revoked',revoked_at=clock_timestamp()
   WHERE workspace_id=p_workspace AND status='active';
  -- Invalidate in-flight receipts. A resumed company cannot accept an old response.
  UPDATE ops.job SET status='failed',attempts=greatest(attempts-1,0),locked_by=NULL,locked_until=NULL,
   lease_token=NULL,available_at=clock_timestamp(),last_error_code='TENANT_PAUSED',updated_at=clock_timestamp()
   WHERE workspace_id=p_workspace AND status='running';
  UPDATE ops.feishu_event SET status='failed',attempts=greatest(attempts-1,0),locked_until=NULL,
   lease_token=NULL,available_at=clock_timestamp(),error_code='TENANT_PAUSED'
   WHERE workspace_id=p_workspace AND status='running';
 END IF;
 UPDATE platform.workspace SET status=p_status,version_no=version_no+1,updated_at=clock_timestamp() WHERE id=p_workspace;
 INSERT INTO control.audit(operator_id,workspace_id,action,reason,before_state,after_state)
 VALUES(actor,p_workspace,'tenant.'||p_status,btrim(p_reason),jsonb_build_object('status',w.status,'version_no',w.version_no),
  jsonb_build_object('status',p_status,'version_no',w.version_no+1));
 RETURN jsonb_build_object('id',p_workspace,'status',p_status,'version_no',w.version_no+1);
END $$;

CREATE FUNCTION control.audit_page(p_session text,p_before bigint DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 PERFORM control.require_actor(p_session);
 RETURN (SELECT COALESCE(jsonb_agg(to_jsonb(a) ORDER BY a.id DESC),'[]') FROM
  (SELECT a.id,a.operator_id,a.workspace_id,a.action,a.reason,a.before_state,a.after_state,a.created_at
   FROM control.audit a WHERE p_before IS NULL OR a.id<p_before ORDER BY a.id DESC LIMIT 100) a);
END $$;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA control FROM PUBLIC;
-- Deployment grants only this explicit API function set to a distinct platform
-- database role. Tenant API and worker roles never receive USAGE on control.
COMMIT;
