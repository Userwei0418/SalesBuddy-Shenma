BEGIN;
SET LOCAL row_security=off;

-- Use the same lock order as account writes; preserve the current usage count.
SELECT security.lock_deployment_account_quota();
ALTER TABLE security.deployment_account_quota
 ALTER COLUMN max_active_accounts SET DEFAULT 60;
UPDATE security.deployment_account_quota SET max_active_accounts=60 WHERE singleton;

INSERT INTO ops.schema_migration(version,description)
 VALUES('V155','Raise deployment-wide active account quota to 60');
COMMIT;
