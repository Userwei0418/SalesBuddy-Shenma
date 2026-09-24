from __future__ import annotations

import uuid
from typing import Any

import asyncpg

from sales_backend.domain.agent import ActorContext
from sales_backend.domain.concurrency import require_version
from sales_backend.repositories.authorization_checks import require_permission
from sales_backend.repositories.customer_risk import enqueue_customer_risk_review
from sales_backend.repositories.jobs import enqueue_battle_map_review
from sales_backend.repositories.visit_normalize import CONTACT_ROLE_CODES


class CustomerMutationRepository:
    async def update(
        self,
        connection: asyncpg.Connection,
        actor: ActorContext,
        *,
        customer_id: str,
        data: dict[str, Any],
        expected_version: int | None = None,
        management_profile: bool = False,
    ) -> dict[str, Any]:
        await require_permission(connection, 'customer.update', customer_id=customer_id)
        if not data:
            raise ValueError("请至少修改一项客户信息")
        customer = await connection.fetchrow(
            """SELECT id::text, name, owner_user_ref_id::text, owner_team_id::text, version_no,
                 industry_code, customer_type_code, source_code, level_code,
                 primary_partner_name, demand_summary, next_action,
                 operation_type, cooperation_years, main_business, customer_budget
                 FROM crm.customer
                WHERE id = $1::uuid AND deleted_at IS NULL
                FOR UPDATE""",
            customer_id,
        )
        if not customer:
            raise LookupError("客户不存在或不在当前权限范围内")
        require_version(customer["version_no"], expected_version)
        mapping = {
            "name": "name",
            "industry": "industry_code",
            "customer_type": "customer_type_code",
            "level_code": "level_code",
            "source": "source_code",
            "partner_name": "primary_partner_name",
            "demand_summary": "demand_summary",
            "next_action": "next_action",
        }
        if management_profile:
            mapping.update({key: key for key in (
                "operation_type", "cooperation_years", "main_business", "customer_budget",
            )})
        contact_mapping = {
            "contact_name": "name", "contact_title": "title", "contact_role": "relationship_role_code",
            "contact_phone": "phone", "contact_email": "email",
        }
        if set(data) - (mapping.keys() | contact_mapping.keys()):
            raise ValueError("包含不可修改的客户字段")
        labels = {
            "name": "客户名称",
            "industry": "行业",
            "customer_type": "客户类型",
            "level_code": "客户优先级",
            "source": "客户来源",
            "partner_name": "所属伙伴",
            "demand_summary": "需求摘要",
            "next_action": "下一步行动",
            "contact_name": "联系人",
            "contact_title": "联系人职位",
            "contact_role": "联系人角色",
            "contact_phone": "联系电话", "contact_email": "联系人邮箱",
            "operation_type": "经营分类", "cooperation_years": "合作年限",
            "main_business": "主营业务", "customer_budget": "客户预算",
        }
        prior = {key: customer[col] for key, col in mapping.items()}
        current_contact = await connection.fetchrow(
            "SELECT id::text,name,title,relationship_role_code,phone,email FROM crm.contact WHERE customer_id=$1::uuid "
            "AND deleted_at IS NULL ORDER BY is_primary DESC,created_at,id LIMIT 1",
            customer_id,
        )
        if current_contact:
            prior.update(
                contact_phone=current_contact["phone"],
                contact_email=current_contact["email"],
                contact_name=current_contact["name"],
                contact_title=current_contact["title"],
                contact_role={v: k for k, v in CONTACT_ROLE_CODES.items()}.get(
                    current_contact["relationship_role_code"]
                ),
            )
        # Omitted fields never change. Only the management contract allows an
        # explicit null cooperation_years; zero is a real value, not "unfilled".
        def comparable(value):
            return "" if value is None else value

        updates = {
            key: value for key, value in data.items()
            if (value is not None or (management_profile and key == "cooperation_years"))
            and comparable(value) != comparable(prior.get(key))
        }
        if not updates:
            return {"id": customer_id, "name": customer["name"], "updated_fields": [], "changed": False}
        if not current_contact and any(key in contact_mapping for key in updates) and not data.get("contact_name"):
            raise ValueError("新增首要联系人时请填写姓名")
        changes = [
            {"label": labels[key],
             "before": "未填写" if prior.get(key) in (None, "") else prior[key],
             "after": "未填写" if value in (None, "") else value}
            for key, value in updates.items()
        ]
        if management_profile and "name" in updates:
            duplicate = await connection.fetchval(
                "SELECT EXISTS(SELECT 1 FROM crm.customer WHERE id<>$1::uuid AND deleted_at IS NULL "
                "AND normalized_name=lower(regexp_replace($2, '\\s+', '', 'g')))",
                customer_id, updates["name"],
            )
            if duplicate:
                raise FileExistsError("同名客户已存在，请核对后再修改")

        values = [customer_id]
        assignments = ["updated_at=clock_timestamp()", "version_no=version_no+1"]
        for key, column in mapping.items():
            if key in updates:
                values.append(updates[key])
                assignments.append(f"{column}=${len(values)}")
                if key == "name":
                    assignments.append(f"normalized_name=lower(regexp_replace(${len(values)}, '\\s+', '', 'g'))")
        # Columns come exclusively from the internal whitelist above, never from request keys.
        await connection.execute(
            "UPDATE crm.customer SET " + ",".join(assignments) + " WHERE id=$1::uuid", *values,  # noqa: S608
        )

        contact_updates = {key: value for key, value in updates.items() if key in contact_mapping}
        if contact_updates:
            if current_contact:
                values = [current_contact["id"]]
                assignments = ["updated_at=clock_timestamp()", "version_no=version_no+1"]
                for key, value in contact_updates.items():
                    values.append(CONTACT_ROLE_CODES.get(value) if key == "contact_role" else value)
                    assignments.append(f"{contact_mapping[key]}=${len(values)}")
                await connection.execute(
                    "UPDATE crm.contact SET " + ",".join(assignments) + " WHERE id=$1::uuid", *values,  # noqa: S608
                )
            else:
                await connection.execute(
                    """INSERT INTO crm.contact (
                         id, workspace_id, customer_id, name, title, relationship_role_code,
                         is_primary, created_by_user_ref_id, phone, email
                       ) VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, true, $7::uuid, $8, $9)""",
                    str(uuid.uuid4()), actor.workspace_id, customer_id, data["contact_name"],
                    data.get("contact_title"), CONTACT_ROLE_CODES.get(data.get("contact_role")), actor.user_id,
                    data.get("contact_phone", ""), data.get("contact_email", ""),
                )

        from sales_backend.repositories.business_changes import record_change

        version = await connection.fetchval("SELECT version_no FROM crm.customer WHERE id=$1::uuid", customer_id)
        review_id = await record_change(connection, actor, customer, version=version, changes=changes)
        await enqueue_battle_map_review(
            connection,
            actor,
            customer_id=customer_id,
            trigger_type="customer.updated",
            trigger_id=review_id,
        )
        await enqueue_customer_risk_review(
            connection,
            actor,
            customer_id=customer_id,
            trigger_type="customer.updated",
            trigger_id=review_id,
        )
        return {
            "id": customer_id,
            "name": data.get("name") or customer["name"],
            "updated_fields": sorted(updates),
            "battle_map_review": "queued",
        }

    async def create(
        self,
        connection: asyncpg.Connection,
        actor: ActorContext,
        *,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        await require_permission(connection, 'customer.create')
        if not str(data.get("company_reference") or "").strip():
            raise ValueError("请填写已核实的公司客户编号或审批单号")
        from sales_backend.repositories.team_directory import selectable_teams

        teams = await selectable_teams(connection, actor, 'assignment', permission='customer.create')
        target_id = data.get('target_team_id')
        matches = [team for team in teams if team['id'] == str(target_id)] if target_id else [
            team for team in teams if team['name'] == data['target_team']]
        if len(matches) != 1:
            raise PermissionError("请选择当前管理范围内的有效业务团队")
        team = matches[0]
        duplicate = await connection.fetchval(
            """
            SELECT EXISTS(SELECT 1 FROM crm.customer
              WHERE workspace_id = $1::uuid AND deleted_at IS NULL
                AND lower(regexp_replace(name, '\\s+', '', 'g')) = lower(regexp_replace($2, '\\s+', '', 'g')))
            """,
            actor.workspace_id,
            data["name"],
        )
        if duplicate:
            raise FileExistsError("客户已存在，请核对后再建档")
        customer_id = str(uuid.uuid4())
        contact_id = str(uuid.uuid4())
        sales_owner_id = None
        lifecycle_status = "lead"
        await connection.execute(
            """
            INSERT INTO crm.customer (
              id, workspace_id, name, normalized_name, industry_code, customer_type_code,
              source_code, lifecycle_status, owner_team_id, owner_user_ref_id, primary_partner_name,
              demand_summary, data_source, data_kind, created_by_user_ref_id, next_action, level_code,
              company_reference,
              company_verified_at, company_verified_by
            ) VALUES ($1::uuid, $2::uuid, $3, lower(regexp_replace($3, '\\s+', '', 'g')),
              $4, $5, $6, $7, $8::uuid, $9::uuid, $10, $11, 'manual', 'production',
              $12::uuid, $13, $14, $15, clock_timestamp(), $12::uuid)
            """,
            customer_id,
            actor.workspace_id,
            data["name"],
            data["industry"],
            data["customer_type"],
            data["source"],
            lifecycle_status,
            team["id"],
            sales_owner_id,
            data["partner_name"],
            None,
            actor.user_id,
            None,
            data["level_code"],
            data["company_reference"].strip(),
        )
        await connection.execute(
            """
            INSERT INTO crm.contact (
              id, workspace_id, customer_id, name, title, relationship_role_code, is_primary,
              created_by_user_ref_id, phone, email
            ) VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $7, true, $6::uuid, $8, $9)
            """,
            contact_id,
            actor.workspace_id,
            customer_id,
            data["contact_name"],
            data["contact_title"],
            actor.user_id,
            CONTACT_ROLE_CODES[data["contact_role"]],
            data.get("contact_phone", ""),
            data.get("contact_email", ""),
        )
        return {
            "id": customer_id,
            "name": data["name"],
            "industry": data["industry"],
            "customer_type": data["customer_type"],
            "level_code": data["level_code"],
            "source": data["source"],
            "target_team": team["name"],
            "partner_name": data["partner_name"],
            "contact_role": data["contact_role"],
            "contact_name": data["contact_name"],
            "contact_title": data["contact_title"],
            "next_action": None,
            "owner_name": None,
            "status": "unclaimed",
        }
