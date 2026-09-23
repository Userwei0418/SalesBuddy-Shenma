-- Sales Hub V1 baseline generated from sales_saas at 2026-09-09T20:01:58+08:00
--
-- PostgreSQL database dump
--

\restrict AlAt19fy9tnBwXmBhVI6sIA1gpwtO64NWLpHnceRaQw2hejHdHlrEdO9kHCAaa2

-- Dumped from database version 16.15
-- Dumped by pg_dump version 16.15

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: activity; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA activity;


--
-- Name: SCHEMA activity; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA activity IS '拜访活动：主表稳定字段 + 表单字段值；action_item 为预留拆条';


--
-- Name: agent; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA agent;


--
-- Name: SCHEMA agent; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA agent IS '对话、运行、模型调用与产物；run_step/confirmation/tool_invocation 为预留';


--
-- Name: common; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA common;


--
-- Name: SCHEMA common; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA common IS '会话上下文与时间戳触发器';


--
-- Name: config; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA config;


--
-- Name: SCHEMA config; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA config IS '可版本化配置：表单、字典、规则、Agent 定义与运行时配置';


--
-- Name: crm; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA crm;


--
-- Name: SCHEMA crm; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA crm IS '客户经营主数据：客户、联系人、商机、产品关系、分配';


--
-- Name: insight; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA insight;


--
-- Name: SCHEMA insight; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA insight IS '洞察：四象限、风险、能力复盘；recommendation/report 为预留';


--
-- Name: ops; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA ops;


--
-- Name: SCHEMA ops; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA ops IS '作业、迁移、幂等与审计';


--
-- Name: platform; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA platform;


--
-- Name: SCHEMA platform; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA platform IS '身份与组织：工作空间、用户引用、团队、角色、会话';


--
-- Name: security; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA security;


--
-- Name: SCHEMA security; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA security IS 'RLS 辅助函数：客户范围、会话、角色';


--
-- Name: workflow; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA workflow;


--
-- Name: SCHEMA workflow; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA workflow IS '任务、通知与投递';


--
-- Name: pgcrypto; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;


--
-- Name: EXTENSION pgcrypto; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pgcrypto IS 'cryptographic functions';


--
-- Name: current_role_code(); Type: FUNCTION; Schema: common; Owner: -
--

CREATE FUNCTION common.current_role_code() RETURNS text
    LANGUAGE sql STABLE
    AS $$
  SELECT NULLIF(current_setting('app.role_code', true), '')
$$;


--
-- Name: current_user_ref_id(); Type: FUNCTION; Schema: common; Owner: -
--

CREATE FUNCTION common.current_user_ref_id() RETURNS uuid
    LANGUAGE sql STABLE
    AS $$
  SELECT NULLIF(current_setting('app.user_ref_id', true), '')::uuid
$$;


--
-- Name: current_workspace_id(); Type: FUNCTION; Schema: common; Owner: -
--

CREATE FUNCTION common.current_workspace_id() RETURNS uuid
    LANGUAGE sql STABLE
    AS $$
  SELECT NULLIF(current_setting('app.workspace_id', true), '')::uuid
$$;


--
-- Name: touch_runtime_updated_at(); Type: FUNCTION; Schema: common; Owner: -
--

CREATE FUNCTION common.touch_runtime_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  NEW.updated_at := clock_timestamp();
  RETURN NEW;
END;
$$;


--
-- Name: touch_updated_at(); Type: FUNCTION; Schema: common; Owner: -
--

CREATE FUNCTION common.touch_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  NEW.updated_at := clock_timestamp();
  IF NEW.version_no IS NOT NULL THEN
    NEW.version_no := OLD.version_no + 1;
  END IF;
  RETURN NEW;
END;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: job; Type: TABLE; Schema: ops; Owner: -
--

CREATE TABLE ops.job (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    job_type text NOT NULL,
    aggregate_type text,
    aggregate_id uuid,
    payload jsonb NOT NULL,
    status text DEFAULT 'queued'::text NOT NULL,
    priority smallint DEFAULT 50 NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    max_attempts integer DEFAULT 3 NOT NULL,
    available_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    locked_by text,
    locked_until timestamp with time zone,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    last_error_code text,
    last_error_detail text,
    correlation_id uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT job_attempts_check CHECK ((attempts >= 0)),
    CONSTRAINT job_max_attempts_check CHECK ((max_attempts > 0)),
    CONSTRAINT job_priority_check CHECK (((priority >= 0) AND (priority <= 100))),
    CONSTRAINT job_status_check CHECK ((status = ANY (ARRAY['queued'::text, 'running'::text, 'succeeded'::text, 'failed'::text, 'cancelled'::text, 'dead_letter'::text])))
);

ALTER TABLE ONLY ops.job FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE job; Type: COMMENT; Schema: ops; Owner: -
--

COMMENT ON TABLE ops.job IS '现行：后台作业队列';


--
-- Name: claim_job(text, integer); Type: FUNCTION; Schema: ops; Owner: -
--

CREATE FUNCTION ops.claim_job(p_worker_id text, p_lock_seconds integer) RETURNS SETOF ops.job
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'ops'
    AS $$
BEGIN
  RETURN QUERY
  UPDATE ops.job j
     SET status = 'running',
         attempts = j.attempts + 1,
         locked_by = p_worker_id,
         locked_until = clock_timestamp() + make_interval(secs => p_lock_seconds),
         started_at = COALESCE(j.started_at, clock_timestamp()),
         updated_at = clock_timestamp()
   WHERE j.id = (
     SELECT candidate.id
     FROM ops.job candidate
     WHERE candidate.status IN ('queued', 'failed')
       AND candidate.available_at <= clock_timestamp()
       AND (candidate.locked_until IS NULL OR candidate.locked_until < clock_timestamp())
     ORDER BY candidate.priority DESC, candidate.available_at, candidate.created_at
     FOR UPDATE SKIP LOCKED
     LIMIT 1
   )
  RETURNING j.*;
END;
$$;


--
-- Name: enqueue_daily_sales_competency_reviews(); Type: FUNCTION; Schema: ops; Owner: -
--

CREATE FUNCTION ops.enqueue_daily_sales_competency_reviews() RETURNS integer
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'platform', 'config', 'insight', 'ops'
    AS $$
DECLARE
  inserted_count integer;
BEGIN
  WITH framework AS (
    SELECT f.version_no
      FROM config.sales_competency_framework f
     WHERE f.status = 'active'
       AND f.effective_to = 'infinity'
     ORDER BY (f.workspace_id IS NOT NULL) DESC, f.version_no DESC
     LIMIT 1
  ), sales_users AS (
    SELECT u.workspace_id, u.id AS user_ref_id,
           COALESCE(array_agg(DISTINCT tm.team_id::text)
             FILTER (WHERE tm.team_id IS NOT NULL), ARRAY[]::text[]) AS team_ids
      FROM platform.user_ref u
      JOIN platform.role_binding ra
        ON ra.user_ref_id = u.id
       AND ra.workspace_id = u.workspace_id
       AND ra.role_code = 'sales'
       AND clock_timestamp() >= ra.valid_from
       AND clock_timestamp() < ra.valid_to
      LEFT JOIN platform.team_membership tm
        ON tm.user_ref_id = u.id
       AND tm.workspace_id = u.workspace_id
       AND clock_timestamp() >= tm.valid_from
       AND clock_timestamp() < tm.valid_to
     WHERE u.deleted_at IS NULL AND u.status = 'active'
     GROUP BY u.workspace_id, u.id
  ), inserted_reviews AS (
    INSERT INTO insight.sales_competency_review (
      workspace_id, subject_user_ref_id, review_date, framework_version, status
    )
    SELECT s.workspace_id, s.user_ref_id,
           timezone('Asia/Shanghai', clock_timestamp())::date,
           f.version_no, 'queued'
      FROM sales_users s CROSS JOIN framework f
    ON CONFLICT (workspace_id, subject_user_ref_id, review_date, framework_version)
    DO NOTHING
    RETURNING id, workspace_id, subject_user_ref_id
  )
  INSERT INTO ops.job (
    workspace_id, job_type, aggregate_type, aggregate_id, payload, priority
  )
  SELECT r.workspace_id, 'sales_competency.review', 'sales_competency_review', r.id,
         jsonb_build_object(
           'user_id', r.subject_user_ref_id::text,
           'role', 'sales',
           'data_scope', 'self',
           'team_ids', s.team_ids
         ), 45
    FROM inserted_reviews r
    JOIN sales_users s ON s.user_ref_id = r.subject_user_ref_id
  ;

  GET DIAGNOSTICS inserted_count = ROW_COUNT;
  RETURN inserted_count;
END;
$$;


--
-- Name: run_priority_reminder_monitor(); Type: FUNCTION; Schema: ops; Owner: -
--

CREATE FUNCTION ops.run_priority_reminder_monitor() RETURNS integer
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'workflow', 'insight', 'crm', 'ops'
    AS $$
DECLARE
  task_count integer := 0;
  risk_count integer := 0;
BEGIN
  INSERT INTO workflow.notification (
    workspace_id, recipient_user_ref_id, channel_code, template_code,
    title, body, object_type, object_id, status, dedupe_key,
    scheduled_at, payload
  )
  SELECT t.workspace_id, ta.assignee_user_ref_id, 'in_app',
         'priority_task_due', '待办即将到期',
         t.title || '；截止时间：' || to_char(t.due_at AT TIME ZONE 'Asia/Shanghai', 'MM-DD HH24:MI'),
         'task', t.id, 'pending', 'priority_monitor:task:' || t.id::text,
         clock_timestamp(),
         jsonb_build_object(
           'agent_code', 'priority_monitor',
           'task_id', t.id::text,
           'description', t.description,
           'due_at', t.due_at,
           'hours_remaining', round(extract(epoch FROM (t.due_at - clock_timestamp())) / 3600.0, 1),
           'monitored_at', clock_timestamp()
         )
    FROM workflow.task t
    JOIN workflow.task_assignee ta
      ON ta.task_id = t.id AND ta.responsibility = 'owner'
   WHERE t.deleted_at IS NULL
     AND t.status IN ('pending_confirm', 'pending_execution', 'in_progress', 'deferred')
     AND t.due_at > clock_timestamp()
     AND t.due_at <= clock_timestamp() + interval '12 hours'
     AND NOT EXISTS (
       SELECT 1 FROM workflow.notification existing
        WHERE existing.workspace_id = t.workspace_id
          AND existing.recipient_user_ref_id = ta.assignee_user_ref_id
          AND existing.dedupe_key = 'priority_monitor:task:' || t.id::text
          AND existing.status <> 'cancelled'
     );
  GET DIAGNOSTICS task_count = ROW_COUNT;

  INSERT INTO workflow.notification (
    workspace_id, recipient_user_ref_id, channel_code, template_code,
    title, body, object_type, object_id, status, dedupe_key,
    scheduled_at, payload
  )
  SELECT r.workspace_id, r.owner_user_ref_id, 'in_app',
         'priority_risk',
         CASE r.severity_code
           WHEN 'critical' THEN '严重风险提醒'
           WHEN 'high' THEN '高风险提醒'
           ELSE '中风险提醒'
         END,
         r.title || COALESCE('；' || NULLIF(btrim(r.description), ''), ''),
         'risk', r.id, 'pending', 'priority_monitor:risk:' || r.id::text,
         clock_timestamp(),
         jsonb_build_object(
           'agent_code', 'priority_monitor',
           'risk_id', r.id::text,
           'severity', r.severity_code,
           'customer_name', c.name,
           'description', r.description,
           'due_at', r.due_at,
           'monitored_at', clock_timestamp()
         )
    FROM insight.risk r
    LEFT JOIN crm.customer c ON c.id = r.customer_id
   WHERE r.deleted_at IS NULL
     AND r.owner_user_ref_id IS NOT NULL
     AND r.status IN ('new', 'pending', 'in_progress', 'escalated')
     AND r.severity_code IN ('medium', 'high', 'critical')
     AND NOT EXISTS (
       SELECT 1 FROM workflow.notification existing
        WHERE existing.workspace_id = r.workspace_id
          AND existing.recipient_user_ref_id = r.owner_user_ref_id
          AND existing.dedupe_key = 'priority_monitor:risk:' || r.id::text
          AND existing.status <> 'cancelled'
     );
  GET DIAGNOSTICS risk_count = ROW_COUNT;

  UPDATE workflow.notification n
     SET status = 'cancelled'
   WHERE n.template_code = 'priority_task_due'
     AND n.status NOT IN ('cancelled', 'read')
     AND NOT EXISTS (
       SELECT 1 FROM workflow.task t
        WHERE t.id = n.object_id
          AND t.deleted_at IS NULL
          AND t.status IN ('pending_confirm', 'pending_execution', 'in_progress', 'deferred')
          AND t.due_at > clock_timestamp()
          AND t.due_at <= clock_timestamp() + interval '12 hours'
     );

  UPDATE workflow.notification n
     SET status = 'cancelled'
   WHERE n.template_code = 'priority_risk'
     AND n.status NOT IN ('cancelled', 'read')
     AND NOT EXISTS (
       SELECT 1 FROM insight.risk r
        WHERE r.id = n.object_id
          AND r.deleted_at IS NULL
          AND r.status IN ('new', 'pending', 'in_progress', 'escalated')
          AND r.severity_code IN ('medium', 'high', 'critical')
     );

  RETURN task_count + risk_count;
END;
$$;


--
-- Name: auth_session_is_active(uuid, uuid, uuid); Type: FUNCTION; Schema: security; Owner: -
--

CREATE FUNCTION security.auth_session_is_active(p_session_id uuid, p_workspace_id uuid, p_user_ref_id uuid) RETURNS boolean
    LANGUAGE sql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'platform', 'security'
    AS $$
  SELECT EXISTS (
    SELECT 1 FROM platform.auth_session
    WHERE id = p_session_id AND workspace_id = p_workspace_id
      AND user_ref_id = p_user_ref_id AND status = 'active'
      AND expires_at > clock_timestamp()
  )
$$;


--
-- Name: create_auth_session(uuid, uuid, uuid, text, timestamp with time zone, jsonb); Type: FUNCTION; Schema: security; Owner: -
--

CREATE FUNCTION security.create_auth_session(p_session_id uuid, p_workspace_id uuid, p_user_ref_id uuid, p_refresh_token_hash text, p_expires_at timestamp with time zone, p_client_context jsonb) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'platform', 'security'
    AS $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM platform.user_ref
    WHERE id = p_user_ref_id AND workspace_id = p_workspace_id
      AND status = 'active' AND deleted_at IS NULL
  ) THEN
    RAISE EXCEPTION 'actor is inactive' USING ERRCODE = '28000';
  END IF;
  INSERT INTO platform.auth_session (
    id, workspace_id, user_ref_id, refresh_token_hash, expires_at, client_context
  ) VALUES (
    p_session_id, p_workspace_id, p_user_ref_id, p_refresh_token_hash,
    p_expires_at, COALESCE(p_client_context, '{}'::jsonb)
  );
END;
$$;


--
-- Name: has_active_role(text); Type: FUNCTION; Schema: security; Owner: -
--

CREATE FUNCTION security.has_active_role(p_role_code text) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'platform', 'common'
    AS $$
  SELECT EXISTS (
    SELECT 1
    FROM platform.role_binding rb
    WHERE rb.workspace_id = common.current_workspace_id()
      AND rb.user_ref_id = common.current_user_ref_id()
      AND rb.role_code = p_role_code
      AND clock_timestamp() >= rb.valid_from
      AND clock_timestamp() < rb.valid_to
  )
$$;


--
-- Name: has_customer_access(uuid); Type: FUNCTION; Schema: security; Owner: -
--

CREATE FUNCTION security.has_customer_access(p_customer_id uuid) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'platform', 'crm', 'common'
    AS $$
  SELECT EXISTS (
    SELECT 1
    FROM crm.customer c
    WHERE c.id = p_customer_id
      AND c.workspace_id = common.current_workspace_id()
      AND c.deleted_at IS NULL
      AND (
        (
          common.current_role_code() = 'manager'
          AND security.has_active_role('manager')
        )
        OR (
          common.current_role_code() = 'supervisor'
          AND security.has_active_role('supervisor')
          AND EXISTS (
            SELECT 1
            FROM platform.team_membership tm
            WHERE tm.workspace_id = c.workspace_id
              AND tm.user_ref_id = common.current_user_ref_id()
              AND tm.team_id = c.owner_team_id
              AND tm.membership_role = 'supervisor'
              AND clock_timestamp() >= tm.valid_from
              AND clock_timestamp() < tm.valid_to
          )
        )
        OR (
          common.current_role_code() = 'sales'
          AND security.has_active_role('sales')
          AND (
            c.owner_user_ref_id = common.current_user_ref_id()
            OR EXISTS (
              SELECT 1
              FROM crm.opportunity o
              JOIN crm.opportunity_participant op ON op.opportunity_id = o.id
              WHERE o.customer_id = c.id
                AND op.user_ref_id = common.current_user_ref_id()
                AND clock_timestamp() >= op.valid_from
                AND clock_timestamp() < op.valid_to
            )
          )
        )
      )
  )
$$;


--
-- Name: FUNCTION has_customer_access(p_customer_id uuid); Type: COMMENT; Schema: security; Owner: -
--

COMMENT ON FUNCTION security.has_customer_access(p_customer_id uuid) IS 'Database guardrail for manager/workspace, supervisor/team and sales/self-or-opportunity-participant scopes. The API must set app.workspace_id, app.user_ref_id and app.role_code from a verified token per transaction.';


--
-- Name: model_usage_summary(integer); Type: FUNCTION; Schema: security; Owner: -
--

CREATE FUNCTION security.model_usage_summary(p_days integer) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE result jsonb;
BEGIN
  IF p_days IS NULL OR p_days < 1 OR p_days > 90 THEN
    RAISE EXCEPTION 'INVALID_PERIOD' USING ERRCODE = '22023';
  END IF;
  IF common.current_role_code() IS DISTINCT FROM 'manager'
     OR NOT security.has_active_role('manager')
     OR NOT EXISTS (
       SELECT 1 FROM platform.user_ref u JOIN platform.workspace w ON w.id=u.workspace_id
       WHERE u.id=common.current_user_ref_id() AND u.workspace_id=common.current_workspace_id()
         AND u.status='active' AND u.deleted_at IS NULL AND w.status='active' AND w.deleted_at IS NULL
     ) THEN
    RAISE EXCEPTION 'MANAGER_ONLY' USING ERRCODE = '42501';
  END IF;
  SELECT COALESCE(jsonb_agg(to_jsonb(usage_row)), '[]'::jsonb) INTO result FROM (
            SELECT i.provider_code, i.model_id, i.endpoint_code,
                   COALESCE(r.intent_code, 'unknown') AS agent_code,
                   count(*) AS calls,
                   count(*) FILTER (WHERE i.status = 'succeeded') AS succeeded,
                   count(*) FILTER (WHERE i.status = 'failed') AS failed,
                   count(*) FILTER (WHERE i.status = 'running') AS running,
                   count(*) FILTER (WHERE i.status = 'cancelled') AS cancelled,
                   count(*) FILTER (WHERE i.attempt_no > 1) AS recorded_retries,
                   avg(i.latency_ms)::float8 AS average_latency_ms,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY i.latency_ms) AS p95_latency_ms,
                   sum(i.input_tokens) AS input_tokens,
                   sum(i.output_tokens) AS output_tokens,
                   count(i.input_tokens) AS input_usage_records,
                   count(i.output_tokens) AS output_usage_records
              FROM agent.model_invocation i
              JOIN agent.run r ON r.id = i.run_id AND r.workspace_id = i.workspace_id
             WHERE i.workspace_id = common.current_workspace_id()
               AND i.started_at >= clock_timestamp() - make_interval(days => p_days)
             GROUP BY i.provider_code, i.model_id, i.endpoint_code, r.intent_code
             ORDER BY count(*) DESC, i.model_id, r.intent_code

  ) usage_row;
  RETURN result;
END;
$$;


--
-- Name: FUNCTION model_usage_summary(p_days integer); Type: COMMENT; Schema: security; Owner: -
--

COMMENT ON FUNCTION security.model_usage_summary(p_days integer) IS 'Manager-only current-workspace aggregate model usage; no raw content or individual identifiers.';


--
-- Name: resolve_demo_actor(text, text); Type: FUNCTION; Schema: security; Owner: -
--

CREATE FUNCTION security.resolve_demo_actor(p_workspace_external_id text, p_account_code text) RETURNS TABLE(workspace_id text, user_id text, account_code text, display_name text, role_code text, data_scope_code text, team_ids text[], team_names text[])
    LANGUAGE sql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'platform', 'security'
    AS $$
  SELECT
    w.id::text,
    u.id::text,
    u.account_code,
    u.display_name,
    rb.role_code,
    rb.data_scope_code,
    COALESCE(array_agg(DISTINCT tm.team_id::text)
      FILTER (WHERE tm.team_id IS NOT NULL), ARRAY[]::text[]),
    COALESCE(array_agg(DISTINCT t.name)
      FILTER (WHERE t.name IS NOT NULL), ARRAY[]::text[])
  FROM platform.workspace w
  JOIN platform.user_ref u
    ON u.workspace_id = w.id AND u.deleted_at IS NULL AND u.status = 'active'
  JOIN platform.role_binding rb
    ON rb.workspace_id = w.id AND rb.user_ref_id = u.id
   AND clock_timestamp() >= rb.valid_from AND clock_timestamp() < rb.valid_to
  LEFT JOIN platform.team_membership tm
    ON tm.workspace_id = w.id AND tm.user_ref_id = u.id
   AND clock_timestamp() >= tm.valid_from AND clock_timestamp() < tm.valid_to
  LEFT JOIN platform.team t ON t.id = tm.team_id AND t.deleted_at IS NULL
  WHERE w.external_workspace_id = p_workspace_external_id
    AND w.status = 'active'
    AND u.account_code = upper(btrim(p_account_code))
  GROUP BY w.id, u.id, u.account_code, u.display_name,
           rb.role_code, rb.data_scope_code
  LIMIT 1
$$;


--
-- Name: revoke_auth_session(uuid, uuid, uuid); Type: FUNCTION; Schema: security; Owner: -
--

CREATE FUNCTION security.revoke_auth_session(p_session_id uuid, p_workspace_id uuid, p_user_ref_id uuid) RETURNS void
    LANGUAGE sql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'platform', 'security'
    AS $$
  UPDATE platform.auth_session
     SET status = 'revoked', revoked_at = clock_timestamp(), updated_at = clock_timestamp()
   WHERE id = p_session_id AND workspace_id = p_workspace_id
     AND user_ref_id = p_user_ref_id
$$;


--
-- Name: rotate_auth_session(text, text, timestamp with time zone); Type: FUNCTION; Schema: security; Owner: -
--

CREATE FUNCTION security.rotate_auth_session(p_old_hash text, p_new_hash text, p_new_expires_at timestamp with time zone) RETURNS TABLE(session_id text, workspace_id text, user_id text, account_code text, display_name text, role_code text, data_scope_code text, team_ids text[], team_names text[])
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'platform', 'security'
    AS $$
BEGIN
  RETURN QUERY
  WITH rotated AS (
    UPDATE platform.auth_session s
       SET refresh_token_hash = p_new_hash,
           expires_at = p_new_expires_at,
           last_seen_at = clock_timestamp(),
           updated_at = clock_timestamp()
     WHERE s.refresh_token_hash = p_old_hash
       AND s.status = 'active'
       AND s.expires_at > clock_timestamp()
    RETURNING s.id, s.workspace_id, s.user_ref_id
  )
  SELECT
    rotated.id::text,
    u.workspace_id::text,
    u.id::text,
    u.account_code,
    u.display_name,
    rb.role_code,
    rb.data_scope_code,
    COALESCE(array_agg(DISTINCT tm.team_id::text)
      FILTER (WHERE tm.team_id IS NOT NULL), ARRAY[]::text[]),
    COALESCE(array_agg(DISTINCT t.name)
      FILTER (WHERE t.name IS NOT NULL), ARRAY[]::text[])
  FROM rotated
  JOIN platform.user_ref u ON u.id = rotated.user_ref_id
  JOIN platform.role_binding rb
    ON rb.workspace_id = u.workspace_id AND rb.user_ref_id = u.id
   AND clock_timestamp() >= rb.valid_from AND clock_timestamp() < rb.valid_to
  LEFT JOIN platform.team_membership tm
    ON tm.workspace_id = u.workspace_id AND tm.user_ref_id = u.id
   AND clock_timestamp() >= tm.valid_from AND clock_timestamp() < tm.valid_to
  LEFT JOIN platform.team t ON t.id = tm.team_id AND t.deleted_at IS NULL
  GROUP BY rotated.id, u.workspace_id, u.id, u.account_code, u.display_name,
           rb.role_code, rb.data_scope_code;
END;
$$;


--
-- Name: enqueue_battle_map_leader_notifications(uuid, uuid, uuid, text, text, numeric, numeric, text); Type: FUNCTION; Schema: workflow; Owner: -
--

CREATE FUNCTION workflow.enqueue_battle_map_leader_notifications(p_workspace_id uuid, p_customer_id uuid, p_trigger_id uuid, p_title text, p_body text, p_potential_score numeric, p_relationship_score numeric, p_quadrant_code text) RETURNS integer
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'workflow', 'platform', 'crm', 'common', 'security'
    AS $$
DECLARE
  v_owner_team_id uuid;
  v_inserted integer := 0;
BEGIN
  IF p_workspace_id IS DISTINCT FROM common.current_workspace_id() THEN
    RAISE EXCEPTION 'WORKSPACE_SCOPE_VIOLATION';
  END IF;

  SELECT customer.owner_team_id
    INTO v_owner_team_id
    FROM crm.customer customer
   WHERE customer.id = p_customer_id
     AND customer.workspace_id = p_workspace_id
     AND customer.deleted_at IS NULL
     AND (
       customer.owner_user_ref_id = common.current_user_ref_id()
       OR security.has_active_role('manager')
       OR (
         security.has_active_role('supervisor')
         AND EXISTS (
           SELECT 1
             FROM platform.team_membership current_tm
            WHERE current_tm.user_ref_id = common.current_user_ref_id()
              AND current_tm.team_id = customer.owner_team_id
              AND clock_timestamp() >= current_tm.valid_from
              AND clock_timestamp() < current_tm.valid_to
         )
       )
     );

  IF NOT FOUND THEN
    RAISE EXCEPTION 'BATTLE_MAP_NOTIFICATION_FORBIDDEN';
  END IF;

  INSERT INTO workflow.notification (
    workspace_id, recipient_user_ref_id, channel_code, template_code,
    title, body, object_type, object_id, status, dedupe_key, payload
  )
  SELECT DISTINCT p_workspace_id, recipient.id, 'in_app', 'battle_map_updated',
         p_title, p_body, 'customer', p_customer_id, 'pending',
         'battle_map_updated:' || p_trigger_id::text || ':' || recipient.id::text,
         jsonb_build_object(
           'customer_id', p_customer_id::text,
           'trigger_type', 'visit.archived',
           'trigger_id', p_trigger_id::text,
           'potential_score', p_potential_score,
           'relationship_score', p_relationship_score,
           'quadrant_code', p_quadrant_code
         )
    FROM platform.user_ref recipient
    JOIN platform.role_binding rb ON rb.user_ref_id = recipient.id
     AND clock_timestamp() >= rb.valid_from
     AND clock_timestamp() < rb.valid_to
    LEFT JOIN platform.team_membership tm ON tm.user_ref_id = recipient.id
     AND tm.is_primary
     AND clock_timestamp() >= tm.valid_from
     AND clock_timestamp() < tm.valid_to
   WHERE recipient.workspace_id = p_workspace_id
     AND recipient.status = 'active'
     AND recipient.deleted_at IS NULL
     AND recipient.id <> common.current_user_ref_id()
     AND (
       rb.role_code = 'manager'
       OR (rb.role_code = 'supervisor' AND tm.team_id = v_owner_team_id)
     )
  ON CONFLICT (workspace_id, recipient_user_ref_id, dedupe_key)
    WHERE dedupe_key IS NOT NULL DO NOTHING;

  GET DIAGNOSTICS v_inserted = ROW_COUNT;
  RETURN v_inserted;
