-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V189__platform_two_step_login_and_company_entry.sql.
-- Original SHA-256: 15c4e6b1b065777942644ec052d0c89cd97738846f01d8ebde21868fa6dd9e17. See shenma_whole_121_manifest.json.
-- Additive two-step platform login. Existing one-step issue_session and
-- sensitive-operation verification remain available under their original ACLs.
CREATE OR REPLACE FUNCTION control.session_profile(p_session text) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('id',o.id,'account_code',o.account_code,'display_name',o.display_name,
  'mfa_enrolled',o.mfa_secret IS NOT NULL,'mfa_verified',s.mfa_verified_at IS NOT NULL,
  'sensitive_until',s.mfa_verified_at+interval '5 minutes','expires_at',s.expires_at,
  'expires_in',greatest(0,ceil(extract(epoch FROM s.expires_at-clock_timestamp()))::integer))
 FROM control.operator o JOIN control.session s ON s.operator_id=o.id
 WHERE s.token_hash=p_session AND o.id=control.password_actor(p_session);
$$;

CREATE FUNCTION control.begin_login(p_operator uuid,p_observed_hash text,p_session text,
 p_account_key text,p_revision integer) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE o control.operator%ROWTYPE;
BEGIN
 SELECT * INTO o FROM control.operator WHERE id=p_operator AND enabled
  AND password_hash=p_observed_hash AND mfa_revision=p_revision FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'PLATFORM_AUTH_REQUIRED' USING ERRCODE='P7401'; END IF;
 INSERT INTO control.session(token_hash,operator_id,expires_at,mfa_revision)
 VALUES(p_session,o.id,clock_timestamp()+interval '5 minutes',o.mfa_revision);
 DELETE FROM control.login_attempt WHERE key_hash=p_account_key;
 INSERT INTO control.audit(operator_id,action) VALUES(o.id,'platform.password_verified');
 RETURN control.session_profile(p_session);
END $$;

CREATE OR REPLACE FUNCTION control.mfa_candidate(p_session text,p_purpose text) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('id',o.id,'password_hash',o.password_hash,'mfa_revision',o.mfa_revision,
  'mfa_secret',CASE WHEN p_purpose='enroll' THEN s.mfa_pending ELSE o.mfa_secret END)
 FROM control.operator o JOIN control.session s ON s.operator_id=o.id
 WHERE s.token_hash=p_session AND o.id=control.password_actor(p_session)
 AND ((p_purpose='enroll' AND o.mfa_secret IS NULL AND s.mfa_pending IS NOT NULL
  AND s.created_at>clock_timestamp()-interval '5 minutes')
  OR (p_purpose='verify' AND o.mfa_secret IS NOT NULL AND s.mfa_verified_at IS NOT NULL)
  OR (p_purpose='login' AND o.mfa_secret IS NOT NULL AND s.mfa_verified_at IS NULL));
$$;

