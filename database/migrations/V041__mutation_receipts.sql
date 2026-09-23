BEGIN;
CREATE TABLE ops.mutation_receipt (
 workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 actor_id uuid NOT NULL REFERENCES platform.user_ref(id),
 operation text NOT NULL CHECK(length(operation) BETWEEN 1 AND 240),
 request_key uuid NOT NULL,
 request_digest text NOT NULL CHECK(length(request_digest)=64),
 response jsonb NOT NULL,
 committed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(workspace_id,actor_id,operation,request_key)
);
COMMENT ON TABLE ops.mutation_receipt IS '正式提交的事务回执；同键同内容回放。不可独立于业务结果提交，不自动过期以免离线重试重复入库';
ALTER TABLE ops.mutation_receipt ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops.mutation_receipt FORCE ROW LEVEL SECURITY;
CREATE POLICY mutation_receipt_actor ON ops.mutation_receipt
 USING(workspace_id=common.current_workspace_id() AND actor_id=common.current_user_ref_id())
 WITH CHECK(workspace_id=common.current_workspace_id() AND actor_id=common.current_user_ref_id());
REVOKE ALL ON ops.mutation_receipt FROM PUBLIC;
DO $$ DECLARE r record; BEGIN
 FOR r IN SELECT DISTINCT grantee FROM information_schema.role_table_grants
 WHERE table_schema='ops' AND table_name='job' AND privilege_type='INSERT' AND grantee<>'PUBLIC'
 LOOP EXECUTE format('GRANT SELECT,INSERT ON ops.mutation_receipt TO %I',r.grantee); END LOOP;
END $$;
INSERT INTO ops.schema_migration(version,description) VALUES('V041','正式提交统一事务幂等回执');
COMMIT;
