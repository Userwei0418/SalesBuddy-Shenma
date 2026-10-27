"""Same-customer confirmation changes keep source ownership and both live gates."""

from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from sales_backend.contracts.visit_flow import QualityRequest, canonical_fields
from sales_backend.services import visit_flow as flow


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "attach",
        "replace",
        "clear",
        "unchanged",
        "missing_source",
        "other_customer",
        "quality_source",
        "import_changed",
        "source_revoked",
        "target_revoked",
        "source_deleted",
    ],
)
async def test_quality_binding_uses_current_target_without_losing_source_guards(monkeypatch, case):
    customer, old, target = str(uuid4()), str(uuid4()), str(uuid4())
    original_id = old if case in {"replace", "clear", "source_deleted", "unchanged"} else None
    selected_id = None if case == "clear" else old if case == "unchanged" else target
    body = QualityRequest(
        customer_id=customer,
        opportunity_id=selected_id,
        source_run_id=str(uuid4()),
        fields=canonical_fields(
            {
                "customer_type": "客户",
                "customer_name": "合成客户",
                "follow_up_record": "陈经理确认样本通过，待补齐版本信息。",
                "next_action": "2026年10月21日提交补充样本",
                "contact_name": "陈经理",
                "interaction_at": "2026-10-20",
                "created_date": "2026-10-20",
            }
        ),
        summary="样本复核",
    )
    source = {
        "business_context": {
            "customer_id": customer,
            "opportunity_id": original_id,
            "visit_request": {"stage": "structure", "source_import_id": None},
        },
        "text_content": "本人原始拜访记录",
        "created_at": datetime(2026, 10, 19, tzinfo=UTC),
        "structure_created_date": "2026-10-19",
    }
    if case == "other_customer":
        source["business_context"]["customer_id"] = str(uuid4())
    if case == "quality_source":
        source["business_context"]["visit_request"]["stage"] = "quality"
    if case == "import_changed":
        source["business_context"]["visit_request"]["source_import_id"] = str(uuid4())
    before = deepcopy(source)
    gates = []

    async def gate(connection, actor, request, stage):
        gates.append((request.opportunity_id, stage))
        if (case == "source_revoked" and stage == "structure") or (case == "target_revoked" and stage == "quality"):
            raise PermissionError("synthetic revoked scope")

    async def fetchval(query, *args):
        assert "deleted_at IS NULL" in query and args[1] == customer
        return None if case == "source_deleted" and args[0] == old else args[0]

    conn = SimpleNamespace(fetchval=fetchval)
    repo = SimpleNamespace(
        create_conversation=AsyncMock(return_value={"id": "conversation"}),
        enqueue_message=AsyncMock(return_value="review-run"),
    )
    monkeypatch.setattr(flow, "require_visit_run_scope", gate)
    monkeypatch.setattr(flow, "follow_up_reference", AsyncMock(return_value={"name": "合成客户"}))
    monkeypatch.setattr(flow, "structure_source", AsyncMock(return_value=None if case == "missing_source" else source))
    monkeypatch.setattr(flow, "AssistantRepository", lambda: repo)
    monkeypatch.setattr(flow, "validate_visit_optional_actions", AsyncMock())
    monkeypatch.setattr(flow, "validate_collaborators", AsyncMock())
    monkeypatch.setattr(flow, "validate_visit_attendance", AsyncMock(return_value=[]))
    if case not in {"attach", "replace", "clear", "unchanged"}:
        with pytest.raises((ValueError, PermissionError)):
            await flow.prepare_visit_run(conn, object(), body, "quality")
        repo.enqueue_message.assert_not_awaited()
    else:
        assert await flow.prepare_visit_run(conn, object(), body, "quality") == {"run_id": "review-run"}
        assert gates == (
            [(selected_id, "quality")]
            if case == "unchanged"
            else [(selected_id, "quality"), (original_id, "structure")]
        )
        saved = repo.enqueue_message.call_args.kwargs
        request = saved["business_context"]["visit_request"]
        assert saved["text"] == source["text_content"]
        assert request["opportunity_id"] == selected_id
        assert request["source_opportunity_id"] == original_id
        assert request["source_run_id"] == body.source_run_id
        assert request["date_anchor"] == "2026-10-19"
        assert repo.create_conversation.call_args.kwargs["opportunity_id"] == selected_id
    assert source == before