END;
$$;


--
-- Name: enqueue_task_notification(uuid, uuid, text, text, text, uuid, text, jsonb); Type: FUNCTION; Schema: workflow; Owner: -
--

CREATE FUNCTION workflow.enqueue_task_notification(p_workspace_id uuid, p_recipient_user_ref_id uuid, p_template_code text, p_title text, p_body text, p_task_id uuid, p_dedupe_key text, p_payload jsonb DEFAULT '{}'::jsonb) RETURNS boolean
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'workflow', 'platform', 'common', 'security'
    AS $$
BEGIN
  IF p_workspace_id IS DISTINCT FROM common.current_workspace_id() THEN
    RAISE EXCEPTION 'WORKSPACE_SCOPE_VIOLATION';
  END IF;

  IF NOT EXISTS (
    SELECT 1
      FROM workflow.task t
     WHERE t.id = p_task_id
       AND t.workspace_id = p_workspace_id
       AND t.deleted_at IS NULL
       AND (
         t.creator_user_ref_id = common.current_user_ref_id()
         OR EXISTS (
           SELECT 1
             FROM workflow.task_assignee ta
            WHERE ta.task_id = t.id
              AND ta.assignee_user_ref_id = common.current_user_ref_id()
         )
       )
  ) THEN
    RAISE EXCEPTION 'TASK_NOTIFICATION_FORBIDDEN';
  END IF;

  IF NOT EXISTS (
    SELECT 1
      FROM platform.user_ref recipient
     WHERE recipient.id = p_recipient_user_ref_id
       AND recipient.workspace_id = p_workspace_id
       AND recipient.status = 'active'
       AND recipient.deleted_at IS NULL
  ) THEN
    RAISE EXCEPTION 'NOTIFICATION_RECIPIENT_NOT_FOUND';
  END IF;

  INSERT INTO workflow.notification (
    workspace_id, recipient_user_ref_id, channel_code, template_code,
    title, body, object_type, object_id, status, dedupe_key, payload
  ) VALUES (
    p_workspace_id, p_recipient_user_ref_id, 'in_app', p_template_code,
    p_title, p_body, 'task', p_task_id, 'pending', p_dedupe_key,
    COALESCE(p_payload, '{}'::jsonb)
  )
  ON CONFLICT (workspace_id, recipient_user_ref_id, dedupe_key)
    WHERE dedupe_key IS NOT NULL DO NOTHING;

  RETURN true;
END;
$$;


--
-- Name: action_item; Type: TABLE; Schema: activity; Owner: -
--

CREATE TABLE activity.action_item (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    customer_id uuid NOT NULL,
    opportunity_id uuid,
    source_visit_id uuid,
    title text NOT NULL,
    description text,
    owner_user_ref_id uuid,
    due_at timestamp with time zone,
    status text DEFAULT 'open'::text NOT NULL,
    attributes jsonb DEFAULT '{}'::jsonb NOT NULL,
    version_no integer DEFAULT 1 NOT NULL,
    created_by_user_ref_id uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    deleted_at timestamp with time zone,
    CONSTRAINT action_item_status_check CHECK ((status = ANY (ARRAY['open'::text, 'in_progress'::text, 'done'::text, 'cancelled'::text]))),
    CONSTRAINT action_item_version_no_check CHECK ((version_no > 0))
);


--
-- Name: TABLE action_item; Type: COMMENT; Schema: activity; Owner: -
--

COMMENT ON TABLE activity.action_item IS '预留：拜访待办拆条；当前待办走 workflow.task';


--
-- Name: visit; Type: TABLE; Schema: activity; Owner: -
--

CREATE TABLE activity.visit (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    customer_id uuid NOT NULL,
    opportunity_id uuid,
    recorder_user_ref_id uuid NOT NULL,
    recorder_team_id uuid,
    form_version_id uuid NOT NULL,
    status text DEFAULT 'recording'::text NOT NULL,
    interaction_at timestamp with time zone NOT NULL,
    interaction_mode_code text,
    visit_location text,
    duration_minutes integer,
    expectation_code text,
    follow_up_record text,
    next_action text,
    partner_name_snapshot text,
    lead_source_snapshot text,
    contact_category_snapshot text,
    contact_title_snapshot text,
    contact_name_snapshot text,
    audio_asset_id uuid,
    idempotency_fingerprint character(64),
    confirmed_by_user_ref_id uuid,
    confirmed_at timestamp with time zone,
    archived_at timestamp with time zone,
    attributes jsonb DEFAULT '{}'::jsonb NOT NULL,
    version_no integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    deleted_at timestamp with time zone,
    source_artifact_id uuid,
    is_first_visit boolean DEFAULT false NOT NULL,
    follow_up_score integer,
    import_meta jsonb DEFAULT '{}'::jsonb NOT NULL,
    quality_review jsonb,
    first_visit_profile jsonb,
    CONSTRAINT visit_duration_minutes_check CHECK (((duration_minutes IS NULL) OR ((duration_minutes >= 1) AND (duration_minutes <= 1440)))),
    CONSTRAINT visit_status_check CHECK ((status = ANY (ARRAY['recording'::text, 'transcribing'::text, 'pending_supplement'::text, 'pending_confirm'::text, 'confirmed'::text, 'archived'::text, 'withdrawn'::text]))),
    CONSTRAINT visit_version_no_check CHECK ((version_no > 0))
);


--
-- Name: TABLE visit; Type: COMMENT; Schema: activity; Owner: -
--

COMMENT ON TABLE activity.visit IS '现行：拜访。quality_review/first_visit_profile 为结构化列，不再塞 attributes';


--
-- Name: COLUMN visit.attributes; Type: COMMENT; Schema: activity; Owner: -
--

COMMENT ON COLUMN activity.visit.attributes IS '开放扩展袋';


--
-- Name: COLUMN visit.import_meta; Type: COMMENT; Schema: activity; Owner: -
--

COMMENT ON COLUMN activity.visit.import_meta IS '导入溯源';


--
-- Name: COLUMN visit.quality_review; Type: COMMENT; Schema: activity; Owner: -
--

COMMENT ON COLUMN activity.visit.quality_review IS '拜访质量复核（原 attributes.quality_review）';


--
-- Name: COLUMN visit.first_visit_profile; Type: COMMENT; Schema: activity; Owner: -
--

COMMENT ON COLUMN activity.visit.first_visit_profile IS '首访画像摘录（原 attributes.first_visit_profile）';


--
-- Name: customer; Type: TABLE; Schema: crm; Owner: -
--

CREATE TABLE crm.customer (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    external_customer_id text,
    customer_code text,
    name text NOT NULL,
    normalized_name text NOT NULL,
    industry_code text,
    customer_type_code text,
    source_code text,
    lifecycle_status text DEFAULT 'prospect'::text NOT NULL,
    level_code text,
    owner_user_ref_id uuid,
    owner_team_id uuid,
    primary_partner_name text,
    demand_summary text,
    data_source text DEFAULT 'manual'::text NOT NULL,
    created_by_user_ref_id uuid NOT NULL,
    attributes jsonb DEFAULT '{}'::jsonb NOT NULL,
    version_no integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    deleted_at timestamp with time zone,
    next_action text,
    operation_type text,
    cooperation_years numeric(8,1),
    main_business text,
    customer_budget text,
    import_meta jsonb DEFAULT '{}'::jsonb NOT NULL,
    data_kind text DEFAULT 'unclassified'::text NOT NULL,
    CONSTRAINT customer_data_kind_check CHECK ((data_kind = ANY (ARRAY['production'::text, 'demo'::text, 'test'::text, 'unclassified'::text]))),
    CONSTRAINT customer_lifecycle_status_check CHECK ((lifecycle_status = ANY (ARRAY['lead'::text, 'prospect'::text, 'active'::text, 'dormant'::text, 'won'::text, 'lost'::text, 'archived'::text]))),
    CONSTRAINT customer_version_no_check CHECK ((version_no > 0))
);


--
-- Name: TABLE customer; Type: COMMENT; Schema: crm; Owner: -
--

COMMENT ON TABLE crm.customer IS '现行：客户主档。稳定字段用列，导入溯源用 import_meta，attributes 仅开放扩展';


--
-- Name: COLUMN customer.attributes; Type: COMMENT; Schema: crm; Owner: -
--

COMMENT ON COLUMN crm.customer.attributes IS '开放扩展袋。新的一等字段先加列再迁出，不要把稳定业务字段长期堆在这里';


--
-- Name: COLUMN customer.next_action; Type: COMMENT; Schema: crm; Owner: -
--

COMMENT ON COLUMN crm.customer.next_action IS '下一步行动（原 attributes.next_action）';


--
-- Name: COLUMN customer.operation_type; Type: COMMENT; Schema: crm; Owner: -
--

COMMENT ON COLUMN crm.customer.operation_type IS '客户经营分类（原 attributes.operation_type）';


--
-- Name: COLUMN customer.import_meta; Type: COMMENT; Schema: crm; Owner: -
--

COMMENT ON COLUMN crm.customer.import_meta IS '导入/种子溯源，不参与业务规则';


--
-- Name: opportunity; Type: TABLE; Schema: crm; Owner: -
--

CREATE TABLE crm.opportunity (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    customer_id uuid NOT NULL,
    external_opportunity_id text,
    name text NOT NULL,
    stage_code text DEFAULT 'identified'::text NOT NULL,
    amount numeric(18,2),
    currency character(3) DEFAULT 'CNY'::bpchar NOT NULL,
    probability numeric(5,2),
    expected_close_date date,
    owner_user_ref_id uuid,
    owner_team_id uuid,
    status text DEFAULT 'open'::text NOT NULL,
    source_code text,
    created_by_user_ref_id uuid,
    attributes jsonb DEFAULT '{}'::jsonb NOT NULL,
    version_no integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    deleted_at timestamp with time zone,
    import_meta jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT ck_opportunity_probability_standard CHECK (((probability IS NULL) OR (probability = ANY (ARRAY[(10)::numeric, (30)::numeric, (50)::numeric, (70)::numeric, (90)::numeric, (100)::numeric])))),
    CONSTRAINT opportunity_probability_check CHECK (((probability IS NULL) OR ((probability >= (0)::numeric) AND (probability <= (100)::numeric)))),
    CONSTRAINT opportunity_status_check CHECK ((status = ANY (ARRAY['open'::text, 'won'::text, 'lost'::text, 'cancelled'::text]))),
    CONSTRAINT opportunity_version_no_check CHECK ((version_no > 0))
);


--
-- Name: TABLE opportunity; Type: COMMENT; Schema: crm; Owner: -
--

COMMENT ON TABLE crm.opportunity IS '现行：商机';


--
-- Name: user_ref; Type: TABLE; Schema: platform; Owner: -
--

CREATE TABLE platform.user_ref (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    external_user_id text NOT NULL,
    account_code text,
    display_name text NOT NULL,
    mobile_masked text,
    status text DEFAULT 'active'::text NOT NULL,
    attributes jsonb DEFAULT '{}'::jsonb NOT NULL,
    version_no integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    deleted_at timestamp with time zone,
    CONSTRAINT user_ref_status_check CHECK ((status = ANY (ARRAY['active'::text, 'inactive'::text]))),
    CONSTRAINT user_ref_version_no_check CHECK ((version_no > 0))
);


--
-- Name: TABLE user_ref; Type: COMMENT; Schema: platform; Owner: -
--

COMMENT ON TABLE platform.user_ref IS '现行：外部用户引用，不含密码';


--
-- Name: v_visit_timeline; Type: VIEW; Schema: activity; Owner: -
--

CREATE VIEW activity.v_visit_timeline WITH (security_invoker='true') AS
 SELECT v.id,
    v.workspace_id,
    v.customer_id,
    c.name AS customer_name,
    v.opportunity_id,
    o.name AS opportunity_name,
    v.recorder_user_ref_id,
    u.display_name AS recorder_name,
    v.status,
    v.interaction_at,
    v.interaction_mode_code,
    v.visit_location,
    v.duration_minutes,
    v.expectation_code,
    v.follow_up_record,
    v.next_action,
    v.confirmed_at
   FROM (((activity.visit v
     JOIN crm.customer c ON ((c.id = v.customer_id)))
     JOIN platform.user_ref u ON ((u.id = v.recorder_user_ref_id)))
     LEFT JOIN crm.opportunity o ON ((o.id = v.opportunity_id)))
  WHERE (v.deleted_at IS NULL);


--
-- Name: visit_contact; Type: TABLE; Schema: activity; Owner: -
--

CREATE TABLE activity.visit_contact (
    visit_id uuid NOT NULL,
    contact_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    participation_role text DEFAULT 'attendee'::text NOT NULL,
    attributes jsonb DEFAULT '{}'::jsonb NOT NULL
);


--
-- Name: TABLE visit_contact; Type: COMMENT; Schema: activity; Owner: -
--

COMMENT ON TABLE activity.visit_contact IS '现行：拜访参与联系人';


--
-- Name: visit_field_value; Type: TABLE; Schema: activity; Owner: -
--

CREATE TABLE activity.visit_field_value (
    visit_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    field_definition_id uuid NOT NULL,
    value_json jsonb,
    value_text text,
    source_type text NOT NULL,
    confidence numeric(5,4),
    is_confirmed boolean DEFAULT false NOT NULL,
    evidence jsonb DEFAULT '[]'::jsonb NOT NULL,
    edited_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT visit_field_value_confidence_check CHECK (((confidence IS NULL) OR ((confidence >= (0)::numeric) AND (confidence <= (1)::numeric)))),
    CONSTRAINT visit_field_value_source_type_check CHECK ((source_type = ANY (ARRAY['agent'::text, 'user'::text, 'system'::text, 'import'::text])))
);


--
-- Name: TABLE visit_field_value; Type: COMMENT; Schema: activity; Owner: -
--

COMMENT ON TABLE activity.visit_field_value IS '现行：表单版本字段值，扩展新拜访字段走这里';


--
-- Name: artifact; Type: TABLE; Schema: agent; Owner: -
--

CREATE TABLE agent.artifact (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    conversation_id uuid,
    run_id uuid,
    artifact_type text NOT NULL,
    schema_code text NOT NULL,
    schema_version integer NOT NULL,
    status text DEFAULT 'draft'::text NOT NULL,
    subject_type text,
    subject_id uuid,
    payload jsonb NOT NULL,
    evidence jsonb DEFAULT '[]'::jsonb NOT NULL,
    confidence numeric(5,4),
    rule_set_id uuid,
    model_ref text,
    supersedes_artifact_id uuid,
    created_by_user_ref_id uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT artifact_confidence_check CHECK (((confidence IS NULL) OR ((confidence >= (0)::numeric) AND (confidence <= (1)::numeric)))),
    CONSTRAINT artifact_schema_version_check CHECK ((schema_version > 0)),
    CONSTRAINT artifact_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'pending_supplement'::text, 'pending_confirm'::text, 'confirmed'::text, 'applied'::text, 'rejected'::text, 'superseded'::text])))
);


--
-- Name: TABLE artifact; Type: COMMENT; Schema: agent; Owner: -
--

COMMENT ON TABLE agent.artifact IS '现行：运行产物';


--
-- Name: confirmation; Type: TABLE; Schema: agent; Owner: -
--

CREATE TABLE agent.confirmation (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    artifact_id uuid NOT NULL,
    confirmer_user_ref_id uuid NOT NULL,
    decision text NOT NULL,
    original_payload_hash character(64) NOT NULL,
    edited_payload jsonb,
    comment text,
    confirmed_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT confirmation_decision_check CHECK ((decision = ANY (ARRAY['confirmed'::text, 'edited_and_confirmed'::text, 'rejected'::text, 'needs_supplement'::text])))
);


--
-- Name: TABLE confirmation; Type: COMMENT; Schema: agent; Owner: -
--

COMMENT ON TABLE agent.confirmation IS '预留：人工确认件';


--
-- Name: conversation; Type: TABLE; Schema: agent; Owner: -
--

CREATE TABLE agent.conversation (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    user_ref_id uuid NOT NULL,
    role_code text NOT NULL,
    data_scope_snapshot jsonb NOT NULL,
    channel_code text DEFAULT 'wechat_mini_program'::text NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    context jsonb DEFAULT '{}'::jsonb NOT NULL,
    started_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    closed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT conversation_role_code_check CHECK ((role_code = ANY (ARRAY['sales'::text, 'supervisor'::text, 'manager'::text]))),
    CONSTRAINT conversation_status_check CHECK ((status = ANY (ARRAY['active'::text, 'closed'::text, 'expired'::text])))
);


--
-- Name: TABLE conversation; Type: COMMENT; Schema: agent; Owner: -
--

COMMENT ON TABLE agent.conversation IS '现行：Agent 会话';


--
-- Name: message; Type: TABLE; Schema: agent; Owner: -
--

CREATE TABLE agent.message (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    conversation_id uuid NOT NULL,
    sender_type text NOT NULL,
    content_type text NOT NULL,
    text_content text,
    structured_content jsonb,
    file_asset_id uuid,
    client_message_id text,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT message_content_type_check CHECK ((content_type = ANY (ARRAY['text'::text, 'audio'::text, 'card'::text, 'structured'::text, 'error'::text]))),
    CONSTRAINT message_sender_type_check CHECK ((sender_type = ANY (ARRAY['user'::text, 'assistant'::text, 'system'::text, 'tool'::text])))
);


--
-- Name: TABLE message; Type: COMMENT; Schema: agent; Owner: -
--

COMMENT ON TABLE agent.message IS '现行：Agent 消息';


--
-- Name: model_invocation; Type: TABLE; Schema: agent; Owner: -
--

CREATE TABLE agent.model_invocation (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    run_id uuid NOT NULL,
    run_step_id uuid,
    provider_code text NOT NULL,
    model_id text NOT NULL,
    endpoint_code text NOT NULL,
    request_hash character(64) NOT NULL,
    request_snapshot jsonb DEFAULT '{}'::jsonb NOT NULL,
    response_snapshot jsonb DEFAULT '{}'::jsonb NOT NULL,
    status text NOT NULL,
    attempt_no integer DEFAULT 1 NOT NULL,
    http_status integer,
    upstream_trace_id text,
    input_tokens integer,
    output_tokens integer,
    usage_detail jsonb DEFAULT '{}'::jsonb NOT NULL,
    latency_ms integer,
    error_code text,
    error_detail text,
    started_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    completed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT model_invocation_attempt_no_check CHECK ((attempt_no > 0)),
    CONSTRAINT model_invocation_latency_ms_check CHECK (((latency_ms IS NULL) OR (latency_ms >= 0))),
    CONSTRAINT model_invocation_status_check CHECK ((status = ANY (ARRAY['running'::text, 'succeeded'::text, 'failed'::text, 'cancelled'::text])))
);

ALTER TABLE ONLY agent.model_invocation FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE model_invocation; Type: COMMENT; Schema: agent; Owner: -
--

COMMENT ON TABLE agent.model_invocation IS '现行：模型调用明细';


--
-- Name: run; Type: TABLE; Schema: agent; Owner: -
--

CREATE TABLE agent.run (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    conversation_id uuid NOT NULL,
    trigger_message_id uuid,
    orchestrator_definition_id uuid,
    intent_code text,
    status text DEFAULT 'queued'::text NOT NULL,
    identity_context jsonb NOT NULL,
    business_context jsonb DEFAULT '{}'::jsonb NOT NULL,
    model_ref text,
    prompt_version text,
    retry_count integer DEFAULT 0 NOT NULL,
    token_usage jsonb DEFAULT '{}'::jsonb NOT NULL,
    cost_amount numeric(18,6),
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    error_code text,
    error_detail text,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT run_retry_count_check CHECK ((retry_count >= 0)),
    CONSTRAINT run_status_check CHECK ((status = ANY (ARRAY['queued'::text, 'running'::text, 'waiting_human'::text, 'succeeded'::text, 'failed'::text, 'cancelled'::text])))
);


--
-- Name: TABLE run; Type: COMMENT; Schema: agent; Owner: -
--

COMMENT ON TABLE agent.run IS '现行：一次 Agent 运行';


--
-- Name: run_step; Type: TABLE; Schema: agent; Owner: -
--

CREATE TABLE agent.run_step (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    run_id uuid NOT NULL,
    sequence_no integer NOT NULL,
    agent_definition_id uuid,
    step_type text NOT NULL,
    status text NOT NULL,
    input_snapshot jsonb DEFAULT '{}'::jsonb NOT NULL,
    output_snapshot jsonb DEFAULT '{}'::jsonb NOT NULL,
    tool_calls jsonb DEFAULT '[]'::jsonb NOT NULL,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    error_detail text,
    CONSTRAINT run_step_sequence_no_check CHECK ((sequence_no > 0)),
    CONSTRAINT run_step_status_check CHECK ((status = ANY (ARRAY['queued'::text, 'running'::text, 'waiting_human'::text, 'succeeded'::text, 'failed'::text, 'skipped'::text])))
);


--
-- Name: TABLE run_step; Type: COMMENT; Schema: agent; Owner: -
--

COMMENT ON TABLE agent.run_step IS '预留：运行步骤拆解';


--
-- Name: tool_invocation; Type: TABLE; Schema: agent; Owner: -
--

CREATE TABLE agent.tool_invocation (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    run_id uuid NOT NULL,
    run_step_id uuid,
    tool_code text NOT NULL,
    input_snapshot jsonb DEFAULT '{}'::jsonb NOT NULL,
    output_snapshot jsonb DEFAULT '{}'::jsonb NOT NULL,
    status text NOT NULL,
    idempotency_key text,
    latency_ms integer,
    error_code text,
    error_detail text,
    started_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    completed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT tool_invocation_latency_ms_check CHECK (((latency_ms IS NULL) OR (latency_ms >= 0))),
    CONSTRAINT tool_invocation_status_check CHECK ((status = ANY (ARRAY['running'::text, 'succeeded'::text, 'failed'::text, 'cancelled'::text])))
);

ALTER TABLE ONLY agent.tool_invocation FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE tool_invocation; Type: COMMENT; Schema: agent; Owner: -
--

COMMENT ON TABLE agent.tool_invocation IS '预留：工具调用明细';


--
-- Name: agent_definition; Type: TABLE; Schema: config; Owner: -
--

CREATE TABLE config.agent_definition (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid,
    agent_code text NOT NULL,
    name text NOT NULL,
    version_no integer NOT NULL,
    status text NOT NULL,
    prompt_ref text,
    tool_policy jsonb DEFAULT '{}'::jsonb NOT NULL,
    output_schema jsonb DEFAULT '{}'::jsonb NOT NULL,
    model_policy jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT agent_definition_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'active'::text, 'retired'::text]))),
    CONSTRAINT agent_definition_version_no_check CHECK ((version_no > 0))
);


--
-- Name: TABLE agent_definition; Type: COMMENT; Schema: config; Owner: -
--

COMMENT ON TABLE config.agent_definition IS '现行：Agent 输出契约';


--
-- Name: agent_runtime_config; Type: TABLE; Schema: config; Owner: -
--

CREATE TABLE config.agent_runtime_config (
    workspace_id uuid NOT NULL,
    provider_base_url text NOT NULL,
    api_key_ciphertext bytea,
    llm_model text NOT NULL,
    asr_model text NOT NULL,
    tts_model text NOT NULL,
    prompt_overrides jsonb DEFAULT '{}'::jsonb NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    updated_by_user_ref_id uuid,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL
);

ALTER TABLE ONLY config.agent_runtime_config FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE agent_runtime_config; Type: COMMENT; Schema: config; Owner: -
--

COMMENT ON TABLE config.agent_runtime_config IS '现行：模型网关与 Prompt 覆盖';


--
-- Name: agent_runtime_release; Type: TABLE; Schema: config; Owner: -
--

CREATE TABLE config.agent_runtime_release (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    version_no integer NOT NULL,
    config_snapshot jsonb NOT NULL,
    created_by_user_ref_id uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT agent_runtime_release_version_no_check CHECK ((version_no > 0))
);

ALTER TABLE ONLY config.agent_runtime_release FORCE ROW LEVEL SECURITY;


--
-- Name: dictionary; Type: TABLE; Schema: config; Owner: -
--

CREATE TABLE config.dictionary (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid,
    code text NOT NULL,
    name text NOT NULL,
    version_no integer DEFAULT 1 NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    attributes jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT dictionary_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'active'::text, 'retired'::text]))),
    CONSTRAINT dictionary_version_no_check CHECK ((version_no > 0))
);


--
-- Name: TABLE dictionary; Type: COMMENT; Schema: config; Owner: -
--

COMMENT ON TABLE config.dictionary IS '现行：枚举字典';


--
-- Name: dictionary_item; Type: TABLE; Schema: config; Owner: -
--

CREATE TABLE config.dictionary_item (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    dictionary_id uuid NOT NULL,
    item_code text NOT NULL,
    item_label text NOT NULL,
    sort_order integer DEFAULT 0 NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    valid_from timestamp with time zone DEFAULT '-infinity'::timestamp with time zone NOT NULL,
    valid_to timestamp with time zone DEFAULT 'infinity'::timestamp with time zone NOT NULL,
    attributes jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT dictionary_item_check CHECK ((valid_to > valid_from))
);


--
-- Name: TABLE dictionary_item; Type: COMMENT; Schema: config; Owner: -
--

COMMENT ON TABLE config.dictionary_item IS '现行：字典项';


--
-- Name: field_definition; Type: TABLE; Schema: config; Owner: -
--

