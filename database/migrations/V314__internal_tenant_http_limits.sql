-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V166__tenant_http_limits.sql.
-- Original SHA-256: da9448b4351cfcd6f17e387e5c839b7b5bd9c084f0c3b68d515b7fa73a5c6bb1. See shenma_whole_121_manifest.json.
BEGIN;
-- One shared token bucket per verified tenant and request class. NULL is unlimited.
CREATE TABLE control.tenant_http_limit(
 workspace_id uuid NOT NULL REFERENCES platform.workspace(id),
 bucket text NOT NULL CHECK(bucket IN ('read','write','upload')),
 rate_per_minute integer CHECK(rate_per_minute BETWEEN 1 AND 1000000),
 burst integer CHECK(burst BETWEEN 1 AND 10000),
 tokens numeric NOT NULL DEFAULT 0 CHECK(tokens>=0),
 refilled_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 accepted bigint NOT NULL DEFAULT 0 CHECK(accepted>=0),
 rejected bigint NOT NULL DEFAULT 0 CHECK(rejected>=0),
 PRIMARY KEY(workspace_id,bucket),CHECK((rate_per_minute IS NULL)=(burst IS NULL))
);
INSERT INTO control.tenant_http_limit(workspace_id,bucket)
 SELECT w.id,b FROM platform.workspace w CROSS JOIN unnest(ARRAY['read','write','upload']) b;
CREATE FUNCTION control.initialize_http_limits() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 INSERT INTO control.tenant_http_limit(workspace_id,bucket) SELECT NEW.id,b FROM unnest(ARRAY['read','write','upload']) b;
 RETURN NEW;
END $$;
CREATE TRIGGER initialize_http_limits AFTER INSERT ON platform.workspace
 FOR EACH ROW EXECUTE FUNCTION control.initialize_http_limits();

