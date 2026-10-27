"""Independent report HTTP surface; every read and idempotent replay rechecks access."""
from datetime import date, datetime, timezone
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Query

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity
from sales_backend.contracts.work_report import (
    DocumentQuery, DocumentSummary, DocumentView, ExportView, FactSource, Page, ReportOptions,
    RevisionCreate, RevisionView, RunCreate, RunView, SchemeCreate, SchemeUpdate, SchemeView,
    ShareRequest, SharePreviewRequest, ShareVisibility, SnapshotSelection, SnapshotView, SourceType, VersionRequest,
)
from sales_backend.contracts.work_report_meeting import MeetingOptions, MeetingSaveDraft, MeetingSelection, MeetingSnapshotView
from sales_backend.contracts.work_report_cancellation import SnapshotCancellationRequest, SnapshotCancellationState
from sales_backend.contracts.work_report_base import PeriodKind, PeriodSelection
from sales_backend.contracts.work_report_overview import GenerationProfile, OverviewScopeSelection, OverviewSelection
from sales_backend.contracts.work_report_overview_http import OverviewOptions, OverviewResponse
from sales_backend.contracts.review_scope import WorkReportScopeSelectionV2
from sales_backend.db import Database
from sales_backend.domain.work_report import WorkReportError, resolve_period
from sales_backend.repositories.work_report import WorkReportRepository
from sales_backend.repositories import work_report_snapshot_cancellation as snapshot_cancellation
from sales_backend.services.idempotency import execute_mutation
from sales_backend.services import work_report_meeting as meeting
from sales_backend.services import work_report_overview as overview
from sales_backend.services.work_report_read_recovery import OverviewReadTimeout, read_overview_with_recovery
from sales_backend.services.work_report_access import assert_permissions, report_options, resolve_scope

router = APIRouter(prefix='/api/v1/work-reports', tags=['work-reports'])
Identity = Annotated[RequestIdentity, Depends(get_identity)]
DB = Annotated[Database, Depends(get_database)]
Key = Annotated[UUID, Header(alias='Idempotency-Key', description='同一次提交和响应不确定后的重试使用同一UUID。')]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]
repository = WorkReportRepository()


async def _require(connection, permission):
    if not await connection.fetchval('SELECT security.authorization_has($1)', permission):
        raise WorkReportError('WORK_REPORT_PERMISSION_CHANGED', '当前没有此报告操作权限', status=403)


async def _read(db, actor, operation):
    async with db.transaction(actor, readonly=True, isolation='repeatable_read') as connection:
        await _require(connection, 'work_report.read')
        return await operation(connection)


async def _mutate(db, actor, key, operation, body, execute, *, preflight=None, recheck=None, permission='work_report.write',
                  isolation='read_committed'):
    async with db.transaction(actor, isolation=isolation) as connection:
        await _require(connection, 'work_report.read')
        await _require(connection, 'work_report.write')
        await _require(connection, permission)
        if preflight:
            await preflight(connection)
        result = await execute_mutation(connection, actor, key, operation, body.model_dump(mode='json'), lambda: execute(connection))
        # Receipt responses include derived text/metrics. Even create replays must
        # recheck their original object before returning a stored response body.
        if recheck:
            await recheck(connection, result)
        return result


def _id(result):
    return str(result['id'] if isinstance(result, dict) else result.id)


async def _enqueue(connection, actor, kind, ident):
    job_id = str(uuid4())
    await connection.execute("""INSERT INTO ops.job(id,workspace_id,job_type,aggregate_type,aggregate_id,payload,priority,correlation_id)
      VALUES($1::uuid,$2::uuid,$3,'work_report',$4::uuid,$5::jsonb,65,$4::uuid)""",
      job_id, actor.workspace_id, 'work_report.' + kind, str(ident), actor.model_dump(mode='json'))
    return job_id


@router.get('/options', response_model=ReportOptions)
async def options(identity: Identity, database: DB):
    return await _read(database, identity.actor, lambda c: report_options(c, identity.actor))


@router.get('/overview/options', response_model=OverviewOptions)
async def overview_options(identity: Identity, database: DB):
    return await _read(database, identity.actor, lambda c: overview.options(c, identity.actor))


