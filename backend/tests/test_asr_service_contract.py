"""Small synthetic-adapter checks; no database credentials or paid requests."""
import asyncio
import io
import wave
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from sales_backend.domain.asr import AsrCapacityRelease, AsrError, HotwordItem, normalized_items
from sales_backend.integrations.senseaudio import Transcription
from sales_backend.services import asr


def settings():
    from sales_backend.config import Settings
    from tests.test_senseaudio import settings as defaults
    value = defaults()
    assert isinstance(value, Settings)
    return value


def pcm():
    data = io.BytesIO()
    with wave.open(data, 'wb') as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(b'\x00\x00' * 16000)
    return data.getvalue()


class MemoryDatabase:
    def __init__(self):
        self.sessions, self.attempts, self.words = {}, {}, {}
        self.artifacts = []

    @asynccontextmanager
    async def transaction(self, actor, **kwargs):
        yield MemoryConnection(self, actor)


class MemoryConnection:
    def __init__(self, db, actor):
        self.db, self.actor = db, actor

    async def fetchrow(self, sql, *args):
        return self.db.sessions.get(tuple(args))

    async def fetchval(self, sql, *args):
        if 'asr_hotword_snapshot' in sql:
            return {'version': 1, 'items': self.db.words[self.actor.workspace_id]}
        if 'close_asr_transport' in sql:
            self.db.attempts[(self.actor.workspace_id, self.actor.user_id, args[0])]['capacity_released'] = True
            return True
        if 'reserve_asr_attempt' in sql:
            op, session_id, stage, unit, request, reuse, provider, seconds = args
            key = (self.actor.workspace_id, self.actor.user_id, op)
            if key in self.db.attempts:
                existing = self.db.attempts[key]
                return {'existing': existing} if existing['request_digest'] == request else {'error': 'ASR_OPERATION_CONFLICT'}
            for k, value in self.db.attempts.items():
                if k[:2] == key[:2] and value['reuse_digest'] == reuse and value['state'] in {'completed', 'sending', 'unknown'}:
                    return {'existing': value}
            self.db.attempts[key] = dict(state='sending', request_digest=request, reuse_digest=reuse,
                                         response=None, error_code=None, created_at=datetime.now(UTC))
            return {'accepted': True}

    async def execute(self, sql, *args):
        if 'INSERT INTO agent.artifact' in sql:
            self.db.artifacts.append(args)
        if 'INSERT INTO activity.asr_session' in sql:
            w, u, sid, scene, language, config, snapshot = args
            self.db.sessions[(w, u, sid)] = dict(workspace_id=w, actor_id=u, id=sid, scene=scene,
                                                language=language, configuration=config, snapshot=snapshot)
        if 'UPDATE activity.asr_attempt' in sql:
            w, u, op, state, response, error = args
            self.db.attempts[(w, u, op)].update(state=state, response=response, error_code=error)
        return 'UPDATE 1'


@pytest.fixture
def isolated(monkeypatch):
    async def allowed(*args):
        pass
    monkeypatch.setattr(asr, 'require_asr', allowed)
    database = MemoryDatabase()
    a = SimpleNamespace(workspace_id=str(uuid4()), user_id=str(uuid4()))
    b = SimpleNamespace(workspace_id=str(uuid4()), user_id=str(uuid4()))
    database.words[a.workspace_id] = [dict(term='OnlyA', scenes=['all'], enabled=True)]
    database.words[b.workspace_id] = [dict(term='OnlyB', scenes=['all'], enabled=True)]
    return database, a, b


def test_word_validation_normalizes_and_refuses_ambiguous_input():
    assert normalized_items([HotwordItem(term=' SalesBuddy ')])[0]['term'] == 'SalesBuddy'
    with pytest.raises(AsrError, match='DUPLICATE'):
        normalized_items([HotwordItem(term='SalesBuddy'), HotwordItem(term='salesbuddy')])
    with pytest.raises(AsrError, match='INVALID'):
        normalized_items([HotwordItem(term='A,B')])


