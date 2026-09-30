"""Durable, self-scoped business Web weekly report jobs and versioned drafts."""
import hashlib
from dataclasses import asdict
from datetime import datetime, timedelta
from uuid import uuid4

import asyncpg
from fastapi import HTTPException

from sales_backend.integrations.supreme_fde import FdeClient, FdeConfig, FdeError
from sales_backend.repositories.jobs import record_job_effect
from sales_backend.services.authorization import require_permission
from sales_backend.services.weekly_source import assert_snapshot_access, build_snapshot, decode_snapshot, report_dates
from sales_backend.weekly_contract.decode_response import decode_response
from sales_backend.weekly_contract.validate_response import validate


class WeeklyOutputRejected(ValueError):
    """Keep transport receipts on rejection without storing unaccepted prose."""

    def __init__(self, code, metadata, errors=()):
        super().__init__(code)
        # Validator paths can include model-controlled unknown property names.
        # Persist only fixed categories; the upstream message ID locates details.
        categories = {
            'Statistics changed/invented': 'statistics_mismatch',
            'Period changed or invalid period preserved': 'period_mismatch',
            'Unknown visit source': 'unknown_evidence',
            'Unknown entity source': 'unknown_evidence',
            'Visit reference crosses customer/opportunity': 'cross_subject_evidence',
            'Entity reference crosses customer': 'cross_subject_evidence',
            'Entity reference crosses opportunity': 'cross_subject_evidence',
        }
        self.metadata = {**metadata, 'rejection_code': code,
            'validation_error_count': len(errors),
            'validation_errors': sorted({categories.get(error,
                'output_schema_invalid' if error.startswith('$') else 'output_rule_violation') for error in errors})}


