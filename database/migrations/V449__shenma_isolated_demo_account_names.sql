BEGIN;

-- Shenma keeps its existing isolated workspace identifier because FXS001 and
-- historical records are bound to it. Permit reserved demo names only in this
-- exact workspace; remove the legacy product demonstration-space exception.
CREATE OR REPLACE FUNCTION security.guard_demo_account_reservation() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF NEW.deleted_at IS NULL
  AND upper(NEW.account_code)=ANY(ARRAY['XS001','XS002','XS003','ZJ001','ZJ002','CPXS001','CPXS002',
   'CPXS003','CPXS004','FDEL001','FDE001','FDE002','FDE003','ZJL001'])
  AND NOT EXISTS(SELECT 1 FROM platform.workspace w WHERE w.id=NEW.workspace_id
   AND (w.id='10b037a3-0d6a-4d63-ba69-f7ffb33dc474'::uuid
     AND w.external_workspace_id='shenma-isolated-demo-20260929'
     AND w.status='active' AND w.deleted_at IS NULL
     AND w.attributes->>'kind'='demo' AND w.attributes->>'purpose'='demo')) THEN
  RAISE EXCEPTION 'DEMO_ACCOUNT_RESERVED' USING ERRCODE='23514',CONSTRAINT='user_ref_demo_account_reserved';
 END IF;
 RETURN NEW;
END $$;

INSERT INTO ops.schema_migration(version,description)
VALUES('V449','Allow reserved demonstration account names in the exact Shenma isolated workspace');
COMMIT;