@pytest.mark.asyncio
async def test_company_snapshots_are_isolated_frozen_and_enforced(isolated):
    database, a, b = isolated
    sid = str(uuid4())
    one = await asr.prepare_session(database, a, settings(), scene='visit_entry', session_id=sid)
    two = await asr.prepare_session(database, b, settings(), scene='visit_entry', session_id=sid)
    database.words[a.workspace_id] = []
    again = await asr.prepare_session(database, a, settings(), scene='visit_entry', session_id=sid)
    assert one['snapshot']['words'] == again['snapshot']['words'] == ['OnlyA']
    assert two['snapshot']['words'] == ['OnlyB']
    with pytest.raises(AsrError, match='SCOPE_DENIED'):
        asr.AsrService(database, b, settings(), one)


@pytest.mark.asyncio
async def test_actual_adapter_parameters_and_completed_receipt_reuse(isolated):
    database, a, b = isolated
    captured = []

    class Client:
        def __init__(self, config, observer):
            assert config.max_retries == 0
        async def transcribe(self, **kwargs):
            captured.append(kwargs['hotwords'])
            return Transcription('原始文字', 1, 'trace', {'text': '原始文字'})
        async def close(self):
            pass

    for actor in (a, b, a):
        session = await asr.prepare_session(database, actor, settings(), scene='visit_entry')
        service = asr.AsrService(database, actor, settings(), session, client_factory=Client)
        result = await service.transcribe(filename='voice.wav', content=pcm(), mime_type='audio/wav')
        assert result.text == '原始文字'
        assert 'words' not in service.last_metadata
    assert captured == [('OnlyA',), ('OnlyB',)]


@pytest.mark.asyncio
async def test_concurrent_same_owner_audio_and_unknown_never_resend(isolated):
    database, actor, _ = isolated
    calls = []

    class Client:
        def __init__(self, config, observer):
            self.observer = observer
        async def transcribe(self, **kwargs):
            self.observer.sent = True
            calls.append(1)
            await asyncio.sleep(.01)
            raise TimeoutError('private upstream detail')
        async def close(self):
            pass

    async def run():
        session = await asr.prepare_session(database, actor, settings(), scene='visit_entry')
        service = asr.AsrService(database, actor, settings(), session, client_factory=Client)
        with pytest.raises(AsrError) as error:
            await service.transcribe(filename='voice.wav', content=pcm(), mime_type='audio/wav')
        return error.value.code
    results = await asyncio.gather(run(), run())
    assert set(results) == {'ASR_OPERATION_IN_PROGRESS', 'ASR_RESULT_UNKNOWN'}
    assert await run() == 'ASR_RESULT_UNKNOWN'
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_unknown_close_releases_local_slot_without_changing_receipt_or_resending(isolated):
    database, actor, _ = isolated
    calls = []
    class Client:
        def __init__(self, config, observer):
            self.observer = observer
        async def transcribe(self, **kwargs):
            calls.append(1)
            self.observer.sent = True
            raise TimeoutError('synthetic unknown')
        async def close(self):
            pass
    session = await asr.prepare_session(database, actor, settings(), scene='visit_entry')
    service = asr.AsrService(database, actor, settings(), session, client_factory=Client)
    for _ in range(2):
        with pytest.raises(AsrError, match='ASR_RESULT_UNKNOWN'):
            await service.transcribe(filename='voice.wav', content=pcm(), mime_type='audio/wav')
    attempt = next(iter(database.attempts.values()))
    assert attempt['state'] == 'unknown' and attempt['capacity_released'] is True
    assert attempt['response'] is None and len(calls) == 1


@pytest.mark.asyncio
async def test_close_failure_does_not_assert_connection_ended(isolated):
    database, actor, _ = isolated
    class Client:
        def __init__(self, config, observer):
            self.observer = observer
        async def transcribe(self, **kwargs):
            self.observer.sent = True
            raise TimeoutError('synthetic unknown')
        async def close(self):
            raise OSError('synthetic close failed')
    session = await asr.prepare_session(database, actor, settings(), scene='visit_entry')
    service = asr.AsrService(database, actor, settings(), session, client_factory=Client)
    with pytest.raises(AsrError, match='ASR_RESULT_UNKNOWN'):
        await service.transcribe(filename='voice.wav', content=pcm(), mime_type='audio/wav')
    attempt = next(iter(database.attempts.values()))
    assert attempt['state'] == 'unknown' and not attempt.get('capacity_released')


