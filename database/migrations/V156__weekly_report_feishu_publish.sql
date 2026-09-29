BEGIN;

-- A weekly report is an explicit, user-confirmed publication.  It is not
-- captured when the Agent finishes a draft; the API below inserts one durable
-- Feishu event only after the author presses "确认并推送飞书".
ALTER TABLE ops.feishu_event DROP CONSTRAINT feishu_event_object_kind_check;
ALTER TABLE ops.feishu_event ADD CONSTRAINT feishu_event_object_kind_check
 CHECK(object_kind IN ('customer','opportunity','visit','partner','task','demo_scene','actual','target',
                       'member','contact','forecast','period_actual_snapshot','weekly_report','refresh'));

ALTER TABLE insight.weekly_report
 ADD COLUMN feishu_publish_event_id uuid,
 ADD COLUMN feishu_publish_requested_at timestamptz,
 ADD COLUMN feishu_publish_snapshot jsonb;

COMMENT ON COLUMN insight.weekly_report.feishu_publish_event_id IS
 'Durable ops.feishu_event created by the explicit author publish action; NULL means not sent';
COMMENT ON COLUMN insight.weekly_report.feishu_publish_requested_at IS
 'Time the author confirmed this report for Feishu publication; it is not a delivery receipt';

-- Keep the externally published snapshot immutable.  The normal application
-- role may still edit the current draft, but it cannot replace the event or
-- the frozen source sent to Feishu after publication was confirmed.
CREATE FUNCTION insight.freeze_weekly_publish() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF OLD.feishu_publish_event_id IS NOT NULL AND (
   NEW.feishu_publish_event_id IS DISTINCT FROM OLD.feishu_publish_event_id OR
   NEW.feishu_publish_requested_at IS DISTINCT FROM OLD.feishu_publish_requested_at OR
   NEW.feishu_publish_snapshot IS DISTINCT FROM OLD.feishu_publish_snapshot
 ) THEN
  RAISE EXCEPTION 'WEEKLY_FEISHU_SNAPSHOT_IMMUTABLE';
 END IF;
 IF OLD.feishu_publish_event_id IS NULL AND (
   (NEW.feishu_publish_event_id IS NULL AND
      (NEW.feishu_publish_requested_at IS NOT NULL OR NEW.feishu_publish_snapshot IS NOT NULL)) OR
   (NEW.feishu_publish_event_id IS NOT NULL AND
      (NEW.feishu_publish_requested_at IS NULL OR NEW.feishu_publish_snapshot IS NULL))
 ) THEN
  RAISE EXCEPTION 'WEEKLY_FEISHU_SNAPSHOT_INCOMPLETE';
 END IF;
 RETURN NEW;
END $$;

CREATE TRIGGER freeze_weekly_publish
 BEFORE UPDATE ON insight.weekly_report
 FOR EACH ROW EXECUTE FUNCTION insight.freeze_weekly_publish();

-- V152 grants UPDATE on the report row to the application roles.  Revoke only
-- the three publication columns; the SECURITY DEFINER function below runs as
-- the table owner and remains the sole controlled writer for them.
DO $$ DECLARE r record; BEGIN
 FOR r IN SELECT DISTINCT grantee
   FROM information_schema.role_table_grants
  WHERE table_schema='insight' AND table_name='weekly_report'
    AND privilege_type='UPDATE' AND grantee NOT IN ('PUBLIC', current_user)
 LOOP
  EXECUTE format(
    'REVOKE UPDATE (feishu_publish_event_id,feishu_publish_requested_at,feishu_publish_snapshot) ON insight.weekly_report FROM %I',
    r.grantee);
 END LOOP;
END $$;

