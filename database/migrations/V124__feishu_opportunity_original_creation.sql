BEGIN;
-- Forward-only, additive source projection. No business data, table definitions,
-- event queues, existing remote mappings or notification settings are changed.
-- CREATE OR REPLACE retains the source function identity, owner and ACLs.
CREATE OR REPLACE FUNCTION ops.feishu_source(p_connection uuid,p_kind text,p_id uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE ws uuid; result jsonb; extra jsonb; original_value jsonb; original_source text; opportunity_meta jsonb;
BEGIN
 SELECT workspace_id INTO ws FROM config.feishu_connection WHERE id=p_connection;
 IF ws IS NULL THEN RAISE insufficient_privilege; END IF;
 IF p_kind='period_actual_snapshot' THEN
  SELECT jsonb_build_object('id',s.id,'opportunity_id',s.opportunity_id,'customer_id',o.customer_id,
   'opportunity_name',o.name,'customer_name',c.name,'year',s.year,'quarter',s.quarter,'kind',s.kind,
   'source_field',s.source_field,'raw_amount',s.raw_amount,'source_unit',s.source_unit,'tax_basis',s.tax_basis,
   'source_record_id',s.source_record_id,'import_batch_id',s.import_batch_id,'created_at',s.created_at,
   'source_system',b.source_system,'source_base_id',b.source_base_id,
   'source_table_id',r.source_table_id,'source_external_record_id',r.source_record_id,
   'company_name',(SELECT name FROM platform.workspace WHERE id=ws)) INTO result
  FROM crm.opportunity_period_actual_snapshot s
  JOIN crm.opportunity o ON o.id=s.opportunity_id AND o.workspace_id=s.workspace_id
  JOIN crm.customer c ON c.id=o.customer_id AND c.workspace_id=o.workspace_id
  JOIN ops.crm_import_record r ON r.id=s.source_record_id AND r.workspace_id=s.workspace_id
  JOIN ops.crm_import_batch b ON b.id=s.import_batch_id AND b.workspace_id=s.workspace_id
  WHERE s.id=p_id AND s.workspace_id=ws AND o.deleted_at IS NULL AND c.deleted_at IS NULL AND c.data_kind='production';
  IF result IS NULL THEN RETURN jsonb_build_object('id',p_id,'excluded',true); END IF;
  RETURN result;
 END IF;
 result:=ops.feishu_source_v120(p_connection,p_kind,p_id);
 IF result->>'excluded'='true' OR result->>'deleted'='true' THEN RETURN result; END IF;
 IF p_kind='opportunity' THEN
  SELECT jsonb_build_object('original_owner_name',original_owner_name,'ownership_resolution',ownership_resolution,
   'ownership_resolution_evidence',ownership_resolution_evidence,'expected_close_year',expected_close_year,
   'expected_close_quarter',expected_close_quarter,
   'associated_partner_ids',(SELECT COALESCE(jsonb_agg(r.partner_id ORDER BY r.partner_id),'[]'::jsonb)
    FROM crm.opportunity_related_partner r WHERE r.workspace_id=ws AND r.opportunity_id=p_id),
   'associated_partner_names',(SELECT COALESCE(jsonb_agg(p.name ORDER BY r.partner_id),'[]'::jsonb)
    FROM crm.opportunity_related_partner r JOIN crm.partner p ON p.id=r.partner_id AND p.workspace_id=r.workspace_id
    WHERE r.workspace_id=ws AND r.opportunity_id=p_id)) INTO extra FROM crm.opportunity WHERE id=p_id AND workspace_id=ws;
  -- Additional raw provenance only. Never replace the existing system creation
  -- timestamp, parse dates, or use an import timestamp as missing historical data.
  IF result->>'deleted_at' IS NULL THEN
   SELECT import_meta INTO opportunity_meta FROM crm.opportunity WHERE id=p_id AND workspace_id=ws;
   IF opportunity_meta->>'import_type'='crm_history' THEN
    original_value:=opportunity_meta->'source_fields'->'商机创建时间';
    original_source:='history_source_fields';
    IF original_value IS NULL OR original_value='null'::jsonb OR
      (jsonb_typeof(original_value)='string' AND (original_value #>> '{}') !~ '[^[:space:]]') THEN
     original_value:=opportunity_meta->'raw_fields'->'商机创建时间';
     original_source:='history_legacy_raw_fields';
    END IF;
    IF original_value IS NULL OR original_value='null'::jsonb OR
      (jsonb_typeof(original_value)='string' AND (original_value #>> '{}') !~ '[^[:space:]]') THEN
     original_value:=NULL; original_source:='historical_unknown';
    END IF;
   ELSE
    original_value:=result->'created_at'; original_source:='system_entry';
   END IF;
   -- A text target preserves strings byte-for-byte; unexpected structured source
   -- values remain JSON text instead of being silently coerced into a date.
   extra:=extra||jsonb_build_object('original_created_at_raw',
    CASE WHEN original_value IS NULL THEN NULL
      WHEN jsonb_typeof(original_value)='string' THEN original_value #>> '{}'
      ELSE original_value::text END,'original_created_at_source',original_source);
  END IF;
 ELSIF p_kind='forecast' THEN
  SELECT jsonb_build_object('collection_confidence',collection_confidence) INTO extra
   FROM crm.opportunity_forecast WHERE id=p_id AND workspace_id=ws;
 ELSIF p_kind='partner' THEN
  SELECT jsonb_build_object('short_name',p.short_name,'principal_name',p.principal_name,
   'channel_manager_user_ref_id',p.channel_manager_user_ref_id,'channel_manager_name',u.display_name,
   'original_channel_manager_name',p.original_channel_manager_name,'priority',p.priority,'progress',p.progress,
   'grade',p.grade,'signed_on',p.signed_on,'partner_type',p.partner_type,'region',p.region,'province',p.province,'note',p.note,
   'last_interaction_at',(SELECT max(v.interaction_at) FROM activity.visit v WHERE v.workspace_id=ws
    AND v.partner_id=p.id AND v.deleted_at IS NULL AND v.status IN ('confirmed','archived')))
   INTO extra FROM crm.partner p LEFT JOIN platform.user_ref u ON u.id=p.channel_manager_user_ref_id AND u.workspace_id=p.workspace_id
   WHERE p.id=p_id AND p.workspace_id=ws;
 ELSIF p_kind='visit' THEN
  -- A multi-link narrative is not exported if any linked parent is excluded.
  IF EXISTS(SELECT 1 FROM activity.visit_opportunity x LEFT JOIN crm.opportunity o
    ON o.id=x.opportunity_id AND o.workspace_id=x.workspace_id
    LEFT JOIN crm.customer c ON c.id=o.customer_id AND c.workspace_id=o.workspace_id
    WHERE x.visit_id=p_id AND x.workspace_id=ws AND
     (o.id IS NULL OR o.deleted_at IS NOT NULL OR c.id IS NULL OR c.deleted_at IS NOT NULL OR c.data_kind<>'production')) THEN
   RETURN jsonb_build_object('id',p_id,'excluded',true);
  END IF;
  SELECT jsonb_build_object('partner_id',v.partner_id,'partner_name',p.name,
   'original_recorder_name',v.original_recorder_name,'manager_user_ref_id',v.manager_user_ref_id,
   'manager_name',u.display_name,'manager_account',u.account_code,
   'opportunity_ids',(SELECT COALESCE(jsonb_agg(x.opportunity_id ORDER BY x.opportunity_id),'[]'::jsonb)
    FROM activity.visit_opportunity x WHERE x.visit_id=v.id AND x.workspace_id=ws),
   'opportunity_names',(SELECT COALESCE(jsonb_agg(o.name ORDER BY o.id),'[]'::jsonb)
    FROM activity.visit_opportunity x JOIN crm.opportunity o ON o.id=x.opportunity_id AND o.workspace_id=x.workspace_id
    WHERE x.visit_id=v.id AND x.workspace_id=ws),
   'customer_ids',(SELECT COALESCE(jsonb_agg(cid ORDER BY cid),'[]'::jsonb) FROM
    (SELECT v.customer_id AS cid WHERE v.customer_id IS NOT NULL UNION
     SELECT o.customer_id FROM activity.visit_opportunity x JOIN crm.opportunity o
     ON o.id=x.opportunity_id AND o.workspace_id=x.workspace_id WHERE x.visit_id=v.id AND x.workspace_id=ws) customers),
   'customer_names',(SELECT COALESCE(jsonb_agg(c.name ORDER BY c.id),'[]'::jsonb) FROM crm.customer c
    WHERE c.workspace_id=ws AND c.id IN (
     SELECT v.customer_id WHERE v.customer_id IS NOT NULL UNION
     SELECT o.customer_id FROM activity.visit_opportunity x JOIN crm.opportunity o
      ON o.id=x.opportunity_id AND o.workspace_id=x.workspace_id WHERE x.visit_id=v.id AND x.workspace_id=ws)))
   INTO extra FROM activity.visit v LEFT JOIN crm.partner p ON p.id=v.partner_id AND p.workspace_id=v.workspace_id
   LEFT JOIN platform.user_ref u ON u.id=v.manager_user_ref_id AND u.workspace_id=v.workspace_id WHERE v.id=p_id AND v.workspace_id=ws;
 END IF;
 RETURN result||COALESCE(extra,'{}'::jsonb);
END $$;
COMMENT ON FUNCTION ops.feishu_source(uuid,text,uuid) IS
 '飞书受控源投影；商机额外提供原建单时间原文及来源，系统created_at保持审计含义；历史缺失不补造';
INSERT INTO ops.schema_migration(version,description)
 VALUES('V124','飞书商机原始建单时间与系统创建时间分离投影');
COMMIT;
