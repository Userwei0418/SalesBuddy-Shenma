from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from sales_backend.contracts.customer_creation import (
    CustomerCreationRequestBody,
    CustomerCreationRequestDecision,
    CustomerCreationRequestPage,
)
from sales_backend.repositories import customer_creation_requests as module


def payload():
    return {
        "company_reference": "审批单-001", "name": "示例客户", "industry": "软件",
        "customer_type": "潜在客户", "level_code": "Tier-2", "source": "销售自拓",
        "target_team": "华东销售", "contact_name": "李四", "contact_title": "采购经理",
        "contact_role": "影响者", "contact_phone": "", "contact_email": "", "partner_name": "",
    }


def test_request_requires_verified_company_reference_and_rejection_reason():
    with pytest.raises(ValidationError):
        CustomerCreationRequestBody.model_validate({**payload(), "company_reference": ""})
    with pytest.raises(ValidationError):
        CustomerCreationRequestDecision(decision="rejected")
    assert CustomerCreationRequestDecision(decision="rejected", reason="重复客户").reason == "重复客户"


@pytest.mark.asyncio
async def test_submit_only_inserts_request(monkeypatch):
    class Connection:
        statements = []

        async def fetchval(self, sql, *args):
            assert "customer_name_is_available" in sql
            return True

        async def fetchrow(self, sql, *args):
            self.statements.append(sql)
            return {"request": {
                "id": "11111111-1111-1111-1111-111111111111", "status": "pending",
                "applicant_user_ref_id": "22222222-2222-2222-2222-222222222222",
                "payload": args[0], "requested_at": datetime.now(timezone.utc),
                "reviewed_at": None, "reviewer_user_ref_id": None,
                "decision_reason": "", "created_customer_id": None,
            }}

    async def allow(*args, **kwargs):
        return None

    async def teams(*args, **kwargs):
        return [{"id": "55555555-5555-5555-5555-555555555555", "name": "华东销售"}]

    monkeypatch.setattr(module, "require_permission", allow)
    monkeypatch.setattr(module, "selectable_teams", teams)
    actor = SimpleNamespace(workspace_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", user_id="22222222-2222-2222-2222-222222222222")
    result = await module.CustomerCreationRequestRepository().submit(Connection(), actor, payload())
    assert result["status"] == "pending"


@pytest.mark.asyncio
async def test_approval_reuses_customer_mutation_in_same_transaction(monkeypatch):
    class Connection:
        async def fetchval(self, sql, *args):
            if "customer_creation_request_lock" in sql:
                return {"id": args[0], "status": "pending", "payload": payload(),
                        "applicant_user_ref_id": "22222222-2222-2222-2222-222222222222"}
            assert "complete_customer_creation_request" in sql
            return {"id": args[0], "status": "approved", "payload": payload(),
                    "created_customer_id": "44444444-4444-4444-4444-444444444444"}

    async def allow(*args, **kwargs):
        return None

    class Mutation:
        async def create(self, connection, actor, *, data):
            assert data["name"] == "示例客户"
            return {"id": "44444444-4444-4444-4444-444444444444", "name": data["name"]}

    monkeypatch.setattr(module, "require_permission", allow)
    monkeypatch.setattr(module, "CustomerMutationRepository", lambda: Mutation())
    actor = SimpleNamespace(workspace_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", user_id="33333333-3333-3333-3333-333333333333")
    result = await module.CustomerCreationRequestRepository().decide(
        Connection(), actor, "11111111-1111-1111-1111-111111111111", "approved"
    )
    assert result["status"] == "approved"
    assert result["customer"]["name"] == "示例客户"


@pytest.mark.asyncio
@pytest.mark.parametrize("method,permission", [("mine", "customer.create_request"), ("queue", "customer.create_review")])
async def test_history_empty_page_preserves_total(monkeypatch, method, permission):
    allowed = []

    async def allow(connection, requested_permission):
        allowed.append(requested_permission)

    class Connection:
        async def fetchrow(self, sql, *args):
            assert "WITH records AS MATERIALIZED" in sql
            return {"items": [], "total": 7}

    monkeypatch.setattr(module, "require_permission", allow)
    actor = SimpleNamespace(workspace_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", user_id="22222222-2222-2222-2222-222222222222")
    result = await getattr(module.CustomerCreationRequestRepository(), method)(
        Connection(), actor, status="all", limit=5, offset=10,
    )
    assert result == {"items": [], "total": 7, "limit": 5, "offset": 10, "has_more": False, "next_offset": None}
    CustomerCreationRequestPage.model_validate(result)
    assert allowed == [permission]


def test_creation_permissions_have_exact_scopes():
    from sales_backend.domain.permission_catalog import CATALOG
    from sales_backend.domain.permission_defaults import default_permissions

    assert CATALOG['customer.create_request'].scopes == ('self',)
    assert CATALOG['customer.create_review'].scopes == ('workspace',)
    for role in ('sales', 'supervisor', 'manager'):
        grants = {item['permission']: item['scope'] for item in default_permissions(role)}
        assert grants['customer.create_request'] == 'self'
        assert 'customer.create_review' not in grants
    for role in ('operations', 'administrator'):
        grants = {item['permission']: item['scope'] for item in default_permissions(role)}
        assert grants['customer.create_review'] == 'workspace'