CREATE FUNCTION security.consume_http_request(p_bucket text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ws uuid:=common.current_workspace_id(); q control.tenant_http_limit; stamp timestamptz; available numeric; wait_seconds integer;
BEGIN
 IF ws IS NULL OR common.current_user_ref_id() IS NULL OR NOT security.workspace_is_active(ws) THEN
  RAISE EXCEPTION 'HTTP_ADMISSION_DENIED' USING ERRCODE='42501'; END IF;
 IF p_bucket IS NULL OR p_bucket NOT IN ('read','write','upload') THEN
  RAISE EXCEPTION 'INVALID_HTTP_BUCKET' USING ERRCODE='P7422'; END IF;
 SELECT * INTO q FROM control.tenant_http_limit WHERE workspace_id=ws AND bucket=p_bucket;
 IF NOT FOUND THEN RAISE EXCEPTION 'HTTP_LIMIT_NOT_INITIALIZED' USING ERRCODE='P7500'; END IF;
 -- No hot row update/lock for companies which have no configured limit.
 IF q.rate_per_minute IS NULL THEN RETURN jsonb_build_object('allowed',true,'retry_after',0); END IF;
 SELECT * INTO q FROM control.tenant_http_limit WHERE workspace_id=ws AND bucket=p_bucket FOR UPDATE;
 IF q.rate_per_minute IS NULL THEN RETURN jsonb_build_object('allowed',true,'retry_after',0); END IF;
 stamp:=clock_timestamp();
 available:=least(q.burst,q.tokens+greatest(0,extract(epoch FROM stamp-q.refilled_at))*q.rate_per_minute/60);
 IF available>=1 THEN
  UPDATE control.tenant_http_limit SET tokens=available-1,refilled_at=stamp,accepted=accepted+1 WHERE workspace_id=ws AND bucket=p_bucket;
  RETURN jsonb_build_object('allowed',true,'retry_after',0);
 END IF;
 wait_seconds:=greatest(1,ceil((1-available)*60/q.rate_per_minute)::integer);
 UPDATE control.tenant_http_limit SET tokens=available,refilled_at=stamp,rejected=rejected+1 WHERE workspace_id=ws AND bucket=p_bucket;
 RETURN jsonb_build_object('allowed',false,'retry_after',wait_seconds);
END $$;

CREATE FUNCTION control.http_limits(p_session text,p_workspace uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE result jsonb;
BEGIN
 PERFORM control.require_actor(p_session);
 SELECT jsonb_build_object('workspace_id',w.id,'version_no',w.version_no,'items',
  (SELECT jsonb_agg(jsonb_build_object('bucket',q.bucket,'label',l.label,'rate_per_minute',q.rate_per_minute,
   'burst',q.burst,'accepted',q.accepted,'rejected',q.rejected) ORDER BY l.ordinal)
   FROM control.tenant_http_limit q JOIN (VALUES('read','读取请求',1),('write','写入请求',2),('upload','文件与录音上传',3)) l(code,label,ordinal)
    ON l.code=q.bucket WHERE q.workspace_id=w.id)) INTO result
 FROM platform.workspace w WHERE w.id=p_workspace AND w.deleted_at IS NULL;
 IF result IS NULL THEN RAISE EXCEPTION 'TENANT_NOT_FOUND' USING ERRCODE='P7404'; END IF;
 RETURN result;
END $$;
CREATE FUNCTION control.save_http_limit(p_session text,p_workspace uuid,p_version integer,p_bucket text,p_rate integer,p_burst integer,p_reason text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE actor uuid:=control.require_sensitive(p_session); version integer; old control.tenant_http_limit;
 before_value jsonb; after_value jsonb; available numeric; stamp timestamptz;
BEGIN
 SELECT version_no INTO version FROM platform.workspace WHERE id=p_workspace AND deleted_at IS NULL FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'TENANT_NOT_FOUND' USING ERRCODE='P7404'; END IF;
 IF version IS DISTINCT FROM p_version THEN RAISE EXCEPTION 'VERSION_CONFLICT' USING ERRCODE='P7409'; END IF;
 IF p_bucket IS NULL OR p_bucket NOT IN ('read','write','upload')
 OR (p_rate IS NULL)<>(p_burst IS NULL) OR (p_rate IS NOT NULL AND p_rate NOT BETWEEN 1 AND 1000000)
 OR (p_burst IS NOT NULL AND p_burst NOT BETWEEN 1 AND 10000)
 OR COALESCE(length(btrim(p_reason)),0) NOT BETWEEN 1 AND 500 THEN
  RAISE EXCEPTION 'INVALID_HTTP_LIMIT' USING ERRCODE='P7422'; END IF;
 SELECT * INTO old FROM control.tenant_http_limit WHERE workspace_id=p_workspace AND bucket=p_bucket FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'HTTP_LIMIT_NOT_INITIALIZED' USING ERRCODE='P7500'; END IF;
 before_value:=control.http_limits(p_session,p_workspace); stamp:=clock_timestamp();
 available:=CASE WHEN p_rate IS NULL THEN 0 WHEN old.rate_per_minute IS NULL THEN p_burst
  ELSE least(p_burst,old.burst,old.tokens+greatest(0,extract(epoch FROM stamp-old.refilled_at))*old.rate_per_minute/60) END;
 UPDATE control.tenant_http_limit SET rate_per_minute=p_rate,burst=p_burst,tokens=available,refilled_at=stamp
 WHERE workspace_id=p_workspace AND bucket=p_bucket;
 UPDATE platform.workspace SET version_no=version_no+1,updated_at=clock_timestamp() WHERE id=p_workspace;
 after_value:=control.http_limits(p_session,p_workspace);
 INSERT INTO control.audit(operator_id,workspace_id,action,reason,before_state,after_state)
 VALUES(actor,p_workspace,'tenant.http_limit',btrim(p_reason),before_value,after_value);
 RETURN after_value;
END $$;
REVOKE ALL ON control.tenant_http_limit FROM PUBLIC,salegent_feishu_worker;
REVOKE ALL ON FUNCTION control.initialize_http_limits(),security.consume_http_request(text),
 control.http_limits(text,uuid),control.save_http_limit(text,uuid,integer,text,integer,integer,text) FROM PUBLIC,salegent_feishu_worker;
ALTER FUNCTION security.reconcile_runtime_grants() RENAME TO reconcile_runtime_grants_before_http_limits;
CREATE FUNCTION security.reconcile_runtime_grants() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE total integer; r record;
BEGIN
 total:=security.reconcile_runtime_grants_before_http_limits();
 FOR r IN SELECT rolname FROM pg_roles WHERE NOT rolsuper AND has_schema_privilege(oid,'security','USAGE')
 AND has_function_privilege(oid,'security.profile_customer_owner(uuid)','EXECUTE') LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION security.reconcile_runtime_grants_before_http_limits() FROM %I',r.rolname);
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.consume_http_request(text) TO %I',r.rolname);
 END LOOP;
 FOR r IN SELECT rolname FROM pg_roles WHERE NOT rolsuper AND has_schema_privilege(oid,'control','USAGE')
 AND has_function_privilege(oid,'control.tenants(text)','EXECUTE') LOOP
  EXECUTE format('GRANT EXECUTE ON FUNCTION control.http_limits(text,uuid),control.save_http_limit(text,uuid,integer,text,integer,integer,text) TO %I',r.rolname);
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION security.reconcile_runtime_grants(),security.reconcile_runtime_grants_before_http_limits() FROM PUBLIC,salegent_feishu_worker;
SELECT security.reconcile_runtime_grants();
INSERT INTO ops.schema_migration(version,description) VALUES('V314','Shared tenant request token buckets and platform configuration');
COMMIT;
