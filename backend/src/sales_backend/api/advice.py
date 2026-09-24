from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity, get_settings
from sales_backend.api.idempotency import MutationKey
from sales_backend.config import Settings
from sales_backend.contracts.models import TaskBatchCreate, TaskCreate
from sales_backend.db import Database
from sales_backend.domain.advice import AdviceError, AdviceRequest
from sales_backend.domain.concurrency import VersionConflict
from sales_backend.domain.tasks import TaskConflict, TaskForbidden, TaskNotFound
from sales_backend.repositories.advice import AdviceRepository
from sales_backend.services.advice import AdviceService
from sales_backend.services.idempotency import execute_mutation

router = APIRouter(prefix="/api/v1/advice", tags=["Business advice"])


class SuggestionDecision(BaseModel):
    decision: Literal["adopted", "no_task"]
    version_no: int = Field(ge=1)
    note: str = Field(default="", max_length=1000)
    task: TaskCreate | None = None
    tasks: list[TaskCreate] | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def one_task_form(self):
        if self.tasks is not None:
            if self.task is not None or self.decision != "adopted":
                raise ValueError("多人采纳只传 tasks，不同时传 task；无需待办时不得传任务")
            TaskBatchCreate(tasks=self.tasks)
        return self


@router.post("", description="请求经营建议。销售未关联商机的拜访可生成日常待办建议；已关联商机的拜访生成客户待办建议。")
async def request_advice(
    body: AdviceRequest,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
):
    try:
        return await AdviceService(database, settings).request(identity.actor, body)
    except AdviceError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@router.get("/statistics")
async def advice_statistics(
    identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database)
):
    async with database.transaction(identity.actor, readonly=True) as connection:
        return {**await AdviceRepository().statistics(connection), "definition_version": "handled_valid_suggestions_v1"}


@router.get("/{analysis_id}")
async def advice_detail(
    analysis_id: UUID,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
):
    try:
        return await AdviceService(database, settings).get(identity.actor, str(analysis_id))
    except AdviceError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@router.post("/suggestions/{suggestion_id}/decision", description="人工采纳建议。销售未关联商机的拜访必须使用 daily 且不传客户或商机；其余建议沿用客户任务关联校验。")
async def decide_suggestion(
    suggestion_id: UUID,
    body: SuggestionDecision,
    identity: RequestIdentity = Depends(get_identity),
    database: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
    idempotency_key: MutationKey = None,
):
    service = AdviceService(database, settings)
    try:
        async with database.transaction(identity.actor, readonly=True) as connection:
            kind = await AdviceRepository().suggestion_kind(connection, str(suggestion_id))
        if not kind:
            raise AdviceError("建议不存在或无权查看", 404)
        runtime = await service.runtime(identity.actor, kind)
        async with database.transaction(identity.actor) as connection:
            return await execute_mutation(
                connection,
                identity.actor,
                idempotency_key,
                "suggestion.decide:" + str(suggestion_id),
                body.model_dump(),
                lambda: service.decide(
                    connection,
                    identity.actor,
                    str(suggestion_id),
                    body.decision,
                    body.note,
                    body.version_no,
                    body.task,
                    runtime,
                    tasks=body.tasks,
                ),
            )
    except AdviceError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    except (VersionConflict, TaskConflict) as exc:
        raise HTTPException(409, str(exc)) from exc
    except TaskForbidden as exc:
        raise HTTPException(403, str(exc)) from exc
    except TaskNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