@router.get('/overview', response_model=OverviewResponse)
async def overview_read(identity: Identity, database: DB, request_id: UUID,
                        period_kind: PeriodKind, scope_kind: Literal['self', 'member', 'team'],
                        anchor_date: date | None = None, member_id: UUID | None = None,
                        team_id: UUID | None = None, generation_profile: GenerationProfile = 'general'):
    from pydantic import ValidationError
    try:
        selection = OverviewSelection(period=PeriodSelection(kind=period_kind, anchor_date=anchor_date),
            scope=OverviewScopeSelection(kind=scope_kind, member_id=member_id, team_id=team_id),
            generation_profile=generation_profile)
    except ValidationError:
        raise WorkReportError('WORK_REPORT_INVALID_INPUT', '周期、范围或生成方案组合不合法', status=422) from None
    async def read_material(connection):
        try:
            return await overview.read(connection, identity.actor, selection, request_id, database.settings)
        except TimeoutError as exc:
            # Mark only the read operation. Transaction setup/rollback/release
            # timeouts must not start another attempt on an uncertain connection.
            raise OverviewReadTimeout() from exc

    # Each attempt closes its own readonly transaction before recovery. The
    # next attempt resolves current permissions, facts and the signed binding
    # together; no partial result from the failed attempt is reused.
    return await read_overview_with_recovery(lambda: _read(database, identity.actor, read_material))


@router.get('/meeting/options', response_model=MeetingOptions)
async def meeting_options(identity: Identity, database: DB):
    return await _read(database, identity.actor, lambda c: meeting.options(c, identity.actor))


@router.post('/meeting/snapshots', response_model=MeetingSnapshotView, status_code=202)
async def meeting_prepare(body: MeetingSelection, identity: Identity, database: DB, idempotency_key: Key):
    return await _mutate(database, identity.actor, idempotency_key, 'work_report.meeting.prepare', body,
        lambda c: meeting.prepare(c, identity.actor, body),
        preflight=lambda c: meeting.resolve(c, identity.actor, body.scope),
        recheck=lambda c, result: meeting.read(c, identity.actor, _id(result)), isolation='repeatable_read')


@router.get('/meeting/snapshots/{snapshot_id}', response_model=MeetingSnapshotView)
async def meeting_snapshot(snapshot_id: UUID, identity: Identity, database: DB):
    return await _read(database, identity.actor, lambda c: meeting.read(c, identity.actor, snapshot_id))


@router.post('/meeting/documents', response_model=DocumentView, status_code=201)
async def meeting_save(body: MeetingSaveDraft, identity: Identity, database: DB, idempotency_key: Key):
    return await _mutate(database, identity.actor, idempotency_key, 'work_report.meeting.save', body,
        lambda c: meeting.save(c, identity.actor, body),
        preflight=lambda c: meeting.validate_save(c, identity.actor, body),
        recheck=lambda c, result: repository.get_document(c, identity.actor, _id(result)), isolation='repeatable_read')


@router.get('/schemes', response_model=Page[SchemeView])
async def schemes(identity: Identity, database: DB, limit: Limit = 40, offset: Offset = 0):
    return await _read(database, identity.actor, lambda c: repository.list_schemes(c, identity.actor, limit=limit, offset=offset))


@router.post('/schemes', response_model=SchemeView, status_code=201)
async def create_scheme(body: SchemeCreate, identity: Identity, database: DB, idempotency_key: Key):
    return await _mutate(database, identity.actor, idempotency_key, 'work_report.scheme.create', body,
        lambda c: repository.create_scheme(c, identity.actor, body),
        recheck=lambda c, r: repository.get_scheme(c, identity.actor, _id(r)))


@router.get('/schemes/{scheme_id}', response_model=SchemeView)
async def scheme(scheme_id: UUID, identity: Identity, database: DB):
    return await _read(database, identity.actor, lambda c: repository.get_scheme(c, identity.actor, str(scheme_id)))


@router.put('/schemes/{scheme_id}', response_model=SchemeView)
async def update_scheme(scheme_id: UUID, body: SchemeUpdate, identity: Identity, database: DB, idempotency_key: Key):
    return await _mutate(database, identity.actor, idempotency_key, f'work_report.scheme.update:{scheme_id}', body,
        lambda c: repository.update_scheme(c, identity.actor, str(scheme_id), body),
        preflight=lambda c: repository.get_scheme(c, identity.actor, str(scheme_id)))