CREATE TABLE config.field_definition (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid,
    object_type text NOT NULL,
    field_key text NOT NULL,
    label text NOT NULL,
    data_type text NOT NULL,
    dictionary_code text,
    validation jsonb DEFAULT '{}'::jsonb NOT NULL,
    sensitivity text DEFAULT 'internal'::text NOT NULL,
    is_system boolean DEFAULT false NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    attributes jsonb DEFAULT '{}'::jsonb NOT NULL,
    version_no integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT field_definition_data_type_check CHECK ((data_type = ANY (ARRAY['text'::text, 'long_text'::text, 'integer'::text, 'decimal'::text, 'boolean'::text, 'date'::text, 'datetime'::text, 'select'::text, 'multi_select'::text, 'reference'::text, 'json'::text]))),
    CONSTRAINT field_definition_sensitivity_check CHECK ((sensitivity = ANY (ARRAY['public'::text, 'internal'::text, 'sensitive'::text, 'restricted'::text]))),
    CONSTRAINT field_definition_version_no_check CHECK ((version_no > 0))
);


--
-- Name: TABLE field_definition; Type: COMMENT; Schema: config; Owner: -
--

COMMENT ON TABLE config.field_definition IS '现行：拜访等对象字段元数据';


--
-- Name: form_definition; Type: TABLE; Schema: config; Owner: -
--

CREATE TABLE config.form_definition (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid,
    form_code text NOT NULL,
    name text NOT NULL,
    object_type text NOT NULL,
    description text,
    attributes jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL
);


--
-- Name: TABLE form_definition; Type: COMMENT; Schema: config; Owner: -
--

COMMENT ON TABLE config.form_definition IS '现行：表单定义';


--
-- Name: form_version; Type: TABLE; Schema: config; Owner: -
--

CREATE TABLE config.form_version (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    form_definition_id uuid NOT NULL,
    version_no integer NOT NULL,
    status text NOT NULL,
    effective_from timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    effective_to timestamp with time zone DEFAULT 'infinity'::timestamp with time zone NOT NULL,
    schema_snapshot jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_by_user_ref_id uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT form_version_check CHECK ((effective_to > effective_from)),
    CONSTRAINT form_version_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'active'::text, 'retired'::text]))),
    CONSTRAINT form_version_version_no_check CHECK ((version_no > 0))
);


--
-- Name: TABLE form_version; Type: COMMENT; Schema: config; Owner: -
--

COMMENT ON TABLE config.form_version IS '现行：表单版本';


--
-- Name: form_version_field; Type: TABLE; Schema: config; Owner: -
--

CREATE TABLE config.form_version_field (
    form_version_id uuid NOT NULL,
    field_definition_id uuid NOT NULL,
    display_order integer NOT NULL,
    is_required boolean DEFAULT false NOT NULL,
    is_readonly boolean DEFAULT false NOT NULL,
    default_value jsonb,
    ui_config jsonb DEFAULT '{}'::jsonb NOT NULL
);


--
-- Name: TABLE form_version_field; Type: COMMENT; Schema: config; Owner: -
--

COMMENT ON TABLE config.form_version_field IS '现行：表单版本字段编排';


--
-- Name: metric_definition; Type: TABLE; Schema: config; Owner: -
--

CREATE TABLE config.metric_definition (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid,
    metric_code text NOT NULL,
    version_no integer NOT NULL,
    status text DEFAULT 'draft'::text NOT NULL,
    display_name text NOT NULL,
    description text NOT NULL,
    unit_code text,
    query_template text NOT NULL,
    allowed_dimensions jsonb DEFAULT '[]'::jsonb NOT NULL,
    allowed_filters jsonb DEFAULT '[]'::jsonb NOT NULL,
    output_schema jsonb NOT NULL,
    data_freshness_seconds integer DEFAULT 300 NOT NULL,
    effective_from timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    effective_to timestamp with time zone DEFAULT 'infinity'::timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT metric_definition_data_freshness_seconds_check CHECK ((data_freshness_seconds >= 0)),
    CONSTRAINT metric_definition_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'active'::text, 'retired'::text]))),
    CONSTRAINT metric_definition_version_no_check CHECK ((version_no > 0))
);


--
-- Name: TABLE metric_definition; Type: COMMENT; Schema: config; Owner: -
--

COMMENT ON TABLE config.metric_definition IS '预留：ChatBI 指标目录';


--
-- Name: prompt_template; Type: TABLE; Schema: config; Owner: -
--

CREATE TABLE config.prompt_template (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid,
    agent_code text NOT NULL,
    prompt_code text NOT NULL,
    version_no integer NOT NULL,
    status text DEFAULT 'draft'::text NOT NULL,
    template_text text NOT NULL,
    output_schema jsonb NOT NULL,
    model_policy jsonb DEFAULT '{}'::jsonb NOT NULL,
    checksum_sha256 character(64) NOT NULL,
    effective_from timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    effective_to timestamp with time zone DEFAULT 'infinity'::timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT prompt_template_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'active'::text, 'retired'::text]))),
    CONSTRAINT prompt_template_version_no_check CHECK ((version_no > 0))
);


--
-- Name: TABLE prompt_template; Type: COMMENT; Schema: config; Owner: -
--

COMMENT ON TABLE config.prompt_template IS '预留：Prompt 版本表；当前覆盖写在 agent_runtime_config';


--
-- Name: rule_set; Type: TABLE; Schema: config; Owner: -
--

CREATE TABLE config.rule_set (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid,
    rule_code text NOT NULL,
    name text NOT NULL,
    rule_type text NOT NULL,
    version_no integer NOT NULL,
    status text NOT NULL,
    effective_from timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    effective_to timestamp with time zone DEFAULT 'infinity'::timestamp with time zone NOT NULL,
    definition jsonb NOT NULL,
    created_by_user_ref_id uuid,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT rule_set_check CHECK ((effective_to > effective_from)),
    CONSTRAINT rule_set_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'active'::text, 'retired'::text]))),
    CONSTRAINT rule_set_version_no_check CHECK ((version_no > 0))
);


--
-- Name: TABLE rule_set; Type: COMMENT; Schema: config; Owner: -
--

COMMENT ON TABLE config.rule_set IS '现行：四象限等规则版本';


--
-- Name: sales_competency_framework; Type: TABLE; Schema: config; Owner: -
--

CREATE TABLE config.sales_competency_framework (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid,
    version_no integer NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    display_name text NOT NULL,
    dimensions jsonb NOT NULL,
    scoring_rules jsonb DEFAULT '{}'::jsonb NOT NULL,
    effective_from timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    effective_to timestamp with time zone DEFAULT 'infinity'::timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT sales_competency_framework_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'active'::text, 'retired'::text]))),
    CONSTRAINT sales_competency_framework_version_no_check CHECK ((version_no > 0))
);


--
-- Name: TABLE sales_competency_framework; Type: COMMENT; Schema: config; Owner: -
--

COMMENT ON TABLE config.sales_competency_framework IS '现行：销售能力模型';


--
-- Name: contact; Type: TABLE; Schema: crm; Owner: -
--

CREATE TABLE crm.contact (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    customer_id uuid NOT NULL,
    external_contact_id text,
    name text NOT NULL,
    title text,
    department text,
    contact_category_code text,
    relationship_role_code text,
    mobile_encrypted bytea,
    email_encrypted bytea,
    is_primary boolean DEFAULT false NOT NULL,
    created_by_user_ref_id uuid,
    attributes jsonb DEFAULT '{}'::jsonb NOT NULL,
    version_no integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    deleted_at timestamp with time zone,
    import_meta jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT contact_version_no_check CHECK ((version_no > 0))
);


--
-- Name: TABLE contact; Type: COMMENT; Schema: crm; Owner: -
--

COMMENT ON TABLE crm.contact IS '现行：联系人';


--
-- Name: customer_assignment; Type: TABLE; Schema: crm; Owner: -
--

CREATE TABLE crm.customer_assignment (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    customer_id uuid NOT NULL,
    assigned_user_ref_id uuid NOT NULL,
    assigned_team_id uuid NOT NULL,
    assigned_by_user_ref_id uuid NOT NULL,
    assignment_type text DEFAULT 'owner'::text NOT NULL,
    status text DEFAULT 'assigned'::text NOT NULL,
    first_action text,
    due_at timestamp with time zone,
    assigned_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    accepted_at timestamp with time zone,
    revoked_at timestamp with time zone,
    attributes jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT customer_assignment_assignment_type_check CHECK ((assignment_type = ANY (ARRAY['owner'::text, 'collaborator'::text, 'handoff'::text]))),
    CONSTRAINT customer_assignment_status_check CHECK ((status = ANY (ARRAY['assigned'::text, 'accepted'::text, 'revoked'::text, 'completed'::text])))
);


--
-- Name: TABLE customer_assignment; Type: COMMENT; Schema: crm; Owner: -
--

COMMENT ON TABLE crm.customer_assignment IS '现行：客户下发历史';


--
-- Name: customer_duplicate_candidate; Type: TABLE; Schema: crm; Owner: -
--

CREATE TABLE crm.customer_duplicate_candidate (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    customer_id uuid NOT NULL,
    candidate_customer_id uuid NOT NULL,
    match_score numeric(5,4) NOT NULL,
    reasons jsonb DEFAULT '[]'::jsonb NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    reviewed_by_user_ref_id uuid,
    reviewed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT customer_duplicate_candidate_check CHECK ((customer_id <> candidate_customer_id)),
    CONSTRAINT customer_duplicate_candidate_match_score_check CHECK (((match_score >= (0)::numeric) AND (match_score <= (1)::numeric))),
    CONSTRAINT customer_duplicate_candidate_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'confirmed_duplicate'::text, 'not_duplicate'::text, 'merged'::text])))
);


--
-- Name: TABLE customer_duplicate_candidate; Type: COMMENT; Schema: crm; Owner: -
--

COMMENT ON TABLE crm.customer_duplicate_candidate IS '预留：客户去重候选';


--
-- Name: customer_product; Type: TABLE; Schema: crm; Owner: -
--

CREATE TABLE crm.customer_product (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    customer_id uuid NOT NULL,
    product_id uuid NOT NULL,
    relationship_status text NOT NULL,
    opportunity_id uuid,
    started_at timestamp with time zone,
    ended_at timestamp with time zone,
    attributes jsonb DEFAULT '{}'::jsonb NOT NULL,
    version_no integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    deleted_at timestamp with time zone,
    CONSTRAINT customer_product_relationship_status_check CHECK ((relationship_status = ANY (ARRAY['recommended'::text, 'evaluating'::text, 'purchased'::text, 'renewal_due'::text, 'rejected'::text, 'not_fit'::text]))),
    CONSTRAINT customer_product_version_no_check CHECK ((version_no > 0))
);


--
-- Name: TABLE customer_product; Type: COMMENT; Schema: crm; Owner: -
--

COMMENT ON TABLE crm.customer_product IS '预留/现行结构：客户-产品关系，工作台已查询';


--
-- Name: opportunity_participant; Type: TABLE; Schema: crm; Owner: -
--

CREATE TABLE crm.opportunity_participant (
    opportunity_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    user_ref_id uuid NOT NULL,
    participant_role text DEFAULT 'member'::text NOT NULL,
    valid_from timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    valid_to timestamp with time zone DEFAULT 'infinity'::timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT opportunity_participant_check CHECK ((valid_to > valid_from))
);


--
-- Name: TABLE opportunity_participant; Type: COMMENT; Schema: crm; Owner: -
--

COMMENT ON TABLE crm.opportunity_participant IS '预留：商机多人参与；has_customer_access 已引用';


--
-- Name: product; Type: TABLE; Schema: crm; Owner: -
--

CREATE TABLE crm.product (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid,
    product_code text NOT NULL,
    name text NOT NULL,
    category_code text,
    status text DEFAULT 'active'::text NOT NULL,
    attributes jsonb DEFAULT '{}'::jsonb NOT NULL,
    version_no integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT product_status_check CHECK ((status = ANY (ARRAY['active'::text, 'inactive'::text]))),
    CONSTRAINT product_version_no_check CHECK ((version_no > 0))
);


--
-- Name: TABLE product; Type: COMMENT; Schema: crm; Owner: -
--

COMMENT ON TABLE crm.product IS '预留/现行结构：产品目录，工作台已查询';


--
-- Name: quadrant_score; Type: TABLE; Schema: insight; Owner: -
--

CREATE TABLE insight.quadrant_score (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    customer_id uuid NOT NULL,
    potential_score numeric(6,2) NOT NULL,
    relationship_score numeric(6,2) NOT NULL,
    quadrant_code text NOT NULL,
    rule_set_id uuid NOT NULL,
    input_snapshot jsonb NOT NULL,
    evidence jsonb DEFAULT '[]'::jsonb NOT NULL,
    calculated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    valid_to timestamp with time zone DEFAULT 'infinity'::timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT quadrant_score_potential_score_check CHECK (((potential_score >= (0)::numeric) AND (potential_score <= (100)::numeric))),
    CONSTRAINT quadrant_score_quadrant_code_check CHECK ((quadrant_code = ANY (ARRAY['main_attack'::text, 'customer_asset'::text, 'order_driven'::text, 'customer_resource'::text]))),
    CONSTRAINT quadrant_score_relationship_score_check CHECK (((relationship_score >= (0)::numeric) AND (relationship_score <= (100)::numeric)))
);


--
-- Name: TABLE quadrant_score; Type: COMMENT; Schema: insight; Owner: -
--

COMMENT ON TABLE insight.quadrant_score IS '现行：作战地图评分';


--
-- Name: v_customer_current_quadrant; Type: VIEW; Schema: crm; Owner: -
--

CREATE VIEW crm.v_customer_current_quadrant WITH (security_invoker='true') AS
 SELECT c.id,
    c.workspace_id,
    c.name,
    c.industry_code,
    c.customer_type_code,
    c.lifecycle_status,
    c.level_code,
    c.owner_user_ref_id,
    c.owner_team_id,
    qs.potential_score,
    qs.relationship_score,
    qs.quadrant_code,
    qs.calculated_at AS quadrant_calculated_at
   FROM (crm.customer c
     LEFT JOIN insight.quadrant_score qs ON (((qs.workspace_id = c.workspace_id) AND (qs.customer_id = c.id) AND (qs.valid_to = 'infinity'::timestamp with time zone))))
  WHERE (c.deleted_at IS NULL);


--
-- Name: recommendation; Type: TABLE; Schema: insight; Owner: -
--

CREATE TABLE insight.recommendation (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    customer_id uuid NOT NULL,
    opportunity_id uuid,
    product_id uuid,
    recommendation_type text NOT NULL,
    title text NOT NULL,
    rationale text NOT NULL,
    suggested_action text,
    status text DEFAULT 'pending'::text NOT NULL,
    rule_set_id uuid,
    model_ref text,
    input_snapshot jsonb NOT NULL,
    evidence jsonb DEFAULT '[]'::jsonb NOT NULL,
    confidence numeric(5,4),
    generated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    expires_at timestamp with time zone,
    acted_by_user_ref_id uuid,
    acted_at timestamp with time zone,
    rejection_reason text,
    attributes jsonb DEFAULT '{}'::jsonb NOT NULL,
    version_no integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    deleted_at timestamp with time zone,
    CONSTRAINT recommendation_confidence_check CHECK (((confidence IS NULL) OR ((confidence >= (0)::numeric) AND (confidence <= (1)::numeric)))),
    CONSTRAINT recommendation_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'viewed'::text, 'adopted'::text, 'followed'::text, 'converted'::text, 'won'::text, 'rejected'::text]))),
    CONSTRAINT recommendation_version_no_check CHECK ((version_no > 0))
);


--
-- Name: TABLE recommendation; Type: COMMENT; Schema: insight; Owner: -
--

COMMENT ON TABLE insight.recommendation IS '预留：交叉销售/管理建议';


--
-- Name: report; Type: TABLE; Schema: insight; Owner: -
--

CREATE TABLE insight.report (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    report_type text NOT NULL,
    title text NOT NULL,
    scope_type text NOT NULL,
    scope_ref_id uuid,
    period_start date NOT NULL,
    period_end date NOT NULL,
    generated_by_user_ref_id uuid,
    rule_set_id uuid,
    data_as_of timestamp with time zone NOT NULL,
    content jsonb NOT NULL,
    source_snapshot jsonb DEFAULT '{}'::jsonb NOT NULL,
    status text DEFAULT 'generated'::text NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT report_check CHECK ((period_end >= period_start)),
    CONSTRAINT report_scope_type_check CHECK ((scope_type = ANY (ARRAY['self'::text, 'team'::text, 'workspace'::text]))),
    CONSTRAINT report_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'generated'::text, 'shared'::text, 'archived'::text])))
);


--
-- Name: TABLE report; Type: COMMENT; Schema: insight; Owner: -
--

COMMENT ON TABLE insight.report IS '预留：报告中心';


--
-- Name: risk; Type: TABLE; Schema: insight; Owner: -
--

CREATE TABLE insight.risk (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    customer_id uuid,
    opportunity_id uuid,
    source_visit_id uuid,
    risk_type_code text NOT NULL,
    title text NOT NULL,
    description text,
    severity_code text NOT NULL,
    status text DEFAULT 'new'::text NOT NULL,
    owner_user_ref_id uuid,
    owner_team_id uuid,
    rule_set_id uuid,
    model_ref text,
    input_snapshot jsonb DEFAULT '{}'::jsonb NOT NULL,
    evidence jsonb DEFAULT '[]'::jsonb NOT NULL,
    opened_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    due_at timestamp with time zone,
    resolved_at timestamp with time zone,
    resolution_note text,
    attributes jsonb DEFAULT '{}'::jsonb NOT NULL,
    version_no integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    deleted_at timestamp with time zone,
    import_meta jsonb DEFAULT '{}'::jsonb NOT NULL,
    source_code text,
    suggested_action text,
    agent_key text,
    source_run_id uuid,
    CONSTRAINT risk_check CHECK (((customer_id IS NOT NULL) OR (opportunity_id IS NOT NULL))),
    CONSTRAINT risk_severity_code_check CHECK ((severity_code = ANY (ARRAY['low'::text, 'medium'::text, 'high'::text, 'critical'::text]))),
    CONSTRAINT risk_status_check CHECK ((status = ANY (ARRAY['new'::text, 'pending'::text, 'in_progress'::text, 'resolved'::text, 'accepted'::text, 'escalated'::text]))),
    CONSTRAINT risk_version_no_check CHECK ((version_no > 0))
);


--
-- Name: TABLE risk; Type: COMMENT; Schema: insight; Owner: -
--

COMMENT ON TABLE insight.risk IS '现行：风险。建议动作与来源已提升为列';


--
-- Name: COLUMN risk.attributes; Type: COMMENT; Schema: insight; Owner: -
--

COMMENT ON COLUMN insight.risk.attributes IS '开放扩展袋';


--
-- Name: COLUMN risk.source_code; Type: COMMENT; Schema: insight; Owner: -
--

COMMENT ON COLUMN insight.risk.source_code IS '风险来源：personal_risk_agent / imported_visit_analysis 等';


--
-- Name: COLUMN risk.suggested_action; Type: COMMENT; Schema: insight; Owner: -
--

COMMENT ON COLUMN insight.risk.suggested_action IS '建议动作（原 attributes.suggested_action）';


--
-- Name: risk_event; Type: TABLE; Schema: insight; Owner: -
--

CREATE TABLE insight.risk_event (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    risk_id uuid NOT NULL,
    event_type text NOT NULL,
    from_status text,
    to_status text,
    actor_user_ref_id uuid,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    occurred_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL
);


--
-- Name: TABLE risk_event; Type: COMMENT; Schema: insight; Owner: -
--

COMMENT ON TABLE insight.risk_event IS '现行：风险处置事件';


--
-- Name: sales_competency_review; Type: TABLE; Schema: insight; Owner: -
--

CREATE TABLE insight.sales_competency_review (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    subject_user_ref_id uuid NOT NULL,
    review_date date NOT NULL,
    framework_version integer NOT NULL,
    status text DEFAULT 'queued'::text NOT NULL,
    overall_score numeric(5,2),
    dimension_scores jsonb DEFAULT '{}'::jsonb NOT NULL,
    summary text,
    strengths jsonb DEFAULT '[]'::jsonb NOT NULL,
    improvements jsonb DEFAULT '[]'::jsonb NOT NULL,
    evidence jsonb DEFAULT '{}'::jsonb NOT NULL,
    input_snapshot jsonb DEFAULT '{}'::jsonb NOT NULL,
    model_id text,
    reviewed_at timestamp with time zone,
    error_code text,
    error_detail text,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT sales_competency_review_overall_score_check CHECK (((overall_score >= (0)::numeric) AND (overall_score <= (100)::numeric))),
    CONSTRAINT sales_competency_review_status_check CHECK ((status = ANY (ARRAY['queued'::text, 'running'::text, 'succeeded'::text, 'failed'::text])))
);

ALTER TABLE ONLY insight.sales_competency_review FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE sales_competency_review; Type: COMMENT; Schema: insight; Owner: -
--

COMMENT ON TABLE insight.sales_competency_review IS '现行：销售能力复盘';


--
-- Name: v_open_risk_summary; Type: VIEW; Schema: insight; Owner: -
--

CREATE VIEW insight.v_open_risk_summary WITH (security_invoker='true') AS
 SELECT workspace_id,
    owner_team_id,
    owner_user_ref_id,
    severity_code,
    count(*) AS risk_count,
    min(opened_at) AS oldest_opened_at
   FROM insight.risk r
  WHERE ((status = ANY (ARRAY['new'::text, 'pending'::text, 'in_progress'::text, 'escalated'::text])) AND (deleted_at IS NULL))
  GROUP BY workspace_id, owner_team_id, owner_user_ref_id, severity_code;


--
-- Name: audit_log; Type: TABLE; Schema: ops; Owner: -
--

CREATE TABLE ops.audit_log (
    id bigint NOT NULL,
    workspace_id uuid,
    actor_user_ref_id uuid,
    actor_role_code text,
    action_code text NOT NULL,
    object_type text,
    object_id uuid,
    request_id uuid,
    client_ip inet,
    user_agent text,
    before_snapshot jsonb,
    after_snapshot jsonb,
    result_code text DEFAULT 'success'::text NOT NULL,
    sensitivity text DEFAULT 'internal'::text NOT NULL,
    occurred_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL
);


--
-- Name: TABLE audit_log; Type: COMMENT; Schema: ops; Owner: -
--

COMMENT ON TABLE ops.audit_log IS '预留：审计日志';


--
-- Name: audit_log_id_seq; Type: SEQUENCE; Schema: ops; Owner: -
--

ALTER TABLE ops.audit_log ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME ops.audit_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: file_asset; Type: TABLE; Schema: ops; Owner: -
--

CREATE TABLE ops.file_asset (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    storage_provider text NOT NULL,
    object_uri text NOT NULL,
    media_type text NOT NULL,
    size_bytes bigint,
    sha256 character(64),
    encryption_key_ref text,
    retention_until timestamp with time zone,
    consent_recorded_at timestamp with time zone,
    uploaded_by_user_ref_id uuid,
    attributes jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    deleted_at timestamp with time zone,
    CONSTRAINT file_asset_size_bytes_check CHECK (((size_bytes IS NULL) OR (size_bytes >= 0)))
);


--
-- Name: TABLE file_asset; Type: COMMENT; Schema: ops; Owner: -
--

COMMENT ON TABLE ops.file_asset IS '预留：对象存储引用；visit.audio_asset_id 已外键指向';


--
-- Name: idempotency_key; Type: TABLE; Schema: ops; Owner: -
--

CREATE TABLE ops.idempotency_key (
    workspace_id uuid NOT NULL,
    key text NOT NULL,
    operation_code text NOT NULL,
    request_hash character(64) NOT NULL,
    response_status integer,
    response_body jsonb,
    locked_until timestamp with time zone,
    expires_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL
);


--
-- Name: TABLE idempotency_key; Type: COMMENT; Schema: ops; Owner: -
--

COMMENT ON TABLE ops.idempotency_key IS '预留：API 幂等';


--
-- Name: outbox_event; Type: TABLE; Schema: ops; Owner: -
--

CREATE TABLE ops.outbox_event (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    aggregate_type text NOT NULL,
    aggregate_id uuid NOT NULL,
    event_type text NOT NULL,
    event_version integer DEFAULT 1 NOT NULL,
    payload jsonb NOT NULL,
    correlation_id uuid,
    causation_id uuid,
    status text DEFAULT 'pending'::text NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    available_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    published_at timestamp with time zone,
    last_error text,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT outbox_event_attempts_check CHECK ((attempts >= 0)),
    CONSTRAINT outbox_event_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'publishing'::text, 'published'::text, 'failed'::text, 'dead_letter'::text])))
);


--
-- Name: TABLE outbox_event; Type: COMMENT; Schema: ops; Owner: -
--

COMMENT ON TABLE ops.outbox_event IS '预留：事务性出站事件';


--
-- Name: schema_migration; Type: TABLE; Schema: ops; Owner: -
--

CREATE TABLE ops.schema_migration (
    version text NOT NULL,
    description text NOT NULL,
    applied_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL
);


--
-- Name: TABLE schema_migration; Type: COMMENT; Schema: ops; Owner: -
--

COMMENT ON TABLE ops.schema_migration IS '现行：已应用迁移';


--
-- Name: v_schema_catalog; Type: VIEW; Schema: ops; Owner: -
--

CREATE VIEW ops.v_schema_catalog AS
 SELECT n.nspname AS schema_name,
    c.relname AS object_name,
        CASE c.relkind
            WHEN 'r'::"char" THEN 'table'::text
            WHEN 'v'::"char" THEN 'view'::text
            ELSE (c.relkind)::text
        END AS object_kind,
    COALESCE(pg_stat_get_live_tuples(c.oid), (0)::bigint) AS live_rows,
    obj_description(c.oid) AS purpose
   FROM (pg_class c
     JOIN pg_namespace n ON ((n.oid = c.relnamespace)))
  WHERE ((n.nspname = ANY (ARRAY['platform'::name, 'config'::name, 'crm'::name, 'activity'::name, 'workflow'::name, 'insight'::name, 'agent'::name, 'ops'::name])) AND (c.relkind = ANY (ARRAY['r'::"char", 'v'::"char"])))
  ORDER BY n.nspname, c.relname;


--
-- Name: auth_session; Type: TABLE; Schema: platform; Owner: -
--

CREATE TABLE platform.auth_session (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    user_ref_id uuid NOT NULL,
    refresh_token_hash character(64) NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    client_context jsonb DEFAULT '{}'::jsonb NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    last_seen_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    revoked_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT auth_session_check CHECK ((expires_at > created_at)),
    CONSTRAINT auth_session_status_check CHECK ((status = ANY (ARRAY['active'::text, 'revoked'::text, 'expired'::text])))
);

ALTER TABLE ONLY platform.auth_session FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE auth_session; Type: COMMENT; Schema: platform; Owner: -
--

COMMENT ON TABLE platform.auth_session IS '现行：演示会话与 refresh token';


