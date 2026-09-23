"""Management projection of inference receipts and original business facts."""

from sales_backend.db import json_value


class AgentAuditRepository:
    async def cases(
        self,
        connection,
        *,
        start,
        end,
        capability=None,
        provider=None,
        outcome=None,
        actor=None,
        test_only=None,
        limit=50,
        offset=0,
    ):
        row = await connection.fetchrow(
            """WITH operations AS MATERIALIZED (
                 SELECT operation_id,case_id,started_at,
                   CASE WHEN record->>'inference_status'='running'
                          AND started_at < now()-interval '15 minutes'
                     THEN record || jsonb_build_object('inference_status','unknown',
                          'receipt_warning','运行超过15分钟未收到结束回执，需要核对；不据此判为成功或失败')
                     ELSE record END AS record
                   FROM security.agent_operation_rows($1,$2)
               ), cases AS (
                 SELECT case_id,max(started_at) AS last_started,
                        (array_agg(record ORDER BY started_at DESC,operation_id DESC))[1] AS latest,
                        jsonb_agg(record ORDER BY started_at,operation_id) AS operations
                   FROM operations GROUP BY case_id
               ), classified AS (
                 SELECT *,EXISTS(SELECT 1 FROM jsonb_array_elements(operations) o,
                         jsonb_array_elements(o->'attempts') a WHERE a->>'test_fault_injected'='true') AS test_injected
                   FROM cases
               ), filtered AS (
                 SELECT * FROM classified
                WHERE ($3::text IS NULL OR latest->>'capability'=$3)
                  AND ($4::text IS NULL OR latest->'trace'->>'provider'=$4)
                  AND ($5::text IS NULL OR latest->>'inference_status'=$5)
                  AND ($6::uuid IS NULL OR latest->>'actor_id'=$6::text)
                  AND ($7::boolean IS NULL OR test_injected=$7)
               ), measured AS (
                 SELECT *,EXISTS(SELECT 1 FROM jsonb_array_elements(operations) o,
                           jsonb_array_elements(o->'attempts') a
                         WHERE a->>'provider'='agent_platform'
                           AND COALESCE(a->>'network_dispatch_suppressed','false')<>'true') AS platform_attempted,
                   (latest->'trace'->>'provider' IS DISTINCT FROM 'rules') AND (
                     COALESCE(latest->'trace'->>'fallback_reason','')<>'' OR
                     EXISTS(SELECT 1 FROM jsonb_array_elements(latest->'events') e
                             WHERE e->>'phase'='fallback_requested')) AS fallback_requested,
                   latest->>'inference_status'='accepted' AS accepted
                 FROM filtered
               ), eligible AS (SELECT * FROM measured WHERE NOT test_injected),
               page AS (SELECT * FROM filtered ORDER BY last_started DESC,case_id DESC LIMIT $8 OFFSET $9)
               SELECT (SELECT count(*)::int FROM filtered) AS total,
                 COALESCE((SELECT jsonb_agg(jsonb_build_object('case_id',case_id,
                     'latest',latest,'operations',operations,'test_injected',test_injected)
                     ORDER BY last_started DESC,case_id DESC) FROM page),'[]'::jsonb) AS items,
                 jsonb_build_object(
                   'eligible',count(*),
                   'excluded_fault_tests',(SELECT count(*) FROM filtered WHERE test_injected),
                   'platform_attempted',count(*) FILTER(WHERE platform_attempted),
                   'platform_accepted',count(*) FILTER(WHERE platform_attempted AND accepted
                                                    AND latest->'trace'->>'provider'='agent_platform'),
                   'fallback_requested',count(*) FILTER(WHERE fallback_requested),
                   'fallback_accepted',count(*) FILTER(WHERE fallback_requested AND accepted
                                                    AND latest->'trace'->>'provider'='senseaudio'),
                   'direct_accepted',count(*) FILTER(WHERE NOT fallback_requested AND accepted
                                                    AND latest->'trace'->>'provider'='senseaudio'),
                   'rule_fallback_accepted',count(*) FILTER(WHERE accepted AND latest->'trace'->>'provider'='rules'),
                   'business_succeeded',count(*) FILTER(WHERE latest->>'business_status'='succeeded'
                      OR (latest->>'capability'='opportunity_change' AND latest->>'job_effect_recorded'='true')),
                   'waiting_human',count(*) FILTER(WHERE latest->>'business_status'='waiting_human'),
                   'unknown',count(*) FILTER(WHERE latest->>'inference_status'='unknown')
                 ) AS statistics FROM eligible""",
            start,
            end,
            capability,
            provider,
            outcome,
            actor,
            test_only,
            limit,
            offset,
        )
        return {
            "items": [self._item(item) for item in json_value(row["items"])],
            "total": row["total"],
            "statistics": json_value(row["statistics"]),
        }

    @staticmethod
    def _item(row):
        latest = json_value(row["latest"])
        return {
            **latest,
            "case_id": row["case_id"],
            "operations": json_value(row["operations"]),
            "test_injected": row["test_injected"],
        }
