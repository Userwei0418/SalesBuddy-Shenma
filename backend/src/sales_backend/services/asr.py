"""The sole ASR dispatch path: tenant snapshots, durable receipts and no blind resend.

Completed results survive downstream failures. An expired/unknown send is never
turned into a fresh attempt. All reads refresh the actor's current authority.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import time
import wave
from dataclasses import replace
from uuid import UUID, uuid4, uuid5

from sales_backend.domain.asr import AsrError, SCENES, configuration_fingerprint, digest, model_info, selected_words
from sales_backend.domain.asr_terminology import (VERSION as SELECTION_VERSION, NORMALIZATION_VERSION,
                                                    normalize_acronyms, provider_words)
from sales_backend.integrations.senseaudio import EmptyTranscriptionError, SenseAudioClient, SenseAudioError, Transcription
from sales_backend.services.capabilities import require_capability
from sales_backend.services.model_calls import DatabaseModelObserver

logger = logging.getLogger(__name__)


async def require_asr(connection, actor, scene):
    if not actor.workspace_id or not actor.user_id or scene not in {*SCENES, 'legacy', 'diagnostic'}:
        raise AsrError('ASR_SCOPE_DENIED', 403)
    if scene not in {'visit_entry', 'visit_import', 'legacy', 'diagnostic'}:
        from sales_backend.services.authorization import require_permission

        await require_permission(connection, 'visit.transcribe')
    await require_capability(connection, actor, 'ai.config_test' if scene == 'diagnostic' else 'visit.transcribe')
    if scene in {'visit_import', 'legacy'}:
        await require_capability(connection, actor, 'visit.upload')
    elif scene == 'customer_create':
        from sales_backend.services.agent_access import require_agent_action
        await require_agent_action(connection, scene)
    elif scene != 'diagnostic':
        await require_capability(connection, actor, {'visit_entry': 'visit.structure', 'management_task': 'task.create_daily',
                                                    'chatbi': 'agent.chatbi', 'demo_scene': 'demo_scene.create'}[scene])


def public_snapshot(session):
    snapshot = session['snapshot']
    return {'session_id': str(session['id']), 'hotword_version': snapshot['version'],
            'hotword_count': len(snapshot['words']), 'enhancement_applied': snapshot['applied'],
            'enhancement_reason': snapshot['reason'], 'budget': {'preview_limit': 1}}


async def prepare_session(database, actor, settings, *, scene, session_id=None, language='zh'):
    sid = str(UUID(str(session_id))) if session_id else str(uuid4())
    config = configuration_fingerprint(settings)
    async with database.transaction(actor) as conn:
        await require_asr(conn, actor, scene)
        await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,282))",
                           'asr-session:' + actor.workspace_id + ':' + actor.user_id + ':' + sid)
        row = await conn.fetchrow('''SELECT * FROM activity.asr_session WHERE workspace_id=$1::uuid
            AND actor_id=$2::uuid AND id=$3::uuid''', actor.workspace_id, actor.user_id, sid)
        if row:
            if row['scene'] != scene or row['language'] != language:
                raise AsrError('ASR_OPERATION_CONFLICT')
            if row['configuration'] != config:
                raise AsrError('ASR_SNAPSHOT_CHANGED')
            return dict(row)
        terms = await conn.fetchval('SELECT security.asr_hotword_snapshot()')
        words = selected_words(terms['items'], scene)
        info = model_info(settings)
        applied = bool(info['supported'] and info['available'] and words)
        snapshot = {'version': terms['version'], 'words': list(words), 'selection_version': SELECTION_VERSION,
                    'provider_words': list(provider_words(words)), 'normalization_version': NORMALIZATION_VERSION,
                    'applied': applied, 'reason': 'applied' if applied else 'empty' if not words else 'model_unsupported',
                    'scene': scene, 'model': info['name']}
        await conn.execute('''INSERT INTO activity.asr_session
            (workspace_id,actor_id,id,scene,language,configuration,snapshot)
            VALUES($1::uuid,$2::uuid,$3::uuid,$4,$5,$6,$7::jsonb)''',
            actor.workspace_id, actor.user_id, sid, scene, language, config, snapshot)
        return {'workspace_id': actor.workspace_id, 'actor_id': actor.user_id, 'id': sid,
                'scene': scene, 'language': language, 'configuration': config, 'snapshot': snapshot}


async def operation_receipt(database, actor, operation_id, *, scene=None):
    async with database.transaction(actor, readonly=True) as conn:
        row = await conn.fetchrow('''SELECT a.*,s.scene,s.snapshot FROM activity.asr_attempt a
            JOIN activity.asr_session s ON (s.workspace_id,s.actor_id,s.id)=(a.workspace_id,a.actor_id,a.session_id)
            WHERE a.workspace_id=$1::uuid AND a.actor_id=$2::uuid AND a.operation_id=$3::uuid''',
            actor.workspace_id, actor.user_id, str(operation_id))
        if not row or (scene and row['scene'] != scene):
            raise AsrError('ASR_OPERATION_NOT_FOUND', 404)
        await require_asr(conn, actor, row['scene'])
        if row['state'] == 'linked':
            original = await conn.fetchrow('''SELECT * FROM activity.asr_attempt WHERE workspace_id=$1::uuid
                AND actor_id=$2::uuid AND operation_id=$3::uuid''', actor.workspace_id, actor.user_id, str(row['linked_operation_id']))
            if not original:
                raise AsrError('ASR_RESULT_UNKNOWN')
            row = {**dict(row), **{key: original[key] for key in ('state', 'response', 'error_code', 'created_at', 'transport_closed_at', 'capacity_released_at')}}
        state = row['state']
        if state == 'sending':
            # Lease expiration changes presentation only, never permits dispatch again.
            from datetime import UTC, datetime
            state = 'unknown' if (datetime.now(UTC) - row['created_at']).total_seconds() > 600 else 'processing'
        response = row['response'] or {}
        return {'status': state, 'operation_id': str(row['operation_id']), 'session_id': str(row['session_id']),
                'text': response.get('text') if state == 'completed' else None,
                'duration_seconds': response.get('duration_seconds'), 'trace_id': response.get('trace_id'),
                'capacity_scope': 'local_dispatch', 'local_transport_closed_at': row.get('transport_closed_at'),
                'capacity_released_at': row.get('capacity_released_at'),
                'source_text': response.get('source_text') if state == 'completed' else None,
                'normalization': response.get('normalization') if state == 'completed' else None,
                'asr': response.get('asr'), 'artifact_id': response.get('artifact_id'), 'purpose': row['scene'], 'error_code': row['error_code'], 'retryable': False}


def duration_seconds(content):
    # Every business caller supplies server-decoded PCM, never a client-provided duration.
    try:
        with wave.open(io.BytesIO(content)) as stream:
            return stream.getnframes() / stream.getframerate()
    except (wave.Error, EOFError, ZeroDivisionError):
        raise AsrError('ASR_AUDIO_INVALID', 422) from None


class DispatchObserver:
    def __init__(self, delegate):
        self.delegate, self.sent = delegate, False

    async def start(self, *args):
        return await self.delegate.start(*args)

    async def prepare_dispatch(self, *args):
        await self.delegate.prepare_dispatch(*args)
        self.sent = True  # conservative: the next operation is the network send

    async def finish(self, *args, **kwargs):
        return await self.delegate.finish(*args, **kwargs)


class AsrService:
    def __init__(self, database, actor, settings, session, *, client_factory=SenseAudioClient):
        if (str(session['workspace_id']), str(session['actor_id'])) != (actor.workspace_id, actor.user_id):
            raise AsrError('ASR_SCOPE_DENIED', 403)
        if session['configuration'] != configuration_fingerprint(settings):
            raise AsrError('ASR_SNAPSHOT_CHANGED')
        self.database, self.actor, self.settings, self.session = database, actor, settings, session
        self.client_factory = client_factory
        self.last_metadata = None
        self.last_artifact_id = None

    async def close(self):
        pass

    async def transcribe(self, *, filename, content, mime_type, language=None, stage='final',
                         operation_id=None, unit_key='full'):
        actor, session = self.actor, self.session
        language = language or session['language']
        if language != session['language'] or stage not in {'preview', 'final', 'diagnostic'}:
            raise AsrError('ASR_OPERATION_CONFLICT')
        if (stage == 'diagnostic') != (session['scene'] == 'diagnostic'):
            raise AsrError('ASR_SCOPE_DENIED', 403)
        op = str(operation_id or uuid5(UUID(str(session['id'])), stage + ':' + unit_key))
        fingerprint = {'workspace': actor.workspace_id, 'owner': actor.user_id, 'scene': session['scene'],
                       'audio': hashlib.sha256(content).hexdigest(), 'stage': stage,
                       'language': language, 'config': session['configuration'],
                       # Display-only rules must not trigger another paid ASR call.
                       'snapshot': {key: value for key, value in session['snapshot'].items()
                                    if key != 'normalization_version'},
                       'codec': 'pcm16k-mono-v1'}
        reuse = digest(fingerprint)
        request = digest({**fingerprint, 'session': str(session['id']), 'unit': unit_key})
        # Bind capacity to provider endpoint (shared across companies), never API keys.
        from urllib.parse import urlsplit
        provider = digest(urlsplit(self.settings.model_api_endpoint or self.settings.senseaudio_base_url).netloc)
        duration = duration_seconds(content)
        queued = time.monotonic()
        while True:
            async with self.database.transaction(actor) as conn:
                await require_asr(conn, actor, session['scene'])
                reservation = await conn.fetchval('SELECT security.reserve_asr_attempt($1::uuid,$2::uuid,$3,$4,$5,$6,$7,$8::numeric)',
                    op, str(session['id']), stage, unit_key, request, reuse, provider, duration)
            code = reservation.get('error')
            if code != 'ASR_CAPACITY_BUSY' or stage == 'preview' or time.monotonic() - queued >= 45:
                break
            # Poll admission only; this loop never sends or repeats a supplier request.
            await asyncio.sleep(.5)
        if code:
            raise AsrError(code, 429 if code in {'ASR_CAPACITY_BUSY', 'ASR_BUDGET_EXCEEDED'} else 409)
        if existing := reservation.get('existing'):
            if existing['state'] == 'completed':
                result = existing['response']
                self.last_artifact_id = result.get('artifact_id')
                self.last_metadata = {**result['asr'], 'operation_id': op, 'session_id': str(session['id']), 'reused': True}
                return Transcription(result['text'], result.get('duration_seconds'), result.get('trace_id'), result.get('raw', {}))
            raise AsrError('ASR_RESULT_UNKNOWN' if existing['state'] == 'unknown' else
                           'ASR_OPERATION_IN_PROGRESS' if existing['state'] == 'sending' else
                           existing.get('error_code') or 'ASR_UPSTREAM_FAILED', 409)
        observer = DispatchObserver(DatabaseModelObserver(self.database, actor, 'asr.' + session['scene'], operation_id=op))
        # Only ASR changes retry policy. Text/TTS keep their existing policies.
        client = None
        sent_at = time.monotonic()
        try:
            client = self.client_factory(replace(self.settings, max_retries=0), observer=observer)
            async with asyncio.timeout(min(180, max(45, self.settings.timeout_seconds * 2))):
                result = await client.transcribe(filename=filename, content=content, mime_type=mime_type,
                    language=language, hotwords=tuple(session['snapshot'].get('provider_words', session['snapshot']['words']))
                    if session['snapshot']['applied'] else ())
            source_text = result.text
            normalization_version = session['snapshot'].get('normalization_version', 0)
            changes = []
            if normalization_version == NORMALIZATION_VERSION:
                text, changes = normalize_acronyms(source_text, session['snapshot']['words'])
                # The supplier payload remains untouched, including its raw text.
                result = replace(result, text=text)
            normalization = {'version': normalization_version, 'changes': changes}
            metadata = {**public_snapshot(session), 'operation_id': op, 'status': 'completed', 'reused': False,
                        'normalization': {'version': normalization_version, 'change_count': len(changes)},
                        'timings': {'queue_ms': round((sent_at - queued) * 1000),
                                    'model_ms': round((time.monotonic() - sent_at) * 1000)}}
            artifact_id = str(uuid5(UUID('ecbff448-844b-4e67-98dd-2829aa1c0b23'), actor.workspace_id + ':' + actor.user_id + ':' + op)) if unit_key == 'full' and stage != 'diagnostic' else None
            response = {'artifact_id': artifact_id, 'text': result.text, 'duration_seconds': result.duration_seconds, 'trace_id': result.trace_id,
                        'raw': result.raw, 'asr': metadata,
                        'source_text': source_text, 'normalization': normalization}
            await self._finish(op, 'completed', response=response)
            self.last_metadata = metadata
            self.last_artifact_id = artifact_id
            return result
        except BaseException as exc:
            # A gateway 5xx/408 cannot prove the supplier rejected a sent request.
            # Only an explicit rejection or a valid empty transcript is definite;
            # malformed successes and transport failures keep their unknown receipt.
            status = exc.status_code if isinstance(exc, SenseAudioError) else None
            rejected = status is not None and 400 <= status < 500 and status != 408
            empty = isinstance(exc, EmptyTranscriptionError) and status is not None and 200 <= status < 300
            known = not observer.sent or rejected or empty
            code = 'ASR_UPSTREAM_FAILED' if known else 'ASR_RESULT_UNKNOWN'
            try:
                await asyncio.shield(self._finish(op, 'failed' if known else 'unknown', error=code))
            except BaseException:
                logger.warning('asr_receipt_pending operation_id=%s', op)
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                raise
            raise AsrError(code, 502 if known else 409) from None
        finally:
            # Admission limits local dispatch connections, not the unknowable
            # lifetime of an upstream job after this connection has closed.
            # Preserve outcome, charged time and no-resend identity forever.
            try:
                if client is not None:
                    async with asyncio.timeout(5):
                        await client.close()
            except BaseException:
                logger.warning('asr_transport_close_unconfirmed operation_id=%s', op)
            else:
                if client is not None:
                    try:
                        await asyncio.shield(self._transport_closed(op))
                    except BaseException:
                        logger.warning('asr_transport_close_receipt_pending operation_id=%s', op)

    async def _transport_closed(self, operation_id):
        async with self.database.transaction(self.actor) as conn:
            await require_asr(conn, self.actor, self.session['scene'])
            await conn.fetchval('SELECT security.close_asr_transport($1::uuid)', operation_id)

    async def _finish(self, operation_id, state, *, response=None, error=None):
        async with self.database.transaction(self.actor) as conn:
            await require_asr(conn, self.actor, self.session['scene'])
            if state == 'completed' and response and response.get('artifact_id'):
                from sales_backend.repositories.audio_artifacts import save_transcript
                await save_transcript(conn, self.actor, response['artifact_id'],
                    Transcription(response['text'], response['duration_seconds'], response['trace_id'], response['raw']),
                    self.session['scene'], None, self.settings.asr_model,
                    source_text=response.get('source_text'), normalization=response.get('normalization'),
                    operation_id=operation_id)
            await conn.execute('''UPDATE activity.asr_attempt SET state=$4,response=$5::jsonb,error_code=$6,
                finished_at=clock_timestamp() WHERE workspace_id=$1::uuid AND actor_id=$2::uuid
                AND operation_id=$3::uuid AND (state='sending' OR (state='unknown' AND $4='completed')) ''',
                self.actor.workspace_id, self.actor.user_id, operation_id, state, response, error)