--
-- Name: identity_binding; Type: TABLE; Schema: platform; Owner: -
--

CREATE TABLE platform.identity_binding (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    user_ref_id uuid NOT NULL,
    provider_code text NOT NULL,
    external_subject_id text NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    bound_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    last_verified_at timestamp with time zone,
    revoked_at timestamp with time zone,
    attributes jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT identity_binding_status_check CHECK ((status = ANY (ARRAY['active'::text, 'disabled'::text, 'revoked'::text])))
);

ALTER TABLE ONLY platform.identity_binding FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE identity_binding; Type: COMMENT; Schema: platform; Owner: -
--

COMMENT ON TABLE platform.identity_binding IS '预留：企业身份绑定（微信/IdP）';


--
-- Name: role_binding; Type: TABLE; Schema: platform; Owner: -
--

CREATE TABLE platform.role_binding (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    user_ref_id uuid NOT NULL,
    role_code text NOT NULL,
    data_scope_code text NOT NULL,
    team_id uuid,
    external_membership_id text,
    valid_from timestamp with time zone DEFAULT '-infinity'::timestamp with time zone NOT NULL,
    valid_to timestamp with time zone DEFAULT 'infinity'::timestamp with time zone NOT NULL,
    attributes jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT role_binding_check CHECK ((valid_to > valid_from)),
    CONSTRAINT role_binding_data_scope_code_check CHECK ((data_scope_code = ANY (ARRAY['self'::text, 'team'::text, 'workspace'::text]))),
    CONSTRAINT role_binding_role_code_check CHECK ((role_code = ANY (ARRAY['sales'::text, 'supervisor'::text, 'manager'::text])))
);


--
-- Name: TABLE role_binding; Type: COMMENT; Schema: platform; Owner: -
--

COMMENT ON TABLE platform.role_binding IS '现行：角色与数据范围';


--
-- Name: team; Type: TABLE; Schema: platform; Owner: -
--

CREATE TABLE platform.team (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    external_team_id text,
    parent_team_id uuid,
    code text NOT NULL,
    name text NOT NULL,
    region_code text,
    status text DEFAULT 'active'::text NOT NULL,
    valid_from timestamp with time zone DEFAULT '-infinity'::timestamp with time zone NOT NULL,
    valid_to timestamp with time zone DEFAULT 'infinity'::timestamp with time zone NOT NULL,
    attributes jsonb DEFAULT '{}'::jsonb NOT NULL,
    version_no integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    deleted_at timestamp with time zone,
    CONSTRAINT team_check CHECK ((valid_to > valid_from)),
    CONSTRAINT team_status_check CHECK ((status = ANY (ARRAY['active'::text, 'inactive'::text]))),
    CONSTRAINT team_version_no_check CHECK ((version_no > 0))
);


--
-- Name: TABLE team; Type: COMMENT; Schema: platform; Owner: -
--

COMMENT ON TABLE platform.team IS '现行：团队';


--
-- Name: team_membership; Type: TABLE; Schema: platform; Owner: -
--

CREATE TABLE platform.team_membership (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    team_id uuid NOT NULL,
    user_ref_id uuid NOT NULL,
    membership_role text NOT NULL,
    is_primary boolean DEFAULT true NOT NULL,
    valid_from timestamp with time zone DEFAULT '-infinity'::timestamp with time zone NOT NULL,
    valid_to timestamp with time zone DEFAULT 'infinity'::timestamp with time zone NOT NULL,
    attributes jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT team_membership_check CHECK ((valid_to > valid_from)),
    CONSTRAINT team_membership_membership_role_check CHECK ((membership_role = ANY (ARRAY['sales'::text, 'supervisor'::text, 'manager'::text])))
);


--
-- Name: TABLE team_membership; Type: COMMENT; Schema: platform; Owner: -
--

COMMENT ON TABLE platform.team_membership IS '现行：团队成员与有效期';


--
-- Name: workspace; Type: TABLE; Schema: platform; Owner: -
--

CREATE TABLE platform.workspace (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    external_workspace_id text NOT NULL,
    name text NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    default_timezone text DEFAULT 'Asia/Shanghai'::text NOT NULL,
    attributes jsonb DEFAULT '{}'::jsonb NOT NULL,
    version_no integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    deleted_at timestamp with time zone,
    CONSTRAINT workspace_status_check CHECK ((status = ANY (ARRAY['active'::text, 'suspended'::text, 'closed'::text]))),
    CONSTRAINT workspace_version_no_check CHECK ((version_no > 0))
);


--
-- Name: TABLE workspace; Type: COMMENT; Schema: platform; Owner: -
--

COMMENT ON TABLE platform.workspace IS '现行：工作空间';


--
-- Name: notification; Type: TABLE; Schema: workflow; Owner: -
--

CREATE TABLE workflow.notification (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    recipient_user_ref_id uuid NOT NULL,
    channel_code text DEFAULT 'in_app'::text NOT NULL,
    template_code text NOT NULL,
    title text NOT NULL,
    body text NOT NULL,
    object_type text,
    object_id uuid,
    status text DEFAULT 'pending'::text NOT NULL,
    dedupe_key text,
    scheduled_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    sent_at timestamp with time zone,
    delivered_at timestamp with time zone,
    read_at timestamp with time zone,
    failure_reason text,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT notification_channel_code_check CHECK ((channel_code = ANY (ARRAY['in_app'::text, 'wechat_subscribe'::text, 'sms'::text, 'email'::text, 'webhook'::text]))),
    CONSTRAINT notification_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'sent'::text, 'delivered'::text, 'read'::text, 'failed'::text, 'cancelled'::text])))
);


--
-- Name: TABLE notification; Type: COMMENT; Schema: workflow; Owner: -
--

COMMENT ON TABLE workflow.notification IS '现行：站内通知';


--
-- Name: notification_delivery; Type: TABLE; Schema: workflow; Owner: -
--

CREATE TABLE workflow.notification_delivery (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    notification_id uuid NOT NULL,
    attempt_no integer NOT NULL,
    channel_code text NOT NULL,
    provider_code text,
    provider_message_id text,
    status text NOT NULL,
    request_snapshot jsonb DEFAULT '{}'::jsonb NOT NULL,
    response_snapshot jsonb DEFAULT '{}'::jsonb NOT NULL,
    error_code text,
    error_detail text,
    attempted_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    delivered_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT notification_delivery_attempt_no_check CHECK ((attempt_no > 0)),
    CONSTRAINT notification_delivery_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'sent'::text, 'delivered'::text, 'failed'::text, 'cancelled'::text])))
);

ALTER TABLE ONLY workflow.notification_delivery FORCE ROW LEVEL SECURITY;


--
-- Name: TABLE notification_delivery; Type: COMMENT; Schema: workflow; Owner: -
--

COMMENT ON TABLE workflow.notification_delivery IS '预留：通知投递尝试';


--
-- Name: task; Type: TABLE; Schema: workflow; Owner: -
--

CREATE TABLE workflow.task (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    task_type text DEFAULT 'management'::text NOT NULL,
    title text NOT NULL,
    description text NOT NULL,
    customer_id uuid,
    opportunity_id uuid,
    source_visit_id uuid,
    creator_user_ref_id uuid NOT NULL,
    creator_team_id uuid,
    priority_code text DEFAULT 'normal'::text NOT NULL,
    status text DEFAULT 'pending_confirm'::text NOT NULL,
    due_at timestamp with time zone NOT NULL,
    accepted_at timestamp with time zone,
    completed_at timestamp with time zone,
    completion_note text,
    verification_status text DEFAULT 'not_required'::text NOT NULL,
    attributes jsonb DEFAULT '{}'::jsonb NOT NULL,
    version_no integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    deleted_at timestamp with time zone,
    source_artifact_id uuid,
    rejection_comment text,
    import_meta jsonb DEFAULT '{}'::jsonb NOT NULL,
    source_code text,
    agent_reason text,
    source_follow_up_record text,
    source_interaction_at timestamp with time zone,
    CONSTRAINT task_check CHECK ((due_at > created_at)),
    CONSTRAINT task_priority_code_check CHECK ((priority_code = ANY (ARRAY['normal'::text, 'medium'::text, 'high'::text, 'urgent'::text]))),
    CONSTRAINT task_status_check CHECK ((status = ANY (ARRAY['pending_confirm'::text, 'pending_execution'::text, 'in_progress'::text, 'completed'::text, 'deferred'::text, 'cancelled'::text]))),
    CONSTRAINT task_verification_status_check CHECK ((verification_status = ANY (ARRAY['not_required'::text, 'pending'::text, 'passed'::text, 'rejected'::text]))),
    CONSTRAINT task_version_no_check CHECK ((version_no > 0))
);


--
-- Name: TABLE task; Type: COMMENT; Schema: workflow; Owner: -
--

COMMENT ON TABLE workflow.task IS '现行：任务。来源与 Agent 理由已提升为列';


--
-- Name: COLUMN task.attributes; Type: COMMENT; Schema: workflow; Owner: -
--

COMMENT ON COLUMN workflow.task.attributes IS '开放扩展袋';


--
-- Name: COLUMN task.rejection_comment; Type: COMMENT; Schema: workflow; Owner: -
--

COMMENT ON COLUMN workflow.task.rejection_comment IS '拒绝意见（原 attributes.rejection_comment）';


--
-- Name: COLUMN task.import_meta; Type: COMMENT; Schema: workflow; Owner: -
--

COMMENT ON COLUMN workflow.task.import_meta IS '导入/演示批次溯源';


--
-- Name: COLUMN task.source_code; Type: COMMENT; Schema: workflow; Owner: -
--

COMMENT ON COLUMN workflow.task.source_code IS '任务来源：mini_program / today_task_agent / demo_customer_task 等';


--
-- Name: task_assignee; Type: TABLE; Schema: workflow; Owner: -
--

CREATE TABLE workflow.task_assignee (
    task_id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    assignee_user_ref_id uuid NOT NULL,
    assignee_team_id uuid,
    assignee_role text NOT NULL,
    responsibility text DEFAULT 'owner'::text NOT NULL,
    assigned_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    accepted_at timestamp with time zone,
    CONSTRAINT task_assignee_assignee_role_check CHECK ((assignee_role = ANY (ARRAY['sales'::text, 'supervisor'::text, 'manager'::text]))),
    CONSTRAINT task_assignee_responsibility_check CHECK ((responsibility = ANY (ARRAY['owner'::text, 'collaborator'::text, 'reviewer'::text])))
);


--
-- Name: TABLE task_assignee; Type: COMMENT; Schema: workflow; Owner: -
--

COMMENT ON TABLE workflow.task_assignee IS '现行：任务负责人/协作人';


--
-- Name: task_event; Type: TABLE; Schema: workflow; Owner: -
--

CREATE TABLE workflow.task_event (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_id uuid NOT NULL,
    task_id uuid NOT NULL,
    event_type text NOT NULL,
    from_status text,
    to_status text,
    actor_user_ref_id uuid,
    note text,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    occurred_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL
);


--
-- Name: TABLE task_event; Type: COMMENT; Schema: workflow; Owner: -
--

COMMENT ON TABLE workflow.task_event IS '现行：任务状态轨迹';


--
-- Name: v_user_inbox; Type: VIEW; Schema: workflow; Owner: -
--

CREATE VIEW workflow.v_user_inbox WITH (security_invoker='true') AS
 SELECT id,
    workspace_id,
    recipient_user_ref_id,
    channel_code,
    template_code,
    title,
    body,
    object_type,
    object_id,
    status,
    scheduled_at,
    sent_at,
    read_at,
    created_at
   FROM workflow.notification n
  WHERE (recipient_user_ref_id = common.current_user_ref_id());


--
-- Name: action_item action_item_pkey; Type: CONSTRAINT; Schema: activity; Owner: -
--

ALTER TABLE ONLY activity.action_item
    ADD CONSTRAINT action_item_pkey PRIMARY KEY (id);


--
-- Name: visit_contact visit_contact_pkey; Type: CONSTRAINT; Schema: activity; Owner: -
--

ALTER TABLE ONLY activity.visit_contact
    ADD CONSTRAINT visit_contact_pkey PRIMARY KEY (visit_id, contact_id);


--
-- Name: visit_field_value visit_field_value_pkey; Type: CONSTRAINT; Schema: activity; Owner: -
--

ALTER TABLE ONLY activity.visit_field_value
    ADD CONSTRAINT visit_field_value_pkey PRIMARY KEY (visit_id, field_definition_id);


--
-- Name: visit visit_pkey; Type: CONSTRAINT; Schema: activity; Owner: -
--

ALTER TABLE ONLY activity.visit
    ADD CONSTRAINT visit_pkey PRIMARY KEY (id);


--
-- Name: artifact artifact_pkey; Type: CONSTRAINT; Schema: agent; Owner: -
--

ALTER TABLE ONLY agent.artifact
    ADD CONSTRAINT artifact_pkey PRIMARY KEY (id);


--
-- Name: confirmation confirmation_artifact_id_confirmer_user_ref_id_confirmed_at_key; Type: CONSTRAINT; Schema: agent; Owner: -
--

ALTER TABLE ONLY agent.confirmation
    ADD CONSTRAINT confirmation_artifact_id_confirmer_user_ref_id_confirmed_at_key UNIQUE (artifact_id, confirmer_user_ref_id, confirmed_at);


--
-- Name: confirmation confirmation_pkey; Type: CONSTRAINT; Schema: agent; Owner: -
--

ALTER TABLE ONLY agent.confirmation
    ADD CONSTRAINT confirmation_pkey PRIMARY KEY (id);


--
-- Name: conversation conversation_pkey; Type: CONSTRAINT; Schema: agent; Owner: -
--

ALTER TABLE ONLY agent.conversation
    ADD CONSTRAINT conversation_pkey PRIMARY KEY (id);


--
-- Name: message message_pkey; Type: CONSTRAINT; Schema: agent; Owner: -
--

ALTER TABLE ONLY agent.message
    ADD CONSTRAINT message_pkey PRIMARY KEY (id);


--
-- Name: model_invocation model_invocation_pkey; Type: CONSTRAINT; Schema: agent; Owner: -
--

ALTER TABLE ONLY agent.model_invocation
    ADD CONSTRAINT model_invocation_pkey PRIMARY KEY (id);


--
-- Name: run run_pkey; Type: CONSTRAINT; Schema: agent; Owner: -
--

ALTER TABLE ONLY agent.run
    ADD CONSTRAINT run_pkey PRIMARY KEY (id);


--
-- Name: run_step run_step_pkey; Type: CONSTRAINT; Schema: agent; Owner: -
--

ALTER TABLE ONLY agent.run_step
    ADD CONSTRAINT run_step_pkey PRIMARY KEY (id);


--
-- Name: run_step run_step_run_id_sequence_no_key; Type: CONSTRAINT; Schema: agent; Owner: -
--

ALTER TABLE ONLY agent.run_step
    ADD CONSTRAINT run_step_run_id_sequence_no_key UNIQUE (run_id, sequence_no);


--
-- Name: tool_invocation tool_invocation_pkey; Type: CONSTRAINT; Schema: agent; Owner: -
--

ALTER TABLE ONLY agent.tool_invocation
    ADD CONSTRAINT tool_invocation_pkey PRIMARY KEY (id);


--
-- Name: agent_definition agent_definition_pkey; Type: CONSTRAINT; Schema: config; Owner: -
--

ALTER TABLE ONLY config.agent_definition
    ADD CONSTRAINT agent_definition_pkey PRIMARY KEY (id);


--
-- Name: agent_definition agent_definition_workspace_id_agent_code_version_no_key; Type: CONSTRAINT; Schema: config; Owner: -
--

ALTER TABLE ONLY config.agent_definition
    ADD CONSTRAINT agent_definition_workspace_id_agent_code_version_no_key UNIQUE NULLS NOT DISTINCT (workspace_id, agent_code, version_no);


--
-- Name: agent_runtime_config agent_runtime_config_pkey; Type: CONSTRAINT; Schema: config; Owner: -
--

ALTER TABLE ONLY config.agent_runtime_config
    ADD CONSTRAINT agent_runtime_config_pkey PRIMARY KEY (workspace_id);


--
-- Name: agent_runtime_release agent_runtime_release_pkey; Type: CONSTRAINT; Schema: config; Owner: -
--

ALTER TABLE ONLY config.agent_runtime_release
    ADD CONSTRAINT agent_runtime_release_pkey PRIMARY KEY (id);


--
-- Name: agent_runtime_release agent_runtime_release_workspace_id_version_no_key; Type: CONSTRAINT; Schema: config; Owner: -
--

ALTER TABLE ONLY config.agent_runtime_release
    ADD CONSTRAINT agent_runtime_release_workspace_id_version_no_key UNIQUE (workspace_id, version_no);


--
-- Name: dictionary_item dictionary_item_dictionary_id_item_code_key; Type: CONSTRAINT; Schema: config; Owner: -
--

ALTER TABLE ONLY config.dictionary_item
    ADD CONSTRAINT dictionary_item_dictionary_id_item_code_key UNIQUE (dictionary_id, item_code);


--
-- Name: dictionary_item dictionary_item_pkey; Type: CONSTRAINT; Schema: config; Owner: -
--

ALTER TABLE ONLY config.dictionary_item
    ADD CONSTRAINT dictionary_item_pkey PRIMARY KEY (id);


--
-- Name: dictionary dictionary_pkey; Type: CONSTRAINT; Schema: config; Owner: -
--

ALTER TABLE ONLY config.dictionary
    ADD CONSTRAINT dictionary_pkey PRIMARY KEY (id);


--
-- Name: dictionary dictionary_workspace_id_code_version_no_key; Type: CONSTRAINT; Schema: config; Owner: -
--

ALTER TABLE ONLY config.dictionary
    ADD CONSTRAINT dictionary_workspace_id_code_version_no_key UNIQUE NULLS NOT DISTINCT (workspace_id, code, version_no);


--
-- Name: field_definition field_definition_pkey; Type: CONSTRAINT; Schema: config; Owner: -
--

ALTER TABLE ONLY config.field_definition
    ADD CONSTRAINT field_definition_pkey PRIMARY KEY (id);


--
-- Name: field_definition field_definition_workspace_id_object_type_field_key_version_key; Type: CONSTRAINT; Schema: config; Owner: -
--

ALTER TABLE ONLY config.field_definition
    ADD CONSTRAINT field_definition_workspace_id_object_type_field_key_version_key UNIQUE NULLS NOT DISTINCT (workspace_id, object_type, field_key, version_no);


--
-- Name: form_definition form_definition_pkey; Type: CONSTRAINT; Schema: config; Owner: -
--

ALTER TABLE ONLY config.form_definition
    ADD CONSTRAINT form_definition_pkey PRIMARY KEY (id);


--
-- Name: form_definition form_definition_workspace_id_form_code_key; Type: CONSTRAINT; Schema: config; Owner: -
--

ALTER TABLE ONLY config.form_definition
    ADD CONSTRAINT form_definition_workspace_id_form_code_key UNIQUE NULLS NOT DISTINCT (workspace_id, form_code);


--
-- Name: form_version_field form_version_field_form_version_id_display_order_key; Type: CONSTRAINT; Schema: config; Owner: -
--

ALTER TABLE ONLY config.form_version_field
    ADD CONSTRAINT form_version_field_form_version_id_display_order_key UNIQUE (form_version_id, display_order);


--
-- Name: form_version_field form_version_field_pkey; Type: CONSTRAINT; Schema: config; Owner: -
--

ALTER TABLE ONLY config.form_version_field
    ADD CONSTRAINT form_version_field_pkey PRIMARY KEY (form_version_id, field_definition_id);


--
-- Name: form_version form_version_form_definition_id_version_no_key; Type: CONSTRAINT; Schema: config; Owner: -
--

ALTER TABLE ONLY config.form_version
    ADD CONSTRAINT form_version_form_definition_id_version_no_key UNIQUE (form_definition_id, version_no);


--
-- Name: form_version form_version_pkey; Type: CONSTRAINT; Schema: config; Owner: -
--

ALTER TABLE ONLY config.form_version
    ADD CONSTRAINT form_version_pkey PRIMARY KEY (id);


--
-- Name: metric_definition metric_definition_pkey; Type: CONSTRAINT; Schema: config; Owner: -
--

ALTER TABLE ONLY config.metric_definition
    ADD CONSTRAINT metric_definition_pkey PRIMARY KEY (id);


--
-- Name: prompt_template prompt_template_pkey; Type: CONSTRAINT; Schema: config; Owner: -
--

ALTER TABLE ONLY config.prompt_template
    ADD CONSTRAINT prompt_template_pkey PRIMARY KEY (id);


--
-- Name: rule_set rule_set_pkey; Type: CONSTRAINT; Schema: config; Owner: -
--

ALTER TABLE ONLY config.rule_set
    ADD CONSTRAINT rule_set_pkey PRIMARY KEY (id);


--
-- Name: rule_set rule_set_workspace_id_rule_code_version_no_key; Type: CONSTRAINT; Schema: config; Owner: -
--

ALTER TABLE ONLY config.rule_set
    ADD CONSTRAINT rule_set_workspace_id_rule_code_version_no_key UNIQUE NULLS NOT DISTINCT (workspace_id, rule_code, version_no);


--
-- Name: sales_competency_framework sales_competency_framework_pkey; Type: CONSTRAINT; Schema: config; Owner: -
--

ALTER TABLE ONLY config.sales_competency_framework
    ADD CONSTRAINT sales_competency_framework_pkey PRIMARY KEY (id);


--
-- Name: contact contact_pkey; Type: CONSTRAINT; Schema: crm; Owner: -
--

ALTER TABLE ONLY crm.contact
    ADD CONSTRAINT contact_pkey PRIMARY KEY (id);


--
-- Name: customer_assignment customer_assignment_pkey; Type: CONSTRAINT; Schema: crm; Owner: -
--

ALTER TABLE ONLY crm.customer_assignment
    ADD CONSTRAINT customer_assignment_pkey PRIMARY KEY (id);


--
-- Name: customer_duplicate_candidate customer_duplicate_candidate_pkey; Type: CONSTRAINT; Schema: crm; Owner: -
--

ALTER TABLE ONLY crm.customer_duplicate_candidate
    ADD CONSTRAINT customer_duplicate_candidate_pkey PRIMARY KEY (id);


--
-- Name: customer_duplicate_candidate customer_duplicate_candidate_workspace_id_customer_id_candi_key; Type: CONSTRAINT; Schema: crm; Owner: -
--

ALTER TABLE ONLY crm.customer_duplicate_candidate
    ADD CONSTRAINT customer_duplicate_candidate_workspace_id_customer_id_candi_key UNIQUE (workspace_id, customer_id, candidate_customer_id);


--
-- Name: customer customer_pkey; Type: CONSTRAINT; Schema: crm; Owner: -
--

ALTER TABLE ONLY crm.customer
    ADD CONSTRAINT customer_pkey PRIMARY KEY (id);


--
-- Name: customer_product customer_product_pkey; Type: CONSTRAINT; Schema: crm; Owner: -
--

ALTER TABLE ONLY crm.customer_product
    ADD CONSTRAINT customer_product_pkey PRIMARY KEY (id);


--
-- Name: customer_product customer_product_workspace_id_customer_id_product_id_relati_key; Type: CONSTRAINT; Schema: crm; Owner: -
--

ALTER TABLE ONLY crm.customer_product
    ADD CONSTRAINT customer_product_workspace_id_customer_id_product_id_relati_key UNIQUE (workspace_id, customer_id, product_id, relationship_status);


--
-- Name: opportunity_participant opportunity_participant_pkey; Type: CONSTRAINT; Schema: crm; Owner: -
--

ALTER TABLE ONLY crm.opportunity_participant
    ADD CONSTRAINT opportunity_participant_pkey PRIMARY KEY (opportunity_id, user_ref_id, valid_from);


--
-- Name: opportunity opportunity_pkey; Type: CONSTRAINT; Schema: crm; Owner: -
--

ALTER TABLE ONLY crm.opportunity
    ADD CONSTRAINT opportunity_pkey PRIMARY KEY (id);


--
-- Name: product product_pkey; Type: CONSTRAINT; Schema: crm; Owner: -
--

ALTER TABLE ONLY crm.product
    ADD CONSTRAINT product_pkey PRIMARY KEY (id);


--
-- Name: product product_workspace_id_product_code_key; Type: CONSTRAINT; Schema: crm; Owner: -
--

ALTER TABLE ONLY crm.product
    ADD CONSTRAINT product_workspace_id_product_code_key UNIQUE NULLS NOT DISTINCT (workspace_id, product_code);


--
-- Name: quadrant_score quadrant_score_pkey; Type: CONSTRAINT; Schema: insight; Owner: -
--

ALTER TABLE ONLY insight.quadrant_score
    ADD CONSTRAINT quadrant_score_pkey PRIMARY KEY (id);


--
-- Name: recommendation recommendation_pkey; Type: CONSTRAINT; Schema: insight; Owner: -
--

ALTER TABLE ONLY insight.recommendation
    ADD CONSTRAINT recommendation_pkey PRIMARY KEY (id);


--
-- Name: report report_pkey; Type: CONSTRAINT; Schema: insight; Owner: -
--

ALTER TABLE ONLY insight.report
    ADD CONSTRAINT report_pkey PRIMARY KEY (id);


--
-- Name: risk_event risk_event_pkey; Type: CONSTRAINT; Schema: insight; Owner: -
--

ALTER TABLE ONLY insight.risk_event
    ADD CONSTRAINT risk_event_pkey PRIMARY KEY (id);


--
-- Name: risk risk_pkey; Type: CONSTRAINT; Schema: insight; Owner: -
--

ALTER TABLE ONLY insight.risk
    ADD CONSTRAINT risk_pkey PRIMARY KEY (id);


--
-- Name: sales_competency_review sales_competency_review_pkey; Type: CONSTRAINT; Schema: insight; Owner: -
--

ALTER TABLE ONLY insight.sales_competency_review
    ADD CONSTRAINT sales_competency_review_pkey PRIMARY KEY (id);


--
-- Name: sales_competency_review sales_competency_review_workspace_id_subject_user_ref_id_re_key; Type: CONSTRAINT; Schema: insight; Owner: -
--

ALTER TABLE ONLY insight.sales_competency_review
    ADD CONSTRAINT sales_competency_review_workspace_id_subject_user_ref_id_re_key UNIQUE (workspace_id, subject_user_ref_id, review_date, framework_version);


--
-- Name: audit_log audit_log_pkey; Type: CONSTRAINT; Schema: ops; Owner: -
--

ALTER TABLE ONLY ops.audit_log
    ADD CONSTRAINT audit_log_pkey PRIMARY KEY (id);


--
-- Name: file_asset file_asset_pkey; Type: CONSTRAINT; Schema: ops; Owner: -
--

ALTER TABLE ONLY ops.file_asset
    ADD CONSTRAINT file_asset_pkey PRIMARY KEY (id);


--
-- Name: file_asset file_asset_workspace_id_object_uri_key; Type: CONSTRAINT; Schema: ops; Owner: -
--