-- Private shared implementation. Lock operator before session, matching owner
-- recovery and enrollment; recheck the old session after both locks are held.
CREATE FUNCTION control.rotate_mfa_session(p_session text,p_purpose text,p_secret jsonb,
 p_revision integer,p_step bigint,p_password_hash text,p_new_session text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE o control.operator%ROWTYPE; s control.session%ROWTYPE; actor uuid;
 stamp timestamptz; current_step bigint; expected_secret jsonb;
BEGIN
 IF p_purpose IS NULL OR p_purpose NOT IN ('login','enroll')
  OR p_new_session IS NULL OR p_new_session !~ '^[a-f0-9]{64}$' OR p_new_session=p_session
 THEN RAISE EXCEPTION 'PLATFORM_MFA_INVALID' USING ERRCODE='P7403'; END IF;
 SELECT operator_id INTO actor FROM control.session WHERE token_hash=p_session;
 IF actor IS NULL THEN RAISE EXCEPTION 'PLATFORM_AUTH_REQUIRED' USING ERRCODE='P7401'; END IF;
 SELECT * INTO o FROM control.operator WHERE id=actor FOR UPDATE;
 SELECT * INTO s FROM control.session WHERE token_hash=p_session AND operator_id=actor FOR UPDATE;
 stamp:=clock_timestamp(); current_step:=floor(extract(epoch FROM stamp)/30)::bigint;
 IF NOT FOUND OR NOT o.enabled OR s.revoked_at IS NOT NULL OR s.expires_at<=stamp
  OR s.mfa_revision IS DISTINCT FROM o.mfa_revision
 THEN RAISE EXCEPTION 'PLATFORM_AUTH_REQUIRED' USING ERRCODE='P7401'; END IF;
 IF s.mfa_verified_at IS NOT NULL
  OR (p_purpose='login' AND o.mfa_secret IS NULL)
  OR (p_purpose='enroll' AND (o.mfa_secret IS NOT NULL OR s.mfa_pending IS NULL
       OR s.created_at<=stamp-interval '5 minutes'))
 THEN RAISE EXCEPTION 'PLATFORM_MFA_INVALID' USING ERRCODE='P7403'; END IF;
 expected_secret:=CASE WHEN p_purpose='enroll' THEN s.mfa_pending ELSE o.mfa_secret END;
 IF o.mfa_revision IS DISTINCT FROM p_revision OR o.password_hash IS DISTINCT FROM p_password_hash
  OR expected_secret IS DISTINCT FROM p_secret OR p_step IS NULL OR p_step<=o.mfa_last_step
  OR p_step NOT BETWEEN current_step-1 AND current_step+1
 THEN RAISE EXCEPTION 'PLATFORM_MFA_INVALID' USING ERRCODE='P7403'; END IF;
 IF p_purpose='enroll' THEN
  UPDATE control.operator SET mfa_secret=p_secret,mfa_revision=mfa_revision+1,mfa_last_step=p_step
   WHERE id=actor RETURNING * INTO o;
  UPDATE control.session SET revoked_at=stamp,mfa_pending=NULL
   WHERE operator_id=actor AND revoked_at IS NULL;
 ELSE
  UPDATE control.operator SET mfa_last_step=p_step WHERE id=actor;
  UPDATE control.session SET revoked_at=stamp,mfa_pending=NULL WHERE token_hash=p_session;
 END IF;
 INSERT INTO control.session(token_hash,operator_id,expires_at,mfa_verified_at,mfa_revision)
 VALUES(p_new_session,actor,stamp+interval '30 minutes',stamp,o.mfa_revision);
 INSERT INTO control.audit(operator_id,action)
 VALUES(actor,CASE WHEN p_purpose='enroll' THEN 'platform.mfa_enroll' ELSE 'platform.login' END);
 RETURN control.session_profile(p_new_session);
END $$;

CREATE FUNCTION control.complete_login(p_session text,p_secret jsonb,p_revision integer,
 p_step bigint,p_password_hash text,p_new_session text) RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT control.rotate_mfa_session(p_session,'login',p_secret,p_revision,p_step,p_password_hash,p_new_session);
$$;
CREATE FUNCTION control.confirm_mfa(p_session text,p_secret jsonb,p_revision integer,
 p_step bigint,p_password_hash text,p_new_session text) RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT control.rotate_mfa_session(p_session,'enroll',p_secret,p_revision,p_step,p_password_hash,p_new_session);
$$;

-- Adding a candidate purpose must not make the legacy finish endpoint accept
-- login and upgrade an old token without rotation. Its enroll/verify contract
-- and existing clients remain unchanged.
CREATE OR REPLACE FUNCTION control.finish_mfa(p_session text,p_purpose text,p_secret jsonb,
 p_revision integer,p_step bigint,p_password_hash text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE actor uuid:=control.password_actor(p_session); o control.operator%ROWTYPE; candidate jsonb;
BEGIN
 IF p_purpose IS NULL OR p_purpose NOT IN ('enroll','verify') THEN
  RAISE EXCEPTION 'PLATFORM_MFA_INVALID' USING ERRCODE='P7403'; END IF;
 IF actor IS NULL THEN RAISE EXCEPTION 'PLATFORM_AUTH_REQUIRED' USING ERRCODE='P7401'; END IF;
 SELECT * INTO o FROM control.operator WHERE id=actor FOR UPDATE;
 candidate:=control.mfa_candidate(p_session,p_purpose);
 IF candidate IS NULL OR o.mfa_revision<>p_revision OR o.password_hash<>p_password_hash
 OR (candidate->'mfa_secret') IS DISTINCT FROM p_secret OR p_step IS NULL OR p_step<=o.mfa_last_step
 OR abs(p_step-floor(extract(epoch FROM clock_timestamp())/30)::bigint)>1 THEN
  RAISE EXCEPTION 'PLATFORM_MFA_INVALID' USING ERRCODE='P7403'; END IF;
 IF p_purpose='enroll' THEN
  UPDATE control.operator SET mfa_secret=p_secret,mfa_revision=mfa_revision+1,mfa_last_step=p_step WHERE id=actor;
  UPDATE control.session SET revoked_at=clock_timestamp(),mfa_pending=NULL WHERE operator_id=actor AND token_hash<>p_session AND revoked_at IS NULL;
  UPDATE control.session SET mfa_revision=o.mfa_revision+1,mfa_pending=NULL,mfa_verified_at=clock_timestamp(),
   expires_at=clock_timestamp()+interval '30 minutes' WHERE token_hash=p_session;
 ELSE
  UPDATE control.operator SET mfa_last_step=p_step WHERE id=actor;
  UPDATE control.session SET mfa_verified_at=clock_timestamp() WHERE token_hash=p_session;
 END IF;
 INSERT INTO control.audit(operator_id,action) VALUES(actor,'platform.mfa_'||p_purpose);
 RETURN control.session_profile(p_session);
END $$;

CREATE FUNCTION security.company_entry(p_code text) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('company_code',w.external_workspace_id,'company_name',w.name)
 FROM platform.workspace w
 WHERE length(btrim(p_code)) BETWEEN 1 AND 128
  AND lower(w.external_workspace_id)=lower(btrim(p_code))
  AND w.status='active' AND w.deleted_at IS NULL;
$$;

-- Remove deployer-specific default grants as well as PUBLIC. Only the exact
-- runtime families below receive the new externally callable functions.
DO $$ DECLARE signature text; grantee record;
BEGIN
 FOREACH signature IN ARRAY ARRAY['control.begin_login(uuid,text,text,text,integer)',
  'control.rotate_mfa_session(text,text,jsonb,integer,bigint,text,text)',
  'control.complete_login(text,jsonb,integer,bigint,text,text)',
  'control.confirm_mfa(text,jsonb,integer,bigint,text,text)','security.company_entry(text)'] LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',signature);
  FOR grantee IN SELECT DISTINCT r.rolname FROM pg_proc p
   CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
   JOIN pg_roles r ON r.oid=a.grantee WHERE p.oid=signature::regprocedure AND a.grantee<>p.proowner LOOP
   EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I',signature,grantee.rolname);
  END LOOP;
 END LOOP;
END $$;

ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_before_login_entry;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; r record; is_api boolean; is_platform boolean;
BEGIN
 total:=security.reconcile_runtime_grants_before_login_entry();
 FOR r IN SELECT rolname FROM pg_roles WHERE NOT rolsuper AND
  (has_schema_privilege(oid,'security','USAGE') OR has_schema_privilege(oid,'control','USAGE')) LOOP
  is_api:=has_schema_privilege(r.rolname,'security','USAGE')
   AND has_function_privilege(r.rolname,'security.save_account_authorization(uuid,jsonb)','EXECUTE');
  is_platform:=has_schema_privilege(r.rolname,'control','USAGE')
   AND has_function_privilege(r.rolname,'control.tenants(text)','EXECUTE');
  EXECUTE format('REVOKE ALL ON FUNCTION security.company_entry(text),security.reconcile_runtime_grants_before_login_entry(),security.reconcile_runtime_grants(),control.begin_login(uuid,text,text,text,integer),control.complete_login(text,jsonb,integer,bigint,text,text),control.confirm_mfa(text,jsonb,integer,bigint,text,text),control.rotate_mfa_session(text,text,jsonb,integer,bigint,text,text) FROM %I',r.rolname);
  IF is_api THEN EXECUTE format('GRANT EXECUTE ON FUNCTION security.company_entry(text) TO %I',r.rolname); END IF;
  IF is_platform THEN
   EXECUTE format('GRANT EXECUTE ON FUNCTION control.begin_login(uuid,text,text,text,integer),control.complete_login(text,jsonb,integer,bigint,text,text),control.confirm_mfa(text,jsonb,integer,bigint,text,text) TO %I',r.rolname);
  END IF;
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_before_login_entry()
 FROM PUBLIC,salegent_feishu_worker;
SELECT security.reconcile_runtime_grants();

COMMENT ON FUNCTION security.company_entry(text) IS
 'API-only exact active company code lookup; exposes only code and display name, never accounts or a company list';
COMMENT ON FUNCTION control.rotate_mfa_session(text,text,jsonb,integer,bigint,text,text) IS
 'Private atomic MFA completion and token rotation; no runtime role may call directly';