@router.post('/snapshots', response_model=SnapshotView, status_code=202)
async def create_snapshot(body: SnapshotSelection, identity: Identity, database: DB, idempotency_key: Key):
    actor = identity.actor
    async def check(c):
        await assert_permissions(c, actor, generate=bool(body.overview_binding), selected_types=body.sources)
        if body.overview_binding:
            await overview.validate_request(c, actor, body, database.settings)
    async def prepare(c):
        await check(c)
        # Array-selected Review reports use the v2 resolver. It freezes every
        # selected subject and its membership in one authorized snapshot;
        # falling back to resolve_scope would silently reject or collapse a
        # multi-team/member selection into the legacy single-scope contract.
        if isinstance(body.scope, WorkReportScopeSelectionV2):
            from sales_backend.services.work_report_access import resolve_review_generation_scope
            scope = (await resolve_review_generation_scope(c, actor, body.scope)).scope
        else:
            scope = await resolve_scope(c, actor, body.scope)
        period = ((await overview.validate_request(c, actor, body, database.settings))[2] if body.overview_binding
                  else resolve_period(body.period, now=datetime.now(timezone.utc)))
        view = await repository.create_snapshot(c, actor, body, period=period, scope=scope)
        job_id = await _enqueue(c, actor, 'snapshot', view.id)
        await repository.attach_snapshot_job(c, actor, str(view.id), job_id)
        return view
    return await _mutate(database, actor, idempotency_key, 'work_report.snapshot.create', body, prepare,
        preflight=check,
        recheck=lambda c, r: repository.get_snapshot(c, actor, _id(r)),
        isolation='repeatable_read' if isinstance(body.scope, WorkReportScopeSelectionV2) else 'read_committed')


@router.get('/snapshots/{snapshot_id}', response_model=SnapshotView)
async def snapshot(snapshot_id: UUID, identity: Identity, database: DB):
    return await _read(database, identity.actor, lambda c: repository.get_snapshot(c, identity.actor, str(snapshot_id)))


@router.get('/snapshots/{snapshot_id}/sources', response_model=Page[FactSource])
async def sources(snapshot_id: UUID, identity: Identity, database: DB, limit: Limit = 40, offset: Offset = 0, source_type: SourceType | None = None):
    return await _read(database, identity.actor, lambda c: repository.list_sources(c, identity.actor, str(snapshot_id),
        source_type=source_type, limit=limit, offset=offset))


@router.get('/snapshots/{snapshot_id}/cancellation', response_model=SnapshotCancellationState)
async def snapshot_cancellation_state(snapshot_id: UUID, identity: Identity, database: DB):
    return await _read(database, identity.actor,
        lambda c: snapshot_cancellation.cancellation_state(c, identity.actor, str(snapshot_id)))


@router.post('/snapshots/{snapshot_id}/cancel', response_model=SnapshotView)
async def cancel_snapshot(snapshot_id: UUID, body: SnapshotCancellationRequest,
                          identity: Identity, database: DB, idempotency_key: Key):
    return await _mutate(database, identity.actor, idempotency_key, f'work_report.snapshot.cancel:{snapshot_id}', body,
        lambda c: snapshot_cancellation.cancel_snapshot(c, identity.actor, str(snapshot_id), body),
        preflight=lambda c: repository.get_snapshot(c, identity.actor, str(snapshot_id)),
        recheck=lambda c, _: repository.get_snapshot(c, identity.actor, str(snapshot_id)))


@router.post('/runs', response_model=RunView, status_code=202)
async def create_run(body: RunCreate, identity: Identity, database: DB, idempotency_key: Key):
    actor = identity.actor
    async def check(c):
        await assert_permissions(c, actor, generate=True)
        await repository.get_snapshot(c, actor, str(body.snapshot_id))
        if body.document_id:
            from sales_backend.domain.work_report_builtins import builtin_scheme
            if builtin_scheme(body.scheme) is None:
                await repository.get_scheme(c, actor, str(body.scheme.scheme_id))
            await repository.get_document(c, actor, str(body.document_id))
        else:
            await repository._saved_scheme(c, actor, body.scheme)
    async def create(c):
        view = await repository.create_run(c, actor, body, rules_version='work_report.v1', business_rules=(
            '参考范文仅影响表达，不计入业务事实和统计。', '生成内容先保存为私人草稿，须用户主动确认后分享。'))
        job_id = await _enqueue(c, actor, 'generate', view.id)
        return await repository.attach_run_job(c, actor, str(view.id), job_id)
    return await _mutate(database, actor, idempotency_key, 'work_report.run.create', body, create, preflight=check,
        recheck=lambda c, r: repository.get_run(c, actor, _id(r)), permission='agent.work_report')