ALTER TABLE ONLY ops.file_asset
    ADD CONSTRAINT file_asset_workspace_id_object_uri_key UNIQUE (workspace_id, object_uri);


--
-- Name: idempotency_key idempotency_key_pkey; Type: CONSTRAINT; Schema: ops; Owner: -
--

ALTER TABLE ONLY ops.idempotency_key
    ADD CONSTRAINT idempotency_key_pkey PRIMARY KEY (workspace_id, key, operation_code);


--
-- Name: job job_pkey; Type: CONSTRAINT; Schema: ops; Owner: -
--

ALTER TABLE ONLY ops.job
    ADD CONSTRAINT job_pkey PRIMARY KEY (id);


--
-- Name: outbox_event outbox_event_pkey; Type: CONSTRAINT; Schema: ops; Owner: -
--

ALTER TABLE ONLY ops.outbox_event
    ADD CONSTRAINT outbox_event_pkey PRIMARY KEY (id);


--
-- Name: schema_migration schema_migration_pkey; Type: CONSTRAINT; Schema: ops; Owner: -
--

ALTER TABLE ONLY ops.schema_migration
    ADD CONSTRAINT schema_migration_pkey PRIMARY KEY (version);


--
-- Name: auth_session auth_session_pkey; Type: CONSTRAINT; Schema: platform; Owner: -
--

ALTER TABLE ONLY platform.auth_session
    ADD CONSTRAINT auth_session_pkey PRIMARY KEY (id);


--
-- Name: auth_session auth_session_refresh_token_hash_key; Type: CONSTRAINT; Schema: platform; Owner: -
--

ALTER TABLE ONLY platform.auth_session
    ADD CONSTRAINT auth_session_refresh_token_hash_key UNIQUE (refresh_token_hash);


--
-- Name: identity_binding identity_binding_pkey; Type: CONSTRAINT; Schema: platform; Owner: -
--

ALTER TABLE ONLY platform.identity_binding
    ADD CONSTRAINT identity_binding_pkey PRIMARY KEY (id);


--
-- Name: identity_binding identity_binding_workspace_id_provider_code_external_subjec_key; Type: CONSTRAINT; Schema: platform; Owner: -
--

ALTER TABLE ONLY platform.identity_binding
    ADD CONSTRAINT identity_binding_workspace_id_provider_code_external_subjec_key UNIQUE (workspace_id, provider_code, external_subject_id);


--
-- Name: identity_binding identity_binding_workspace_id_user_ref_id_provider_code_key; Type: CONSTRAINT; Schema: platform; Owner: -
--

ALTER TABLE ONLY platform.identity_binding
    ADD CONSTRAINT identity_binding_workspace_id_user_ref_id_provider_code_key UNIQUE (workspace_id, user_ref_id, provider_code);


--
-- Name: role_binding role_binding_pkey; Type: CONSTRAINT; Schema: platform; Owner: -
--

ALTER TABLE ONLY platform.role_binding
    ADD CONSTRAINT role_binding_pkey PRIMARY KEY (id);


--
-- Name: role_binding role_binding_workspace_id_user_ref_id_role_code_team_id_val_key; Type: CONSTRAINT; Schema: platform; Owner: -
--

ALTER TABLE ONLY platform.role_binding
    ADD CONSTRAINT role_binding_workspace_id_user_ref_id_role_code_team_id_val_key UNIQUE (workspace_id, user_ref_id, role_code, team_id, valid_from);


--
-- Name: team_membership team_membership_pkey; Type: CONSTRAINT; Schema: platform; Owner: -
--

ALTER TABLE ONLY platform.team_membership
    ADD CONSTRAINT team_membership_pkey PRIMARY KEY (id);


--
-- Name: team_membership team_membership_workspace_id_team_id_user_ref_id_membership_key; Type: CONSTRAINT; Schema: platform; Owner: -
--

ALTER TABLE ONLY platform.team_membership
    ADD CONSTRAINT team_membership_workspace_id_team_id_user_ref_id_membership_key UNIQUE (workspace_id, team_id, user_ref_id, membership_role, valid_from);


--
-- Name: team team_pkey; Type: CONSTRAINT; Schema: platform; Owner: -
--

ALTER TABLE ONLY platform.team
    ADD CONSTRAINT team_pkey PRIMARY KEY (id);


--
-- Name: team team_workspace_id_code_key; Type: CONSTRAINT; Schema: platform; Owner: -
--

ALTER TABLE ONLY platform.team
    ADD CONSTRAINT team_workspace_id_code_key UNIQUE (workspace_id, code);


--
-- Name: user_ref user_ref_pkey; Type: CONSTRAINT; Schema: platform; Owner: -
--

ALTER TABLE ONLY platform.user_ref
    ADD CONSTRAINT user_ref_pkey PRIMARY KEY (id);


--
-- Name: user_ref user_ref_workspace_id_external_user_id_key; Type: CONSTRAINT; Schema: platform; Owner: -
--

ALTER TABLE ONLY platform.user_ref
    ADD CONSTRAINT user_ref_workspace_id_external_user_id_key UNIQUE (workspace_id, external_user_id);


--
-- Name: workspace workspace_external_workspace_id_key; Type: CONSTRAINT; Schema: platform; Owner: -
--

ALTER TABLE ONLY platform.workspace
    ADD CONSTRAINT workspace_external_workspace_id_key UNIQUE (external_workspace_id);


--
-- Name: workspace workspace_pkey; Type: CONSTRAINT; Schema: platform; Owner: -
--

ALTER TABLE ONLY platform.workspace
    ADD CONSTRAINT workspace_pkey PRIMARY KEY (id);


--
-- Name: notification_delivery notification_delivery_notification_id_attempt_no_key; Type: CONSTRAINT; Schema: workflow; Owner: -
--

ALTER TABLE ONLY workflow.notification_delivery
    ADD CONSTRAINT notification_delivery_notification_id_attempt_no_key UNIQUE (notification_id, attempt_no);


--
-- Name: notification_delivery notification_delivery_pkey; Type: CONSTRAINT; Schema: workflow; Owner: -
--

ALTER TABLE ONLY workflow.notification_delivery
    ADD CONSTRAINT notification_delivery_pkey PRIMARY KEY (id);


--
-- Name: notification notification_pkey; Type: CONSTRAINT; Schema: workflow; Owner: -
--

ALTER TABLE ONLY workflow.notification
    ADD CONSTRAINT notification_pkey PRIMARY KEY (id);


--
-- Name: task_assignee task_assignee_pkey; Type: CONSTRAINT; Schema: workflow; Owner: -
--

ALTER TABLE ONLY workflow.task_assignee
    ADD CONSTRAINT task_assignee_pkey PRIMARY KEY (task_id, assignee_user_ref_id, responsibility);


--
-- Name: task_event task_event_pkey; Type: CONSTRAINT; Schema: workflow; Owner: -
--

ALTER TABLE ONLY workflow.task_event
    ADD CONSTRAINT task_event_pkey PRIMARY KEY (id);


--
-- Name: task task_pkey; Type: CONSTRAINT; Schema: workflow; Owner: -
--

ALTER TABLE ONLY workflow.task
    ADD CONSTRAINT task_pkey PRIMARY KEY (id);


--
-- Name: idx_action_owner_due; Type: INDEX; Schema: activity; Owner: -
--

CREATE INDEX idx_action_owner_due ON activity.action_item USING btree (workspace_id, owner_user_ref_id, status, due_at) WHERE (deleted_at IS NULL);


--
-- Name: idx_visit_customer_time; Type: INDEX; Schema: activity; Owner: -
--

CREATE INDEX idx_visit_customer_time ON activity.visit USING btree (workspace_id, customer_id, interaction_at DESC) WHERE (deleted_at IS NULL);


--
-- Name: idx_visit_field_value_text; Type: INDEX; Schema: activity; Owner: -
--

CREATE INDEX idx_visit_field_value_text ON activity.visit_field_value USING btree (workspace_id, field_definition_id, value_text);


--
-- Name: idx_visit_recorder_time; Type: INDEX; Schema: activity; Owner: -
--

CREATE INDEX idx_visit_recorder_time ON activity.visit USING btree (workspace_id, recorder_user_ref_id, interaction_at DESC) WHERE (deleted_at IS NULL);


--
-- Name: idx_visit_status; Type: INDEX; Schema: activity; Owner: -
--

CREATE INDEX idx_visit_status ON activity.visit USING btree (workspace_id, status, updated_at DESC) WHERE (deleted_at IS NULL);


--
-- Name: idx_visit_team_time; Type: INDEX; Schema: activity; Owner: -
--

CREATE INDEX idx_visit_team_time ON activity.visit USING btree (workspace_id, recorder_team_id, interaction_at DESC) WHERE (deleted_at IS NULL);


--
-- Name: uq_visit_idempotency_present; Type: INDEX; Schema: activity; Owner: -
--

CREATE UNIQUE INDEX uq_visit_idempotency_present ON activity.visit USING btree (workspace_id, idempotency_fingerprint) WHERE (idempotency_fingerprint IS NOT NULL);


--
-- Name: idx_artifact_payload_gin; Type: INDEX; Schema: agent; Owner: -
--

CREATE INDEX idx_artifact_payload_gin ON agent.artifact USING gin (payload jsonb_path_ops);


--
-- Name: idx_artifact_subject; Type: INDEX; Schema: agent; Owner: -
--

CREATE INDEX idx_artifact_subject ON agent.artifact USING btree (workspace_id, subject_type, subject_id, artifact_type, created_at DESC);


--
-- Name: idx_conversation_user_time; Type: INDEX; Schema: agent; Owner: -
--

CREATE INDEX idx_conversation_user_time ON agent.conversation USING btree (workspace_id, user_ref_id, started_at DESC);


--
-- Name: idx_message_conversation_time; Type: INDEX; Schema: agent; Owner: -
--

CREATE INDEX idx_message_conversation_time ON agent.message USING btree (workspace_id, conversation_id, created_at);


--
-- Name: idx_model_invocation_model_time; Type: INDEX; Schema: agent; Owner: -
--

CREATE INDEX idx_model_invocation_model_time ON agent.model_invocation USING btree (workspace_id, model_id, started_at DESC);


--
-- Name: idx_model_invocation_run; Type: INDEX; Schema: agent; Owner: -
--

CREATE INDEX idx_model_invocation_run ON agent.model_invocation USING btree (workspace_id, run_id, started_at);


--
-- Name: idx_run_status; Type: INDEX; Schema: agent; Owner: -
--

CREATE INDEX idx_run_status ON agent.run USING btree (workspace_id, status, created_at);


--
-- Name: idx_tool_invocation_run; Type: INDEX; Schema: agent; Owner: -
--

CREATE INDEX idx_tool_invocation_run ON agent.tool_invocation USING btree (workspace_id, run_id, started_at);


--
-- Name: uq_message_client_id_present; Type: INDEX; Schema: agent; Owner: -
--

CREATE UNIQUE INDEX uq_message_client_id_present ON agent.message USING btree (workspace_id, conversation_id, client_message_id) WHERE (client_message_id IS NOT NULL);


--
-- Name: uq_metric_definition_active; Type: INDEX; Schema: config; Owner: -
--

CREATE UNIQUE INDEX uq_metric_definition_active ON config.metric_definition USING btree (COALESCE(workspace_id, '00000000-0000-0000-0000-000000000000'::uuid), metric_code) WHERE ((status = 'active'::text) AND (effective_to = 'infinity'::timestamp with time zone));


--
-- Name: uq_metric_definition_version; Type: INDEX; Schema: config; Owner: -
--

CREATE UNIQUE INDEX uq_metric_definition_version ON config.metric_definition USING btree (COALESCE(workspace_id, '00000000-0000-0000-0000-000000000000'::uuid), metric_code, version_no);


--
-- Name: uq_prompt_template_active; Type: INDEX; Schema: config; Owner: -
--

CREATE UNIQUE INDEX uq_prompt_template_active ON config.prompt_template USING btree (COALESCE(workspace_id, '00000000-0000-0000-0000-000000000000'::uuid), agent_code, prompt_code) WHERE ((status = 'active'::text) AND (effective_to = 'infinity'::timestamp with time zone));


--
-- Name: uq_prompt_template_version; Type: INDEX; Schema: config; Owner: -
--

CREATE UNIQUE INDEX uq_prompt_template_version ON config.prompt_template USING btree (COALESCE(workspace_id, '00000000-0000-0000-0000-000000000000'::uuid), agent_code, prompt_code, version_no);


--
-- Name: uq_sales_competency_framework_active; Type: INDEX; Schema: config; Owner: -
--

CREATE UNIQUE INDEX uq_sales_competency_framework_active ON config.sales_competency_framework USING btree (COALESCE(workspace_id, '00000000-0000-0000-0000-000000000000'::uuid)) WHERE ((status = 'active'::text) AND (effective_to = 'infinity'::timestamp with time zone));


--
-- Name: idx_assignment_user_status; Type: INDEX; Schema: crm; Owner: -
--

CREATE INDEX idx_assignment_user_status ON crm.customer_assignment USING btree (workspace_id, assigned_user_ref_id, status, assigned_at DESC);


--
-- Name: idx_contact_customer; Type: INDEX; Schema: crm; Owner: -
--

CREATE INDEX idx_contact_customer ON crm.contact USING btree (workspace_id, customer_id) WHERE (deleted_at IS NULL);


--
-- Name: idx_customer_attributes_gin; Type: INDEX; Schema: crm; Owner: -
--

CREATE INDEX idx_customer_attributes_gin ON crm.customer USING gin (attributes jsonb_path_ops);


--
-- Name: idx_customer_name; Type: INDEX; Schema: crm; Owner: -
--

CREATE INDEX idx_customer_name ON crm.customer USING btree (workspace_id, normalized_name) WHERE (deleted_at IS NULL);


--
-- Name: idx_customer_scope; Type: INDEX; Schema: crm; Owner: -
--

CREATE INDEX idx_customer_scope ON crm.customer USING btree (workspace_id, owner_team_id, owner_user_ref_id) WHERE (deleted_at IS NULL);


--
-- Name: idx_customer_workspace_data_kind; Type: INDEX; Schema: crm; Owner: -
--

CREATE INDEX idx_customer_workspace_data_kind ON crm.customer USING btree (workspace_id, data_kind) WHERE (deleted_at IS NULL);


--
-- Name: idx_opportunity_customer_status; Type: INDEX; Schema: crm; Owner: -
--

CREATE INDEX idx_opportunity_customer_status ON crm.opportunity USING btree (workspace_id, customer_id, status) WHERE (deleted_at IS NULL);


--
-- Name: idx_opportunity_owner; Type: INDEX; Schema: crm; Owner: -
--

CREATE INDEX idx_opportunity_owner ON crm.opportunity USING btree (workspace_id, owner_team_id, owner_user_ref_id, status) WHERE (deleted_at IS NULL);


--
-- Name: uq_contact_external_id_present; Type: INDEX; Schema: crm; Owner: -
--

CREATE UNIQUE INDEX uq_contact_external_id_present ON crm.contact USING btree (workspace_id, external_contact_id) WHERE (external_contact_id IS NOT NULL);


--
-- Name: uq_contact_primary; Type: INDEX; Schema: crm; Owner: -
--

CREATE UNIQUE INDEX uq_contact_primary ON crm.contact USING btree (workspace_id, customer_id) WHERE (is_primary AND (deleted_at IS NULL));


--
-- Name: uq_customer_code_present; Type: INDEX; Schema: crm; Owner: -
--

CREATE UNIQUE INDEX uq_customer_code_present ON crm.customer USING btree (workspace_id, customer_code) WHERE (customer_code IS NOT NULL);


--
-- Name: uq_customer_external_id_present; Type: INDEX; Schema: crm; Owner: -
--

CREATE UNIQUE INDEX uq_customer_external_id_present ON crm.customer USING btree (workspace_id, external_customer_id) WHERE (external_customer_id IS NOT NULL);


--
-- Name: uq_opportunity_external_id_present; Type: INDEX; Schema: crm; Owner: -
--

CREATE UNIQUE INDEX uq_opportunity_external_id_present ON crm.opportunity USING btree (workspace_id, external_opportunity_id) WHERE (external_opportunity_id IS NOT NULL);


--
-- Name: idx_quadrant_customer_time; Type: INDEX; Schema: insight; Owner: -
--

CREATE INDEX idx_quadrant_customer_time ON insight.quadrant_score USING btree (workspace_id, customer_id, calculated_at DESC);


--
-- Name: idx_recommendation_customer; Type: INDEX; Schema: insight; Owner: -
--

CREATE INDEX idx_recommendation_customer ON insight.recommendation USING btree (workspace_id, customer_id, status, generated_at DESC) WHERE (deleted_at IS NULL);


--
-- Name: idx_risk_scope; Type: INDEX; Schema: insight; Owner: -
--

CREATE INDEX idx_risk_scope ON insight.risk USING btree (workspace_id, owner_team_id, owner_user_ref_id, status, severity_code) WHERE (deleted_at IS NULL);


--
-- Name: idx_risk_source_code; Type: INDEX; Schema: insight; Owner: -
--

CREATE INDEX idx_risk_source_code ON insight.risk USING btree (workspace_id, source_code) WHERE ((deleted_at IS NULL) AND (source_code IS NOT NULL));


--
-- Name: idx_risk_source_run; Type: INDEX; Schema: insight; Owner: -
--

CREATE INDEX idx_risk_source_run ON insight.risk USING btree (source_run_id) WHERE (source_run_id IS NOT NULL);


--
-- Name: idx_sales_competency_review_subject_date; Type: INDEX; Schema: insight; Owner: -
--

CREATE INDEX idx_sales_competency_review_subject_date ON insight.sales_competency_review USING btree (workspace_id, subject_user_ref_id, review_date DESC);


--
-- Name: uq_personal_risk_source_type; Type: INDEX; Schema: insight; Owner: -
--

CREATE UNIQUE INDEX uq_personal_risk_source_type ON insight.risk USING btree (workspace_id, owner_user_ref_id, source_visit_id, risk_type_code) WHERE ((source_visit_id IS NOT NULL) AND (deleted_at IS NULL));


--
-- Name: uq_quadrant_current; Type: INDEX; Schema: insight; Owner: -
--

CREATE UNIQUE INDEX uq_quadrant_current ON insight.quadrant_score USING btree (workspace_id, customer_id) WHERE (valid_to = 'infinity'::timestamp with time zone);


--
-- Name: uq_recommendation_active_product; Type: INDEX; Schema: insight; Owner: -
--

CREATE UNIQUE INDEX uq_recommendation_active_product ON insight.recommendation USING btree (workspace_id, customer_id, product_id, recommendation_type) WHERE ((status = ANY (ARRAY['pending'::text, 'viewed'::text, 'adopted'::text, 'followed'::text])) AND (deleted_at IS NULL));


--
-- Name: idx_audit_actor_time; Type: INDEX; Schema: ops; Owner: -
--

CREATE INDEX idx_audit_actor_time ON ops.audit_log USING btree (workspace_id, actor_user_ref_id, occurred_at DESC);


--
-- Name: idx_audit_object_time; Type: INDEX; Schema: ops; Owner: -
--

CREATE INDEX idx_audit_object_time ON ops.audit_log USING btree (workspace_id, object_type, object_id, occurred_at DESC);


--
-- Name: idx_job_aggregate; Type: INDEX; Schema: ops; Owner: -
--

CREATE INDEX idx_job_aggregate ON ops.job USING btree (workspace_id, aggregate_type, aggregate_id, created_at DESC);


--
-- Name: idx_job_claim; Type: INDEX; Schema: ops; Owner: -
--

CREATE INDEX idx_job_claim ON ops.job USING btree (priority DESC, available_at, created_at) WHERE (status = ANY (ARRAY['queued'::text, 'failed'::text]));


--
-- Name: idx_outbox_pending; Type: INDEX; Schema: ops; Owner: -
--

CREATE INDEX idx_outbox_pending ON ops.outbox_event USING btree (status, available_at, created_at) WHERE (status = ANY (ARRAY['pending'::text, 'failed'::text]));


--
-- Name: idx_auth_session_actor; Type: INDEX; Schema: platform; Owner: -
--

CREATE INDEX idx_auth_session_actor ON platform.auth_session USING btree (workspace_id, user_ref_id, status, expires_at DESC);


--
-- Name: idx_membership_team_active; Type: INDEX; Schema: platform; Owner: -
--

CREATE INDEX idx_membership_team_active ON platform.team_membership USING btree (workspace_id, team_id, valid_from, valid_to);


--
-- Name: idx_membership_user_active; Type: INDEX; Schema: platform; Owner: -
--

CREATE INDEX idx_membership_user_active ON platform.team_membership USING btree (workspace_id, user_ref_id, valid_from, valid_to);


--
-- Name: idx_role_binding_user_active; Type: INDEX; Schema: platform; Owner: -
--

CREATE INDEX idx_role_binding_user_active ON platform.role_binding USING btree (workspace_id, user_ref_id, valid_from, valid_to);


--
-- Name: idx_team_workspace_parent; Type: INDEX; Schema: platform; Owner: -
--

CREATE INDEX idx_team_workspace_parent ON platform.team USING btree (workspace_id, parent_team_id) WHERE (deleted_at IS NULL);


--
-- Name: idx_user_ref_workspace_name; Type: INDEX; Schema: platform; Owner: -
--

CREATE INDEX idx_user_ref_workspace_name ON platform.user_ref USING btree (workspace_id, display_name) WHERE (deleted_at IS NULL);


--
-- Name: uq_user_ref_account_code_present; Type: INDEX; Schema: platform; Owner: -
--

CREATE UNIQUE INDEX uq_user_ref_account_code_present ON platform.user_ref USING btree (workspace_id, account_code) WHERE (account_code IS NOT NULL);


--
-- Name: idx_notification_delivery_status; Type: INDEX; Schema: workflow; Owner: -
--

CREATE INDEX idx_notification_delivery_status ON workflow.notification_delivery USING btree (workspace_id, status, attempted_at DESC);


--
-- Name: idx_notification_inbox; Type: INDEX; Schema: workflow; Owner: -
--

CREATE INDEX idx_notification_inbox ON workflow.notification USING btree (workspace_id, recipient_user_ref_id, status, created_at DESC);


--
-- Name: idx_task_assignee_status_due; Type: INDEX; Schema: workflow; Owner: -
--

CREATE INDEX idx_task_assignee_status_due ON workflow.task_assignee USING btree (workspace_id, assignee_user_ref_id, assigned_at DESC);


--
-- Name: idx_task_creator; Type: INDEX; Schema: workflow; Owner: -
--

CREATE INDEX idx_task_creator ON workflow.task USING btree (workspace_id, creator_user_ref_id, created_at DESC) WHERE (deleted_at IS NULL);


--
-- Name: idx_task_event_task_time; Type: INDEX; Schema: workflow; Owner: -
--

CREATE INDEX idx_task_event_task_time ON workflow.task_event USING btree (workspace_id, task_id, occurred_at);


--
-- Name: idx_task_source_code; Type: INDEX; Schema: workflow; Owner: -
--

CREATE INDEX idx_task_source_code ON workflow.task USING btree (workspace_id, source_code) WHERE ((deleted_at IS NULL) AND (source_code IS NOT NULL));


--
-- Name: idx_task_status_due; Type: INDEX; Schema: workflow; Owner: -
--

CREATE INDEX idx_task_status_due ON workflow.task USING btree (workspace_id, status, due_at) WHERE (deleted_at IS NULL);


--
-- Name: uq_notification_dedupe_present; Type: INDEX; Schema: workflow; Owner: -
--

CREATE UNIQUE INDEX uq_notification_dedupe_present ON workflow.notification USING btree (workspace_id, recipient_user_ref_id, dedupe_key) WHERE (dedupe_key IS NOT NULL);


--
-- Name: uq_task_visit_follow_up; Type: INDEX; Schema: workflow; Owner: -
--

CREATE UNIQUE INDEX uq_task_visit_follow_up ON workflow.task USING btree (workspace_id, source_visit_id) WHERE ((task_type = 'visit_follow_up'::text) AND (source_visit_id IS NOT NULL) AND (deleted_at IS NULL));


--
-- Name: action_item trg_action_item_touch; Type: TRIGGER; Schema: activity; Owner: -
--

CREATE TRIGGER trg_action_item_touch BEFORE UPDATE ON activity.action_item FOR EACH ROW EXECUTE FUNCTION common.touch_updated_at();


--
-- Name: visit trg_visit_touch; Type: TRIGGER; Schema: activity; Owner: -
--

CREATE TRIGGER trg_visit_touch BEFORE UPDATE ON activity.visit FOR EACH ROW EXECUTE FUNCTION common.touch_updated_at();


--
-- Name: metric_definition trg_metric_definition_touch; Type: TRIGGER; Schema: config; Owner: -
--

CREATE TRIGGER trg_metric_definition_touch BEFORE UPDATE ON config.metric_definition FOR EACH ROW EXECUTE FUNCTION common.touch_runtime_updated_at();


--
-- Name: prompt_template trg_prompt_template_touch; Type: TRIGGER; Schema: config; Owner: -
--

CREATE TRIGGER trg_prompt_template_touch BEFORE UPDATE ON config.prompt_template FOR EACH ROW EXECUTE FUNCTION common.touch_runtime_updated_at();


--
-- Name: sales_competency_framework trg_sales_competency_framework_touch; Type: TRIGGER; Schema: config; Owner: -
--

CREATE TRIGGER trg_sales_competency_framework_touch BEFORE UPDATE ON config.sales_competency_framework FOR EACH ROW EXECUTE FUNCTION common.touch_runtime_updated_at();


--
-- Name: contact trg_contact_touch; Type: TRIGGER; Schema: crm; Owner: -
--

CREATE TRIGGER trg_contact_touch BEFORE UPDATE ON crm.contact FOR EACH ROW EXECUTE FUNCTION common.touch_updated_at();


--
-- Name: customer trg_customer_touch; Type: TRIGGER; Schema: crm; Owner: -
--

CREATE TRIGGER trg_customer_touch BEFORE UPDATE ON crm.customer FOR EACH ROW EXECUTE FUNCTION common.touch_updated_at();


--
-- Name: opportunity trg_opportunity_touch; Type: TRIGGER; Schema: crm; Owner: -
--

CREATE TRIGGER trg_opportunity_touch BEFORE UPDATE ON crm.opportunity FOR EACH ROW EXECUTE FUNCTION common.touch_updated_at();


--
-- Name: recommendation trg_recommendation_touch; Type: TRIGGER; Schema: insight; Owner: -
--

CREATE TRIGGER trg_recommendation_touch BEFORE UPDATE ON insight.recommendation FOR EACH ROW EXECUTE FUNCTION common.touch_updated_at();


--
-- Name: risk trg_risk_touch; Type: TRIGGER; Schema: insight; Owner: -
--

