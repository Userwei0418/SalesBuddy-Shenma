"""Common transaction and error boundary for operations mutations."""

import asyncpg

from sales_backend.domain.concurrency import VersionConflict
from sales_backend.services.idempotency import execute_mutation


class OperationsError(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


async def management_write(database, actor, key, operation, payload, action, *, authorize=None):
    try:
        async with database.transaction(actor) as connection:
            if authorize is not None:
                await authorize(connection)
            return await execute_mutation(connection, actor, key, operation, payload, lambda: action(connection))
    except (VersionConflict, asyncpg.SerializationError, asyncpg.DeadlockDetectedError) as exc:
        raise OperationsError(str(exc), 409) from exc
    except asyncpg.InsufficientPrivilegeError as exc:
        raise OperationsError("当前账号无权执行此操作", 403) from exc
    except asyncpg.NoDataFoundError as exc:
        raise OperationsError("记录不存在", 404) from exc
    except asyncpg.UniqueViolationError as exc:
        if exc.constraint_name == "user_ref_global_phone_unique":
            raise OperationsError("手机号已被其他账号使用，请核对或联系管理员处理") from exc
        raise OperationsError("记录已存在，请刷新后核对") from exc
    except asyncpg.CheckViolationError as exc:
        if exc.constraint_name == "user_ref_demo_account_reserved":
            raise OperationsError("该账号名为演示专用，请使用其他账号名", 422) from exc
        raise OperationsError(exc.message, 422) from exc
    except (asyncpg.RaiseError, asyncpg.InvalidParameterValueError) as exc:
        raise OperationsError(exc.message, 409) from exc
    except asyncpg.PostgresError as exc:
        if exc.sqlstate == 'P7429':
            raise OperationsError('公司启用账号额度已用完，请停用不使用的账号或联系平台调整额度', 409) from exc
        raise
    except FileExistsError as exc:
        raise OperationsError(str(exc), 409) from exc
    except LookupError as exc:
        raise OperationsError(str(exc), 404) from exc
    except PermissionError as exc:
        raise OperationsError(str(exc), 403) from exc
    except ValueError as exc:
        raise OperationsError(str(exc), 422) from exc
