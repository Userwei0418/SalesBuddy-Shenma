-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V158__platform_mfa.sql.
-- Original SHA-256: dcd6f0cefe8519c9f98d4bbee23cc454ff3fa14164141774967d90d675c68621. See shenma_whole_121_manifest.json.
BEGIN;
ALTER TABLE control.operator ADD COLUMN mfa_secret jsonb,
 ADD COLUMN mfa_revision integer NOT NULL DEFAULT 0,
 ADD COLUMN mfa_last_step bigint NOT NULL DEFAULT -1;
ALTER TABLE control.session ADD COLUMN mfa_verified_at timestamptz,
 ADD COLUMN mfa_revision integer NOT NULL DEFAULT 0,
 ADD COLUMN mfa_pending jsonb;
UPDATE control.session SET revoked_at=clock_timestamp() WHERE revoked_at IS NULL;

CREATE FUNCTION control.password_actor(p_session text) RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT o.id FROM control.session s JOIN control.operator o ON o.id=s.operator_id
 WHERE s.token_hash=p_session AND s.revoked_at IS NULL AND s.expires_at>clock_timestamp()
 AND o.enabled AND s.mfa_revision=o.mfa_revision;
$$;
CREATE OR REPLACE FUNCTION control.actor(p_session text) RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT o.id FROM control.session s JOIN control.operator o ON o.id=s.operator_id
 WHERE s.token_hash=p_session AND s.revoked_at IS NULL AND s.expires_at>clock_timestamp() AND o.enabled
 AND o.mfa_secret IS NOT NULL AND s.mfa_verified_at IS NOT NULL AND s.mfa_revision=o.mfa_revision;