@router.get('/runs/{run_id}', response_model=RunView)
async def run(run_id: UUID, identity: Identity, database: DB):
    return await _read(database, identity.actor, lambda c: repository.get_run(c, identity.actor, str(run_id)))


@router.post('/runs/{run_id}/cancel', response_model=RunView)
async def cancel_run(run_id: UUID, body: VersionRequest, identity: Identity, database: DB, idempotency_key: Key):
    return await _mutate(database, identity.actor, idempotency_key, f'work_report.run.cancel:{run_id}', body,
        lambda c: repository.cancel_run(c, identity.actor, str(run_id), body),
        preflight=lambda c: repository.get_run(c, identity.actor, str(run_id)))


@router.get('/documents', response_model=Page[DocumentSummary])
async def documents(identity: Identity, database: DB, q: Annotated[str | None, Query(max_length=120)] = None,
                    status: Literal['private', 'shared'] | None = None, limit: Limit = 40, offset: Offset = 0):
    query = DocumentQuery(q=q, status=status, limit=limit, offset=offset)
    return await _read(database, identity.actor, lambda c: repository.list_documents(c, identity.actor, query))


@router.get('/documents/{document_id}', response_model=DocumentView)
async def document(document_id: UUID, identity: Identity, database: DB):
    return await _read(database, identity.actor, lambda c: repository.get_document(c, identity.actor, str(document_id)))


@router.get('/documents/{document_id}/revisions', response_model=Page[RevisionView])
async def revisions(document_id: UUID, identity: Identity, database: DB, limit: Limit = 40, offset: Offset = 0):
    return await _read(database, identity.actor, lambda c: repository.list_revisions(c, identity.actor, str(document_id), limit=limit, offset=offset))


@router.get('/documents/{document_id}/revisions/{revision_id}', response_model=RevisionView)
async def revision(document_id: UUID, revision_id: UUID, identity: Identity, database: DB):
    return await _read(database, identity.actor, lambda c: repository.get_revision(c, identity.actor, str(document_id), str(revision_id)))


@router.post('/documents/{document_id}/revisions', response_model=DocumentView)
async def create_revision(document_id: UUID, body: RevisionCreate, identity: Identity, database: DB, idempotency_key: Key):
    return await _mutate(database, identity.actor, idempotency_key, f'work_report.document.revise:{document_id}', body,
        lambda c: repository.create_revision(c, identity.actor, str(document_id), body),
        preflight=lambda c: repository.assert_document_readable(c, identity.actor, str(document_id)))


@router.post('/documents/{document_id}/share-preview', response_model=ShareVisibility)
async def share_preview(document_id: UUID, body: SharePreviewRequest, identity: Identity, database: DB):
    return await _read(database, identity.actor,
        lambda c: repository.share_preview(c, identity.actor, str(document_id), body))


@router.post('/documents/{document_id}/share', response_model=DocumentView)
async def share(document_id: UUID, body: ShareRequest, identity: Identity, database: DB, idempotency_key: Key):
    from sales_backend.services.work_report_access import validate_audience
    async def check(c):
        await repository.get_document(c, identity.actor, str(document_id))
        await validate_audience(c, identity.actor, body.audience)
    return await _mutate(database, identity.actor, idempotency_key, f'work_report.document.share:{document_id}', body,
        lambda c: repository.share(c, identity.actor, str(document_id), body), preflight=check, permission='work_report.share')


@router.post('/documents/{document_id}/unshare', response_model=DocumentView)
async def unshare(document_id: UUID, body: VersionRequest, identity: Identity, database: DB, idempotency_key: Key):
    return await _mutate(database, identity.actor, idempotency_key, f'work_report.document.unshare:{document_id}', body,
        lambda c: repository.unshare(c, identity.actor, str(document_id), body),
        preflight=lambda c: repository.get_document(c, identity.actor, str(document_id)), permission='work_report.share')


@router.get('/documents/{document_id}/export', response_model=ExportView)
async def export(document_id: UUID, identity: Identity, database: DB,
                 format: Literal['markdown', 'text'] = 'markdown', revision_id: UUID | None = None):
    return await _read(database, identity.actor, lambda c: repository.export(c, identity.actor, str(document_id),
        format=format, revision_id=str(revision_id) if revision_id else None))