@pytest.mark.asyncio
async def test_new_acronym_snapshot_is_frozen_and_old_snapshot_keeps_old_wire_words(isolated):
    database, actor, _ = isolated
    database.words[actor.workspace_id] = [dict(term='ACV', scenes=['all'], enabled=True)]
    session = await asr.prepare_session(database, actor, settings(), scene='visit_entry')
    assert session['snapshot']['words'] == ['ACV']
    assert session['snapshot']['provider_words'] == ['ACV', 'A C V']
    assert session['snapshot']['selection_version'] == 2
    captured = []
    class Client:
        def __init__(self, *args, **kwargs): pass
        async def transcribe(self, **kwargs):
            captured.append(kwargs['hotwords'])
            return Transcription('RCV', 1, None, {'text': 'RCV'})
        async def close(self): pass
    service = asr.AsrService(database, actor, settings(), session, client_factory=Client)
    assert (await service.transcribe(filename='voice.wav', content=pcm(), mime_type='audio/wav')).text == 'RCV'
    old = dict(session, id=str(uuid4()), snapshot={k: v for k,v in session['snapshot'].items() if k!='provider_words'})
    old['snapshot']['selection_version'] = 1
    service = asr.AsrService(database, actor, settings(), old, client_factory=Client)
    await service.transcribe(filename='voice.wav', content=pcm(), mime_type='audio/wav')
    assert captured == [('ACV','A C V'),('ACV',)]


def test_release_requires_one_explicit_and_matching_evidence_basis():
    from pydantic import ValidationError
    fields = dict(owner_id=uuid4(), evidence='synthetic evidence with no sensitive values')
    assert AsrCapacityRelease(**fields, provider_finished=True).resolution_kind == 'provider_finished'
    local = AsrCapacityRelease(**fields, resolution_kind='local_transport_closed', local_transport_closed=True)
    assert local.provider_finished is None
    for args in ({}, {'resolution_kind':'local_transport_closed','provider_finished':True},
                 {'provider_finished':True,'local_transport_closed':True}):
        with pytest.raises(ValidationError):
            AsrCapacityRelease(**fields, **args)


@pytest.mark.asyncio
async def test_new_normalization_preserves_raw_and_artifact_source_without_changing_response_contract(isolated):
    from sales_backend.api.models import AudioTranscriptionResponse
    database, actor, _ = isolated
    database.words[actor.workspace_id] = [dict(term=word, scenes=['all'], enabled=True) for word in ('FDE','ACV')]
    source = '安排F D E参与，核对A C V和R C V。'
    raw = {'text': source, 'duration': 1, 'provider_field': 'untouched'}
    class Client:
        def __init__(self, *args, **kwargs): pass
        async def transcribe(self, **kwargs):
            return Transcription(source, 1, 'original-trace', raw)
        async def close(self): pass
    session = await asr.prepare_session(database, actor, settings(), scene='visit_entry')
    service = asr.AsrService(database, actor, settings(), session, client_factory=Client)
    result = await service.transcribe(filename='voice.wav', content=pcm(), mime_type='audio/wav')
    expected = '安排FDE参与，核对ACV和R C V。'
    assert result.text == expected and result.raw is raw and raw['text'] == source
    receipt = next(iter(database.attempts.values()))['response']
    assert receipt['source_text'] == source and receipt['raw'] == raw
    assert receipt['normalization']['version'] == 1
    assert [item['after'] for item in receipt['normalization']['changes']] == ['FDE','ACV']
    artifact = database.artifacts[-1]
    assert artifact[2]['text'] == expected and artifact[2]['source_text'] == source
    assert artifact[2]['normalization'] == receipt['normalization']
    assert artifact[3][0]['asr_operation_id'] == service.last_metadata['operation_id']
    response = AudioTranscriptionResponse(text=result.text, purpose='visit_entry', artifact_id=service.last_artifact_id,
                                         asr=service.last_metadata, duration_seconds=1, trace_id=result.trace_id)
    assert response.text == expected and response.trace_id == 'original-trace'


