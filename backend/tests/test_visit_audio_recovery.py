"""Real PCM files + fake provider/database: no customer recording or live call."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import io
import wave
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from sales_backend.integrations.ffmpeg import ProcessResult
from sales_backend.integrations.senseaudio import EmptyTranscriptionError, InvalidTranscriptionError, SenseAudioError
from sales_backend.job_context import JobLeaseLost
from sales_backend.repositories.jobs import ClaimedJob
from sales_backend.services import visit_import as module
from sales_backend.worker import Worker

pytestmark = pytest.mark.asyncio
RATE = 16000


def write_wav(path, frames, marker=1):
    with wave.open(str(path), 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(RATE)
        stream.writeframes(bytes([marker, 0]) * frames)


def pcm(content):
    with wave.open(io.BytesIO(content)) as stream:
        return stream.readframes(stream.getnframes())


def converter(monkeypatch, durations):
    expected = []

    async def convert(*args, timeout):
        expected.clear()
        for index, seconds in enumerate(durations):
            frames = round(seconds * RATE)
            path = Path(args[-1].replace('%03d', f'{index:03d}'))
            write_wav(path, frames, index + 1)
            expected.append(bytes([index + 1, 0]) * frames)
        return ProcessResult(0, b'')

    monkeypatch.setattr(module, 'run_ffmpeg', convert)
    return expected


def provider(results):
    calls = []
    results = iter(results)

    async def transcribe(**kwargs):
        calls.append(kwargs['content'])
        result = next(results)
        if isinstance(result, BaseException):
            raise result
        return SimpleNamespace(text=result, trace_id='synthetic-trace')

    return SimpleNamespace(transcribe=transcribe, close=AsyncMock(), calls=calls)


@pytest.mark.parametrize('durations,expected_count', [
    ([240.012, 0.36], 1), ([240, 240.012, 0.36], 2),
    ([239.9], 1), ([240], 1), ([240, 1 / RATE], 1),
    ([240, 1.999], 1), ([240, 2], 2), ([5], 1),
    ([240, 240, 240, 6], 4),
])
async def test_boundaries_preserve_every_pcm_frame(monkeypatch, durations, expected_count):
    expected = converter(monkeypatch, durations)
    client = provider([f'文字{i}' for i in range(expected_count)])
    text, evidence = await module.audio_text('recording.wav', b'original', client)
    assert len(client.calls) == len(evidence) == expected_count
    # Checks order, duplicate/lost samples, and the full quiet tail, not just duration.
    assert b''.join(pcm(c) for c in client.calls) == b''.join(expected)
    assert all(len(c) <= module.MAX_ASR_FILE_BYTES for c in client.calls)
    assert text == '\n'.join(f'文字{i}' for i in range(expected_count))


async def test_tail_merge_limit_fails_before_any_provider_call(monkeypatch):
    converter(monkeypatch, [3, .36])
    monkeypatch.setattr(module, 'MAX_ASR_FILE_BYTES', 100000)
    client = provider([])
    with pytest.raises(ValueError, match='尾段合并后超过'):
        await module.audio_text('recording.wav', b'original', client)
    assert client.calls == []


async def test_failed_non_tiny_segment_is_not_skipped_and_resume_uses_saved_prefix(monkeypatch):
    converter(monkeypatch, [240, 3, 4])
    saved = []

    async def checkpoint(value):
        saved[:] = copy.deepcopy(value)

    first = provider(['前段文字', EmptyTranscriptionError()])
    with pytest.raises(module.AudioImportError) as error:
        await module.audio_text('x.wav', b'original', first, checkpoint=checkpoint, configuration='v1')
    assert error.value.code == 'ASR_NO_TEXT'
    assert '第2/3段' in str(error.value) and '已保留前1段' in str(error.value)
    assert not error.value.retryable
    assert saved[0]['segments'][0]['text'] == '前段文字'
    second = provider(['中段文字', '后段文字'])
    text, evidence = await module.audio_text('x.wav', b'original', second,
        resume_evidence=saved, checkpoint=checkpoint, configuration='v1')
    assert len(second.calls) == 2
    assert text == '前段文字\n中段文字\n后段文字'
    assert [r['segment'] for r in evidence] == [1, 2, 3]


@pytest.mark.parametrize('changed', ['source', 'configuration', 'decoded_audio', 'legacy', 'invalid_prefix'])
async def test_mismatched_or_invalid_checkpoint_is_not_reused(monkeypatch, changed):
    converter(monkeypatch, [3, 3])
    saved = []

    async def checkpoint(value):
        saved[:] = copy.deepcopy(value)

    with pytest.raises(module.AudioImportError):
        await module.audio_text('x.wav', b'original', provider(['old', EmptyTranscriptionError()]),
                                checkpoint=checkpoint, configuration='v1')
    content, configuration = b'original', 'v1'
    if changed == 'source':
        content = b'different-original'
    elif changed == 'configuration':
        configuration = 'v2'
    elif changed == 'decoded_audio':
        converter(monkeypatch, [4, 3])
    elif changed == 'legacy':
        saved = [{'segment': 1, 'trace_id': 'legacy-no-text'}]
    else:
        saved[0]['segments'][0]['text'] = ''
    client = provider(['new1', 'new2'])
    text, _ = await module.audio_text('x.wav', content, client,
                                     resume_evidence=saved, configuration=configuration)
    assert len(client.calls) == 2 and text == 'new1\nnew2'


async def test_silence_is_failure_not_fake_complete(monkeypatch):
    converter(monkeypatch, [5])
    with pytest.raises(module.AudioImportError) as error:
        await module.audio_text('silence.wav', b'original', provider([EmptyTranscriptionError()]))
    assert error.value.code == 'ASR_NO_TEXT' and '未识别到有效文字' in str(error.value)
    assert '服务暂不可用' not in str(error.value)


class ImportDatabase:
    """Transactional in-memory fixture; not a PostgreSQL/RLS acceptance claim."""

    def __init__(self):
        self.row = {'filename': 'x.wav', 'status': 'queued', 'evidence': [],
                    'extracted_text': None, 'error_message': None}
        self.settings = SimpleNamespace(asr_model='test', senseaudio_base_url='https://test.invalid',
                                        model_api_endpoint='', model_api_metadata={})
        self.borrowed = 0
        self.final_failure = False
        self.checkpoint_updates = 0
        self.allow_checkpoint = True
        self.checkpoint_failures = []
        self.fail_at_segments = 1

    @asynccontextmanager
    async def transaction(self, actor):
        previous = copy.deepcopy(self.row)
        self.borrowed += 1
        try:
            yield self
        except BaseException:
            self.row = previous
            raise
        finally:
            self.borrowed -= 1

    async def fetchrow(self, sql, *args):
        return copy.deepcopy(self.row)

    async def fetchval(self, sql, *args):
        assert 'ops.job_effect' in sql
        return False

    async def execute(self, sql, *args):
        if "SET status='processing'" in sql:
            self.row['status'] = 'processing'
        elif 'SET evidence=' in sql:
            self.checkpoint_updates += 1
            assert 'created_by_user_ref_id=$3' in sql and 'common.current_workspace_id()' in sql
            if not self.allow_checkpoint:
                return 'UPDATE 0'
            if len(args[1][0]['segments']) == self.fail_at_segments and self.checkpoint_failures:
                raise self.checkpoint_failures.pop(0)
            self.row['evidence'] = copy.deepcopy(args[1])
        elif "status='succeeded'" in sql:
            if self.final_failure:
                self.final_failure = False
                raise TimeoutError('synthetic commit failure')
            self.row.update(status='succeeded', extracted_text=args[1], evidence=copy.deepcopy(args[2]))
        elif 'SET status=$2,error_message=$3' in sql:
            self.row.update(status=args[1], error_message=args[2])
        else:
            raise AssertionError(sql)
        return 'UPDATE 1'


def handler_environment(monkeypatch, database, clients):
    from sales_backend.services.material_storage import MaterialStorage
    clients = iter(clients)
    capability = AsyncMock()
    effect = AsyncMock()
    monkeypatch.setattr(module, 'require_capability', capability)
    monkeypatch.setattr(module, 'record_job_effect', effect)
    monkeypatch.setattr(MaterialStorage, 'read', AsyncMock(return_value=b'original'))
    monkeypatch.setattr(module, 'load_runtime_configuration', AsyncMock(
        return_value=SimpleNamespace(settings=database.settings)))
    monkeypatch.setattr(module, 'SenseAudioClient', lambda *args, **kwargs: next(clients))
    actor = SimpleNamespace(user_id=str(uuid4()), workspace_id=str(uuid4()))
    return actor, effect, capability


async def test_handler_retains_partial_privately_then_retries_to_full_success(monkeypatch):
    converter(monkeypatch, [3, 3])
    database = ImportDatabase()
    first, second = provider(['first', EmptyTranscriptionError()]), provider(['second'])
    actor, effect, capability = handler_environment(monkeypatch, database, [first, second])
    with pytest.raises(module.AudioImportError):
        await module.VisitImportHandler(database).handle('import', actor)
    assert database.row['extracted_text'] is None
    assert database.row['evidence'][0]['segments'][0]['text'] == 'first'
    assert database.row['status'] != 'succeeded' and effect.await_count == 0
    assert first.close.await_count == 1 and database.borrowed == 0
    # Retry entry point preserves evidence; use a new handler as a restarted Worker would.
    database.row['status'] = 'queued'
    await module.VisitImportHandler(database).handle('import', actor)
    assert len(second.calls) == 1 and database.row['status'] == 'succeeded'
    assert database.row['extracted_text'] == 'first\nsecond' and effect.await_count == 1
    assert all('text' not in e for e in database.row['evidence'])
    assert len(capability.await_args_list) >= 10


async def test_complete_checkpoint_survives_final_write_failure_without_rebilling(monkeypatch):
    converter(monkeypatch, [3, 3])
    database = ImportDatabase()
    database.final_failure = True
    first, second = provider(['one', 'two']), provider([])
    actor, effect, _ = handler_environment(monkeypatch, database, [first, second])
    with pytest.raises(TimeoutError):
        await module.VisitImportHandler(database).handle('import', actor)
    assert database.row['extracted_text'] is None
    assert len(database.row['evidence'][0]['segments']) == 2
    await module.VisitImportHandler(database).handle('import', actor)
    assert second.calls == [] and database.row['extracted_text'] == 'one\ntwo'
    assert effect.await_count == 1


async def test_cancellation_keeps_committed_prefix(monkeypatch):
    converter(monkeypatch, [3, 3])
    database = ImportDatabase()
    first = provider(['one', asyncio.CancelledError()])
    actor, effect, _ = handler_environment(monkeypatch, database, [first])
    with pytest.raises(asyncio.CancelledError):
        await module.VisitImportHandler(database).handle('import', actor)
    assert database.row['evidence'][0]['segments'][0]['text'] == 'one'
    assert database.row['extracted_text'] is None and effect.await_count == 0
    assert first.close.await_count == 1 and database.borrowed == 0


async def test_checkpoint_authority_loss_stops_before_provider(monkeypatch):
    converter(monkeypatch, [3, 3])
    database = ImportDatabase()
    database.allow_checkpoint = False
    client = provider([])
    actor, effect, _ = handler_environment(monkeypatch, database, [client])
    with pytest.raises(PermissionError, match='状态已变化'):
        await module.VisitImportHandler(database).handle('import', actor)
    assert not client.calls and not effect.await_count


@pytest.mark.parametrize('cause,expected_code,retry', [
    (EmptyTranscriptionError(), 'ASR_NO_TEXT', False),
    (InvalidTranscriptionError(), 'ASR_INVALID_RESPONSE', False),
    (SenseAudioError('upstream', status_code=503, retryable=True), 'ASR_SERVICE_UNAVAILABLE', True),
])
async def test_actual_worker_failure_keeps_checkpoint_and_correct_error(cause, expected_code, retry):
    database = ImportDatabase()
    database.row['evidence'] = [{'text': 'saved-prefix'}]
    worker = Worker(database)
    worker.jobs = SimpleNamespace(fail=AsyncMock(), succeed=AsyncMock())
    job = ClaimedJob(str(uuid4()), str(uuid4()), 'visit.import', str(uuid4()),
                    {'user_id': str(uuid4()), 'role': 'manager', 'data_scope': 'workspace'},
                    1, 3, str(uuid4()), 2.5)
    error = module.AudioImportError(cause, 2, 3, 1)
    await worker._finish_failed(job, error)
    assert database.row['status'] == ('queued' if retry else 'failed')
    assert database.row['error_message'] == error.user_message
    assert database.row['evidence'] == [{'text': 'saved-prefix'}]
    assert worker.jobs.fail.await_args.kwargs['error_code'] == expected_code
    assert worker.jobs.fail.await_args.kwargs['retryable'] == retry


async def test_configuration_fingerprint_excludes_credentials():
    config = SimpleNamespace(asr_model='a', senseaudio_base_url='https://test.invalid',
                             model_api_endpoint='', model_api_metadata={}, senseaudio_api_key='one')
    first = module.asr_configuration_fingerprint(config)
    config.senseaudio_api_key = 'two'
    assert module.asr_configuration_fingerprint(config) == first
    config.asr_model = 'b'
    assert module.asr_configuration_fingerprint(config) != first
    assert len(first) == hashlib.sha256().digest_size * 2


async def test_transient_checkpoint_failures_retry_storage_not_asr(monkeypatch):
    converter(monkeypatch, [3, 3])
    database = ImportDatabase()
    database.checkpoint_failures = [TimeoutError('synthetic'), ConnectionError('synthetic')]
    client = provider(['one', 'two'])
    original = client.transcribe

    async def transcribe(**kwargs):
        assert database.borrowed == 0  # no database connection held across the model wait
        return await original(**kwargs)

    client.transcribe = transcribe
    actor, effect, _ = handler_environment(monkeypatch, database, [client])
    await module.VisitImportHandler(database).handle('import', actor)
    assert len(client.calls) == 2 and database.checkpoint_updates == 5
    assert database.row['extracted_text'] == 'one\ntwo' and effect.await_count == 1


async def test_checkpoint_retry_exhaustion_keeps_last_committed_prefix(monkeypatch):
    converter(monkeypatch, [3, 3])
    database = ImportDatabase()
    database.fail_at_segments = 2
    database.checkpoint_failures = [TimeoutError('synthetic') for _ in range(3)]
    client = provider(['one', 'two'])
    actor, effect, _ = handler_environment(monkeypatch, database, [client])
    with pytest.raises(TimeoutError):
        await module.VisitImportHandler(database).handle('import', actor)
    assert len(client.calls) == 2 and database.checkpoint_updates == 5
    assert database.row['evidence'][0]['segments'][0]['text'] == 'one'
    assert len(database.row['evidence'][0]['segments']) == 1
    assert database.row['extracted_text'] is None and effect.await_count == 0


async def test_lost_lease_does_not_retry_checkpoint_or_continue(monkeypatch):
    converter(monkeypatch, [3, 3])
    database = ImportDatabase()
    database.checkpoint_failures = [JobLeaseLost('synthetic lost lease')]
    client = provider(['one'])
    actor, effect, _ = handler_environment(monkeypatch, database, [client])
    with pytest.raises(JobLeaseLost):
        await module.VisitImportHandler(database).handle('import', actor)
    assert len(client.calls) == 1 and database.checkpoint_updates == 2
    assert database.row['extracted_text'] is None and not effect.await_count


async def test_revocation_after_model_response_blocks_save_and_next_segment(monkeypatch):
    converter(monkeypatch, [3, 3])
    database = ImportDatabase()
    client = provider(['one'])
    actor, effect, capability = handler_environment(monkeypatch, database, [client])
    original = client.transcribe

    async def transcribe(**kwargs):
        result = await original(**kwargs)
        capability.side_effect = PermissionError('synthetic revoked')
        return result

    client.transcribe = transcribe
    with pytest.raises(PermissionError):
        await module.VisitImportHandler(database).handle('import', actor)
    assert len(client.calls) == 1 and database.row['extracted_text'] is None
    assert database.row['evidence'][0]['segments'] == [] and not effect.await_count


async def test_already_successful_import_does_not_transcribe_again(monkeypatch):
    database = ImportDatabase()
    database.row.update(status='succeeded', extracted_text='confirmed')
    client = provider([])
    actor, effect, _ = handler_environment(monkeypatch, database, [client])
    await module.VisitImportHandler(database).handle('import', actor)
    assert not client.calls and database.row['extracted_text'] == 'confirmed' and not effect.await_count


@pytest.mark.parametrize('duration,accepted', [(3600, True), (3601, False)])
async def test_sixty_minute_boundary_uses_total_decoded_duration(monkeypatch, duration, accepted):
    # Low-rate PCM is a small duration-arithmetic fixture, not real FFmpeg acceptance.
    monkeypatch.setitem(globals(), 'RATE', 1)
    converter(monkeypatch, [duration])
    client = provider(['valid'])
    if accepted:
        assert (await module.audio_text('x.wav', b'source', client))[0] == 'valid'
    else:
        with pytest.raises(ValueError, match='最长60分钟'):
            await module.audio_text('x.wav', b'source', client)
        assert not client.calls


async def test_no_frames_does_not_call_provider(monkeypatch):
    converter(monkeypatch, [0])
    client = provider([])
    with pytest.raises(ValueError, match='没有有效音频'):
        await module.audio_text('x.wav', b'source', client)
    assert not client.calls


async def test_truncated_pcm_is_rejected_before_provider(monkeypatch):
    async def convert(*args, timeout):
        path = Path(args[-1].replace('%03d', '000'))
        write_wav(path, 300)
        path.write_bytes(path.read_bytes()[:-2])
        return ProcessResult(0, b'')
    monkeypatch.setattr(module, 'run_ffmpeg', convert)
    client = provider([])
    with pytest.raises(ValueError, match='分段不完整'):
        await module.audio_text('x.wav', b'source', client)
    assert not client.calls


async def test_text_limit_does_not_lose_previous_checkpoint(monkeypatch):
    converter(monkeypatch, [3, 3])
    saved = []

    async def checkpoint(value):
        saved[:] = copy.deepcopy(value)

    with pytest.raises(ValueError, match='超过5万字'):
        await module.audio_text('x.wav', b'source', provider(['prefix', 'x' * module.MAX_TEXT]),
                                checkpoint=checkpoint)
    assert saved[0]['segments'][0]['text'] == 'prefix' and len(saved[0]['segments']) == 1


@pytest.mark.parametrize('error', [OSError('/private/local/file'), wave.Error('corrupt'), EOFError(), TimeoutError()])
async def test_incidental_failure_messages_are_actionable_without_server_paths(error):
    message = module.import_failure_message(error)
    assert message and '/private/' not in message and '语音服务暂不可用' not in message