CREATE TRIGGER trg_risk_touch BEFORE UPDATE ON insight.risk FOR EACH ROW EXECUTE FUNCTION common.touch_updated_at();


--
-- Name: sales_competency_review trg_sales_competency_review_touch; Type: TRIGGER; Schema: insight; Owner: -
--

CREATE TRIGGER trg_sales_competency_review_touch BEFORE UPDATE ON insight.sales_competency_review FOR EACH ROW EXECUTE FUNCTION common.touch_runtime_updated_at();


--
-- Name: job trg_job_touch; Type: TRIGGER; Schema: ops; Owner: -
--

CREATE TRIGGER trg_job_touch BEFORE UPDATE ON ops.job FOR EACH ROW EXECUTE FUNCTION common.touch_runtime_updated_at();


--
-- Name: identity_binding trg_identity_binding_touch; Type: TRIGGER; Schema: platform; Owner: -
--

CREATE TRIGGER trg_identity_binding_touch BEFORE UPDATE ON platform.identity_binding FOR EACH ROW EXECUTE FUNCTION common.touch_runtime_updated_at();


--
-- Name: team trg_team_touch; Type: TRIGGER; Schema: platform; Owner: -
--

CREATE TRIGGER trg_team_touch BEFORE UPDATE ON platform.team FOR EACH ROW EXECUTE FUNCTION common.touch_updated_at();


--
-- Name: user_ref trg_user_ref_touch; Type: TRIGGER; Schema: platform; Owner: -
--

CREATE TRIGGER trg_user_ref_touch BEFORE UPDATE ON platform.user_ref FOR EACH ROW EXECUTE FUNCTION common.touch_updated_at();


--
-- Name: workspace trg_workspace_touch; Type: TRIGGER; Schema: platform; Owner: -
--

CREATE TRIGGER trg_workspace_touch BEFORE UPDATE ON platform.workspace FOR EACH ROW EXECUTE FUNCTION common.touch_updated_at();


--
-- Name: task trg_task_touch; Type: TRIGGER; Schema: workflow; Owner: -
--

CREATE TRIGGER trg_task_touch BEFORE UPDATE ON workflow.task FOR EACH ROW EXECUTE FUNCTION common.touch_updated_at();


--
-- Name: action_item action_item_created_by_user_ref_id_fkey; Type: FK CONSTRAINT; Schema: activity; Owner: -
--

ALTER TABLE ONLY activity.action_item
    ADD CONSTRAINT action_item_created_by_user_ref_id_fkey FOREIGN KEY (created_by_user_ref_id) REFERENCES platform.user_ref(id);


--
-- Name: action_item action_item_customer_id_fkey; Type: FK CONSTRAINT; Schema: activity; Owner: -
--

ALTER TABLE ONLY activity.action_item
    ADD CONSTRAINT action_item_customer_id_fkey FOREIGN KEY (customer_id) REFERENCES crm.customer(id);


--
-- Name: action_item action_item_opportunity_id_fkey; Type: FK CONSTRAINT; Schema: activity; Owner: -
--

ALTER TABLE ONLY activity.action_item
    ADD CONSTRAINT action_item_opportunity_id_fkey FOREIGN KEY (opportunity_id) REFERENCES crm.opportunity(id);


--
-- Name: action_item action_item_owner_user_ref_id_fkey; Type: FK CONSTRAINT; Schema: activity; Owner: -
--

ALTER TABLE ONLY activity.action_item
    ADD CONSTRAINT action_item_owner_user_ref_id_fkey FOREIGN KEY (owner_user_ref_id) REFERENCES platform.user_ref(id);


--
-- Name: action_item action_item_source_visit_id_fkey; Type: FK CONSTRAINT; Schema: activity; Owner: -
--

ALTER TABLE ONLY activity.action_item
    ADD CONSTRAINT action_item_source_visit_id_fkey FOREIGN KEY (source_visit_id) REFERENCES activity.visit(id);


--
-- Name: action_item action_item_workspace_id_fkey; Type: FK CONSTRAINT; Schema: activity; Owner: -
--

ALTER TABLE ONLY activity.action_item
    ADD CONSTRAINT action_item_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: visit visit_audio_asset_id_fkey; Type: FK CONSTRAINT; Schema: activity; Owner: -
--

ALTER TABLE ONLY activity.visit
    ADD CONSTRAINT visit_audio_asset_id_fkey FOREIGN KEY (audio_asset_id) REFERENCES ops.file_asset(id);


--
-- Name: visit visit_confirmed_by_user_ref_id_fkey; Type: FK CONSTRAINT; Schema: activity; Owner: -
--

ALTER TABLE ONLY activity.visit
    ADD CONSTRAINT visit_confirmed_by_user_ref_id_fkey FOREIGN KEY (confirmed_by_user_ref_id) REFERENCES platform.user_ref(id);


--
-- Name: visit_contact visit_contact_contact_id_fkey; Type: FK CONSTRAINT; Schema: activity; Owner: -
--

ALTER TABLE ONLY activity.visit_contact
    ADD CONSTRAINT visit_contact_contact_id_fkey FOREIGN KEY (contact_id) REFERENCES crm.contact(id);


--
-- Name: visit_contact visit_contact_visit_id_fkey; Type: FK CONSTRAINT; Schema: activity; Owner: -
--

ALTER TABLE ONLY activity.visit_contact
    ADD CONSTRAINT visit_contact_visit_id_fkey FOREIGN KEY (visit_id) REFERENCES activity.visit(id) ON DELETE CASCADE;


--
-- Name: visit_contact visit_contact_workspace_id_fkey; Type: FK CONSTRAINT; Schema: activity; Owner: -
--

ALTER TABLE ONLY activity.visit_contact
    ADD CONSTRAINT visit_contact_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: visit visit_customer_id_fkey; Type: FK CONSTRAINT; Schema: activity; Owner: -
--

ALTER TABLE ONLY activity.visit
    ADD CONSTRAINT visit_customer_id_fkey FOREIGN KEY (customer_id) REFERENCES crm.customer(id);


--
-- Name: visit_field_value visit_field_value_field_definition_id_fkey; Type: FK CONSTRAINT; Schema: activity; Owner: -
--

ALTER TABLE ONLY activity.visit_field_value
    ADD CONSTRAINT visit_field_value_field_definition_id_fkey FOREIGN KEY (field_definition_id) REFERENCES config.field_definition(id);


--
-- Name: visit_field_value visit_field_value_visit_id_fkey; Type: FK CONSTRAINT; Schema: activity; Owner: -
--

ALTER TABLE ONLY activity.visit_field_value
    ADD CONSTRAINT visit_field_value_visit_id_fkey FOREIGN KEY (visit_id) REFERENCES activity.visit(id) ON DELETE CASCADE;


--
-- Name: visit_field_value visit_field_value_workspace_id_fkey; Type: FK CONSTRAINT; Schema: activity; Owner: -
--

ALTER TABLE ONLY activity.visit_field_value
    ADD CONSTRAINT visit_field_value_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: visit visit_form_version_id_fkey; Type: FK CONSTRAINT; Schema: activity; Owner: -
--

ALTER TABLE ONLY activity.visit
    ADD CONSTRAINT visit_form_version_id_fkey FOREIGN KEY (form_version_id) REFERENCES config.form_version(id);


--
-- Name: visit visit_opportunity_id_fkey; Type: FK CONSTRAINT; Schema: activity; Owner: -
--

ALTER TABLE ONLY activity.visit
    ADD CONSTRAINT visit_opportunity_id_fkey FOREIGN KEY (opportunity_id) REFERENCES crm.opportunity(id);


--
-- Name: visit visit_recorder_team_id_fkey; Type: FK CONSTRAINT; Schema: activity; Owner: -
--

ALTER TABLE ONLY activity.visit
    ADD CONSTRAINT visit_recorder_team_id_fkey FOREIGN KEY (recorder_team_id) REFERENCES platform.team(id);


--
-- Name: visit visit_recorder_user_ref_id_fkey; Type: FK CONSTRAINT; Schema: activity; Owner: -
--

ALTER TABLE ONLY activity.visit
    ADD CONSTRAINT visit_recorder_user_ref_id_fkey FOREIGN KEY (recorder_user_ref_id) REFERENCES platform.user_ref(id);


--
-- Name: visit visit_source_artifact_id_fkey; Type: FK CONSTRAINT; Schema: activity; Owner: -
--

ALTER TABLE ONLY activity.visit
    ADD CONSTRAINT visit_source_artifact_id_fkey FOREIGN KEY (source_artifact_id) REFERENCES agent.artifact(id);


--
-- Name: visit visit_workspace_id_fkey; Type: FK CONSTRAINT; Schema: activity; Owner: -
--

ALTER TABLE ONLY activity.visit
    ADD CONSTRAINT visit_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: artifact artifact_conversation_id_fkey; Type: FK CONSTRAINT; Schema: agent; Owner: -
--

ALTER TABLE ONLY agent.artifact
    ADD CONSTRAINT artifact_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES agent.conversation(id);


--
-- Name: artifact artifact_created_by_user_ref_id_fkey; Type: FK CONSTRAINT; Schema: agent; Owner: -
--

ALTER TABLE ONLY agent.artifact
    ADD CONSTRAINT artifact_created_by_user_ref_id_fkey FOREIGN KEY (created_by_user_ref_id) REFERENCES platform.user_ref(id);


--
-- Name: artifact artifact_rule_set_id_fkey; Type: FK CONSTRAINT; Schema: agent; Owner: -
--

ALTER TABLE ONLY agent.artifact
    ADD CONSTRAINT artifact_rule_set_id_fkey FOREIGN KEY (rule_set_id) REFERENCES config.rule_set(id);


--
-- Name: artifact artifact_run_id_fkey; Type: FK CONSTRAINT; Schema: agent; Owner: -
--

ALTER TABLE ONLY agent.artifact
    ADD CONSTRAINT artifact_run_id_fkey FOREIGN KEY (run_id) REFERENCES agent.run(id);


--
-- Name: artifact artifact_supersedes_artifact_id_fkey; Type: FK CONSTRAINT; Schema: agent; Owner: -
--

ALTER TABLE ONLY agent.artifact
    ADD CONSTRAINT artifact_supersedes_artifact_id_fkey FOREIGN KEY (supersedes_artifact_id) REFERENCES agent.artifact(id);


--
-- Name: artifact artifact_workspace_id_fkey; Type: FK CONSTRAINT; Schema: agent; Owner: -
--

ALTER TABLE ONLY agent.artifact
    ADD CONSTRAINT artifact_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: confirmation confirmation_artifact_id_fkey; Type: FK CONSTRAINT; Schema: agent; Owner: -
--

ALTER TABLE ONLY agent.confirmation
    ADD CONSTRAINT confirmation_artifact_id_fkey FOREIGN KEY (artifact_id) REFERENCES agent.artifact(id);


--
-- Name: confirmation confirmation_confirmer_user_ref_id_fkey; Type: FK CONSTRAINT; Schema: agent; Owner: -
--

ALTER TABLE ONLY agent.confirmation
    ADD CONSTRAINT confirmation_confirmer_user_ref_id_fkey FOREIGN KEY (confirmer_user_ref_id) REFERENCES platform.user_ref(id);


--
-- Name: confirmation confirmation_workspace_id_fkey; Type: FK CONSTRAINT; Schema: agent; Owner: -
--

ALTER TABLE ONLY agent.confirmation
    ADD CONSTRAINT confirmation_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: conversation conversation_user_ref_id_fkey; Type: FK CONSTRAINT; Schema: agent; Owner: -
--

ALTER TABLE ONLY agent.conversation
    ADD CONSTRAINT conversation_user_ref_id_fkey FOREIGN KEY (user_ref_id) REFERENCES platform.user_ref(id);


--
-- Name: conversation conversation_workspace_id_fkey; Type: FK CONSTRAINT; Schema: agent; Owner: -
--

ALTER TABLE ONLY agent.conversation
    ADD CONSTRAINT conversation_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: message message_conversation_id_fkey; Type: FK CONSTRAINT; Schema: agent; Owner: -
--

ALTER TABLE ONLY agent.message
    ADD CONSTRAINT message_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES agent.conversation(id) ON DELETE CASCADE;


--
-- Name: message message_file_asset_id_fkey; Type: FK CONSTRAINT; Schema: agent; Owner: -
--

ALTER TABLE ONLY agent.message
    ADD CONSTRAINT message_file_asset_id_fkey FOREIGN KEY (file_asset_id) REFERENCES ops.file_asset(id);


--
-- Name: message message_workspace_id_fkey; Type: FK CONSTRAINT; Schema: agent; Owner: -
--

ALTER TABLE ONLY agent.message
    ADD CONSTRAINT message_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: model_invocation model_invocation_run_id_fkey; Type: FK CONSTRAINT; Schema: agent; Owner: -
--

ALTER TABLE ONLY agent.model_invocation
    ADD CONSTRAINT model_invocation_run_id_fkey FOREIGN KEY (run_id) REFERENCES agent.run(id) ON DELETE CASCADE;


--
-- Name: model_invocation model_invocation_run_step_id_fkey; Type: FK CONSTRAINT; Schema: agent; Owner: -
--

ALTER TABLE ONLY agent.model_invocation
    ADD CONSTRAINT model_invocation_run_step_id_fkey FOREIGN KEY (run_step_id) REFERENCES agent.run_step(id) ON DELETE SET NULL;


--
-- Name: model_invocation model_invocation_workspace_id_fkey; Type: FK CONSTRAINT; Schema: agent; Owner: -
--

ALTER TABLE ONLY agent.model_invocation
    ADD CONSTRAINT model_invocation_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: run run_conversation_id_fkey; Type: FK CONSTRAINT; Schema: agent; Owner: -
--

ALTER TABLE ONLY agent.run
    ADD CONSTRAINT run_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES agent.conversation(id);


--
-- Name: run run_orchestrator_definition_id_fkey; Type: FK CONSTRAINT; Schema: agent; Owner: -
--

ALTER TABLE ONLY agent.run
    ADD CONSTRAINT run_orchestrator_definition_id_fkey FOREIGN KEY (orchestrator_definition_id) REFERENCES config.agent_definition(id);


--
-- Name: run_step run_step_agent_definition_id_fkey; Type: FK CONSTRAINT; Schema: agent; Owner: -
--

ALTER TABLE ONLY agent.run_step
    ADD CONSTRAINT run_step_agent_definition_id_fkey FOREIGN KEY (agent_definition_id) REFERENCES config.agent_definition(id);


--
-- Name: run_step run_step_run_id_fkey; Type: FK CONSTRAINT; Schema: agent; Owner: -
--

ALTER TABLE ONLY agent.run_step
    ADD CONSTRAINT run_step_run_id_fkey FOREIGN KEY (run_id) REFERENCES agent.run(id) ON DELETE CASCADE;


--
-- Name: run_step run_step_workspace_id_fkey; Type: FK CONSTRAINT; Schema: agent; Owner: -
--

ALTER TABLE ONLY agent.run_step
    ADD CONSTRAINT run_step_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: run run_trigger_message_id_fkey; Type: FK CONSTRAINT; Schema: agent; Owner: -
--

ALTER TABLE ONLY agent.run
    ADD CONSTRAINT run_trigger_message_id_fkey FOREIGN KEY (trigger_message_id) REFERENCES agent.message(id);


--
-- Name: run run_workspace_id_fkey; Type: FK CONSTRAINT; Schema: agent; Owner: -
--

ALTER TABLE ONLY agent.run
    ADD CONSTRAINT run_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: tool_invocation tool_invocation_run_id_fkey; Type: FK CONSTRAINT; Schema: agent; Owner: -
--

ALTER TABLE ONLY agent.tool_invocation
    ADD CONSTRAINT tool_invocation_run_id_fkey FOREIGN KEY (run_id) REFERENCES agent.run(id) ON DELETE CASCADE;


--
-- Name: tool_invocation tool_invocation_run_step_id_fkey; Type: FK CONSTRAINT; Schema: agent; Owner: -
--

ALTER TABLE ONLY agent.tool_invocation
    ADD CONSTRAINT tool_invocation_run_step_id_fkey FOREIGN KEY (run_step_id) REFERENCES agent.run_step(id) ON DELETE SET NULL;


--
-- Name: tool_invocation tool_invocation_workspace_id_fkey; Type: FK CONSTRAINT; Schema: agent; Owner: -
--

ALTER TABLE ONLY agent.tool_invocation
    ADD CONSTRAINT tool_invocation_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: agent_definition agent_definition_workspace_id_fkey; Type: FK CONSTRAINT; Schema: config; Owner: -
--

ALTER TABLE ONLY config.agent_definition
    ADD CONSTRAINT agent_definition_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: agent_runtime_config agent_runtime_config_updated_by_user_ref_id_fkey; Type: FK CONSTRAINT; Schema: config; Owner: -
--

ALTER TABLE ONLY config.agent_runtime_config
    ADD CONSTRAINT agent_runtime_config_updated_by_user_ref_id_fkey FOREIGN KEY (updated_by_user_ref_id) REFERENCES platform.user_ref(id);


--
-- Name: agent_runtime_config agent_runtime_config_workspace_id_fkey; Type: FK CONSTRAINT; Schema: config; Owner: -
--

ALTER TABLE ONLY config.agent_runtime_config
    ADD CONSTRAINT agent_runtime_config_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: agent_runtime_release agent_runtime_release_created_by_user_ref_id_fkey; Type: FK CONSTRAINT; Schema: config; Owner: -
--

ALTER TABLE ONLY config.agent_runtime_release
    ADD CONSTRAINT agent_runtime_release_created_by_user_ref_id_fkey FOREIGN KEY (created_by_user_ref_id) REFERENCES platform.user_ref(id);


--
-- Name: agent_runtime_release agent_runtime_release_workspace_id_fkey; Type: FK CONSTRAINT; Schema: config; Owner: -
--

ALTER TABLE ONLY config.agent_runtime_release
    ADD CONSTRAINT agent_runtime_release_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: dictionary_item dictionary_item_dictionary_id_fkey; Type: FK CONSTRAINT; Schema: config; Owner: -
--

ALTER TABLE ONLY config.dictionary_item
    ADD CONSTRAINT dictionary_item_dictionary_id_fkey FOREIGN KEY (dictionary_id) REFERENCES config.dictionary(id) ON DELETE CASCADE;


--
-- Name: dictionary dictionary_workspace_id_fkey; Type: FK CONSTRAINT; Schema: config; Owner: -
--

ALTER TABLE ONLY config.dictionary
    ADD CONSTRAINT dictionary_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: field_definition field_definition_workspace_id_fkey; Type: FK CONSTRAINT; Schema: config; Owner: -
--

ALTER TABLE ONLY config.field_definition
    ADD CONSTRAINT field_definition_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: form_definition form_definition_workspace_id_fkey; Type: FK CONSTRAINT; Schema: config; Owner: -
--

ALTER TABLE ONLY config.form_definition
    ADD CONSTRAINT form_definition_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: form_version form_version_created_by_user_ref_id_fkey; Type: FK CONSTRAINT; Schema: config; Owner: -
--

ALTER TABLE ONLY config.form_version
    ADD CONSTRAINT form_version_created_by_user_ref_id_fkey FOREIGN KEY (created_by_user_ref_id) REFERENCES platform.user_ref(id);


--
-- Name: form_version_field form_version_field_field_definition_id_fkey; Type: FK CONSTRAINT; Schema: config; Owner: -
--

ALTER TABLE ONLY config.form_version_field
    ADD CONSTRAINT form_version_field_field_definition_id_fkey FOREIGN KEY (field_definition_id) REFERENCES config.field_definition(id);


--
-- Name: form_version_field form_version_field_form_version_id_fkey; Type: FK CONSTRAINT; Schema: config; Owner: -
--

ALTER TABLE ONLY config.form_version_field
    ADD CONSTRAINT form_version_field_form_version_id_fkey FOREIGN KEY (form_version_id) REFERENCES config.form_version(id) ON DELETE CASCADE;


--
-- Name: form_version form_version_form_definition_id_fkey; Type: FK CONSTRAINT; Schema: config; Owner: -
--

ALTER TABLE ONLY config.form_version
    ADD CONSTRAINT form_version_form_definition_id_fkey FOREIGN KEY (form_definition_id) REFERENCES config.form_definition(id) ON DELETE CASCADE;


--
-- Name: metric_definition metric_definition_workspace_id_fkey; Type: FK CONSTRAINT; Schema: config; Owner: -
--

ALTER TABLE ONLY config.metric_definition
    ADD CONSTRAINT metric_definition_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: prompt_template prompt_template_workspace_id_fkey; Type: FK CONSTRAINT; Schema: config; Owner: -
--

ALTER TABLE ONLY config.prompt_template
    ADD CONSTRAINT prompt_template_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: rule_set rule_set_created_by_user_ref_id_fkey; Type: FK CONSTRAINT; Schema: config; Owner: -
--

ALTER TABLE ONLY config.rule_set
    ADD CONSTRAINT rule_set_created_by_user_ref_id_fkey FOREIGN KEY (created_by_user_ref_id) REFERENCES platform.user_ref(id);


--
-- Name: rule_set rule_set_workspace_id_fkey; Type: FK CONSTRAINT; Schema: config; Owner: -
--

ALTER TABLE ONLY config.rule_set
    ADD CONSTRAINT rule_set_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: sales_competency_framework sales_competency_framework_workspace_id_fkey; Type: FK CONSTRAINT; Schema: config; Owner: -
--

ALTER TABLE ONLY config.sales_competency_framework
    ADD CONSTRAINT sales_competency_framework_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: contact contact_created_by_user_ref_id_fkey; Type: FK CONSTRAINT; Schema: crm; Owner: -
--

ALTER TABLE ONLY crm.contact
    ADD CONSTRAINT contact_created_by_user_ref_id_fkey FOREIGN KEY (created_by_user_ref_id) REFERENCES platform.user_ref(id);


--
-- Name: contact contact_customer_id_fkey; Type: FK CONSTRAINT; Schema: crm; Owner: -
--

ALTER TABLE ONLY crm.contact
    ADD CONSTRAINT contact_customer_id_fkey FOREIGN KEY (customer_id) REFERENCES crm.customer(id);


--
-- Name: contact contact_workspace_id_fkey; Type: FK CONSTRAINT; Schema: crm; Owner: -
--

ALTER TABLE ONLY crm.contact
    ADD CONSTRAINT contact_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: customer_assignment customer_assignment_assigned_by_user_ref_id_fkey; Type: FK CONSTRAINT; Schema: crm; Owner: -
--

ALTER TABLE ONLY crm.customer_assignment
    ADD CONSTRAINT customer_assignment_assigned_by_user_ref_id_fkey FOREIGN KEY (assigned_by_user_ref_id) REFERENCES platform.user_ref(id);


--
-- Name: customer_assignment customer_assignment_assigned_team_id_fkey; Type: FK CONSTRAINT; Schema: crm; Owner: -
--

ALTER TABLE ONLY crm.customer_assignment
    ADD CONSTRAINT customer_assignment_assigned_team_id_fkey FOREIGN KEY (assigned_team_id) REFERENCES platform.team(id);


--
-- Name: customer_assignment customer_assignment_assigned_user_ref_id_fkey; Type: FK CONSTRAINT; Schema: crm; Owner: -
--

ALTER TABLE ONLY crm.customer_assignment
    ADD CONSTRAINT customer_assignment_assigned_user_ref_id_fkey FOREIGN KEY (assigned_user_ref_id) REFERENCES platform.user_ref(id);


--
-- Name: customer_assignment customer_assignment_customer_id_fkey; Type: FK CONSTRAINT; Schema: crm; Owner: -
--

ALTER TABLE ONLY crm.customer_assignment
    ADD CONSTRAINT customer_assignment_customer_id_fkey FOREIGN KEY (customer_id) REFERENCES crm.customer(id);


--
-- Name: customer_assignment customer_assignment_workspace_id_fkey; Type: FK CONSTRAINT; Schema: crm; Owner: -
--

ALTER TABLE ONLY crm.customer_assignment
    ADD CONSTRAINT customer_assignment_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: customer customer_created_by_user_ref_id_fkey; Type: FK CONSTRAINT; Schema: crm; Owner: -
--

ALTER TABLE ONLY crm.customer
    ADD CONSTRAINT customer_created_by_user_ref_id_fkey FOREIGN KEY (created_by_user_ref_id) REFERENCES platform.user_ref(id);


--
-- Name: customer_duplicate_candidate customer_duplicate_candidate_candidate_customer_id_fkey; Type: FK CONSTRAINT; Schema: crm; Owner: -
--

ALTER TABLE ONLY crm.customer_duplicate_candidate
    ADD CONSTRAINT customer_duplicate_candidate_candidate_customer_id_fkey FOREIGN KEY (candidate_customer_id) REFERENCES crm.customer(id);


--
-- Name: customer_duplicate_candidate customer_duplicate_candidate_customer_id_fkey; Type: FK CONSTRAINT; Schema: crm; Owner: -
--

ALTER TABLE ONLY crm.customer_duplicate_candidate
    ADD CONSTRAINT customer_duplicate_candidate_customer_id_fkey FOREIGN KEY (customer_id) REFERENCES crm.customer(id);


--
-- Name: customer_duplicate_candidate customer_duplicate_candidate_reviewed_by_user_ref_id_fkey; Type: FK CONSTRAINT; Schema: crm; Owner: -
--

ALTER TABLE ONLY crm.customer_duplicate_candidate
    ADD CONSTRAINT customer_duplicate_candidate_reviewed_by_user_ref_id_fkey FOREIGN KEY (reviewed_by_user_ref_id) REFERENCES platform.user_ref(id);


--
-- Name: customer_duplicate_candidate customer_duplicate_candidate_workspace_id_fkey; Type: FK CONSTRAINT; Schema: crm; Owner: -
--

ALTER TABLE ONLY crm.customer_duplicate_candidate
    ADD CONSTRAINT customer_duplicate_candidate_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: customer customer_owner_team_id_fkey; Type: FK CONSTRAINT; Schema: crm; Owner: -
--

ALTER TABLE ONLY crm.customer
    ADD CONSTRAINT customer_owner_team_id_fkey FOREIGN KEY (owner_team_id) REFERENCES platform.team(id);


--
-- Name: customer customer_owner_user_ref_id_fkey; Type: FK CONSTRAINT; Schema: crm; Owner: -
--

ALTER TABLE ONLY crm.customer
    ADD CONSTRAINT customer_owner_user_ref_id_fkey FOREIGN KEY (owner_user_ref_id) REFERENCES platform.user_ref(id);


--
-- Name: customer_product customer_product_customer_id_fkey; Type: FK CONSTRAINT; Schema: crm; Owner: -
--

ALTER TABLE ONLY crm.customer_product
    ADD CONSTRAINT customer_product_customer_id_fkey FOREIGN KEY (customer_id) REFERENCES crm.customer(id);


--
-- Name: customer_product customer_product_opportunity_id_fkey; Type: FK CONSTRAINT; Schema: crm; Owner: -
--