$$;
CREATE OR REPLACE FUNCTION control.require_actor(p_session text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE actor uuid;
BEGIN
 SELECT o.id INTO actor FROM control.session s JOIN control.operator o ON o.id=s.operator_id
 WHERE s.token_hash=p_session AND s.revoked_at IS NULL AND s.expires_at>clock_timestamp() AND o.enabled
 AND o.mfa_secret IS NOT NULL AND s.mfa_verified_at IS NOT NULL AND s.mfa_revision=o.mfa_revision
 FOR SHARE OF s,o;
 IF actor IS NULL THEN
  IF control.password_actor(p_session) IS NULL THEN RAISE EXCEPTION 'PLATFORM_AUTH_REQUIRED' USING ERRCODE='P7401'; END IF;
  RAISE EXCEPTION 'PLATFORM_MFA_REQUIRED' USING ERRCODE='P7403';
 END IF;
 RETURN actor;
END $$;
CREATE FUNCTION control.require_sensitive(p_session text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE actor uuid:=control.require_actor(p_session);
BEGIN
 IF NOT EXISTS(SELECT 1 FROM control.session WHERE token_hash=p_session
  AND mfa_verified_at>clock_timestamp()-interval '5 minutes') THEN
  RAISE EXCEPTION 'PLATFORM_REAUTH_REQUIRED' USING ERRCODE='P7403'; END IF;
 RETURN actor;
END $$;
CREATE OR REPLACE FUNCTION control.login_candidate(p_account text) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('id',id,'password_hash',password_hash,'mfa_secret',mfa_secret,'mfa_revision',mfa_revision)
 FROM control.operator WHERE account_code=upper(btrim(p_account)) AND enabled;
$$;
CREATE OR REPLACE FUNCTION control.session_profile(p_session text) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('id',o.id,'account_code',o.account_code,'display_name',o.display_name,
  'mfa_enrolled',o.mfa_secret IS NOT NULL,'mfa_verified',s.mfa_verified_at IS NOT NULL,
  'sensitive_until',s.mfa_verified_at+interval '5 minutes')
 FROM control.operator o JOIN control.session s ON s.operator_id=o.id
 WHERE s.token_hash=p_session AND o.id=control.password_actor(p_session);
$$;
DROP FUNCTION control.issue_session(uuid,text,text,text);
CREATE FUNCTION control.issue_session(p_operator uuid,p_observed_hash text,p_session text,p_account_key text,p_revision integer,p_step bigint) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE o control.operator%ROWTYPE; checked_at timestamptz;
BEGIN
 SELECT * INTO o FROM control.operator WHERE id=p_operator AND enabled
  AND password_hash=p_observed_hash AND mfa_revision=p_revision FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'PLATFORM_AUTH_REQUIRED' USING ERRCODE='P7401'; END IF;
 IF o.mfa_secret IS NOT NULL THEN
  IF p_step IS NULL OR p_step<=o.mfa_last_step OR abs(p_step-floor(extract(epoch FROM clock_timestamp())/30)::bigint)>1 THEN
   RAISE EXCEPTION 'PLATFORM_MFA_INVALID' USING ERRCODE='P7403'; END IF;
  checked_at:=clock_timestamp();
  UPDATE control.operator SET mfa_last_step=p_step WHERE id=o.id;
 ELSIF p_step IS NOT NULL THEN RAISE EXCEPTION 'PLATFORM_MFA_INVALID' USING ERRCODE='P7403';
 END IF;
 INSERT INTO control.session(token_hash,operator_id,expires_at,mfa_verified_at,mfa_revision)
  VALUES(p_session,o.id,clock_timestamp()+CASE WHEN checked_at IS NULL THEN interval '5 minutes' ELSE interval '30 minutes' END,checked_at,o.mfa_revision);
 DELETE FROM control.login_attempt WHERE key_hash=p_account_key;
 INSERT INTO control.audit(operator_id,action) VALUES(o.id,CASE WHEN checked_at IS NULL THEN 'platform.password_verified' ELSE 'platform.login' END);
 RETURN control.session_profile(p_session);
END $$;
CREATE FUNCTION control.prepare_mfa(p_session text,p_secret jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE actor uuid:=control.password_actor(p_session);
BEGIN
 IF actor IS NULL THEN RAISE EXCEPTION 'PLATFORM_AUTH_REQUIRED' USING ERRCODE='P7401'; END IF;
 PERFORM 1 FROM control.operator WHERE id=actor AND mfa_secret IS NULL FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'PLATFORM_MFA_ALREADY_ENROLLED' USING ERRCODE='P7403'; END IF;
 IF NOT EXISTS(SELECT 1 FROM control.session WHERE token_hash=p_session AND created_at>clock_timestamp()-interval '5 minutes') THEN
  RAISE EXCEPTION 'PLATFORM_AUTH_REQUIRED' USING ERRCODE='P7401'; END IF;
 UPDATE control.session SET mfa_pending=p_secret WHERE token_hash=p_session;
END $$;
CREATE FUNCTION control.mfa_candidate(p_session text,p_purpose text) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT jsonb_build_object('id',o.id,'password_hash',o.password_hash,'mfa_revision',o.mfa_revision,
  'mfa_secret',CASE WHEN p_purpose='enroll' THEN s.mfa_pending ELSE o.mfa_secret END)
 FROM control.operator o JOIN control.session s ON s.operator_id=o.id
 WHERE s.token_hash=p_session AND o.id=control.password_actor(p_session)
 AND ((p_purpose='enroll' AND o.mfa_secret IS NULL AND s.mfa_pending IS NOT NULL
  AND s.created_at>clock_timestamp()-interval '5 minutes')
  OR (p_purpose='verify' AND o.mfa_secret IS NOT NULL AND s.mfa_verified_at IS NOT NULL));
$$;
CREATE FUNCTION control.finish_mfa(p_session text,p_purpose text,p_secret jsonb,p_revision integer,p_step bigint,p_password_hash text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE actor uuid:=control.password_actor(p_session); o control.operator%ROWTYPE; candidate jsonb;
BEGIN
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
-- All existing tenant mutations require recent second-factor verification in SQL.
DO $$ DECLARE sig text; definition text;
BEGIN
 FOREACH sig IN ARRAY ARRAY['control.provision(text,uuid,text,jsonb,text)',
  'control.reissue_activation(text,uuid,integer,text,text)','control.transition(text,uuid,integer,text,text)',
  'control.save_modules(text,uuid,integer,jsonb,text)'] LOOP
  SELECT pg_get_functiondef(sig::regprocedure) INTO definition;
  EXECUTE replace(definition,'control.require_actor(p_session)','control.require_sensitive(p_session)');
 END LOOP;
END $$;
REVOKE ALL ON FUNCTION control.password_actor(text),control.require_sensitive(text),
 control.issue_session(uuid,text,text,text,integer,bigint),control.prepare_mfa(text,jsonb),
 control.mfa_candidate(text,text),control.finish_mfa(text,text,jsonb,integer,bigint,text) FROM PUBLIC;
ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_internal_v156;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; r record;
BEGIN
 total:=security.reconcile_runtime_grants_internal_v156();
 FOR r IN SELECT rolname FROM pg_roles WHERE NOT rolsuper AND has_schema_privilege(oid,'security','USAGE')
  AND has_function_privilege(oid,'security.profile_customer_owner(uuid)','EXECUTE') LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION security.reconcile_runtime_grants_internal_v156() FROM %I',r.rolname);
 END LOOP;
 FOR r IN SELECT rolname FROM pg_roles WHERE NOT rolsuper AND has_schema_privilege(oid,'control','USAGE')
  AND has_function_privilege(oid,'control.tenants(text)','EXECUTE') LOOP
  EXECUTE format('GRANT EXECUTE ON FUNCTION control.issue_session(uuid,text,text,text,integer,bigint),control.prepare_mfa(text,jsonb),control.mfa_candidate(text,text),control.finish_mfa(text,text,jsonb,integer,bigint,text) TO %I',r.rolname);
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_internal_v156() FROM PUBLIC,salegent_feishu_worker;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description) VALUES('V306','Platform mandatory TOTP and recent verification');
COMMIT;