-- The worker role reads this bounded projection through SECURITY DEFINER.  It
-- never receives input_snapshot, credentials, or arbitrary report rows.
CREATE FUNCTION ops.feishu_weekly_source(p_connection uuid,p_id uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
 ws uuid;
 raw jsonb;
 stats jsonb;
 snapshot jsonb;
BEGIN
 SELECT workspace_id INTO ws FROM config.feishu_connection WHERE id=p_connection;
 IF ws IS NULL THEN RAISE insufficient_privilege; END IF;
 SELECT r.feishu_publish_snapshot INTO raw FROM insight.weekly_report r
  WHERE r.id=p_id AND r.workspace_id=ws AND r.feishu_publish_event_id IS NOT NULL;
 IF raw IS NULL THEN RETURN jsonb_build_object('id',p_id,'deleted',true); END IF;
 -- Only a completed, contract-accepted report can be published.  A report
 -- that is cancelled, failed, or still queued is excluded without a remote
 -- write, preserving the existing worker's fail-closed behavior.
 IF raw->>'status' <> 'succeeded' OR raw->>'result_status' <> 'ready'
    OR NULLIF(raw->>'draft_markdown','') IS NULL THEN
  RETURN jsonb_build_object('id',p_id,'excluded',true);
 END IF;
 stats:=COALESCE(raw->'statistics','{}'::jsonb);
 RETURN jsonb_build_object(
   'id',raw->>'id',
   'title',COALESCE(raw->>'title','销售周报'),
   'report_week',raw->>'report_week',
   'report_week_end',to_jsonb((raw->>'report_week')::date+6),
   'author_name',(SELECT display_name FROM platform.user_ref WHERE workspace_id=ws AND id=(raw->>'author_id')::uuid),
   'author_account',(SELECT account_code FROM platform.user_ref WHERE workspace_id=ws AND id=(raw->>'author_id')::uuid),
   'status',raw->>'status',
   'result_status',raw->>'result_status',
   'body_markdown',raw->>'draft_markdown',
   'stats_summary',format('跟进 %s 条｜客户 %s 家｜商机 %s 个',
      COALESCE(stats->>'record_count','0'),COALESCE(stats->>'customer_count','0'),
      COALESCE(stats->>'opportunity_count','0')),
   'record_count',COALESCE(stats->>'record_count','0')::integer,
   'customer_count',COALESCE(stats->>'customer_count','0')::integer,
   'opportunity_count',COALESCE(stats->>'opportunity_count','0')::integer,
   'source_cutoff_at',raw->>'source_cutoff_at',
   'snapshot_at',raw->>'snapshot_at',
   'draft_version',COALESCE(raw->>'draft_version','0')::integer,
   'generated_at',raw->>'finished_at',
   'updated_at',raw->>'updated_at',
   'published_at',raw->>'published_at',
   'feishu_push_status',CASE
      WHEN raw->>'published_at' IS NULL THEN '未推送'
      ELSE COALESCE((SELECT CASE d.status WHEN 'sent' THEN '已推送'
                                         WHEN 'unknown' THEN '待核对'
                                         WHEN 'failed' THEN '推送失败'
                                         ELSE '排队中' END
                       FROM ops.feishu_delivery d
                       JOIN ops.feishu_event e ON e.id=d.event_id
                       WHERE e.object_kind='weekly_report' AND e.object_id=p_id
                       ORDER BY d.created_at DESC LIMIT 1),'排队中') END,
   'feishu_message_id',(SELECT d.message_id FROM ops.feishu_delivery d
                         JOIN ops.feishu_event e ON e.id=d.event_id
                         WHERE e.object_kind='weekly_report' AND e.object_id=p_id
                           AND d.message_id IS NOT NULL ORDER BY d.created_at DESC LIMIT 1),
   'created_at',raw->>'created_at',
   'detail_url',NULL::text
 );
END $$;

REVOKE ALL ON FUNCTION ops.feishu_weekly_source(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.feishu_weekly_source(uuid,uuid) TO salegent_feishu_worker;

CREATE FUNCTION security.publish_weekly_report_feishu(p_id uuid,p_version integer) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r insight.weekly_report%ROWTYPE; c config.feishu_connection%ROWTYPE;
 event_id uuid; snapshot jsonb; published_at timestamptz:=clock_timestamp();
BEGIN
 IF security.authorization_has('weekly_report.edit') IS NOT TRUE THEN RAISE insufficient_privilege; END IF;
 SELECT * INTO r FROM insight.weekly_report WHERE id=p_id
  AND workspace_id=common.current_workspace_id() AND author_id=common.current_user_ref_id() FOR UPDATE;
 IF NOT FOUND THEN RAISE insufficient_privilege; END IF;
 -- A replay returns the same event even if the config was subsequently paused.
 IF r.feishu_publish_event_id IS NOT NULL THEN RETURN r.feishu_publish_event_id; END IF;
 IF r.status<>'succeeded' OR r.result_status<>'ready' OR NULLIF(btrim(r.draft_markdown),'') IS NULL THEN
  RAISE EXCEPTION 'WEEKLY_DRAFT_NOT_READY' USING ERRCODE='22023'; END IF;
 IF r.draft_version<>p_version THEN
  RAISE EXCEPTION 'WEEKLY_DRAFT_VERSION_CONFLICT' USING ERRCODE='22023'; END IF;
 SELECT * INTO c FROM config.feishu_connection WHERE workspace_id=r.workspace_id FOR SHARE;
 IF NOT FOUND OR NOT c.enabled OR c.validated_revision IS DISTINCT FROM c.revision
   OR c.settings->>'direction'<>'system_to_base'
   OR COALESCE((c.settings->'mappings'->'weekly_report'->>'enabled')::boolean,false) IS NOT TRUE
   OR COALESCE((c.settings->'notification'->>'enabled')::boolean,false) IS NOT TRUE
   OR c.settings->'notification'->>'routing_mode'<>'single_group'
   OR NULLIF(c.settings->'notification'->>'default_chat_id','') IS NULL
   OR NOT (COALESCE(c.settings->'notification'->'on_create','[]'::jsonb) ? 'weekly_report') THEN
  RAISE EXCEPTION 'WEEKLY_FEISHU_NOT_READY' USING ERRCODE='22023'; END IF;
 event_id:=gen_random_uuid();
 snapshot:=jsonb_build_object('id',r.id,'author_id',r.author_id,'status',r.status,
  'result_status',r.result_status,'title',r.original_result->>'title','draft_markdown',r.draft_markdown,
  'draft_version',r.draft_version,'statistics',r.original_result->'statistics','report_week',r.report_week,
  'source_cutoff_at',r.source_cutoff_at,'snapshot_at',r.input_snapshot::jsonb->'context'->>'as_of',
  'created_at',r.created_at,'updated_at',r.updated_at,'finished_at',r.finished_at,'published_at',published_at);
 UPDATE insight.weekly_report SET feishu_publish_event_id=event_id,
  feishu_publish_requested_at=published_at,feishu_publish_snapshot=snapshot WHERE id=p_id;
 INSERT INTO ops.feishu_event(id,connection_id,workspace_id,object_kind,object_id,first_formal_create)
  VALUES(event_id,c.id,r.workspace_id,'weekly_report',p_id,true);
 RETURN event_id;
END $$;

CREATE FUNCTION security.weekly_feishu_status(p_id uuid) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r insight.weekly_report%ROWTYPE; result jsonb;
BEGIN
 IF security.authorization_has('weekly_report.read') IS NOT TRUE THEN RAISE insufficient_privilege; END IF;
 SELECT * INTO r FROM insight.weekly_report WHERE id=p_id
  AND workspace_id=common.current_workspace_id() AND author_id=common.current_user_ref_id();
 IF NOT FOUND THEN RETURN NULL; END IF;
 IF r.feishu_publish_event_id IS NULL THEN RETURN jsonb_build_object('status','not_published'); END IF;
 SELECT jsonb_build_object('status',CASE
    WHEN d.status='sent' THEN 'sent'
    WHEN d.status='unknown' THEN 'unknown'
    WHEN e.status='dead_letter' OR d.status='failed' THEN 'failed'
    ELSE 'pending' END,
   'event_id',e.id,'requested_at',r.feishu_publish_requested_at,
   'draft_version',r.feishu_publish_snapshot->'draft_version','message_id',d.message_id,
   'error_code',COALESCE(d.error_code,e.error_code),'sent_at',d.sent_at)
 INTO result FROM ops.feishu_event e LEFT JOIN ops.feishu_delivery d ON d.event_id=e.id
 WHERE e.id=r.feishu_publish_event_id ORDER BY d.created_at DESC LIMIT 1;
 RETURN COALESCE(result,jsonb_build_object('status','failed','error_code','WEEKLY_FEISHU_EVENT_MISSING'));
END $$;
REVOKE ALL ON FUNCTION security.publish_weekly_report_feishu(uuid,integer),
 security.weekly_feishu_status(uuid) FROM PUBLIC;

-- Use the existing application role inventory.  The API receives only narrow
-- functions, never direct write access to the shared Feishu queue or secrets.
DO $$ DECLARE r record; BEGIN
 FOR r IN SELECT DISTINCT role.rolname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  CROSS JOIN LATERAL aclexplode(c.relacl) a JOIN pg_roles role ON role.oid=a.grantee
  WHERE n.nspname='insight' AND c.relname='weekly_report' AND a.privilege_type='SELECT'
   AND a.grantee<>c.relowner
 LOOP
  EXECUTE format('GRANT EXECUTE ON FUNCTION security.publish_weekly_report_feishu(uuid,integer),security.weekly_feishu_status(uuid) TO %I',r.rolname);
 END LOOP;
END $$;

INSERT INTO ops.schema_migration(version,description)
 VALUES('V156','Explicit weekly report Feishu publication and bounded source projection');
COMMIT;