ALTER TABLE ONLY crm.customer_product
    ADD CONSTRAINT customer_product_opportunity_id_fkey FOREIGN KEY (opportunity_id) REFERENCES crm.opportunity(id);


--
-- Name: customer_product customer_product_product_id_fkey; Type: FK CONSTRAINT; Schema: crm; Owner: -
--

ALTER TABLE ONLY crm.customer_product
    ADD CONSTRAINT customer_product_product_id_fkey FOREIGN KEY (product_id) REFERENCES crm.product(id);


--
-- Name: customer_product customer_product_workspace_id_fkey; Type: FK CONSTRAINT; Schema: crm; Owner: -
--

ALTER TABLE ONLY crm.customer_product
    ADD CONSTRAINT customer_product_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: customer customer_workspace_id_fkey; Type: FK CONSTRAINT; Schema: crm; Owner: -
--

ALTER TABLE ONLY crm.customer
    ADD CONSTRAINT customer_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: opportunity opportunity_created_by_user_ref_id_fkey; Type: FK CONSTRAINT; Schema: crm; Owner: -
--

ALTER TABLE ONLY crm.opportunity
    ADD CONSTRAINT opportunity_created_by_user_ref_id_fkey FOREIGN KEY (created_by_user_ref_id) REFERENCES platform.user_ref(id);


--
-- Name: opportunity opportunity_customer_id_fkey; Type: FK CONSTRAINT; Schema: crm; Owner: -
--

ALTER TABLE ONLY crm.opportunity
    ADD CONSTRAINT opportunity_customer_id_fkey FOREIGN KEY (customer_id) REFERENCES crm.customer(id);


--
-- Name: opportunity opportunity_owner_team_id_fkey; Type: FK CONSTRAINT; Schema: crm; Owner: -
--

ALTER TABLE ONLY crm.opportunity
    ADD CONSTRAINT opportunity_owner_team_id_fkey FOREIGN KEY (owner_team_id) REFERENCES platform.team(id);


--
-- Name: opportunity opportunity_owner_user_ref_id_fkey; Type: FK CONSTRAINT; Schema: crm; Owner: -
--

ALTER TABLE ONLY crm.opportunity
    ADD CONSTRAINT opportunity_owner_user_ref_id_fkey FOREIGN KEY (owner_user_ref_id) REFERENCES platform.user_ref(id);


--
-- Name: opportunity_participant opportunity_participant_opportunity_id_fkey; Type: FK CONSTRAINT; Schema: crm; Owner: -
--

ALTER TABLE ONLY crm.opportunity_participant
    ADD CONSTRAINT opportunity_participant_opportunity_id_fkey FOREIGN KEY (opportunity_id) REFERENCES crm.opportunity(id) ON DELETE CASCADE;


--
-- Name: opportunity_participant opportunity_participant_user_ref_id_fkey; Type: FK CONSTRAINT; Schema: crm; Owner: -
--

ALTER TABLE ONLY crm.opportunity_participant
    ADD CONSTRAINT opportunity_participant_user_ref_id_fkey FOREIGN KEY (user_ref_id) REFERENCES platform.user_ref(id);


--
-- Name: opportunity_participant opportunity_participant_workspace_id_fkey; Type: FK CONSTRAINT; Schema: crm; Owner: -
--

ALTER TABLE ONLY crm.opportunity_participant
    ADD CONSTRAINT opportunity_participant_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: opportunity opportunity_workspace_id_fkey; Type: FK CONSTRAINT; Schema: crm; Owner: -
--

ALTER TABLE ONLY crm.opportunity
    ADD CONSTRAINT opportunity_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: product product_workspace_id_fkey; Type: FK CONSTRAINT; Schema: crm; Owner: -
--

ALTER TABLE ONLY crm.product
    ADD CONSTRAINT product_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: quadrant_score quadrant_score_customer_id_fkey; Type: FK CONSTRAINT; Schema: insight; Owner: -
--

ALTER TABLE ONLY insight.quadrant_score
    ADD CONSTRAINT quadrant_score_customer_id_fkey FOREIGN KEY (customer_id) REFERENCES crm.customer(id);


--
-- Name: quadrant_score quadrant_score_rule_set_id_fkey; Type: FK CONSTRAINT; Schema: insight; Owner: -
--

ALTER TABLE ONLY insight.quadrant_score
    ADD CONSTRAINT quadrant_score_rule_set_id_fkey FOREIGN KEY (rule_set_id) REFERENCES config.rule_set(id);


--
-- Name: quadrant_score quadrant_score_workspace_id_fkey; Type: FK CONSTRAINT; Schema: insight; Owner: -
--

ALTER TABLE ONLY insight.quadrant_score
    ADD CONSTRAINT quadrant_score_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: recommendation recommendation_acted_by_user_ref_id_fkey; Type: FK CONSTRAINT; Schema: insight; Owner: -
--

ALTER TABLE ONLY insight.recommendation
    ADD CONSTRAINT recommendation_acted_by_user_ref_id_fkey FOREIGN KEY (acted_by_user_ref_id) REFERENCES platform.user_ref(id);


--
-- Name: recommendation recommendation_customer_id_fkey; Type: FK CONSTRAINT; Schema: insight; Owner: -
--

ALTER TABLE ONLY insight.recommendation
    ADD CONSTRAINT recommendation_customer_id_fkey FOREIGN KEY (customer_id) REFERENCES crm.customer(id);


--
-- Name: recommendation recommendation_opportunity_id_fkey; Type: FK CONSTRAINT; Schema: insight; Owner: -
--

ALTER TABLE ONLY insight.recommendation
    ADD CONSTRAINT recommendation_opportunity_id_fkey FOREIGN KEY (opportunity_id) REFERENCES crm.opportunity(id);


--
-- Name: recommendation recommendation_product_id_fkey; Type: FK CONSTRAINT; Schema: insight; Owner: -
--

ALTER TABLE ONLY insight.recommendation
    ADD CONSTRAINT recommendation_product_id_fkey FOREIGN KEY (product_id) REFERENCES crm.product(id);


--
-- Name: recommendation recommendation_rule_set_id_fkey; Type: FK CONSTRAINT; Schema: insight; Owner: -
--

ALTER TABLE ONLY insight.recommendation
    ADD CONSTRAINT recommendation_rule_set_id_fkey FOREIGN KEY (rule_set_id) REFERENCES config.rule_set(id);


--
-- Name: recommendation recommendation_workspace_id_fkey; Type: FK CONSTRAINT; Schema: insight; Owner: -
--

ALTER TABLE ONLY insight.recommendation
    ADD CONSTRAINT recommendation_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: report report_generated_by_user_ref_id_fkey; Type: FK CONSTRAINT; Schema: insight; Owner: -
--

ALTER TABLE ONLY insight.report
    ADD CONSTRAINT report_generated_by_user_ref_id_fkey FOREIGN KEY (generated_by_user_ref_id) REFERENCES platform.user_ref(id);


--
-- Name: report report_rule_set_id_fkey; Type: FK CONSTRAINT; Schema: insight; Owner: -
--

ALTER TABLE ONLY insight.report
    ADD CONSTRAINT report_rule_set_id_fkey FOREIGN KEY (rule_set_id) REFERENCES config.rule_set(id);


--
-- Name: report report_workspace_id_fkey; Type: FK CONSTRAINT; Schema: insight; Owner: -
--

ALTER TABLE ONLY insight.report
    ADD CONSTRAINT report_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: risk risk_customer_id_fkey; Type: FK CONSTRAINT; Schema: insight; Owner: -
--

ALTER TABLE ONLY insight.risk
    ADD CONSTRAINT risk_customer_id_fkey FOREIGN KEY (customer_id) REFERENCES crm.customer(id);


--
-- Name: risk_event risk_event_actor_user_ref_id_fkey; Type: FK CONSTRAINT; Schema: insight; Owner: -
--

ALTER TABLE ONLY insight.risk_event
    ADD CONSTRAINT risk_event_actor_user_ref_id_fkey FOREIGN KEY (actor_user_ref_id) REFERENCES platform.user_ref(id);


--
-- Name: risk_event risk_event_risk_id_fkey; Type: FK CONSTRAINT; Schema: insight; Owner: -
--

ALTER TABLE ONLY insight.risk_event
    ADD CONSTRAINT risk_event_risk_id_fkey FOREIGN KEY (risk_id) REFERENCES insight.risk(id) ON DELETE CASCADE;


--
-- Name: risk_event risk_event_workspace_id_fkey; Type: FK CONSTRAINT; Schema: insight; Owner: -
--

ALTER TABLE ONLY insight.risk_event
    ADD CONSTRAINT risk_event_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: risk risk_opportunity_id_fkey; Type: FK CONSTRAINT; Schema: insight; Owner: -
--

ALTER TABLE ONLY insight.risk
    ADD CONSTRAINT risk_opportunity_id_fkey FOREIGN KEY (opportunity_id) REFERENCES crm.opportunity(id);


--
-- Name: risk risk_owner_team_id_fkey; Type: FK CONSTRAINT; Schema: insight; Owner: -
--

ALTER TABLE ONLY insight.risk
    ADD CONSTRAINT risk_owner_team_id_fkey FOREIGN KEY (owner_team_id) REFERENCES platform.team(id);


--
-- Name: risk risk_owner_user_ref_id_fkey; Type: FK CONSTRAINT; Schema: insight; Owner: -
--

ALTER TABLE ONLY insight.risk
    ADD CONSTRAINT risk_owner_user_ref_id_fkey FOREIGN KEY (owner_user_ref_id) REFERENCES platform.user_ref(id);


--
-- Name: risk risk_rule_set_id_fkey; Type: FK CONSTRAINT; Schema: insight; Owner: -
--

ALTER TABLE ONLY insight.risk
    ADD CONSTRAINT risk_rule_set_id_fkey FOREIGN KEY (rule_set_id) REFERENCES config.rule_set(id);


--
-- Name: risk risk_source_run_id_fkey; Type: FK CONSTRAINT; Schema: insight; Owner: -
--

ALTER TABLE ONLY insight.risk
    ADD CONSTRAINT risk_source_run_id_fkey FOREIGN KEY (source_run_id) REFERENCES agent.run(id) ON DELETE SET NULL;


--
-- Name: risk risk_source_visit_id_fkey; Type: FK CONSTRAINT; Schema: insight; Owner: -
--

ALTER TABLE ONLY insight.risk
    ADD CONSTRAINT risk_source_visit_id_fkey FOREIGN KEY (source_visit_id) REFERENCES activity.visit(id);


--
-- Name: risk risk_workspace_id_fkey; Type: FK CONSTRAINT; Schema: insight; Owner: -
--

ALTER TABLE ONLY insight.risk
    ADD CONSTRAINT risk_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: sales_competency_review sales_competency_review_subject_user_ref_id_fkey; Type: FK CONSTRAINT; Schema: insight; Owner: -
--

ALTER TABLE ONLY insight.sales_competency_review
    ADD CONSTRAINT sales_competency_review_subject_user_ref_id_fkey FOREIGN KEY (subject_user_ref_id) REFERENCES platform.user_ref(id);


--
-- Name: sales_competency_review sales_competency_review_workspace_id_fkey; Type: FK CONSTRAINT; Schema: insight; Owner: -
--

ALTER TABLE ONLY insight.sales_competency_review
    ADD CONSTRAINT sales_competency_review_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: audit_log audit_log_actor_user_ref_id_fkey; Type: FK CONSTRAINT; Schema: ops; Owner: -
--

ALTER TABLE ONLY ops.audit_log
    ADD CONSTRAINT audit_log_actor_user_ref_id_fkey FOREIGN KEY (actor_user_ref_id) REFERENCES platform.user_ref(id);


--
-- Name: audit_log audit_log_workspace_id_fkey; Type: FK CONSTRAINT; Schema: ops; Owner: -
--

ALTER TABLE ONLY ops.audit_log
    ADD CONSTRAINT audit_log_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: file_asset file_asset_uploaded_by_user_ref_id_fkey; Type: FK CONSTRAINT; Schema: ops; Owner: -
--

ALTER TABLE ONLY ops.file_asset
    ADD CONSTRAINT file_asset_uploaded_by_user_ref_id_fkey FOREIGN KEY (uploaded_by_user_ref_id) REFERENCES platform.user_ref(id);


--
-- Name: file_asset file_asset_workspace_id_fkey; Type: FK CONSTRAINT; Schema: ops; Owner: -
--

ALTER TABLE ONLY ops.file_asset
    ADD CONSTRAINT file_asset_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: idempotency_key idempotency_key_workspace_id_fkey; Type: FK CONSTRAINT; Schema: ops; Owner: -
--

ALTER TABLE ONLY ops.idempotency_key
    ADD CONSTRAINT idempotency_key_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: job job_workspace_id_fkey; Type: FK CONSTRAINT; Schema: ops; Owner: -
--

ALTER TABLE ONLY ops.job
    ADD CONSTRAINT job_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: outbox_event outbox_event_workspace_id_fkey; Type: FK CONSTRAINT; Schema: ops; Owner: -
--

ALTER TABLE ONLY ops.outbox_event
    ADD CONSTRAINT outbox_event_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: auth_session auth_session_user_ref_id_fkey; Type: FK CONSTRAINT; Schema: platform; Owner: -
--

ALTER TABLE ONLY platform.auth_session
    ADD CONSTRAINT auth_session_user_ref_id_fkey FOREIGN KEY (user_ref_id) REFERENCES platform.user_ref(id);


--
-- Name: auth_session auth_session_workspace_id_fkey; Type: FK CONSTRAINT; Schema: platform; Owner: -
--

ALTER TABLE ONLY platform.auth_session
    ADD CONSTRAINT auth_session_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: identity_binding identity_binding_user_ref_id_fkey; Type: FK CONSTRAINT; Schema: platform; Owner: -
--

ALTER TABLE ONLY platform.identity_binding
    ADD CONSTRAINT identity_binding_user_ref_id_fkey FOREIGN KEY (user_ref_id) REFERENCES platform.user_ref(id);


--
-- Name: identity_binding identity_binding_workspace_id_fkey; Type: FK CONSTRAINT; Schema: platform; Owner: -
--

ALTER TABLE ONLY platform.identity_binding
    ADD CONSTRAINT identity_binding_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: role_binding role_binding_team_id_fkey; Type: FK CONSTRAINT; Schema: platform; Owner: -
--

ALTER TABLE ONLY platform.role_binding
    ADD CONSTRAINT role_binding_team_id_fkey FOREIGN KEY (team_id) REFERENCES platform.team(id);


--
-- Name: role_binding role_binding_user_ref_id_fkey; Type: FK CONSTRAINT; Schema: platform; Owner: -
--

ALTER TABLE ONLY platform.role_binding
    ADD CONSTRAINT role_binding_user_ref_id_fkey FOREIGN KEY (user_ref_id) REFERENCES platform.user_ref(id);


--
-- Name: role_binding role_binding_workspace_id_fkey; Type: FK CONSTRAINT; Schema: platform; Owner: -
--

ALTER TABLE ONLY platform.role_binding
    ADD CONSTRAINT role_binding_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: team_membership team_membership_team_id_fkey; Type: FK CONSTRAINT; Schema: platform; Owner: -
--

ALTER TABLE ONLY platform.team_membership
    ADD CONSTRAINT team_membership_team_id_fkey FOREIGN KEY (team_id) REFERENCES platform.team(id);


--
-- Name: team_membership team_membership_user_ref_id_fkey; Type: FK CONSTRAINT; Schema: platform; Owner: -
--

ALTER TABLE ONLY platform.team_membership
    ADD CONSTRAINT team_membership_user_ref_id_fkey FOREIGN KEY (user_ref_id) REFERENCES platform.user_ref(id);


--
-- Name: team_membership team_membership_workspace_id_fkey; Type: FK CONSTRAINT; Schema: platform; Owner: -
--

ALTER TABLE ONLY platform.team_membership
    ADD CONSTRAINT team_membership_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: team team_parent_team_id_fkey; Type: FK CONSTRAINT; Schema: platform; Owner: -
--

ALTER TABLE ONLY platform.team
    ADD CONSTRAINT team_parent_team_id_fkey FOREIGN KEY (parent_team_id) REFERENCES platform.team(id);


--
-- Name: team team_workspace_id_fkey; Type: FK CONSTRAINT; Schema: platform; Owner: -
--

ALTER TABLE ONLY platform.team
    ADD CONSTRAINT team_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: user_ref user_ref_workspace_id_fkey; Type: FK CONSTRAINT; Schema: platform; Owner: -
--

ALTER TABLE ONLY platform.user_ref
    ADD CONSTRAINT user_ref_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: notification_delivery notification_delivery_notification_id_fkey; Type: FK CONSTRAINT; Schema: workflow; Owner: -
--

ALTER TABLE ONLY workflow.notification_delivery
    ADD CONSTRAINT notification_delivery_notification_id_fkey FOREIGN KEY (notification_id) REFERENCES workflow.notification(id) ON DELETE CASCADE;


--
-- Name: notification_delivery notification_delivery_workspace_id_fkey; Type: FK CONSTRAINT; Schema: workflow; Owner: -
--

ALTER TABLE ONLY workflow.notification_delivery
    ADD CONSTRAINT notification_delivery_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: notification notification_recipient_user_ref_id_fkey; Type: FK CONSTRAINT; Schema: workflow; Owner: -
--

ALTER TABLE ONLY workflow.notification
    ADD CONSTRAINT notification_recipient_user_ref_id_fkey FOREIGN KEY (recipient_user_ref_id) REFERENCES platform.user_ref(id);


--
-- Name: notification notification_workspace_id_fkey; Type: FK CONSTRAINT; Schema: workflow; Owner: -
--

ALTER TABLE ONLY workflow.notification
    ADD CONSTRAINT notification_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: task_assignee task_assignee_assignee_team_id_fkey; Type: FK CONSTRAINT; Schema: workflow; Owner: -
--

ALTER TABLE ONLY workflow.task_assignee
    ADD CONSTRAINT task_assignee_assignee_team_id_fkey FOREIGN KEY (assignee_team_id) REFERENCES platform.team(id);


--
-- Name: task_assignee task_assignee_assignee_user_ref_id_fkey; Type: FK CONSTRAINT; Schema: workflow; Owner: -
--

ALTER TABLE ONLY workflow.task_assignee
    ADD CONSTRAINT task_assignee_assignee_user_ref_id_fkey FOREIGN KEY (assignee_user_ref_id) REFERENCES platform.user_ref(id);


--
-- Name: task_assignee task_assignee_task_id_fkey; Type: FK CONSTRAINT; Schema: workflow; Owner: -
--

ALTER TABLE ONLY workflow.task_assignee
    ADD CONSTRAINT task_assignee_task_id_fkey FOREIGN KEY (task_id) REFERENCES workflow.task(id) ON DELETE CASCADE;


--
-- Name: task_assignee task_assignee_workspace_id_fkey; Type: FK CONSTRAINT; Schema: workflow; Owner: -
--

ALTER TABLE ONLY workflow.task_assignee
    ADD CONSTRAINT task_assignee_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: task task_creator_team_id_fkey; Type: FK CONSTRAINT; Schema: workflow; Owner: -
--

ALTER TABLE ONLY workflow.task
    ADD CONSTRAINT task_creator_team_id_fkey FOREIGN KEY (creator_team_id) REFERENCES platform.team(id);


--
-- Name: task task_creator_user_ref_id_fkey; Type: FK CONSTRAINT; Schema: workflow; Owner: -
--

ALTER TABLE ONLY workflow.task
    ADD CONSTRAINT task_creator_user_ref_id_fkey FOREIGN KEY (creator_user_ref_id) REFERENCES platform.user_ref(id);


--
-- Name: task task_customer_id_fkey; Type: FK CONSTRAINT; Schema: workflow; Owner: -
--

ALTER TABLE ONLY workflow.task
    ADD CONSTRAINT task_customer_id_fkey FOREIGN KEY (customer_id) REFERENCES crm.customer(id);


--
-- Name: task_event task_event_actor_user_ref_id_fkey; Type: FK CONSTRAINT; Schema: workflow; Owner: -
--

ALTER TABLE ONLY workflow.task_event
    ADD CONSTRAINT task_event_actor_user_ref_id_fkey FOREIGN KEY (actor_user_ref_id) REFERENCES platform.user_ref(id);


--
-- Name: task_event task_event_task_id_fkey; Type: FK CONSTRAINT; Schema: workflow; Owner: -
--

ALTER TABLE ONLY workflow.task_event
    ADD CONSTRAINT task_event_task_id_fkey FOREIGN KEY (task_id) REFERENCES workflow.task(id) ON DELETE CASCADE;


--
-- Name: task_event task_event_workspace_id_fkey; Type: FK CONSTRAINT; Schema: workflow; Owner: -
--

ALTER TABLE ONLY workflow.task_event
    ADD CONSTRAINT task_event_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: task task_opportunity_id_fkey; Type: FK CONSTRAINT; Schema: workflow; Owner: -
--

ALTER TABLE ONLY workflow.task
    ADD CONSTRAINT task_opportunity_id_fkey FOREIGN KEY (opportunity_id) REFERENCES crm.opportunity(id);


--
-- Name: task task_source_artifact_id_fkey; Type: FK CONSTRAINT; Schema: workflow; Owner: -
--

ALTER TABLE ONLY workflow.task
    ADD CONSTRAINT task_source_artifact_id_fkey FOREIGN KEY (source_artifact_id) REFERENCES agent.artifact(id);


--
-- Name: task task_source_visit_id_fkey; Type: FK CONSTRAINT; Schema: workflow; Owner: -
--

ALTER TABLE ONLY workflow.task
    ADD CONSTRAINT task_source_visit_id_fkey FOREIGN KEY (source_visit_id) REFERENCES activity.visit(id);


--
-- Name: task task_workspace_id_fkey; Type: FK CONSTRAINT; Schema: workflow; Owner: -
--

ALTER TABLE ONLY workflow.task
    ADD CONSTRAINT task_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES platform.workspace(id);


--
-- Name: action_item; Type: ROW SECURITY; Schema: activity; Owner: -
--

ALTER TABLE activity.action_item ENABLE ROW LEVEL SECURITY;

--
-- Name: action_item customer_object_scope; Type: POLICY; Schema: activity; Owner: -
--

CREATE POLICY customer_object_scope ON activity.action_item USING (((workspace_id = common.current_workspace_id()) AND security.has_customer_access(customer_id))) WITH CHECK (((workspace_id = common.current_workspace_id()) AND security.has_customer_access(customer_id)));


--
-- Name: visit customer_object_scope; Type: POLICY; Schema: activity; Owner: -
--

CREATE POLICY customer_object_scope ON activity.visit USING (((workspace_id = common.current_workspace_id()) AND security.has_customer_access(customer_id))) WITH CHECK (((workspace_id = common.current_workspace_id()) AND security.has_customer_access(customer_id)));


--
-- Name: visit; Type: ROW SECURITY; Schema: activity; Owner: -
--

ALTER TABLE activity.visit ENABLE ROW LEVEL SECURITY;

--
-- Name: visit_contact; Type: ROW SECURITY; Schema: activity; Owner: -
--

ALTER TABLE activity.visit_contact ENABLE ROW LEVEL SECURITY;

--
-- Name: visit_contact visit_contact_scope; Type: POLICY; Schema: activity; Owner: -
--

CREATE POLICY visit_contact_scope ON activity.visit_contact USING (((workspace_id = common.current_workspace_id()) AND (EXISTS ( SELECT 1
   FROM activity.visit v
  WHERE ((v.id = visit_contact.visit_id) AND security.has_customer_access(v.customer_id)))))) WITH CHECK ((workspace_id = common.current_workspace_id()));


--
-- Name: visit_field_value visit_field_scope; Type: POLICY; Schema: activity; Owner: -
--

CREATE POLICY visit_field_scope ON activity.visit_field_value USING (((workspace_id = common.current_workspace_id()) AND (EXISTS ( SELECT 1
   FROM activity.visit v
  WHERE ((v.id = visit_field_value.visit_id) AND security.has_customer_access(v.customer_id)))))) WITH CHECK ((workspace_id = common.current_workspace_id()));


--
-- Name: visit_field_value; Type: ROW SECURITY; Schema: activity; Owner: -
--

ALTER TABLE activity.visit_field_value ENABLE ROW LEVEL SECURITY;

--
-- Name: artifact; Type: ROW SECURITY; Schema: agent; Owner: -
--

ALTER TABLE agent.artifact ENABLE ROW LEVEL SECURITY;

--
-- Name: artifact artifact_scope; Type: POLICY; Schema: agent; Owner: -
--

CREATE POLICY artifact_scope ON agent.artifact USING (((workspace_id = common.current_workspace_id()) AND ((created_by_user_ref_id = common.current_user_ref_id()) OR (EXISTS ( SELECT 1
   FROM agent.conversation c
  WHERE ((c.id = artifact.conversation_id) AND (c.user_ref_id = common.current_user_ref_id()))))))) WITH CHECK ((workspace_id = common.current_workspace_id()));


--
-- Name: confirmation; Type: ROW SECURITY; Schema: agent; Owner: -
--

ALTER TABLE agent.confirmation ENABLE ROW LEVEL SECURITY;

--
-- Name: confirmation confirmation_scope; Type: POLICY; Schema: agent; Owner: -
--

CREATE POLICY confirmation_scope ON agent.confirmation USING (((workspace_id = common.current_workspace_id()) AND (confirmer_user_ref_id = common.current_user_ref_id()))) WITH CHECK (((workspace_id = common.current_workspace_id()) AND (confirmer_user_ref_id = common.current_user_ref_id())));


--
-- Name: conversation; Type: ROW SECURITY; Schema: agent; Owner: -
--

ALTER TABLE agent.conversation ENABLE ROW LEVEL SECURITY;

--
-- Name: conversation conversation_owner_scope; Type: POLICY; Schema: agent; Owner: -
--

CREATE POLICY conversation_owner_scope ON agent.conversation USING (((workspace_id = common.current_workspace_id()) AND (user_ref_id = common.current_user_ref_id()))) WITH CHECK (((workspace_id = common.current_workspace_id()) AND (user_ref_id = common.current_user_ref_id())));


--
-- Name: message; Type: ROW SECURITY; Schema: agent; Owner: -
--

ALTER TABLE agent.message ENABLE ROW LEVEL SECURITY;

--
-- Name: message message_conversation_scope; Type: POLICY; Schema: agent; Owner: -
--

CREATE POLICY message_conversation_scope ON agent.message USING (((workspace_id = common.current_workspace_id()) AND (EXISTS ( SELECT 1
   FROM agent.conversation c
  WHERE ((c.id = message.conversation_id) AND (c.user_ref_id = common.current_user_ref_id())))))) WITH CHECK ((workspace_id = common.current_workspace_id()));


--
-- Name: model_invocation; Type: ROW SECURITY; Schema: agent; Owner: -
--

ALTER TABLE agent.model_invocation ENABLE ROW LEVEL SECURITY;

