"""V2 explicit object basis, canonical reasons, and the unchanged legacy boundary."""

import hashlib
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncpg
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from sales_backend.api.advice import SuggestionDecision
from sales_backend.api.tasks import alignment_capabilities, router
from sales_backend.contracts.models import TaskBatchCreate, TaskCreate
from sales_backend.contracts.task_alignment import (
    ECMASCRIPT_WHITESPACE,
    TaskAlignmentInput,
    TaskAlignmentUpdate,
    TaskAlignmentV2Input,
    legacy_safe_task_payload,
)
from sales_backend.contracts.task_imports import TaskImportConfirmedItem
from sales_backend.domain.task_imports import confirmation_hash
from sales_backend.services.task_alignment import save_task_alignment

ID = "10000000-0000-4000-8000-000000000001"
KR = "20000000-0000-4000-8000-000000000002"
TASK = {
    "description": "请核实合成客户后汇报",
    "assignee_account_code": "XS001",
    "due_at": datetime(2030, 1, 2, tzinfo=UTC),
}


def request(alignment):
    return TaskCreate(**TASK, alignment_contract_version=2, alignment=alignment)


@pytest.mark.parametrize(
    "objects", [{"target_ids": [ID, ID]}, {"key_result_ids": [KR, KR]}, {"target_ids": [ID], "key_result_ids": [KR]}]
)
def test_v2_selects_exact_objects_and_never_accepts_metrics(objects):
    item = request(objects)
    assert isinstance(item.alignment, TaskAlignmentV2Input)
    assert item.model_dump()["alignment_contract_version"] == 2
    assert len(item.alignment.target_ids) <= 1 and len(item.alignment.key_result_ids) <= 1
    assert "metric_codes" not in item.alignment.model_dump()


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"metric_codes": ["collection"]},
        {"target_ids": [ID], "other_reason": "也填其他"},
        {"other_reason": 1},
        {"other_reason": "x" * 2001},
        {"target_ids": ["not-uuid"]},
        {"other_reason": "必要原因", "expected_contribution": {"metric_code": "collection", "value": "2"}},
    ],
)
def test_v2_rejects_ambiguous_basis(bad):
    with pytest.raises(ValidationError):
        request(bad)


@pytest.mark.parametrize("version", [True, 1, 3, "2", 2.0])
def test_version_never_coerces_or_silently_downgrades(version):
    with pytest.raises(ValidationError):
        TaskCreate(**TASK, alignment_contract_version=version, alignment={"other_reason": "必要原因"})


@pytest.mark.parametrize("bad", [None, {"target_ids": [ID]}, {"other_reason": "必要原因"}])
def test_version_is_explicit_and_v2_alignment_required(bad):
    with pytest.raises(ValidationError):
        TaskCreate(**TASK, **({"alignment_contract_version": 2} if bad is None else {}), alignment=bad)


def test_every_ecmascript_trim_character_has_the_same_boundary():
    for whitespace in ECMASCRIPT_WHITESPACE:
        assert request({"other_reason": whitespace + "a\nb" + whitespace}).alignment.other_reason == "a\nb"
        with pytest.raises(ValidationError):
            request({"other_reason": whitespace})
    assert len(request({"other_reason": ECMASCRIPT_WHITESPACE + "x" * 2000}).alignment.other_reason) == 2000
    # Do not silently strip characters which JS trim does not remove.
    assert request({"other_reason": "\u0085"}).alignment.other_reason == "\u0085"