@pytest.mark.asyncio
@pytest.mark.parametrize('old_state', ['completed','unknown'])
async def test_display_rule_upgrade_does_not_resend_or_rewrite_old_receipt(isolated, old_state):
    database, actor, _ = isolated
    database.words[actor.workspace_id] = [dict(term='ACV', scenes=['all'], enabled=True)]
    calls = []
    class Client:
        def __init__(self, config, observer): self.observer = observer
        async def transcribe(self, **kwargs):
            calls.append(1)
            self.observer.sent = True
            if old_state == 'unknown': raise TimeoutError('synthetic unknown')
            return Transcription('核对A C V。', 1, None, {'text':'核对A C V。'})
        async def close(self): pass
    old = await asr.prepare_session(database, actor, settings(), scene='visit_entry')
    old['snapshot'].pop('normalization_version')
    service = asr.AsrService(database, actor, settings(), old, client_factory=Client)
    if old_state == 'unknown':
        with pytest.raises(AsrError, match='ASR_RESULT_UNKNOWN'):
            await service.transcribe(filename='voice.wav', content=pcm(), mime_type='audio/wav')
    else:
        assert (await service.transcribe(filename='voice.wav', content=pcm(), mime_type='audio/wav')).text == '核对A C V。'
    old_receipt = next(iter(database.attempts.values()))['response']
    new = await asr.prepare_session(database, actor, settings(), scene='visit_entry')
    assert new['snapshot']['normalization_version'] == 1
    service = asr.AsrService(database, actor, settings(), new, client_factory=Client)
    if old_state == 'unknown':
        with pytest.raises(AsrError, match='ASR_RESULT_UNKNOWN'):
            await service.transcribe(filename='voice.wav', content=pcm(), mime_type='audio/wav')
    else:
        assert (await service.transcribe(filename='voice.wav', content=pcm(), mime_type='audio/wav')).text == '核对A C V。'
        assert service.last_metadata['reused'] is True
        assert next(iter(database.attempts.values()))['response'] is old_receipt
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [500, 502, 504, 408])
async def test_sent_upstream_error_keeps_unknown_receipt_and_never_resends(isolated, status):
    from sales_backend.integrations.senseaudio import SenseAudioError
    database, actor, _ = isolated
    calls = []
    class Client:
        def __init__(self, config, observer): self.observer = observer
        async def transcribe(self, **kwargs):
            self.observer.sent = True
            calls.append(1)
            raise SenseAudioError('synthetic upstream failure', status_code=status)
        async def close(self): pass
    session = await asr.prepare_session(database, actor, settings(), scene='visit_entry')
    service = asr.AsrService(database, actor, settings(), session, client_factory=Client)
    for _ in range(2):
        with pytest.raises(AsrError, match='ASR_RESULT_UNKNOWN'):
            await service.transcribe(filename='voice.wav', content=pcm(), mime_type='audio/wav')
    another_session = await asr.prepare_session(database, actor, settings(), scene='visit_entry')
    another = asr.AsrService(database, actor, settings(), another_session, client_factory=Client)
    with pytest.raises(AsrError, match='ASR_RESULT_UNKNOWN'):
        await another.transcribe(filename='voice.wav', content=pcm(), mime_type='audio/wav')
    attempt = next(iter(database.attempts.values()))
    assert attempt['state'] == 'unknown' and attempt['error_code'] == 'ASR_RESULT_UNKNOWN'
    assert attempt['capacity_released'] is True and attempt['response'] is None
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['unsent', 'rejected', 'empty', 'invalid_success'])
async def test_only_definite_asr_failures_are_recorded_as_failed(isolated, kind):
    from sales_backend.integrations.senseaudio import EmptyTranscriptionError, InvalidTranscriptionError, SenseAudioError
    database, actor, _ = isolated
    class Client:
        def __init__(self, config, observer): self.observer = observer
        async def transcribe(self, **kwargs):
            self.observer.sent = kind != 'unsent'
            if kind == 'empty': raise EmptyTranscriptionError()
            if kind == 'invalid_success': raise InvalidTranscriptionError()
            raise SenseAudioError('synthetic failure', status_code=401 if kind == 'rejected' else None)
        async def close(self): pass
    session = await asr.prepare_session(database, actor, settings(), scene='visit_entry')
    service = asr.AsrService(database, actor, settings(), session, client_factory=Client)
    code = 'ASR_RESULT_UNKNOWN' if kind == 'invalid_success' else 'ASR_UPSTREAM_FAILED'
    with pytest.raises(AsrError, match=code):
        await service.transcribe(filename='voice.wav', content=pcm(), mime_type='audio/wav')
    assert next(iter(database.attempts.values()))['state'] == ('unknown' if kind == 'invalid_success' else 'failed')
