"""Shared customer membership; opportunity ownership is a separate boundary."""

import asyncio

import asyncpg

from sales_backend.db import json_value
from sales_backend.domain.customer_search import matching_names, phonetic_query


class CustomerMemberRepository:
    async def claim_pool_page(self, connection, *, query=None, limit=50, offset=0,
                              industry=None, claim_status=None):
        query = query.strip() if query else None
        phonetic = phonetic_query(query)
        names = []
        if phonetic:
            rows = await connection.fetch(
                "SELECT name FROM security.company_customer_directory_search_names($1) AS name", industry
            )
            names = await asyncio.to_thread(matching_names, [row["name"] for row in rows], phonetic)
        return json_value(await connection.fetchval(
            "SELECT security.company_customer_directory_search($1,$2,$3,$4,$5,$6::text[])",
            query, limit, offset, industry, claim_status, names,
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
