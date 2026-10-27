"""Only expected domain SQL states become API errors; infrastructure faults surface."""

import asyncpg
import pytest

from sales_backend.services.task_execution import TaskExecutionError, read_execution


class FailedConnection:
    def __init__(self, error):
        self.error = error

    async def fetchval(self, *args):
        raise self.error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,status",
    [
        (asyncpg.InsufficientPrivilegeError("TASK_EXECUTION_FORBIDDEN"), 403),
        (asyncpg.NoDataFoundError("TASK_NOT_FOUND"), 404),
        (asyncpg.SerializationError("TASK_EXECUTION_VERSION_CONFLICT"), 409),
        (asyncpg.InvalidParameterValueError("TASK_ITEM_STATE_INVALID"), 422),
    ],
)
async def test_domain_sql_error_mapping(error, status):
    with pytest.raises(TaskExecutionError) as caught:
        await read_execution(FailedConnection(error), "00000000-0000-0000-0000-000000000001")
    assert caught.value.status == status
    assert caught.value.code == str(error)


@pytest.mark.asyncio
async def test_database_infrastructure_error_is_not_claimed_as_user_conflict():
    with pytest.raises(asyncpg.ConnectionDoesNotExistError):
        await read_execution(
            FailedConnection(asyncpg.ConnectionDoesNotExistError()), "00000000-0000-0000-0000-000000000001"
        )


@pytest.mark.asyncio
async def test_database_privilege_failure_is_not_mislabeled_as_task_state_conflict():
    error = asyncpg.InsufficientPrivilegeError("permission denied for function task_execution_person")
    with pytest.raises(asyncpg.InsufficientPrivilegeError) as caught:
        await read_execution(FailedConnection(error), "00000000-0000-0000-0000-000000000001")
    assert caught.value is error


@pytest.mark.asyncio
@pytest.mark.parametrize("present", [False, True])
async def test_omitted_and_null_contribution_preserve_legacy_receipt_body(present):
    from sales_backend.services.task_execution import execute_command

    class Capture:
        async def fetchval(self, sql, task, action, body, key):
            assert body == {"note": "evidence", "version_no": 1}
            return {"saved": True}

    body = {"note": "evidence", "version_no": 1}
    if present:
        body["actual_contribution"] = None
    assert await execute_command(Capture(), task_id="task", action="submit_completion", body=body, key="key") == {
        "saved": True
    }
    assert ("actual_contribution" in body) == present
