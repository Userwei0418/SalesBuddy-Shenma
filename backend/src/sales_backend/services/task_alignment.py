"""Alignment changes share the caller's task transaction and optimistic version."""

import asyncpg

from sales_backend.contracts.task_alignment import TaskActualContribution, alignment_request
from sales_backend.domain.tasks import TaskConflict, TaskForbidden, TaskNotFound
from sales_backend.repositories.task_alignment import TaskAlignmentRepository


async def _checked(operation):
    try:
        return await operation
    except asyncpg.InsufficientPrivilegeError as exc:
        raise TaskForbidden("无权修改任务关联，或所选目标/KR不可见") from exc
    except asyncpg.NoDataFoundError as exc:
        raise TaskNotFound("任务或关联记录不存在") from exc
    except (asyncpg.RaiseError, asyncpg.UniqueViolationError) as exc:
        raise TaskConflict(str(exc).splitlines()[0]) from exc
    except (asyncpg.InvalidParameterValueError, asyncpg.CheckViolationError) as exc:
        raise TaskConflict(str(exc).splitlines()[0]) from exc


async def save_task_alignment(connection, task_id, alignment, expected_version=None, *, contract_version=None):
    request = alignment_request({"alignment_contract_version": contract_version, "alignment": alignment})
    if request["alignment"] is None:
        return None  # Legacy request: no inferred metric, no new write.
    body = request["alignment"].model_dump(mode="json")
    if contract_version == 2:
        body["contract_version"] = 2
    return await _checked(
        TaskAlignmentRepository.save(
            connection,
            task_id,
            body,
            expected_version,
        )
    )


async def report_task_contribution(connection, task_id, contribution, expected_version):
    if contribution is None:
        return None  # Existing completion and review requests preserve all links.
    body = TaskActualContribution.model_validate(contribution)
    if expected_version is None:
        raise TaskConflict("填写实际贡献声明须提供任务当前版本")
    return await _checked(
        TaskAlignmentRepository.report(
            connection,
            task_id,
            body.model_dump(mode="json"),
            expected_version,
        )
    )
