from uuid import UUID
from datetime import date

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from sales_backend.api.dependencies import RequestIdentity, get_database
from sales_backend.api.management_dependencies import get_password_identity
from sales_backend.db import Database
from sales_backend.contracts.weekly_reports import WeeklySummary, WeeklyDetail, WeeklyList, WeeklySources
from sales_backend.services.weekly_reports import WeeklyReportService

router = APIRouter(prefix='/api/v1/web/weekly-reports', tags=['Business Web weekly reports'])


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: UUID
    report_week: date | None = None


class SaveDraftRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: int = Field(ge=1)
    body_markdown: str = Field(min_length=1, max_length=200000)


def service(response: Response, database: Database = Depends(get_database)):
    response.headers['Cache-Control'] = 'no-store'
    return WeeklyReportService(database)


@router.post('', status_code=202, response_model=WeeklySummary)
async def generate(body: GenerateRequest, identity: RequestIdentity = Depends(get_password_identity),
                   reports: WeeklyReportService = Depends(service)):
    return await reports.generate(identity.actor, str(body.request_id), body.report_week)


@router.get('', response_model=WeeklyList)
async def list_reports(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0, le=100000),
                       report_week: date | None = None,
                       identity: RequestIdentity = Depends(get_password_identity),
                       reports: WeeklyReportService = Depends(service)):
    return await reports.list(identity.actor, limit, offset, report_week)


@router.get('/sources', response_model=WeeklySources)
async def sources(report_week: date | None = None, limit: int = Query(50, ge=1, le=100),
                  offset: int = Query(0, ge=0, le=100000),
                  identity: RequestIdentity = Depends(get_password_identity),
                  reports: WeeklyReportService = Depends(service)):
    return await reports.sources(identity.actor, report_week, limit, offset)


@router.get('/{report_id}/sources', response_model=WeeklySources)
async def report_sources(report_id: UUID, limit: int = Query(50, ge=1, le=100),
                         offset: int = Query(0, ge=0, le=100000),
                         identity: RequestIdentity = Depends(get_password_identity),
                         reports: WeeklyReportService = Depends(service)):
    return await reports.report_sources(identity.actor, str(report_id), limit, offset)


@router.get('/{report_id}',  response_model=WeeklyDetail)
async def detail(report_id: UUID, identity: RequestIdentity = Depends(get_password_identity),
                 reports: WeeklyReportService = Depends(service)):
    return await reports.read(identity.actor, str(report_id))


@router.patch('/{report_id}/draft', response_model=WeeklyDetail)
async def save_draft(report_id: UUID, body: SaveDraftRequest,
                     identity: RequestIdentity = Depends(get_password_identity),
                     reports: WeeklyReportService = Depends(service)):
    return await reports.save(identity.actor, str(report_id), body.expected_version, body.body_markdown)


@router.post('/{report_id}/cancel', response_model=WeeklySummary)
async def cancel(report_id: UUID, identity: RequestIdentity = Depends(get_password_identity),
                 reports: WeeklyReportService = Depends(service)):
    return await reports.cancel(identity.actor, str(report_id))
