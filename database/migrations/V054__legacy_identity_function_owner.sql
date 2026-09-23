BEGIN;

-- CREATE OR REPLACE preserves a function's historical owner. Environments whose
-- baseline was installed by another schema owner therefore cannot let the V048
-- resolver read the new credential table. Keep that table private: align only
-- this narrow SECURITY DEFINER function with the credential table's trusted owner.
DO $$
DECLARE credential_owner text;
BEGIN
  SELECT pg_catalog.pg_get_userbyid(c.relowner) INTO STRICT credential_owner
    FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='platform' AND c.relname='password_credential';
  EXECUTE format('ALTER FUNCTION security.resolve_demo_actor(text,text) OWNER TO %I',credential_owner);
END $$;

COMMIT;
