"""Leased report jobs. No model call holds a business transaction open."""
from __future__ import annotations

import asyncio

from sales_backend.contracts.review_scope import ReviewResolvedScopeV2
from sales_backend.contracts.work_report import (
    GenerationInput,
    ReportError,
    ResolvedPeriod,
    ResolvedScope,
    ScopeSelection,
    SnapshotSelection,
)
from sales_backend.db import json_value
from sales_backend.domain.work_report import WorkReportError, canonical_hash
from sales_backend.job_context import JobLeaseLost, current_job_lease
from sales_backend.repositories.work_report_facts import assert_source_access, prepare_facts


def failure_error(exc: Exception, *, retryable: bool) -> ReportError:
    """Queue/projection diagnostics never contain SQL, prompts or provider text."""
    if isinstance(exc, WorkReportError):
        return ReportError(code=exc.code, detail='工作汇报状态或权限已变化，请刷新后重试',
                           retryable=exc.retryable, current_version=exc.current_version)
    return ReportError(code='WORK_REPORT_JOB_FAILED', detail='工作汇报处理失败，请稍后重试',
                       retryable=retryable)


def _lease():
    if current_job_lease.get() is None:
        raise JobLeaseLost('work-report execution requires a current queue lease')


def _scope_selection(scope):
    return ScopeSelection(kind=scope.kind, member_id=scope.member_id, team_id=scope.team_id)


async def _check(connection, actor, access, *, scope, selected_types, permission_version=None, generate=False):
    await access.assert_permissions(connection, actor, generate=generate, selected_types=selected_types)
    if isinstance(scope, ReviewResolvedScopeV2):
        from sales_backend.services.review_scope import assert_review_scope
        current = await assert_review_scope(connection, actor, scope)
        if current.scope != scope:
            raise WorkReportError('WORK_REPORT_PERMISSION_CHANGED', '报告范围已变化，请重新获取材料', status=403)
    else:
        resolved = await access.resolve_scope(connection, actor, _scope_selection(scope))
        if resolved != scope:
            raise WorkReportError('WORK_REPORT_PERMISSION_CHANGED', '报告范围已变化，请重新获取材料', status=403)
    current = await access.permission_version(connection, actor)
    if permission_version is not None and current != permission_version:
        raise WorkReportError('WORK_REPORT_PERMISSION_CHANGED', '报告权限版本已变化', status=403)
    return current


class WorkReportSnapshotHandler:
    def __init__(self, database, *, repository=None, access=None, facts=prepare_facts):
        if repository is None:
            from sales_backend.repositories.work_report import WorkReportRepository
            repository = WorkReportRepository()
        if access is None:
            from sales_backend.services import work_report_access as access
        self.database, self.repository, self.access, self.facts = database, repository, access, facts

    async def handle(self, snapshot_id, actor):
        _lease()
        async with self.database.transaction(actor) as connection:
            if await self.repository.get_completed_job(connection, actor, 'snapshot', snapshot_id) is not None:
                return
        async with self.database.transaction(actor, readonly=True, isolation='repeatable_read') as connection:
            row = await self.repository.snapshot_for_job(connection, actor, snapshot_id)
            selection = SnapshotSelection.model_validate(json_value(row['selection']))
            scope_payload = json_value(row['scope'])
            scope = (ReviewResolvedScopeV2.model_validate(scope_payload)
                     if scope_payload.get('contract_version') == 'review.scope.v2'
                     else ResolvedScope.model_validate(scope_payload))
            period = ResolvedPeriod.model_validate(json_value(row['period']))
            version = await _check(connection, actor, self.access, scope=scope, selected_types=selection.sources)
            if selection.overview_binding:
                from sales_backend.services.work_report_overview import prepare_bound
                facts = await prepare_bound(connection, actor, selection, self.database.settings, scope=scope, period=period)
            else:
                data_as_of = await connection.fetchval('SELECT transaction_timestamp()')
                facts = await self.facts(connection, selection, scope, period=period, data_as_of=data_as_of,
                                         **({'actor': actor} if selection.facts_contract or isinstance(scope, ReviewResolvedScopeV2) else {}))
            if selection.review_mode:
                from sales_backend.services.work_report_review_facts import prepare_review_facts
                facts.review_facts, facts.review_dependencies = await prepare_review_facts(
                    connection, actor, selection=selection, scope=scope, period=period,
                    data_as_of=facts.data_as_of, permission_version=version, mode=selection.review_mode)
            frozen = canonical_hash({'selection': selection.model_dump(mode='json'),
                                     'scope': scope.model_dump(mode='json'), 'period': period.model_dump(mode='json')})
        async with self.database.transaction(actor) as connection:
            current = await self.repository.snapshot_for_job(connection, actor, snapshot_id, lock=True)
            if canonical_hash({key: json_value(current[key]) for key in ('selection', 'scope', 'period')}) != frozen:
                raise WorkReportError('WORK_REPORT_CONFLICT', '报告材料请求已变化')
            await _check(connection, actor, self.access, scope=scope, selected_types=selection.sources,
                         permission_version=version)
            await assert_source_access(connection, sources=facts.sources, selected_types=selection.sources,
                **({'sales_week_facts': facts.sales_week_facts, 'actor': actor} if facts.sales_week_facts else {}))
            if facts.review_facts is not None:
                from sales_backend.services.work_report_review_facts import assert_review_access
                await assert_review_access(connection, actor, facts.review_facts,
                    dependencies=facts.review_dependencies, permission='work_report.write')
            # The repository locks the projection, rechecks sources and lease,
            # and records ops.job_effect in this exact transaction.
            await self.repository.complete_snapshot(connection, actor, snapshot_id,
                                                     facts=facts, permission_version=version)