--
-- Name: model_invocation model_invocation_run_scope; Type: POLICY; Schema: agent; Owner: -
--

CREATE POLICY model_invocation_run_scope ON agent.model_invocation USING (((workspace_id = common.current_workspace_id()) AND (EXISTS ( SELECT 1
   FROM agent.run r
  WHERE (r.id = model_invocation.run_id))))) WITH CHECK (((workspace_id = common.current_workspace_id()) AND (EXISTS ( SELECT 1
   FROM agent.run r
  WHERE (r.id = model_invocation.run_id)))));


--
-- Name: run; Type: ROW SECURITY; Schema: agent; Owner: -
--

ALTER TABLE agent.run ENABLE ROW LEVEL SECURITY;

--
-- Name: run run_conversation_scope; Type: POLICY; Schema: agent; Owner: -
--

CREATE POLICY run_conversation_scope ON agent.run USING (((workspace_id = common.current_workspace_id()) AND (EXISTS ( SELECT 1
   FROM agent.conversation c
  WHERE ((c.id = run.conversation_id) AND (c.user_ref_id = common.current_user_ref_id())))))) WITH CHECK ((workspace_id = common.current_workspace_id()));


--
-- Name: run_step; Type: ROW SECURITY; Schema: agent; Owner: -
--

ALTER TABLE agent.run_step ENABLE ROW LEVEL SECURITY;

--
-- Name: run_step run_step_scope; Type: POLICY; Schema: agent; Owner: -
--

CREATE POLICY run_step_scope ON agent.run_step USING (((workspace_id = common.current_workspace_id()) AND (EXISTS ( SELECT 1
   FROM (agent.run r
     JOIN agent.conversation c ON ((c.id = r.conversation_id)))
  WHERE ((r.id = run_step.run_id) AND (c.user_ref_id = common.current_user_ref_id())))))) WITH CHECK ((workspace_id = common.current_workspace_id()));


--
-- Name: tool_invocation; Type: ROW SECURITY; Schema: agent; Owner: -
--

ALTER TABLE agent.tool_invocation ENABLE ROW LEVEL SECURITY;

--
-- Name: tool_invocation tool_invocation_run_scope; Type: POLICY; Schema: agent; Owner: -
--

CREATE POLICY tool_invocation_run_scope ON agent.tool_invocation USING (((workspace_id = common.current_workspace_id()) AND (EXISTS ( SELECT 1
   FROM agent.run r
  WHERE (r.id = tool_invocation.run_id))))) WITH CHECK (((workspace_id = common.current_workspace_id()) AND (EXISTS ( SELECT 1
   FROM agent.run r
  WHERE (r.id = tool_invocation.run_id)))));


--
-- Name: agent_definition; Type: ROW SECURITY; Schema: config; Owner: -
--

ALTER TABLE config.agent_definition ENABLE ROW LEVEL SECURITY;

--
-- Name: agent_runtime_config; Type: ROW SECURITY; Schema: config; Owner: -
--

ALTER TABLE config.agent_runtime_config ENABLE ROW LEVEL SECURITY;

--
-- Name: agent_runtime_config agent_runtime_config_workspace; Type: POLICY; Schema: config; Owner: -
--

CREATE POLICY agent_runtime_config_workspace ON config.agent_runtime_config USING ((workspace_id = common.current_workspace_id())) WITH CHECK ((workspace_id = common.current_workspace_id()));


--
-- Name: agent_runtime_release; Type: ROW SECURITY; Schema: config; Owner: -
--

ALTER TABLE config.agent_runtime_release ENABLE ROW LEVEL SECURITY;

--
-- Name: agent_runtime_release agent_runtime_release_workspace; Type: POLICY; Schema: config; Owner: -
--

CREATE POLICY agent_runtime_release_workspace ON config.agent_runtime_release USING ((workspace_id = common.current_workspace_id())) WITH CHECK ((workspace_id = common.current_workspace_id()));


--
-- Name: dictionary; Type: ROW SECURITY; Schema: config; Owner: -
--

ALTER TABLE config.dictionary ENABLE ROW LEVEL SECURITY;

--
-- Name: field_definition; Type: ROW SECURITY; Schema: config; Owner: -
--

ALTER TABLE config.field_definition ENABLE ROW LEVEL SECURITY;

--
-- Name: field_definition field_definition_read; Type: POLICY; Schema: config; Owner: -
--

CREATE POLICY field_definition_read ON config.field_definition FOR SELECT USING (((workspace_id IS NULL) OR (workspace_id = common.current_workspace_id())));


--
-- Name: form_definition; Type: ROW SECURITY; Schema: config; Owner: -
--

ALTER TABLE config.form_definition ENABLE ROW LEVEL SECURITY;

--
-- Name: metric_definition; Type: ROW SECURITY; Schema: config; Owner: -
--

ALTER TABLE config.metric_definition ENABLE ROW LEVEL SECURITY;

--
-- Name: metric_definition metric_definition_read; Type: POLICY; Schema: config; Owner: -
--

CREATE POLICY metric_definition_read ON config.metric_definition FOR SELECT USING (((workspace_id IS NULL) OR (workspace_id = common.current_workspace_id())));


--
-- Name: prompt_template; Type: ROW SECURITY; Schema: config; Owner: -
--

ALTER TABLE config.prompt_template ENABLE ROW LEVEL SECURITY;

--
-- Name: prompt_template prompt_template_read; Type: POLICY; Schema: config; Owner: -
--

CREATE POLICY prompt_template_read ON config.prompt_template FOR SELECT USING (((workspace_id IS NULL) OR (workspace_id = common.current_workspace_id())));


--
-- Name: rule_set; Type: ROW SECURITY; Schema: config; Owner: -
--

ALTER TABLE config.rule_set ENABLE ROW LEVEL SECURITY;

--
-- Name: rule_set rule_set_read; Type: POLICY; Schema: config; Owner: -
--

CREATE POLICY rule_set_read ON config.rule_set FOR SELECT USING (((workspace_id IS NULL) OR (workspace_id = common.current_workspace_id())));


--
-- Name: rule_set rule_set_write; Type: POLICY; Schema: config; Owner: -
--

CREATE POLICY rule_set_write ON config.rule_set USING ((workspace_id = common.current_workspace_id())) WITH CHECK ((workspace_id = common.current_workspace_id()));


--
-- Name: sales_competency_framework; Type: ROW SECURITY; Schema: config; Owner: -
--

ALTER TABLE config.sales_competency_framework ENABLE ROW LEVEL SECURITY;

--
-- Name: sales_competency_framework sales_competency_framework_read; Type: POLICY; Schema: config; Owner: -
--

CREATE POLICY sales_competency_framework_read ON config.sales_competency_framework FOR SELECT USING (((workspace_id IS NULL) OR (workspace_id = common.current_workspace_id())));


--
-- Name: agent_definition workspace_isolation; Type: POLICY; Schema: config; Owner: -
--

CREATE POLICY workspace_isolation ON config.agent_definition USING ((workspace_id = common.current_workspace_id())) WITH CHECK ((workspace_id = common.current_workspace_id()));


--
-- Name: dictionary workspace_isolation; Type: POLICY; Schema: config; Owner: -
--

CREATE POLICY workspace_isolation ON config.dictionary USING ((workspace_id = common.current_workspace_id())) WITH CHECK ((workspace_id = common.current_workspace_id()));


--
-- Name: form_definition workspace_isolation; Type: POLICY; Schema: config; Owner: -
--

CREATE POLICY workspace_isolation ON config.form_definition USING ((workspace_id = common.current_workspace_id())) WITH CHECK ((workspace_id = common.current_workspace_id()));


--
-- Name: contact; Type: ROW SECURITY; Schema: crm; Owner: -
--

ALTER TABLE crm.contact ENABLE ROW LEVEL SECURITY;

--
-- Name: customer; Type: ROW SECURITY; Schema: crm; Owner: -
--

ALTER TABLE crm.customer ENABLE ROW LEVEL SECURITY;

--
-- Name: customer_assignment; Type: ROW SECURITY; Schema: crm; Owner: -
--

ALTER TABLE crm.customer_assignment ENABLE ROW LEVEL SECURITY;

--
-- Name: customer_duplicate_candidate; Type: ROW SECURITY; Schema: crm; Owner: -
--

ALTER TABLE crm.customer_duplicate_candidate ENABLE ROW LEVEL SECURITY;

--
-- Name: contact customer_object_scope; Type: POLICY; Schema: crm; Owner: -
--

CREATE POLICY customer_object_scope ON crm.contact USING (((workspace_id = common.current_workspace_id()) AND security.has_customer_access(customer_id))) WITH CHECK (((workspace_id = common.current_workspace_id()) AND security.has_customer_access(customer_id)));


--
-- Name: customer_assignment customer_object_scope; Type: POLICY; Schema: crm; Owner: -
--

CREATE POLICY customer_object_scope ON crm.customer_assignment USING (((workspace_id = common.current_workspace_id()) AND security.has_customer_access(customer_id))) WITH CHECK (((workspace_id = common.current_workspace_id()) AND security.has_customer_access(customer_id)));


--
-- Name: customer_duplicate_candidate customer_object_scope; Type: POLICY; Schema: crm; Owner: -
--

CREATE POLICY customer_object_scope ON crm.customer_duplicate_candidate USING (((workspace_id = common.current_workspace_id()) AND security.has_customer_access(customer_id))) WITH CHECK (((workspace_id = common.current_workspace_id()) AND security.has_customer_access(customer_id)));


--
-- Name: customer_product customer_object_scope; Type: POLICY; Schema: crm; Owner: -
--

CREATE POLICY customer_object_scope ON crm.customer_product USING (((workspace_id = common.current_workspace_id()) AND security.has_customer_access(customer_id))) WITH CHECK (((workspace_id = common.current_workspace_id()) AND security.has_customer_access(customer_id)));


--
-- Name: opportunity customer_object_scope; Type: POLICY; Schema: crm; Owner: -
--

CREATE POLICY customer_object_scope ON crm.opportunity USING (((workspace_id = common.current_workspace_id()) AND security.has_customer_access(customer_id))) WITH CHECK (((workspace_id = common.current_workspace_id()) AND security.has_customer_access(customer_id)));


--
-- Name: customer_product; Type: ROW SECURITY; Schema: crm; Owner: -
--

ALTER TABLE crm.customer_product ENABLE ROW LEVEL SECURITY;

--
-- Name: customer customer_scope; Type: POLICY; Schema: crm; Owner: -
--

CREATE POLICY customer_scope ON crm.customer USING (security.has_customer_access(id)) WITH CHECK (((workspace_id = common.current_workspace_id()) AND (security.has_active_role('manager'::text) OR security.has_active_role('supervisor'::text) OR (owner_user_ref_id = common.current_user_ref_id()))));


--
-- Name: opportunity; Type: ROW SECURITY; Schema: crm; Owner: -
--

ALTER TABLE crm.opportunity ENABLE ROW LEVEL SECURITY;

--
-- Name: opportunity_participant; Type: ROW SECURITY; Schema: crm; Owner: -
--

ALTER TABLE crm.opportunity_participant ENABLE ROW LEVEL SECURITY;

--
-- Name: opportunity_participant opportunity_participant_scope; Type: POLICY; Schema: crm; Owner: -
--

CREATE POLICY opportunity_participant_scope ON crm.opportunity_participant USING (((workspace_id = common.current_workspace_id()) AND (EXISTS ( SELECT 1
   FROM crm.opportunity o
  WHERE ((o.id = opportunity_participant.opportunity_id) AND security.has_customer_access(o.customer_id)))))) WITH CHECK ((workspace_id = common.current_workspace_id()));


--
-- Name: product; Type: ROW SECURITY; Schema: crm; Owner: -
--

ALTER TABLE crm.product ENABLE ROW LEVEL SECURITY;

--
-- Name: product workspace_isolation; Type: POLICY; Schema: crm; Owner: -
--

CREATE POLICY workspace_isolation ON crm.product USING ((workspace_id = common.current_workspace_id())) WITH CHECK ((workspace_id = common.current_workspace_id()));


--
-- Name: quadrant_score customer_object_scope; Type: POLICY; Schema: insight; Owner: -
--

CREATE POLICY customer_object_scope ON insight.quadrant_score USING (((workspace_id = common.current_workspace_id()) AND security.has_customer_access(customer_id))) WITH CHECK (((workspace_id = common.current_workspace_id()) AND security.has_customer_access(customer_id)));


--
-- Name: recommendation customer_object_scope; Type: POLICY; Schema: insight; Owner: -
--

CREATE POLICY customer_object_scope ON insight.recommendation USING (((workspace_id = common.current_workspace_id()) AND security.has_customer_access(customer_id))) WITH CHECK (((workspace_id = common.current_workspace_id()) AND security.has_customer_access(customer_id)));


--
-- Name: quadrant_score; Type: ROW SECURITY; Schema: insight; Owner: -
--

ALTER TABLE insight.quadrant_score ENABLE ROW LEVEL SECURITY;

--
-- Name: recommendation; Type: ROW SECURITY; Schema: insight; Owner: -
--

ALTER TABLE insight.recommendation ENABLE ROW LEVEL SECURITY;

--
-- Name: report; Type: ROW SECURITY; Schema: insight; Owner: -
--

ALTER TABLE insight.report ENABLE ROW LEVEL SECURITY;

--
-- Name: risk; Type: ROW SECURITY; Schema: insight; Owner: -
--

ALTER TABLE insight.risk ENABLE ROW LEVEL SECURITY;

--
-- Name: risk_event; Type: ROW SECURITY; Schema: insight; Owner: -
--

ALTER TABLE insight.risk_event ENABLE ROW LEVEL SECURITY;

--
-- Name: risk risk_scope; Type: POLICY; Schema: insight; Owner: -
--

CREATE POLICY risk_scope ON insight.risk USING (((workspace_id = common.current_workspace_id()) AND (((customer_id IS NOT NULL) AND security.has_customer_access(customer_id)) OR (EXISTS ( SELECT 1
   FROM crm.opportunity o
  WHERE ((o.id = risk.opportunity_id) AND security.has_customer_access(o.customer_id))))))) WITH CHECK ((workspace_id = common.current_workspace_id()));


--
-- Name: sales_competency_review; Type: ROW SECURITY; Schema: insight; Owner: -
--

ALTER TABLE insight.sales_competency_review ENABLE ROW LEVEL SECURITY;

--
-- Name: sales_competency_review sales_competency_review_scope; Type: POLICY; Schema: insight; Owner: -
--

CREATE POLICY sales_competency_review_scope ON insight.sales_competency_review USING (((workspace_id = common.current_workspace_id()) AND ((subject_user_ref_id = common.current_user_ref_id()) OR (common.current_role_code() = 'manager'::text) OR ((common.current_role_code() = 'supervisor'::text) AND (EXISTS ( SELECT 1
   FROM (platform.team_membership supervisor_tm
     JOIN platform.team_membership subject_tm ON (((subject_tm.team_id = supervisor_tm.team_id) AND (subject_tm.user_ref_id = sales_competency_review.subject_user_ref_id) AND (clock_timestamp() >= subject_tm.valid_from) AND (clock_timestamp() < subject_tm.valid_to))))
  WHERE ((supervisor_tm.user_ref_id = common.current_user_ref_id()) AND (supervisor_tm.membership_role = 'supervisor'::text) AND (clock_timestamp() >= supervisor_tm.valid_from) AND (clock_timestamp() < supervisor_tm.valid_to)))))))) WITH CHECK (((workspace_id = common.current_workspace_id()) AND ((subject_user_ref_id = common.current_user_ref_id()) OR (common.current_role_code() = ANY (ARRAY['supervisor'::text, 'manager'::text])))));


--
-- Name: report workspace_isolation; Type: POLICY; Schema: insight; Owner: -
--

CREATE POLICY workspace_isolation ON insight.report USING ((workspace_id = common.current_workspace_id())) WITH CHECK ((workspace_id = common.current_workspace_id()));


--
-- Name: risk_event workspace_isolation; Type: POLICY; Schema: insight; Owner: -
--

CREATE POLICY workspace_isolation ON insight.risk_event USING ((workspace_id = common.current_workspace_id())) WITH CHECK ((workspace_id = common.current_workspace_id()));


--
-- Name: audit_log; Type: ROW SECURITY; Schema: ops; Owner: -
--

ALTER TABLE ops.audit_log ENABLE ROW LEVEL SECURITY;

--
-- Name: file_asset; Type: ROW SECURITY; Schema: ops; Owner: -
--

ALTER TABLE ops.file_asset ENABLE ROW LEVEL SECURITY;

--
-- Name: idempotency_key; Type: ROW SECURITY; Schema: ops; Owner: -
--

ALTER TABLE ops.idempotency_key ENABLE ROW LEVEL SECURITY;

--
-- Name: job; Type: ROW SECURITY; Schema: ops; Owner: -
--

ALTER TABLE ops.job ENABLE ROW LEVEL SECURITY;

--
-- Name: job job_workspace; Type: POLICY; Schema: ops; Owner: -
--

CREATE POLICY job_workspace ON ops.job USING ((workspace_id = common.current_workspace_id())) WITH CHECK ((workspace_id = common.current_workspace_id()));


--
-- Name: outbox_event; Type: ROW SECURITY; Schema: ops; Owner: -
--

ALTER TABLE ops.outbox_event ENABLE ROW LEVEL SECURITY;

--
-- Name: audit_log workspace_isolation; Type: POLICY; Schema: ops; Owner: -
--

CREATE POLICY workspace_isolation ON ops.audit_log USING ((workspace_id = common.current_workspace_id())) WITH CHECK ((workspace_id = common.current_workspace_id()));


--
-- Name: file_asset workspace_isolation; Type: POLICY; Schema: ops; Owner: -
--

CREATE POLICY workspace_isolation ON ops.file_asset USING ((workspace_id = common.current_workspace_id())) WITH CHECK ((workspace_id = common.current_workspace_id()));


--
-- Name: idempotency_key workspace_isolation; Type: POLICY; Schema: ops; Owner: -
--

CREATE POLICY workspace_isolation ON ops.idempotency_key USING ((workspace_id = common.current_workspace_id())) WITH CHECK ((workspace_id = common.current_workspace_id()));


--
-- Name: outbox_event workspace_isolation; Type: POLICY; Schema: ops; Owner: -
--

CREATE POLICY workspace_isolation ON ops.outbox_event USING ((workspace_id = common.current_workspace_id())) WITH CHECK ((workspace_id = common.current_workspace_id()));


--
-- Name: auth_session; Type: ROW SECURITY; Schema: platform; Owner: -
--

ALTER TABLE platform.auth_session ENABLE ROW LEVEL SECURITY;

--
-- Name: auth_session auth_session_owner_scope; Type: POLICY; Schema: platform; Owner: -
--

CREATE POLICY auth_session_owner_scope ON platform.auth_session USING (((workspace_id = common.current_workspace_id()) AND (user_ref_id = common.current_user_ref_id()))) WITH CHECK (((workspace_id = common.current_workspace_id()) AND (user_ref_id = common.current_user_ref_id())));


--
-- Name: identity_binding; Type: ROW SECURITY; Schema: platform; Owner: -
--

ALTER TABLE platform.identity_binding ENABLE ROW LEVEL SECURITY;

--
-- Name: identity_binding identity_binding_workspace; Type: POLICY; Schema: platform; Owner: -
--

CREATE POLICY identity_binding_workspace ON platform.identity_binding USING ((workspace_id = common.current_workspace_id())) WITH CHECK ((workspace_id = common.current_workspace_id()));


--
-- Name: role_binding; Type: ROW SECURITY; Schema: platform; Owner: -
--

ALTER TABLE platform.role_binding ENABLE ROW LEVEL SECURITY;

--
-- Name: team; Type: ROW SECURITY; Schema: platform; Owner: -
--

ALTER TABLE platform.team ENABLE ROW LEVEL SECURITY;

--
-- Name: team_membership; Type: ROW SECURITY; Schema: platform; Owner: -
--

ALTER TABLE platform.team_membership ENABLE ROW LEVEL SECURITY;

--
-- Name: user_ref; Type: ROW SECURITY; Schema: platform; Owner: -
--

ALTER TABLE platform.user_ref ENABLE ROW LEVEL SECURITY;

--
-- Name: role_binding workspace_isolation; Type: POLICY; Schema: platform; Owner: -
--

CREATE POLICY workspace_isolation ON platform.role_binding USING ((workspace_id = common.current_workspace_id())) WITH CHECK ((workspace_id = common.current_workspace_id()));


--
-- Name: team workspace_isolation; Type: POLICY; Schema: platform; Owner: -
--

CREATE POLICY workspace_isolation ON platform.team USING ((workspace_id = common.current_workspace_id())) WITH CHECK ((workspace_id = common.current_workspace_id()));


--
-- Name: team_membership workspace_isolation; Type: POLICY; Schema: platform; Owner: -
--

CREATE POLICY workspace_isolation ON platform.team_membership USING ((workspace_id = common.current_workspace_id())) WITH CHECK ((workspace_id = common.current_workspace_id()));


--
-- Name: user_ref workspace_isolation; Type: POLICY; Schema: platform; Owner: -
--

CREATE POLICY workspace_isolation ON platform.user_ref USING ((workspace_id = common.current_workspace_id())) WITH CHECK ((workspace_id = common.current_workspace_id()));


--
-- Name: notification; Type: ROW SECURITY; Schema: workflow; Owner: -
--

ALTER TABLE workflow.notification ENABLE ROW LEVEL SECURITY;

--
-- Name: notification_delivery; Type: ROW SECURITY; Schema: workflow; Owner: -
--

ALTER TABLE workflow.notification_delivery ENABLE ROW LEVEL SECURITY;

--
-- Name: notification_delivery notification_delivery_scope; Type: POLICY; Schema: workflow; Owner: -
--

CREATE POLICY notification_delivery_scope ON workflow.notification_delivery USING (((workspace_id = common.current_workspace_id()) AND (EXISTS ( SELECT 1
   FROM workflow.notification n
  WHERE (n.id = notification_delivery.notification_id))))) WITH CHECK (((workspace_id = common.current_workspace_id()) AND (EXISTS ( SELECT 1
   FROM workflow.notification n
  WHERE (n.id = notification_delivery.notification_id)))));


--
-- Name: notification notification_insert_scope; Type: POLICY; Schema: workflow; Owner: -
--

CREATE POLICY notification_insert_scope ON workflow.notification FOR INSERT WITH CHECK (((workspace_id = common.current_workspace_id()) AND (security.has_active_role('sales'::text) OR security.has_active_role('supervisor'::text) OR security.has_active_role('manager'::text)) AND (EXISTS ( SELECT 1
   FROM platform.user_ref recipient
  WHERE ((recipient.id = notification.recipient_user_ref_id) AND (recipient.workspace_id = common.current_workspace_id()) AND (recipient.status = 'active'::text) AND (recipient.deleted_at IS NULL))))));


--
-- Name: notification notification_select_scope; Type: POLICY; Schema: workflow; Owner: -
--

CREATE POLICY notification_select_scope ON workflow.notification FOR SELECT USING (((workspace_id = common.current_workspace_id()) AND (recipient_user_ref_id = common.current_user_ref_id())));


--
-- Name: notification notification_update_scope; Type: POLICY; Schema: workflow; Owner: -
--

CREATE POLICY notification_update_scope ON workflow.notification FOR UPDATE USING (((workspace_id = common.current_workspace_id()) AND (recipient_user_ref_id = common.current_user_ref_id()))) WITH CHECK (((workspace_id = common.current_workspace_id()) AND (recipient_user_ref_id = common.current_user_ref_id())));


--
-- Name: task; Type: ROW SECURITY; Schema: workflow; Owner: -
--

ALTER TABLE workflow.task ENABLE ROW LEVEL SECURITY;

--
-- Name: task_assignee; Type: ROW SECURITY; Schema: workflow; Owner: -
--

ALTER TABLE workflow.task_assignee ENABLE ROW LEVEL SECURITY;

--
-- Name: task_event; Type: ROW SECURITY; Schema: workflow; Owner: -
--

ALTER TABLE workflow.task_event ENABLE ROW LEVEL SECURITY;

--
-- Name: task task_owner_update; Type: POLICY; Schema: workflow; Owner: -
--

CREATE POLICY task_owner_update ON workflow.task FOR UPDATE USING (((workspace_id = common.current_workspace_id()) AND (EXISTS ( SELECT 1
   FROM workflow.task_assignee ta
  WHERE ((ta.task_id = task.id) AND (ta.assignee_user_ref_id = common.current_user_ref_id()) AND (ta.responsibility = 'owner'::text)))))) WITH CHECK ((workspace_id = common.current_workspace_id()));


--
-- Name: task task_sales_followup_insert; Type: POLICY; Schema: workflow; Owner: -
--

CREATE POLICY task_sales_followup_insert ON workflow.task FOR INSERT WITH CHECK (((workspace_id = common.current_workspace_id()) AND security.has_active_role('sales'::text) AND (creator_user_ref_id = common.current_user_ref_id()) AND (task_type = 'visit_follow_up'::text) AND (source_visit_id IS NOT NULL) AND (EXISTS ( SELECT 1
   FROM activity.visit v
  WHERE ((v.id = task.source_visit_id) AND (v.workspace_id = common.current_workspace_id()) AND (v.recorder_user_ref_id = common.current_user_ref_id()))))));


--
-- Name: task task_scope; Type: POLICY; Schema: workflow; Owner: -
--

CREATE POLICY task_scope ON workflow.task USING (((workspace_id = common.current_workspace_id()) AND ((creator_user_ref_id = common.current_user_ref_id()) OR (EXISTS ( SELECT 1
   FROM workflow.task_assignee ta
  WHERE ((ta.task_id = task.id) AND (ta.assignee_user_ref_id = common.current_user_ref_id())))) OR ((customer_id IS NOT NULL) AND security.has_customer_access(customer_id))))) WITH CHECK (((workspace_id = common.current_workspace_id()) AND (creator_user_ref_id = common.current_user_ref_id()) AND (security.has_active_role('sales'::text) OR security.has_active_role('supervisor'::text) OR security.has_active_role('manager'::text))));


--
-- Name: task_assignee workspace_isolation; Type: POLICY; Schema: workflow; Owner: -
--

CREATE POLICY workspace_isolation ON workflow.task_assignee USING ((workspace_id = common.current_workspace_id())) WITH CHECK ((workspace_id = common.current_workspace_id()));


--
-- Name: task_event workspace_isolation; Type: POLICY; Schema: workflow; Owner: -
--

CREATE POLICY workspace_isolation ON workflow.task_event USING ((workspace_id = common.current_workspace_id())) WITH CHECK ((workspace_id = common.current_workspace_id()));


--
-- PostgreSQL database dump complete
--

\unrestrict AlAt19fy9tnBwXmBhVI6sIA1gpwtO64NWLpHnceRaQw2hejHdHlrEdO9kHCAaa2