class WeeklyReportService:
    def __init__(self, database, client_factory=FdeClient):
        self.database, self.settings, self.client_factory = database, database.settings, client_factory

    def binding(self, actor):
        s = self.settings
        if actor.workspace_id not in s.weekly_enabled_workspaces.split(',') or not (
            s.weekly_agent_api_key and s.weekly_agent_app_id and s.weekly_agent_snapshot_id
        ):
            raise HTTPException(503, 'WEEKLY_AGENT_NOT_CONFIGURED')
        # The only allowed runtime is this customer's independent middle platform.
        if s.agent_fde_base_url.rstrip('/') != 'https://ops-salesbuddy.shenzhoukuntai.com:18899/v1':
            raise HTTPException(503, 'WEEKLY_CUSTOMER_PLATFORM_REQUIRED')
        return {'app_id': s.weekly_agent_app_id, 'expected_snapshot_id': s.weekly_agent_snapshot_id,
                'contract_version': 'weekly.v2'}

    async def generate(self, actor, request_id, report_week=None):
        # An idempotent replay still works if the Agent was subsequently disabled.
        async with self.database.transaction(actor, readonly=True) as c:
            await require_permission(c, 'weekly_report.generate')
            old = await c.fetchrow('''SELECT r.*,j.status AS queue_status FROM insight.weekly_report r
                    LEFT JOIN ops.job j ON j.id=r.job_id WHERE r.request_id=$1::uuid''', request_id)
            if old:
                return await self.replay(c, old, report_week)
        binding = self.binding(actor)
        generation, job_id = str(uuid4()), str(uuid4())
        try:
            async with self.database.transaction(actor, isolation='repeatable_read') as c:
                source, raw, digest = await build_snapshot(c, actor, generation, report_week)
                week, cutoff = report_dates(datetime.fromisoformat(source['current_time']), report_week)
                if len(raw.encode()) > self.settings.weekly_max_input_bytes:
                    raise HTTPException(422, 'WEEKLY_CONTEXT_TOO_LARGE')
                empty = not source['records']
                result = None
                if empty:
                    result = dict(schema_version='weekly.v2', status='insufficient_data', title='周报',
                        period=source['period'], body_markdown='', sections=[], statistics=source['statistics'], warnings=[])
                if not empty:
                    await c.execute('''INSERT INTO ops.job(id,workspace_id,job_type,aggregate_type,aggregate_id,
                        payload,max_attempts) VALUES($1::uuid,$2::uuid,'weekly_report.generate','weekly_report',$3::uuid,$4,1)''',
                        job_id, actor.workspace_id, generation, actor.model_dump(mode='json'))
                row = await c.fetchrow('''INSERT INTO insight.weekly_report(id,workspace_id,author_id,request_id,
                    job_id,status,result_status,input_snapshot,input_sha256,binding,original_result,finished_at,report_week,source_cutoff_at)
                    VALUES($1::uuid,$2::uuid,$3::uuid,$4::uuid,$5::uuid,$6,$7,$8,$9,$10,$11,
                    CASE WHEN $6='succeeded' THEN clock_timestamp() END,$12,$13) RETURNING *''', generation,
                    actor.workspace_id, actor.user_id, request_id, None if empty else job_id,
                    'succeeded' if empty else 'queued', 'insufficient_data' if empty else None, raw, digest, binding, result, week, cutoff)
                return await self._view(c, row)
        except ValueError as exc:
            code = str(exc)
            raise HTTPException(422, code if code in {
                'WEEKLY_INVALID_REPORT_WEEK', 'WEEKLY_SOURCE_SUBJECT_UNSUPPORTED',
                'WEEKLY_SOURCE_ASSOCIATION_UNSUPPORTED'} else 'WEEKLY_INVALID_SOURCE') from None
        except asyncpg.UniqueViolationError:
            async with self.database.transaction(actor, readonly=True) as c:
                old = await c.fetchrow('''SELECT r.*,j.status AS queue_status FROM insight.weekly_report r
                    LEFT JOIN ops.job j ON j.id=r.job_id WHERE r.request_id=$1::uuid''', request_id)
                if old:
                    return await self.replay(c, old, report_week)
            raise HTTPException(409, 'WEEKLY_GENERATION_IN_PROGRESS') from None

    async def replay(self, connection, row, report_week):
        if report_week is not None and row['report_week'] != report_week:
            raise HTTPException(409, 'WEEKLY_REQUEST_ID_CONFLICT')
        return await self._view(connection, row)

    @staticmethod
    def source_page(source, week, cutoff, limit, offset):
        records = source['records']
        return dict(report_week=week, report_week_end=week + timedelta(days=6), period=source['period'],
            source_cutoff_at=cutoff, snapshot_at=source['context']['as_of'], statistics=source['statistics'],
            items=[{k: v for k, v in row.items() if k != 'source_ref'} for row in records[offset:offset+limit]],
            total=len(records), has_more=offset+limit < len(records),
            next_offset=offset+limit if offset+limit < len(records) else None)

    async def sources(self, actor, report_week, limit, offset):
        try:
            async with self.database.transaction(actor, readonly=True, isolation='repeatable_read') as c:
                source, _, _ = await build_snapshot(c, actor, str(uuid4()), report_week)
                week, cutoff = report_dates(datetime.fromisoformat(source['current_time']), report_week)
                return self.source_page(source, week, cutoff, limit, offset)
        except ValueError as exc:
            code = str(exc)
            raise HTTPException(422, code if code in {'WEEKLY_INVALID_REPORT_WEEK',
                'WEEKLY_SOURCE_SUBJECT_UNSUPPORTED', 'WEEKLY_SOURCE_ASSOCIATION_UNSUPPORTED'} else 'WEEKLY_INVALID_SOURCE') from None

    async def report_sources(self, actor, ident, limit, offset):
        async with self.database.transaction(actor, readonly=True) as c:
            await require_permission(c, 'weekly_report.read')
            row = await c.fetchrow('SELECT * FROM insight.weekly_report WHERE id=$1::uuid', ident)
            if not row:
                raise HTTPException(404, 'WEEKLY_REPORT_NOT_FOUND')
            source = decode_snapshot(row['input_snapshot'])
            await assert_snapshot_access(c, actor, source)
            return self.source_page(source, row['report_week'], row['source_cutoff_at'], limit, offset)

    @staticmethod
    def view(row, *, content=False):
        source = decode_snapshot(row['input_snapshot'])
        result = row['original_result']
        status, error = row['status'], row['error_code']
        # Queue expiry/rejection must not leave the browser polling forever.
        if status in ('queued', 'running') and row.get('queue_status') in ('failed', 'dead_letter', 'cancelled', 'succeeded'):
            status, error = 'failed', 'WEEKLY_WORKER_INTERRUPTED'
        view = dict(id=str(row['id']), request_id=str(row['request_id']), status=status,
            result_status=row['result_status'], period=source['period'], statistics=source['statistics'],
            report_week=row['report_week'], report_week_end=row['report_week']+timedelta(days=6),
            source_cutoff_at=row['source_cutoff_at'], snapshot_at=source['context']['as_of'],
            input_sha256=row['input_sha256'], draft_version=row['draft_version'], error_code=error,
            created_at=row['created_at'], updated_at=row['updated_at'], finished_at=row['finished_at'],
            runtime_snapshot_verified=False, actual_snapshot_id=None)
        # A publication event is not a delivery receipt. Only the DB status
        # reader below can label an already-published report as sent/failed.
        view['feishu'] = None if row.get('feishu_publish_event_id') else {'status': 'not_published'}
        # Validation checks references/structure; factual prose still requires human review.
        if content:
            view.update(title=result['title'] if result else None, body_markdown=row['draft_markdown'],
                original_result=result, draft_source=('manual' if row['draft_version'] > 1 else 'agent') if row['draft_version'] else None,
                draft_references_validated=row['draft_version'] == 1 and row['result_status'] == 'ready',
                runtime_metadata=row['runtime_metadata'])
        return view

    async def _view(self, connection, row, *, content=False):
        result = self.view(row, content=content)
        if row.get('feishu_publish_event_id'):
            result['feishu'] = await connection.fetchval(
                'SELECT security.weekly_feishu_status($1::uuid)', row['id'])
        return result

    async def publish_feishu(self, actor, ident, expected_version):
        async with self.database.transaction(actor) as c:
            await require_permission(c, 'weekly_report.edit')
            row = await c.fetchrow('SELECT * FROM insight.weekly_report WHERE id=$1::uuid FOR UPDATE', ident)
            if not row:
                raise HTTPException(404, 'WEEKLY_REPORT_NOT_FOUND')
            # A report may remain visible after its source records or the
            # author's scope changed. Re-check the frozen source immediately
            # before creating the external event.
            await assert_snapshot_access(c, actor, decode_snapshot(row['input_snapshot']))
            try:
                await c.fetchval('SELECT security.publish_weekly_report_feishu($1::uuid,$2)',
                                            ident, expected_version)
            except asyncpg.InsufficientPrivilegeError:
                raise HTTPException(404, 'WEEKLY_REPORT_NOT_FOUND') from None
            except asyncpg.PostgresError as exc:
                detail = str(exc).split('CONTEXT', 1)[0].strip()
                if detail in {'WEEKLY_DRAFT_NOT_READY', 'WEEKLY_DRAFT_VERSION_CONFLICT',
                              'WEEKLY_FEISHU_NOT_READY', 'WEEKLY_SYNTHETIC_TRIAL_SOURCE'}:
                    raise HTTPException(409, detail) from None
                raise
            row = await c.fetchrow('SELECT * FROM insight.weekly_report WHERE id=$1::uuid', ident)
            return await self._view(c, row, content=True)

    async def feishu_status(self, actor, ident):
        async with self.database.transaction(actor, readonly=True) as c:
            await require_permission(c, 'weekly_report.read')
            value = await c.fetchval('SELECT security.weekly_feishu_status($1::uuid)', ident)
            if value is None:
                raise HTTPException(404, 'WEEKLY_REPORT_NOT_FOUND')
            return value

    async def list(self, actor, limit, offset, report_week=None):
        async with self.database.transaction(actor, readonly=True) as c:
            await require_permission(c, 'weekly_report.read')
            rows = await c.fetch('''SELECT r.*,j.status AS queue_status FROM insight.weekly_report r
                LEFT JOIN ops.job j ON j.id=r.job_id WHERE ($3::date IS NULL OR r.report_week=$3)
                ORDER BY r.created_at DESC,r.id DESC LIMIT $1 OFFSET $2''', limit+1, offset, report_week)
            return dict(items=[await self._view(c, r) for r in rows[:limit]], has_more=len(rows)>limit,
                current_week=report_dates(await c.fetchval('SELECT transaction_timestamp()'))[0])

    async def read(self, actor, ident):
        async with self.database.transaction(actor, readonly=True) as c:
            await require_permission(c, 'weekly_report.read')
            row = await c.fetchrow('''SELECT r.*,j.status AS queue_status FROM insight.weekly_report r
                LEFT JOIN ops.job j ON j.id=r.job_id WHERE r.id=$1::uuid''', ident)
            if not row:
                raise HTTPException(404, 'WEEKLY_REPORT_NOT_FOUND')
            await assert_snapshot_access(c, actor, decode_snapshot(row['input_snapshot']))
            return await self._view(c, row, content=True)

    async def save(self, actor, ident, expected_version, body):
        async with self.database.transaction(actor) as c:
            await require_permission(c, 'weekly_report.edit')
            row = await c.fetchrow('SELECT * FROM insight.weekly_report WHERE id=$1::uuid FOR UPDATE', ident)
            if not row:
                raise HTTPException(404, 'WEEKLY_REPORT_NOT_FOUND')
            await assert_snapshot_access(c, actor, decode_snapshot(row['input_snapshot']))
            if row['status'] != 'succeeded' or row['result_status'] != 'ready':
                raise HTTPException(409, 'WEEKLY_DRAFT_NOT_READY')
            if row['draft_version'] != expected_version:
                raise HTTPException(409, 'WEEKLY_DRAFT_VERSION_CONFLICT')
            row = await c.fetchrow('''UPDATE insight.weekly_report SET draft_markdown=$2,
                draft_version=draft_version+1,updated_at=clock_timestamp() WHERE id=$1::uuid RETURNING *''', ident, body)
            await c.execute('''INSERT INTO insight.weekly_report_revision
                (workspace_id,author_id,report_id,version_no,body_markdown,source)
                VALUES($1::uuid,$2::uuid,$3::uuid,$4,$5,'manual')''', actor.workspace_id, actor.user_id, ident,
                row['draft_version'], body)
            return await self._view(c, row, content=True)

    async def cancel(self, actor, ident):
        async with self.database.transaction(actor) as c:
            await require_permission(c, 'weekly_report.cancel')
            row = await c.fetchrow('''UPDATE insight.weekly_report SET status='cancelled',
                finished_at=clock_timestamp(),updated_at=clock_timestamp() WHERE id=$1::uuid
                AND status IN ('queued','running') RETURNING *''', ident)
            if not row:
                row = await c.fetchrow('SELECT * FROM insight.weekly_report WHERE id=$1::uuid', ident)
            if not row:
                raise HTTPException(404, 'WEEKLY_REPORT_NOT_FOUND')
            # Logical cancellation discards late results. No claim that upstream compute stopped.
            return await self._view(c, row)

    async def handle(self, actor, ident):
        try:
            await self._run(actor, ident)
        except (FdeError, ValueError, PermissionError, HTTPException) as exc:
            code = 'WEEKLY_UPSTREAM_' + exc.code.upper() if isinstance(exc, FdeError) else (
                'WEEKLY_PERMISSION_CHANGED' if isinstance(exc, PermissionError) else 'WEEKLY_GENERATION_REJECTED')
            metadata = (exc.metadata if isinstance(exc, WeeklyOutputRejected) else
                dict(**asdict(exc.ids), dispatch_started=exc.dispatch_started) if isinstance(exc, FdeError) else {})
            async with self.database.transaction(actor) as c:
                await c.execute('''UPDATE insight.weekly_report SET status='failed',error_code=$2,runtime_metadata=$3,
                    finished_at=clock_timestamp(),updated_at=clock_timestamp() WHERE id=$1::uuid
                    AND status IN ('queued','running')''', ident, code, metadata)
                await record_job_effect(c, actor.workspace_id)

    async def _run(self, actor, ident):
        async with self.database.transaction(actor) as c:
            await require_permission(c, 'weekly_report.generate')
            row = await c.fetchrow('SELECT * FROM insight.weekly_report WHERE id=$1::uuid FOR UPDATE', ident)
            if not row or row['status'] in ('cancelled','succeeded','failed'):
                return
            if row['status'] != 'queued':
                # A previous attempt may have reached the model; never automatically resubmit.
                raise ValueError('WEEKLY_DISPATCH_OUTCOME_UNKNOWN')
            binding = self.binding(actor)
            if row['binding'] != binding:
                raise ValueError('WEEKLY_BINDING_CHANGED')
            source = decode_snapshot(row['input_snapshot'])
            await assert_snapshot_access(c, actor, source)
            await c.execute("UPDATE insight.weekly_report SET status='running',started_at=clock_timestamp() WHERE id=$1::uuid", ident)
        s = self.settings
        config = FdeConfig(s.agent_fde_base_url, s.weekly_agent_api_key, 'agent_final',
                           s.weekly_timeout_seconds, s.agent_fde_ca_bundle)
        user = 'weekly-' + hashlib.sha256(f'{actor.workspace_id}:{actor.user_id}'.encode()).hexdigest()
        async with self.client_factory(config) as client:
            info = await client.info()
            if info.get('name') != '神码-销售周报-weekly.v2':
                raise ValueError('WEEKLY_AGENT_NAME_MISMATCH')
            parameters = await client.parameters()
            if parameters.get('user_input_form'):
                raise ValueError('WEEKLY_UNEXPECTED_INPUT_FORM')
            response = await client.chat(query=row['input_snapshot'], user=user, inputs={}, conversation_id='')
        metadata = dict(**asdict(response.ids), input_tokens=response.input_tokens,
            output_tokens=response.output_tokens, expected_snapshot_id=binding['expected_snapshot_id'],
            actual_snapshot_id=None, runtime_snapshot_verified=False)
        try:
            result = decode_response(response.answer)
        except ValueError:
            raise WeeklyOutputRejected('WEEKLY_OUTPUT_JSON_INVALID', metadata) from None
        errors = validate(source, result)
        if errors:
            raise WeeklyOutputRejected('WEEKLY_OUTPUT_CONTRACT_INVALID', metadata, errors)
        async with self.database.transaction(actor) as c:
            await require_permission(c, 'weekly_report.generate')
            await assert_snapshot_access(c, actor, source)
            ready = result['status'] == 'ready'
            saved = await c.fetchval('''UPDATE insight.weekly_report SET status='succeeded',result_status=$2,
                original_result=$3,draft_markdown=$4,draft_version=$5,runtime_metadata=$6,
                finished_at=clock_timestamp(),updated_at=clock_timestamp()
                WHERE id=$1::uuid AND status='running' AND draft_version=0 RETURNING id''', ident,
                result['status'], result, result['body_markdown'] if ready else None, 1 if ready else 0,
                metadata)
            if saved and ready:
                await c.execute('''INSERT INTO insight.weekly_report_revision
                    (workspace_id,author_id,report_id,version_no,body_markdown,source)
                    VALUES($1::uuid,$2::uuid,$3::uuid,1,$4,'agent')''', actor.workspace_id, actor.user_id,
                    ident, result['body_markdown'])
            await record_job_effect(c, actor.workspace_id)