async def _watched(invoke, check, *, interval):
    """Stop waiting on cancelled/revoked jobs; late provider output has no writer."""
    async def watch():
        while True:
            await asyncio.sleep(interval)
            await check()

    work, watcher = asyncio.create_task(invoke()), asyncio.create_task(watch())
    try:
        done, _ = await asyncio.wait((work, watcher), return_when=asyncio.FIRST_COMPLETED)
        if watcher in done:
            await watcher
        return await work
    finally:
        for task in (work, watcher):
            if not task.done():
                task.cancel()
        # A transport suppressing cancellation must not hold a worker forever.
        # It can only finish its discarded computation; persistence is below.
        done, pending = await asyncio.wait((work, watcher), timeout=1)
        for task in done:
            if not task.cancelled():
                task.exception()
        for task in pending:
            task.add_done_callback(lambda completed: None if completed.cancelled() else completed.exception())


class WorkReportGenerationHandler:
    def __init__(self, database, settings=None, *, repository=None, access=None, generate=None, poll_seconds=0.5):
        if repository is None:
            from sales_backend.repositories.work_report import WorkReportRepository
            repository = WorkReportRepository()
        if access is None:
            from sales_backend.services import work_report_access as access
        if generate is None:
            from sales_backend.services.work_report_providers import generate_report
            generate = generate_report
        self.database, self.settings = database, settings or database.settings
        self.repository, self.access, self.generate = repository, access, generate
        self.poll_seconds = poll_seconds

    async def handle(self, run_id, actor):
        _lease()
        async with self.database.transaction(actor) as connection:
            if await self.repository.get_completed_job(connection, actor, 'generate', run_id) is not None:
                return
            run = await self.repository.start_run(connection, actor, run_id)
            request = GenerationInput.model_validate(
                (await self.repository.generation_input(connection, actor, run_id)).model_dump(mode='json'))
            version = run.version_no
        selected = [item.source_type for item in request.coverage if item.selected]

        async def check_connection(connection, *, lock=False):
            await self.repository.assert_run_active(connection, actor, run_id, version_no=version,
                                                    request=request, lock=lock)
            await _check(connection, actor, self.access, scope=request.scope, selected_types=selected,
                         permission_version=request.permission_version, generate=True)
            await assert_source_access(connection, sources=request.sources, selected_types=selected,
                **({'sales_week_facts': request.sales_week_facts, 'actor': actor} if request.sales_week_facts else {}))

        async def check():
            _lease()
            async with self.database.transaction(actor, readonly=True) as connection:
                await check_connection(connection)

        await check()
        result = await _watched(
            lambda: self.generate(request.model_copy(deep=True), database=self.database, actor=actor,
                                  settings=self.settings, assert_active=check),
            check, interval=self.poll_seconds,
        )
        async with self.database.transaction(actor) as connection:
            await check_connection(connection, lock=True)
            await self.repository.complete_run(connection, actor, run_id, version_no=version,
                                               request=request, result=result)
