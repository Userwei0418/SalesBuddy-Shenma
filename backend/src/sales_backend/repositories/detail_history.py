# ruff: noqa: S608 -- Only fixed SQL fragments and placeholder positions are interpolated; values stay bound.
"""Paged history read models. Every source table retains transaction-local RLS."""

from sales_backend.repositories.task_outcomes import NORMAL_COMPLETED_SQL, STOPPED_SQL
from sales_backend.domain.detail_paging import read_visit_cursor, visit_cursor
from sales_backend.domain.visit_dates import visit_date_fields
from sales_backend.repositories.visit_subjects import visit_subject_predicate


def page_result(rows, limit, offset, *, total=None):
    more = len(rows) > limit
    result = {"items": rows[:limit], "has_more": more, "next_offset": offset + limit if more else None}
    if total is not None:
        result["total"] = total
    return result


class DetailHistoryRepository:
    async def visits(self, connection, customer_id, *, opportunity_id=None, limit=20, offset=0, cursor=None, sort=None, own_only=False):
        if sort not in (None, "created_desc"):
            raise ValueError("不支持的跟进排序方式")
        position = read_visit_cursor(cursor, customer_id, opportunity_id, sort=sort)
        if position and offset:
            raise ValueError("游标分页不能同时指定offset")
        args = [customer_id]
        if opportunity_id:
            args.append(opportunity_id)
        predicates = [visit_subject_predicate(opportunity_scoped=bool(opportunity_id))]
        if own_only:
            predicates.append("v.recorder_user_ref_id=common.current_user_ref_id() "
                              "AND v.created_by_user_ref_id=common.current_user_ref_id() "
                              "AND v.opportunity_id IS NOT NULL")
        order_by = (
            "v.created_at DESC,v.id DESC"
            if sort == "created_desc"
            else "v.interaction_at DESC NULLS LAST,v.history_sort_date DESC,v.id DESC"
        )
        ranges = []
        if position and sort == "created_desc":
            args.extend([position.created_at, position.id])
            predicates.append(f"(v.created_at,v.id)<(${len(args) - 1}::timestamptz,${len(args)}::uuid)")
        elif position:
            if position.interaction_at is not None:
                args.append(position.interaction_at)
                at = f"${len(args)}::timestamptz"
                same_time = f"v.interaction_at={at}"
                ranges.extend([f"v.interaction_at<{at}", "v.interaction_at IS NULL"])
            else:
                same_time = "v.interaction_at IS NULL"
            args.extend([position.recorded_date, position.id])
            day, row_id = f"${len(args) - 1}::date", f"${len(args)}::uuid"
            ranges[:0] = [
                f"{same_time} AND v.history_sort_date={day} AND v.id<{row_id}",
                f"{same_time} AND v.history_sort_date<{day}",
            ]
        args.extend([limit + 1, offset])
        limit_arg, offset_arg = f"${len(args) - 1}", f"${len(args)}"
        source = "FROM activity.visit v WHERE " + " AND ".join(predicates)
        # Partition the exact subject predicate into disjoint principal/bridge
        # cases. The common direct-customer branch can then seek the existing
        # history index instead of authorizing and sorting every matching row.
        # Every branch still reads the original RLS table and full predicate.
        subjects = (
            "v.customer_id=$1::uuid",
            "v.customer_id IS DISTINCT FROM $1::uuid AND v.opportunity_id IS NULL",
            "v.customer_id IS DISTINCT FROM $1::uuid AND v.opportunity_id IS NOT NULL",
        )
        columns = "v.id,v.created_at" if sort == "created_desc" else "v.id,v.interaction_at,v.history_sort_date"
        parts = [
            f"(SELECT {columns} {source} AND ({subject}) AND ({clause}) "
            f"ORDER BY {order_by} LIMIT ({limit_arg}::bigint+{offset_arg}::bigint))"
            for subject in subjects for clause in (ranges or ["true"])
        ]
        page_sql = (
            "SELECT id FROM (" + " UNION ALL ".join(parts) + ") candidates "
            f"ORDER BY {order_by.replace('v.', '')} LIMIT {limit_arg} OFFSET {offset_arg}"
        )
        rows = await connection.fetch(
            f"""WITH page AS MATERIALIZED ({page_sql}
            ) SELECT v.id::text,v.customer_id::text,v.opportunity_id::text,v.status,
              v.interaction_at,v.created_at,v.recorded_on,
              v.partner_id::text,COALESCE(partner.name,v.partner_name_snapshot) AS partner_name,
              v.original_recorder_name,manager.display_name AS manager_name,
              COALESCE((SELECT jsonb_agg(jsonb_build_object('id',linked.id::text,'name',linked.name)
                ORDER BY linked.name,linked.id) FROM activity.visit_opportunity vo
                JOIN crm.opportunity linked ON linked.id=vo.opportunity_id AND linked.deleted_at IS NULL
                WHERE vo.visit_id=v.id),'[]'::jsonb) AS linked_opportunities,
              v.interaction_mode_code,v.expectation_code,v.contact_name_snapshot,v.is_first_visit,
              v.visit_goal,v.duration_minutes,v.recording_role_code_snapshot AS recorder_role,
              v.recording_role_code_snapshot,
              v.recorder_user_ref_id::text AS recorder_id,
              v.recorder_user_ref_id::text AS recorder_user_id,
              COALESCE(v.import_meta->>'source_system',v.import_meta->>'source') AS source_system,
              COALESCE((SELECT jsonb_agg(jsonb_build_object('id',p.user_ref_id::text,'name',pu.display_name,
                'team_id',p.team_id_at_event,'role',p.role_code_at_event) ORDER BY pu.display_name,p.user_ref_id)
                FROM activity.visit_participant p JOIN platform.user_ref pu ON pu.id=p.user_ref_id
                WHERE p.visit_id=v.id AND p.participant_role='fde'),'[]'::jsonb) AS fde_participants,
              COALESCE((SELECT jsonb_agg(jsonb_build_object('id',p.user_ref_id::text,'name',pu.display_name)
                ORDER BY pu.display_name,p.user_ref_id)
                FROM activity.visit_participant p JOIN platform.user_ref pu ON pu.id=p.user_ref_id
                WHERE p.visit_id=v.id AND p.participant_role='collaborator'),'[]'::jsonb) AS collaborators,
              COALESCE((SELECT array_agg(p.user_ref_id::text ORDER BY p.user_ref_id)
                FROM activity.visit_participant p
                WHERE p.visit_id=v.id AND p.participant_role='fde'),
                '{{}}'::text[]) AS fde_participant_ids,
              v.follow_up_score,
              CASE WHEN jsonb_typeof(v.quality_review)='object' AND v.quality_review<>'{{}}'::jsonb THEN
                jsonb_build_object(
                  'follow_up_score',CASE WHEN jsonb_typeof(v.quality_review->'follow_up_score')='number'
                    THEN v.quality_review->'follow_up_score' END,
                  'grade',CASE WHEN jsonb_typeof(v.quality_review->'grade')='string'
                    THEN v.quality_review->'grade' END,
                  'next_action_passed',CASE WHEN jsonb_typeof(v.quality_review->'next_action_passed')='boolean'
                    THEN v.quality_review->'next_action_passed' END
                ) END AS quality_review,
              left(v.follow_up_record,600) AS follow_up_record,left(v.next_action,600) AS next_action,
              COALESCE(
                v.archived_fields->>'customer_name',security.customer_reference(v.customer_id)->>'name'
              ) AS customer_name,
              v.customer_type_code_snapshot AS customer_type,u.display_name AS recorder_name,
              creator.display_name AS creator_name,o.name AS opportunity_name,true AS is_summary
            FROM page JOIN LATERAL (
              SELECT id,customer_id,opportunity_id,status,interaction_at,created_at,recorded_on,history_sort_date,
                partner_id,partner_name_snapshot,original_recorder_name,manager_user_ref_id,
                interaction_mode_code,expectation_code,contact_name_snapshot,is_first_visit,visit_goal,duration_minutes,
                follow_up_record,next_action,customer_type_code_snapshot,recorder_user_ref_id,created_by_user_ref_id,
                follow_up_score,quality_review,recording_role_code_snapshot,import_meta,archived_fields
              FROM activity.visit WHERE id=page.id LIMIT 1
            ) v ON true
            LEFT JOIN platform.user_ref u ON u.id=v.recorder_user_ref_id
            LEFT JOIN platform.user_ref manager ON manager.id=v.manager_user_ref_id
            LEFT JOIN crm.partner partner ON partner.id=v.partner_id
            LEFT JOIN platform.user_ref creator ON creator.id=v.created_by_user_ref_id
            LEFT JOIN crm.opportunity o ON o.id=v.opportunity_id
            ORDER BY {order_by}""",
            *args,
        )
        result = page_result([visit_date_fields(dict(row)) for row in rows], limit, offset)
        result["next_cursor"] = (
            visit_cursor(result["items"][-1], customer_id, opportunity_id, sort=sort) if result["has_more"] else None
        )
        if sort == "created_desc":
            result["sort"] = sort
        if cursor:
            result["next_offset"] = None
        return result

    async def contacts(self, connection, customer_id, *, limit=20, offset=0):
        rows = await connection.fetch(
            """SELECT id::text,name,title,department,contact_category_code,relationship_role_code,is_primary
            FROM crm.contact WHERE customer_id=$1::uuid AND deleted_at IS NULL
            ORDER BY is_primary DESC,name,id LIMIT $2 OFFSET $3""",
            customer_id,
            limit + 1,
            offset,
        )
        return page_result([dict(row) for row in rows], limit, offset)

    async def timeline(self, connection, opportunity_id, *, limit=20, offset=0):
        # The deferred subject constraint keeps a non-null principal opportunity
        # equal to the sole bridge row. Avoid repeating bridge RLS on this common
        # path; true multi-opportunity histories have a null principal ID.
        rows = await connection.fetch(
            """WITH events AS (
              SELECT 'created' AS key,'商机创建' AS title,created_at AS at,left(name,600) AS detail,
                'opportunity' AS object_type,id::text AS object_id FROM crm.opportunity
                WHERE id=$1::uuid AND deleted_at IS NULL
              UNION ALL SELECT 'updated','商机信息更新',updated_at,
                '此为信息更新时间，不代表阶段变更时间','opportunity',id::text FROM crm.opportunity
                WHERE id=$1::uuid AND deleted_at IS NULL AND updated_at<>created_at
              UNION ALL SELECT 'visit:'||id,'跟进记录',interaction_at,
                left(COALESCE(NULLIF(follow_up_record,''),NULLIF(visit_goal,''),'已记录跟进'),600),
                'visit',id::text FROM activity.visit v WHERE (v.opportunity_id=$1::uuid OR
                  (v.opportunity_id IS NULL AND EXISTS (SELECT 1 FROM activity.visit_opportunity vo
                    WHERE vo.visit_id=v.id AND vo.opportunity_id=$1::uuid)))
                  AND deleted_at IS NULL
              UNION ALL SELECT 'task:'||id,'任务创建',created_at,left(title,600),'task',id::text
                FROM workflow.task WHERE opportunity_id=$1::uuid AND deleted_at IS NULL
              UNION ALL SELECT 'done:'||t.id,CASE WHEN """ + NORMAL_COMPLETED_SQL + """ THEN '任务完成'
                  WHEN """ + STOPPED_SQL + """ THEN '任务停止' ELSE '任务已结束（结果待核对）' END,
                  t.completed_at,left(t.title,600),'task',t.id::text
                FROM workflow.task t WHERE t.opportunity_id=$1::uuid AND t.deleted_at IS NULL AND t.status='completed'
            ) SELECT * FROM events WHERE at IS NOT NULL ORDER BY at DESC,key LIMIT $2 OFFSET $3""",
            opportunity_id,
            limit + 1,
            offset,
        )
        return page_result([dict(row) for row in rows], limit, offset)

    async def actual_quarters(self, connection, customer_id, opportunity_id, *, as_of, team_id=None, owner_id=None):
        rows = await connection.fetch(
            """SELECT a.* FROM crm.effective_actual a
            WHERE a.customer_id=$1::uuid AND a.opportunity_id=$2::uuid
              AND (a.occurred_on IS NULL OR a.occurred_on<=$3::date) AND security.customer_reference(a.customer_id) IS NOT NULL
              AND ($4::uuid IS NULL OR a.owner_team_id=$4) AND ($5::uuid IS NULL OR a.owner_user_ref_id=$5)
            ORDER BY year,quarter,a.id""",
            customer_id,
            opportunity_id,
            as_of,
            team_id,
            owner_id,
        )
        from collections import defaultdict
        from sales_backend.domain.finance_totals import actual_totals
        periods = defaultdict(list)
        for row in rows: periods[(row['year'],row['quarter'])].append(dict(row))
        items = [dict(year=year,quarter=quarter,entry_count=len(facts),**actual_totals(facts))
                 for (year,quarter),facts in periods.items()]
        return {
            "items": items,
            "years": sorted({as_of.year, *(row["year"] for row in items)}, reverse=True),
            "as_of": as_of,
            "data_source": "database",
        }