def test_reason_is_canonical_before_receipt_and_nested_legacy_shapes_do_not_drift():
    left = request({"other_reason": " \t必要原因\ufeff"})
    right = request({"other_reason": "必要原因"})
    assert left.model_dump() == right.model_dump()
    old = TaskCreate(**TASK).model_dump()
    assert "alignment_contract_version" not in old
    expected = legacy_safe_task_payload(old)
    for key, value in (("task", TASK), ("tasks", [TASK])):
        body = SuggestionDecision(decision="adopted", version_no=1, **{key: value})
        dumped = legacy_safe_task_payload(body.model_dump())
        assert dumped[key] == (expected if key == "task" else [expected])
    item = TaskImportConfirmedItem(**TASK, item_id=ID)
    original = item.model_dump(mode="json", exclude={"duplicate_acknowledged", "alignment"})
    assert "alignment_contract_version" not in original
    assert (
        confirmation_hash([item])
        == hashlib.sha256(json.dumps([original], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    )
    mixed = [left.model_dump(), {**TASK, "assignee_account_code": "XS002"}]
    with pytest.raises(ValidationError):
        TaskBatchCreate(tasks=mixed)


def test_patch_requires_protocol_and_preserves_old_payload_fingerprint():
    body = TaskAlignmentUpdate(version_no=3, alignment={"metric_codes": ["daily_routine"]})
    assert isinstance(body.alignment, TaskAlignmentInput)
    assert legacy_safe_task_payload(body.model_dump()) == {
        "version_no": 3,
        "alignment": {"metric_codes": ["daily_routine"], "key_result_ids": [], "expected_contribution": None},
    }
    body = TaskAlignmentUpdate(version_no=3, alignment_contract_version=2, alignment={"other_reason": " 原因 "})
    assert body.alignment.other_reason == "原因"


def test_v2_nested_advice_and_meeting_requests_keep_explicit_version_and_canonical_reason():
    task = {**TASK, "alignment_contract_version": 2, "alignment": {"other_reason": " 原因\ufeff"}}
    for key, value in (("task", task), ("tasks", [task])):
        body = SuggestionDecision(decision="adopted", version_no=1, **{key: value})
        nested = body.model_dump()[key]
        nested = nested[0] if isinstance(nested, list) else nested
        assert nested["alignment_contract_version"] == 2
        assert nested["alignment"]["other_reason"] == "原因"
    item = TaskImportConfirmedItem(**task, item_id=ID)
    assert item.model_dump()["alignment_contract_version"] == 2
    assert item.model_dump()["alignment"]["other_reason"] == "原因"
    canonical = TaskImportConfirmedItem(**{**task, "alignment": {"other_reason": "原因"}}, item_id=ID)
    assert confirmation_hash([item]) == confirmation_hash([canonical])


@pytest.mark.asyncio
async def test_database_payload_preserves_legacy_and_explicit_v2_discriminator():
    connection = SimpleNamespace(fetchval=AsyncMock(return_value={"contract_version": 2}))
    await save_task_alignment(connection, ID, {"other_reason": " 原因 "}, 3, contract_version=2)
    payload = connection.fetchval.call_args.args[2]
    assert payload == {
        "contract_version": 2,
        "target_ids": [],
        "key_result_ids": [],
        "other_reason": "原因",
        "expected_contribution": None,
    }
    await save_task_alignment(connection, ID, {"metric_codes": ["daily_routine"]}, 3)
    assert "contract_version" not in connection.fetchval.call_args.args[2]


@pytest.mark.asyncio
@pytest.mark.parametrize("migration,versions", [(True, [1, 2]), (False, [1])])
async def test_capabilities_requires_actual_migration_and_precedes_dynamic_route(migration, versions):
    connection = SimpleNamespace(fetchval=AsyncMock(return_value=migration))

    @asynccontextmanager
    async def transaction(actor, readonly):
        assert readonly
        yield connection

    result = await alignment_capabilities(SimpleNamespace(actor=object()), SimpleNamespace(transaction=transaction))
    assert result["supported_versions"] == versions
    assert "V244" in connection.fetchval.call_args.args[0]
    paths = [route.path for route in router.routes]
    assert paths.index("/api/v1/tasks/alignment-capabilities") < paths.index("/api/v1/tasks/{task_id}")


@pytest.mark.asyncio
async def test_capability_failure_is_not_a_false_version_claim():
    connection = SimpleNamespace(fetchval=AsyncMock(side_effect=asyncpg.InsufficientPrivilegeError("denied")))

    @asynccontextmanager
    async def transaction(actor, readonly):
        yield connection

    with pytest.raises(HTTPException) as failure:
        await alignment_capabilities(SimpleNamespace(actor=object()), SimpleNamespace(transaction=transaction))
    assert failure.value.status_code == 503
