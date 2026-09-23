"""Common transaction and error boundary for operations mutations."""

import asyncpg
from sales_backend.domain.concurrency import VersionConflict

from sales_backend.services.idempotency import execute_mutation


class OperationsError(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


async def management_write(database, actor, key, operation, payload, action):
    try:
        async with database.transaction(actor) as connection:
            return await execute_mutation(connection, actor, key, operation, payload, lambda: action(connection))
    except VersionConflict as exc:
        raise OperationsError(str(exc), 409) from exc
    except asyncpg.InsufficientPrivilegeError as exc:
        raise OperationsError("当前账号无权执行此操作", 403) from exc
    except asyncpg.NoDataFoundError as exc:
        raise OperationsError("记录不存在", 404) from exc
    except asyncpg.UniqueViolationError as exc:
        raise OperationsError("记录已存在，请刷新后核对") from exc
    except (asyncpg.RaiseError, asyncpg.InvalidParameterValueError) as exc:
        raise OperationsError(exc.message, 409) from exc
    except FileExistsError as exc:
        raise OperationsError(str(exc), 409) from exc
    except LookupError as exc:
        raise OperationsError(str(exc), 404) from exc
    except PermissionError as exc:
        raise OperationsError(str(exc), 403) from exc
    except ValueError as exc:
        raise OperationsError(str(exc), 422) from exc
