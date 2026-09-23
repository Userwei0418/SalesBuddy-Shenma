-- Run as shenma_runtime in the customer database; returns only access booleans.
SELECT current_user AS runtime_role, rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user;
SELECT count(*) AS owned_business_tables FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user) AND n.nspname IN ('platform','crm','activity','workflow','ops','security','config');
SELECT has_table_privilege(current_user,'platform.password_credential','SELECT') AS can_read_password_hashes,
       has_table_privilege(current_user,'security.login_throttle','SELECT') AS can_read_private_throttle,
       has_function_privilege(current_user,'security.reconcile_runtime_grants()','EXECUTE') AS can_change_grants;
