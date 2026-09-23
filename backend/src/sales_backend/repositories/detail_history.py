# ruff: noqa: S608 -- Only fixed SQL fragments and placeholder positions are interpolated; values stay bound.
"""Paged history read models. Every source table retains transaction-local RLS."""

from sales_backend.domain.detail_paging import read_visit_cursor, visit_cursor
from sales_backend.domain.visit_dates import visit_date_fields


def page_result(rows, limit, offset, *, total=None):
    more = len(rows) > limit
    result = {"items": rows[:limit], "has_more": more, "next_offset": offset + limit if more else None}
    if total is not None:
        result["total"] = total
    return result


class DetailHistoryRepository:
    async def visits(self, connection, customer_id, *, opportunity_id=None, limit=20, offset=0, cursor=None, sort=None):
        if sort not in (None, "created_desc"):
            raise ValueError("不支持的跟进排序方式")
        position = read_visit_cursor(cursor, customer_id, opportunity_id, sort=sort)
        if position and offset:
            raise ValueError("游标分页不能同时指定offset")
        args = [customer_id]
        predicates = ["""(v.customer_id=$1::uuid OR (v.opportunity_id IS NULL AND EXISTS (
            SELECT 1 FROM activity.visit_opportunity vo JOIN crm.opportunity linked ON linked.id=vo.opportunity_id
            WHERE vo.visit_id=v.id AND linked.customer_id=$1::uuid AND linked.deleted_at IS NULL))
            OR (v.customer_id IS NULL AND v.opportunity_id IS NOT NULL AND EXISTS (
              SELECT 1 FROM crm.opportunity linked WHERE linked.id=v.opportunity_id
                AND linked.customer_id=$1::uuid AND linked.deleted_at IS NULL)))""",
            "v.deleted_at IS NULL"]
        if opportunity_id:
            args.append(opportunity_id)
            predicates.append(f"(v.opportunity_id=${len(args)}::uuid OR (v.opportunity_id IS NULL AND "
                              f"EXISTS(SELECT 1 FROM activity.visit_opportunity vo "
                              f"WHERE vo.visit_id=v.id AND vo.opportunity_id=${len(args)}::uuid)))")
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
        if ranges:
            # Disjoint scalar ranges preserve NULLS LAST and let PostgreSQL seek
            # through typed index columns before per-row RLS. Each range is bounded.
            parts = [
                f"(SELECT v.id,v.interaction_at,v.history_sort_date {source} AND {clause} "
                f"ORDER BY {order_by} LIMIT {limit_arg})"
                for clause in ranges
            ]
            page_sql = (
                "SELECT id FROM (" + " UNION ALL ".join(parts) + ") candidates "
                "ORDER BY interaction_at DESC NULLS LAST,history_sort_date DESC,id DESC "
                f"LIMIT {limit_arg} OFFSET {offset_arg}"
            )
        else:
            page_sql = f"SELECT v.id {source} ORDER BY {order_by} LIMIT {limit_arg} OFFSET {offset_arg}"
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
              v.customer_type_code_snapshot AS customer_type,u.display_name AS recorder_name,
              creator.display_name AS creator_name,o.name AS opportunity_name,true AS is_summary
            FROM page JOIN LATERAL (
              SELECT id,customer_id,opportunity_id,status,interaction_at,created_at,recorded_on,history_sort_date,
                partner_id,partner_name_snapshot,original_recorder_name,manager_user_ref_id,
                interaction_mode_code,expectation_code,contact_name_snapshot,is_first_visit,
                follow_up_record,next_action,customer_type_code_snapshot,recorder_user_ref_id,created_by_user_ref_id,
                follow_up_score,quality_review
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
              UNION ALL SELECT 'done:'||id,'任务完成',completed_at,left(title,600),'task',id::text
                FROM workflow.task WHERE opportunity_id=$1::uuid AND deleted_at IS NULL AND status='completed'
            ) SELECT * FROM events WHERE at IS NOT NULL ORDER BY at DESC,key LIMIT $2 OFFSET $3""",
            opportunity_id,
            limit + 1,
            offset,
        )
        return page_result([dict(row) for row in rows], limit, offset)

    async def actual_quarters(self, connection, customer_id, opportunity_id, *, as_of, team_id=None, owner_id=None):
        rows = await connection.fetch(
            """SELECT extract(year FROM a.occurred_on)::integer AS year,
              extract(quarter FROM a.occurred_on)::integer AS quarter,
              sum(a.amount) FILTER(WHERE a.kind='recognized') AS recognized_amount,
              sum(a.amount) FILTER(WHERE a.kind='collection') AS collection_amount,
              count(*) FILTER(WHERE a.kind='recognized')::integer AS recognized_count,
              count(*) FILTER(WHERE a.kind='collection')::integer AS collection_count,
              count(*)::integer AS entry_count
            FROM crm.customer_actual a
            JOIN crm.opportunity o ON o.id=a.opportunity_id AND o.deleted_at IS NULL
            LEFT JOIN crm.customer c ON c.id=a.customer_id
            WHERE a.customer_id=$1::uuid AND a.opportunity_id=$2::uuid AND a.voided_at IS NULL
              AND a.occurred_on<=$3::date AND security.customer_reference(a.customer_id) IS NOT NULL
              AND ($4::uuid IS NULL OR COALESCE(o.owner_team_id,c.owner_team_id)=$4)
              AND ($5::uuid IS NULL OR COALESCE(o.owner_user_ref_id,c.owner_user_ref_id)=$5)
            GROUP BY extract(year FROM a.occurred_on),extract(quarter FROM a.occurred_on)
            ORDER BY year,quarter""",
            customer_id,
            opportunity_id,
            as_of,
            team_id,
            owner_id,
        )
        items = [dict(row) for row in rows]
        return {
            "items": items,
            "years": sorted({as_of.year, *(row["year"] for row in items)}, reverse=True),
            "as_of": as_of,
            "data_source": "database",
        }
