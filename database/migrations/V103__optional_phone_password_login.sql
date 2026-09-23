BEGIN;

ALTER TABLE platform.user_ref ADD COLUMN phone_number text
 CHECK (phone_number IS NULL OR phone_number ~ '^1[3-9][0-9]{9}$');
CREATE UNIQUE INDEX user_ref_workspace_phone_unique ON platform.user_ref(workspace_id,phone_number)
 WHERE deleted_at IS NULL AND phone_number IS NOT NULL;
COMMENT ON COLUMN platform.user_ref.phone_number IS '管理员维护的可选11位手机号登录标识，不代表已验证手机所有权；与账号共用密码';

-- Serialize both kinds of identifier within the same company. The phone index
-- alone cannot prevent another member using the number as a legacy account code.
CREATE FUNCTION security.guard_account_phone_collision() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('account-management:'||NEW.workspace_id::text,0));
 IF NEW.deleted_at IS NULL AND EXISTS (
  SELECT 1 FROM platform.user_ref u WHERE u.workspace_id=NEW.workspace_id AND u.id<>NEW.id AND u.deleted_at IS NULL
  AND ((NEW.phone_number IS NOT NULL AND upper(u.account_code)=NEW.phone_number)
    OR (u.phone_number IS NOT NULL AND u.phone_number=upper(NEW.account_code)))
 ) THEN RAISE EXCEPTION '手机号与已有登录账号冲突' USING ERRCODE='23505'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER user_ref_phone_collision BEFORE INSERT OR UPDATE OF account_code,phone_number,workspace_id,deleted_at
 ON platform.user_ref FOR EACH ROW EXECUTE FUNCTION security.guard_account_phone_collision();

-- Return a canonical identity only when exactly one active password account
-- matches. An omitted company never chooses arbitrarily between tenants.
CREATE FUNCTION security.password_login_identifier(p_workspace text,p_identifier text)
 RETURNS TABLE(workspace text,account_code text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 WITH candidates AS (
  SELECT w.external_workspace_id AS workspace,u.account_code
  FROM platform.workspace w JOIN platform.user_ref u ON u.workspace_id=w.id
  JOIN platform.password_credential p ON p.user_ref_id=u.id AND p.workspace_id=u.workspace_id
  WHERE w.status='active' AND w.deleted_at IS NULL AND u.status='active' AND u.deleted_at IS NULL
   AND (NULLIF(btrim(p_workspace),'') IS NULL OR w.external_workspace_id=btrim(p_workspace))
   AND (u.account_code=upper(btrim(p_identifier)) OR u.phone_number=btrim(p_identifier))
 ) SELECT c.workspace,c.account_code FROM candidates c WHERE (SELECT count(*) FROM candidates)=1;
$$;
CREATE OR REPLACE FUNCTION security.password_account_workspace(p_account text) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT i.workspace FROM security.password_login_identifier(NULL,p_account) i;
$$;
CREATE OR REPLACE FUNCTION security.password_candidate(p_workspace text,p_account text,p_role text) RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT to_jsonb(a)||jsonb_build_object('password_hash',p.password_hash,'must_change_password',p.must_change_password AND COALESCE(policy.require_initial_change,true))
 FROM security.password_login_identifier(p_workspace,p_account) i
 CROSS JOIN LATERAL security.resolve_account_actor(i.workspace,i.account_code,p_role) a
 JOIN platform.password_credential p ON p.user_ref_id=a.user_id::uuid AND p.workspace_id=a.workspace_id::uuid
 LEFT JOIN security.password_policy policy ON policy.workspace_id=p.workspace_id;
$$;

-- After password hashing, hold the member row until session issuance commits.
-- A concurrent rename/phone removal must not leave an obsolete alias session.
CREATE FUNCTION security.lock_password_login_identifier(p_identifier text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE found_id uuid;
BEGIN
 SELECT u.id INTO found_id FROM platform.user_ref u
 WHERE u.id=common.current_user_ref_id() AND u.workspace_id=common.current_workspace_id()
 AND u.status='active' AND u.deleted_at IS NULL
 AND (u.account_code=upper(btrim(p_identifier)) OR u.phone_number=btrim(p_identifier))
 FOR SHARE;
 RETURN found_id IS NOT NULL;
END $$;
GRANT EXECUTE ON FUNCTION security.password_login_identifier(text,text),security.lock_password_login_identifier(text) TO PUBLIC;
-- Administrative edits must revoke the target's sessions despite owner-only RLS.
CREATE FUNCTION security.revoke_managed_account_sessions(p_user uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF security.management_actor() IS NOT TRUE OR NOT EXISTS(
  SELECT 1 FROM platform.user_ref u WHERE u.id=p_user
  AND u.workspace_id=common.current_workspace_id() AND u.deleted_at IS NULL
  AND COALESCE(u.attributes->>'platform_managed','false')<>'true'
 ) THEN RAISE EXCEPTION '无权管理此账号会话' USING ERRCODE='42501'; END IF;
 IF common.current_role_code()<>'administrator' AND EXISTS(
  SELECT 1 FROM platform.role_binding r WHERE r.user_ref_id=p_user
  AND r.workspace_id=common.current_workspace_id() AND r.role_code IN ('operations','administrator')
  AND clock_timestamp()>=r.valid_from AND clock_timestamp()<r.valid_to
 ) THEN RAISE EXCEPTION '管理账号会话由系统管理员管理' USING ERRCODE='42501'; END IF;
 UPDATE platform.auth_session SET status='revoked',revoked_at=clock_timestamp()
 WHERE user_ref_id=p_user AND workspace_id=common.current_workspace_id() AND status='active';
END $$;
GRANT EXECUTE ON FUNCTION security.revoke_managed_account_sessions(uuid) TO PUBLIC;
COMMIT;
