"""Shared customer membership; opportunity ownership is a separate boundary."""

import asyncio

import asyncpg

from sales_backend.db import json_value
from sales_backend.domain.customer_search import matching_names, phonetic_query


class CustomerMemberRepository:
    async def claim_pool_page(self, connection, *, query=None, limit=50, offset=0,
                              industry=None, claim_status=None, business_identity="all", order="name"):
        query = query.strip() if query else None
        phonetic = phonetic_query(query)
        names = []
        if phonetic:
            rows = await connection.fetch(
                "SELECT name FROM security.company_customer_directory_search_names($1) AS name", industry
            )
            names = await asyncio.to_thread(matching_names, [row["name"] for row in rows], phonetic)
        if order == "recent_follow_up":
            return json_value(await connection.fetchval(
                RECENT_CUSTOMER_PAGE_SQL, query, industry, claim_status, names, business_identity, limit, offset
            ))
        return json_value(await connection.fetchval(
            "SELECT security.company_customer_directory_search($1,$2,$3,$4,$5,$6::text[],$7)",
            query, limit, offset, industry, claim_status, names, business_identity,
        ))

    async def claim_pool_options(self, connection):
        industries = json_value(await connection.fetchval(
            "SELECT security.company_customer_directory_industries()"
        ))
        return {"industries": [{"value": "", "label": "全部行业"}, *industries],
                "claim_statuses": [
                    {"value": "", "label": "全部认领状态"},
                    {"value": "unclaimed", "label": "未认领"},
                    {"value": "claimed", "label": "已认领"},
                    {"value": "mine", "label": "本人已认领"},
                    {"value": "pending", "label": "我的申请待审批"},
                    {"value": "legacy_review", "label": "待运营核对"},
                ]}

    async def claim_pool(self, connection, *, query=None, limit=100):
        rows = await connection.fetch(
            "SELECT security.customer_claim_pool($1,$2) AS customer",
            query,
            limit,
        )
        return [json_value(row["customer"]) for row in rows]

    async def claim(self, connection, customer_id):
        try:
            return json_value(await connection.fetchval("SELECT security.claim_customer($1::uuid)", customer_id))
        except asyncpg.InsufficientPrivilegeError as exc:
            raise PermissionError("当前账号没有此客户的认领权限") from exc
        except asyncpg.NoDataFoundError as exc:
            raise LookupError("客户不存在") from exc
        except asyncpg.RaiseError as exc:
            raise ValueError(str(exc)) from exc

    async def contains(self, connection, customer_id, user_id):
        return await connection.fetchval(
            "SELECT EXISTS(SELECT 1 FROM crm.customer_sales_member WHERE customer_id=$1::uuid "
            "AND user_ref_id=$2::uuid)",
            customer_id,
            user_id,
        )


# V281 exposes only the existing claim-directory candidate scope. Visit facts
# are deliberately read here as the API role, so all visit RLS stays in effect.
RECENT_CUSTOMER_PAGE_SQL = """WITH candidates AS MATERIALIZED (
  SELECT id,name FROM security.company_customer_directory_candidates($1,$2,$3,$4::text[],$5)
), latest_visits AS MATERIALIZED (
  SELECT v.customer_id,max(v.interaction_at) AS latest_follow_up_at
  FROM activity.visit v
  WHERE v.workspace_id=common.current_workspace_id() AND v.deleted_at IS NULL
    AND v.status IN ('confirmed','archived') AND v.interaction_at IS NOT NULL
    AND v.customer_id IN (SELECT id FROM candidates)
  GROUP BY v.customer_id
), ranked AS MATERIALIZED (
  SELECT c.id,c.name,v.latest_follow_up_at FROM candidates c
  LEFT JOIN latest_visits v ON v.customer_id=c.id
), page AS MATERIALIZED (
  SELECT * FROM ranked ORDER BY latest_follow_up_at DESC NULLS LAST,name,id LIMIT $6 OFFSET $7
), page_ids AS (
  SELECT COALESCE(array_agg(id ORDER BY latest_follow_up_at DESC NULLS LAST,name,id),'{}'::uuid[]) AS ids
  FROM page
), totals AS (SELECT count(*) AS total FROM candidates)
SELECT jsonb_build_object('items',security.company_customer_directory_page_items(page_ids.ids),
  'total',totals.total,'has_more',$7+cardinality(page_ids.ids)<totals.total,
  'next_offset',CASE WHEN $7+cardinality(page_ids.ids)<totals.total THEN $7+cardinality(page_ids.ids) ELSE NULL END)
FROM page_ids CROSS JOIN totals"""
