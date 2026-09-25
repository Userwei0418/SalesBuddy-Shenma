BEGIN;
ALTER TABLE insight.weekly_report ADD COLUMN report_week date;
ALTER TABLE insight.weekly_report ADD COLUMN source_cutoff_at timestamptz;
UPDATE insight.weekly_report SET
 report_week=date_trunc('week', (input_snapshot::jsonb->'period'->>'end_date')::date)::date,
 source_cutoff_at=(input_snapshot::jsonb->>'current_time')::timestamptz;
ALTER TABLE insight.weekly_report ALTER COLUMN report_week SET NOT NULL;
ALTER TABLE insight.weekly_report ALTER COLUMN source_cutoff_at SET NOT NULL;
ALTER TABLE insight.weekly_report ADD CONSTRAINT weekly_report_monday CHECK(extract(isodow FROM report_week)=1);
CREATE INDEX weekly_report_owner_week ON insight.weekly_report(workspace_id,author_id,report_week,created_at DESC);
CREATE OR REPLACE FUNCTION insight.freeze_weekly_source() RETURNS trigger
 LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF ROW(NEW.id,NEW.workspace_id,NEW.author_id,NEW.request_id,NEW.job_id,NEW.input_snapshot,NEW.input_sha256,NEW.binding,NEW.created_at,NEW.report_week,NEW.source_cutoff_at)
 IS DISTINCT FROM ROW(OLD.id,OLD.workspace_id,OLD.author_id,OLD.request_id,OLD.job_id,OLD.input_snapshot,OLD.input_sha256,OLD.binding,OLD.created_at,OLD.report_week,OLD.source_cutoff_at)
 OR (OLD.original_result IS NOT NULL AND NEW.original_result IS DISTINCT FROM OLD.original_result) THEN
  RAISE EXCEPTION 'Weekly source and original result are immutable';
 END IF;
 RETURN NEW;
END $$;
COMMENT ON COLUMN insight.weekly_report.report_week IS '报告归属周的周一；独立于weekly.v2的14天素材窗口';
COMMENT ON COLUMN insight.weekly_report.source_cutoff_at IS '上传记录截止水位；历史周截至周日，档案仍为生成时快照';
COMMIT;
