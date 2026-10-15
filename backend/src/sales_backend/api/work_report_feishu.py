"""Explicit author-confirmed publication of a frozen report revision to Shenma Feishu."""

from typing import Annotated, Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field, field_validator

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity
from sales_backend.api.work_reports import _mutate, _read
from sales_backend.db import Database, json_value
from sales_backend.domain.work_report import WorkReportError, render_report_text
from sales_backend.repositories.work_report import WorkReportRepository

router = APIRouter(prefix="/api/v1/work-reports/documents", tags=["Shenma work report publication"])
Identity = Annotated[RequestIdentity, Depends(get_identity)]
DB = Annotated[Database, Depends(get_database)]
Key = Annotated[UUID, Header(alias="Idempotency-Key")]
repository = WorkReportRepository()


class PublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1, strict=True)
    revision_id: UUID
    confirm: Literal[True]

    @field_validator("confirm", mode="before")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("请明确确认将此版本推送至飞书")
        return value


def publication_error(exc):
    """Expose only fixed messages; SQL bodies and source facts never enter an error response."""
    if exc.sqlstate == "22023" and getattr(exc, "message", None) in {
        "WORK_REPORT_FEISHU_ALREADY_PUBLISHED", "WORK_REPORT_FEISHU_VERSION_CONFLICT",
    }:
        return WorkReportError("WORK_REPORT_VERSION_CONFLICT", "报告已更新或已有冻结发布版本，请刷新核对", status=409)
    status, code, message = {
        "42501": (403, "WORK_REPORT_PERMISSION_CHANGED", "当前报告或来源不可推送至飞书"),
        "22023": (422, "WORK_REPORT_INVALID_INPUT", "此报告不符合飞书发布条件，请核对来源及配置"),
        "40001": (409, "WORK_REPORT_VERSION_CONFLICT", "报告已更新，请刷新后确认要发布的版本"),
        "23505": (409, "WORK_REPORT_VERSION_CONFLICT", "此报告版本已有发布记录，请查看推送状态"),
    }[exc.sqlstate]
    return WorkReportError(code, message, status=status)


@router.post("/{document_id}/feishu/publish")
async def publish(document_id: UUID, body: PublishRequest, identity: Identity, database: DB, idempotency_key: Key):
    async def preflight(connection):
        document = await repository.get_document(connection, identity.actor, str(document_id))
        if str(document.owner_id) != identity.actor.user_id or not document.can_share:
            raise WorkReportError("WORK_REPORT_PERMISSION_CHANGED", "仅报告作者可确认推送", status=403)

    async def execute(connection):
        # Publishing uses the existing share scope, not the separate permission
        # to download/export reports. Re-read every source and render the same
        # frozen content/metrics used by export; never accept caller prose.
        document = await repository.get_document(connection, identity.actor, str(document_id))
        if str(document.owner_id) != identity.actor.user_id or not document.can_share:
            raise WorkReportError("WORK_REPORT_PERMISSION_CHANGED", "仅报告作者可确认推送", status=403)
        if document.version_no != body.expected_version or document.revision.id != body.revision_id:
            raise WorkReportError("WORK_REPORT_VERSION_CONFLICT", "报告已更新，请刷新后确认发布版本", status=409)
        markdown = render_report_text(document.revision.content,
                                      {metric.metric_id: metric.display_value for metric in document.metrics},
                                      markdown=True,
                                      **({"review_facts": document.review_facts.model_dump(mode="json")}
                                         if document.review_facts is not None else {}))
        try:
            event_id = await connection.fetchval(
                "SELECT security.publish_work_report_feishu($1::uuid,$2,$3::uuid,$4)",
                str(document_id), body.expected_version, str(body.revision_id), markdown,
            )
        except asyncpg.PostgresError as exc:
            if exc.sqlstate in {"42501", "22023", "40001", "23505"}:
                raise publication_error(exc) from None
            raise
        return {"document_id": str(document_id), "revision_id": str(body.revision_id),
                "event_id": str(event_id), "status": "queued"}

    return await _mutate(database, identity.actor, idempotency_key,
                         f"work_report.feishu.publish:{document_id}", body, execute,
                         preflight=preflight, permission="work_report.share")


@router.get("/{document_id}/feishu")
async def status(document_id: UUID, identity: Identity, database: DB):
    async def read(connection):
        await repository.get_document(connection, identity.actor, str(document_id))
        try:
            return json_value(await connection.fetchval(
                "SELECT security.work_report_feishu_status($1::uuid)", str(document_id)
            ))
        except asyncpg.PostgresError as exc:
            if exc.sqlstate in {"42501", "22023", "40001", "23505"}:
                raise publication_error(exc) from None
            raise
    return await _read(database, identity.actor, read)
