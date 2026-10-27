"""Target SQL validation must not masquerade as a stale-version conflict."""
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import asyncpg
import pytest

from sales_backend.error_contract import error_response
from sales_backend.services import operations


@pytest.mark.asyncio
@pytest.mark.parametrize('operation,error,status,code', [
    ('review.target.save', asyncpg.InvalidParameterValueError('目标指标、金额或版本格式无效，请刷新表单'), 422, 'TARGET_INPUT_INVALID'),
    ('console.target.batch.save', asyncpg.InvalidParameterValueError('目标金额须为非负数且最多两位小数，数量须为非负整数'), 422, 'TARGET_VALUE_INVALID'),
    ('review.target.save', asyncpg.RaiseError('目标已更新，请刷新后再提交'), 409, 'VERSION_CONFLICT'),
    ('unrelated.save', asyncpg.InvalidParameterValueError('other'), 409, 'CONFLICT'),
])
async def test_target_input_rejection_preserves_conflict_boundary(monkeypatch, operation, error, status, code):
    @asynccontextmanager
    async def transaction(_actor):
        yield object()

    # asyncpg populates message from the server; direct exception construction does not.
    error.message = str(error)

    async def fail(*_args):
        raise error

    monkeypatch.setattr(operations, 'execute_mutation', fail)
    with pytest.raises(operations.OperationsError) as caught:
        await operations.management_write(SimpleNamespace(transaction=transaction), object(), 'key', operation, {}, None)
    exc = caught.value
    assert exc.status == status
    public = json.loads(error_response(status_code=exc.status, detail=str(exc)).body)
    assert public['code'] == code
    assert public['retryable'] is False
