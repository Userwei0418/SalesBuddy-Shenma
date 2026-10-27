-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V218__fde_membership_finance_lock_order.sql.
-- Original SHA-256: ddc51593c14567fc91463050138fca32c66da57029354c84fec21428dbbbab4c. See shenma_whole_121_manifest.json.
BEGIN;
SET LOCAL check_function_bodies=on;

-- V212 guards every opportunity UPDATE with a workspace finance lock. Acquire
-- it before V069's explicit tuple lock, matching full saves and initialization.
-- CREATE OR REPLACE retains the original function identity, owner and ACL.
CREATE OR REPLACE FUNCTION security.bump_fde_membership_version(p_opportunity uuid,p_expected_version integer) RETURNS integer
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
 DECLARE current_version integer;
 BEGIN
 IF NOT security.can_manage_fde_members(p_opportunity) THEN RAISE insufficient_privilege; END IF;
 PERFORM security.finance_write_lock();
 SELECT version_no INTO current_version FROM crm.opportunity
 WHERE id=p_opportunity AND workspace_id=common.current_workspace_id() AND deleted_at IS NULL FOR UPDATE;
 IF current_version IS DISTINCT FROM p_expected_version THEN
 RAISE EXCEPTION '商机已变化，请刷新后重试' USING ERRCODE='40001'; END IF;
 UPDATE crm.opportunity SET version_no=version_no+1,updated_at=clock_timestamp()
 WHERE id=p_opportunity RETURNING version_no INTO current_version;
 RETURN current_version;
 END; $$;

INSERT INTO ops.schema_migration(version,description)
 VALUES('V366','Align legacy FDE membership aggregate lock with finance-first writers');
COMMIT;
