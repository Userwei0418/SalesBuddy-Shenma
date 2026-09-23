"""Shared customer membership; opportunity ownership is a separate boundary."""

import asyncpg

from sales_backend.db import json_value


class CustomerMemberRepository:
    async def claim_pool_page(self, connection, *, query=None, limit=50, offset=0):
        return json_value(await connection.fetchval(
            "SELECT security.company_customer_directory_page($1,$2,$3)",
            query.strip() if query else None,
            limit,
            offset,
        ))

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
            raise PermissionError("仅有效的一线销售、销售主管或销售总经理身份可发起客户认领申请") from exc
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
