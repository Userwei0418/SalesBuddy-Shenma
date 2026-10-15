"""No database/network: failure injection exercises the actual journal consumer."""

import asyncio
import logging
from contextlib import asynccontextmanager
from threading import Thread
from types import SimpleNamespace

import asyncpg
import pytest

from sales_backend.services.system_events import DatabaseEventSink

WORKSPACE = "00000000-0000-4000-a000-000000000001"
USER = "00000000-0000-4000-a000-000000000002"


class Database:
    def __init__(self, execute=None, acquire=None):
        self.on_execute = execute
        self.on_acquire = acquire
        self.attempts = []
        self.saved = []
        self.acquisitions = 0

    @asynccontextmanager
    async def connection(self):
        self.acquisitions += 1
        if self.on_acquire:
            await self.on_acquire(self.acquisitions)
        yield self

    async def execute(self, query, *args):
        assert query.startswith("SELECT ops.record_system_event(")
        self.attempts.append(args)
        if self.on_execute:
            await self.on_execute(args)
        self.saved.append(args)


def record(message, *, actor=None, name="sales_backend.test", level=logging.WARNING):
    value = logging.LogRecord(name, level, __file__, 1, message, (), None)
    if actor:
        value.actor = actor
    return value


@asynccontextmanager
async def running_sink(database, **settings):
    sink = DatabaseEventSink(database)
    sink.RETRY_DELAYS = (0, 0)
    for key, value in settings.items():
        setattr(sink, key, value)
    try:
        yield sink
    finally:
        await sink.shutdown()


async def drained(sink):
    await asyncio.sleep(0)  # emit schedules enqueue on its owning loop.
    await asyncio.wait_for(sink.queue.join(), 1)


@pytest.mark.asyncio
async def test_unsupported_text_is_safe_and_secrets_remain_redacted():
    database = Database()
    async with running_sink(database) as sink:
        value = record("before\x00after\ud800 password=private-password " + "x" * 9000,
                       actor=SimpleNamespace(workspace_id=WORKSPACE, user_id=USER))
        error = ValueError("api_key=private-key\x00tail")
        value.exc_info = (type(error), error, None)
        value.request_id = "invalid-request-id"
        sink.emit(value)
        await drained(sink)
        assert len(database.saved) == 1
        event = database.saved[0]
        assert event[:3] == (WORKSPACE, USER, None)
        assert event[6].startswith("before\\0after?") and len(event[6]) == 8000
        assert "\x00" not in event[6] + event[7]
        assert "private-password" not in event[6] and "private-key" not in event[7]
        (event[6] + event[7]).encode("utf-8")
        assert not sink.dropped


@pytest.mark.asyncio
async def test_invalid_tenant_or_message_does_not_become_global_or_block_next_event(capsys):
    database = Database()
    async with running_sink(database) as sink:
        sink.emit(record("private-business-data", actor=SimpleNamespace(workspace_id="bad", user_id=USER)))
        invalid = record("%d")
        invalid.args = ("private-invalid-argument",)
        sink.emit(invalid)
        sink.emit(record("valid"))
        await drained(sink)
        assert [item[6] for item in database.saved] == ["valid"]
        assert sink.dropped == {"invalid_event": 2}
    output = capsys.readouterr().err
    assert "reason=invalid_event" in output
    assert "private" not in output


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [asyncpg.InvalidTextRepresentationError("private-invalid-value"),
                                  asyncpg.InsufficientPrivilegeError("private-schema"),
                                  RuntimeError("private-program-error")])
async def test_permanent_failure_does_not_poison_following_events(error, capsys):
    async def execute(args):
        if args[6] == "poison":
            raise error

    database = Database(execute=execute)
    async with running_sink(database) as sink:
        sink.emit(record("poison"))
        sink.emit(record("next"))
        await drained(sink)
        assert [item[6] for item in database.attempts] == ["poison", "next"]
        assert [item[6] for item in database.saved] == ["next"]
        assert sink.dropped == {"permanent_failure": 1}
        assert sink.retry_count == 0
    assert "private" not in capsys.readouterr().err


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [ConnectionError("private-host"),
                                  asyncpg.PostgresConnectionError("private-connection"),
                                  asyncpg.CannotConnectNowError("private-restart"),
                                  asyncpg.DeadlockDetectedError("private-lock"),
                                  asyncpg.TooManyConnectionsError("private-capacity"),
                                  asyncpg.QueryCanceledError("private-timeout")])
async def test_transient_database_failures_retry_and_recover(error, capsys):
    async def execute(args):
        if len(database.attempts) < 3:
            raise error

    database = Database(execute=execute)
    async with running_sink(database) as sink:
        sink.emit(record("persist-after-recovery"))
        await drained(sink)
        assert len(database.attempts) == 3 and len(database.saved) == 1
        assert sink.retry_count == 2 and not sink.dropped
    output = capsys.readouterr().err
    assert "reason=retrying" in output and "private" not in output


@pytest.mark.asyncio
async def test_exhausted_retries_are_counted_and_later_events_progress(capsys):
    async def execute(args):
        if args[6] == "unavailable":
            raise ConnectionError("private-host")

    database = Database(execute=execute)
    async with running_sink(database) as sink:
        sink.emit(record("unavailable"))
        sink.emit(record("recovered"))
        await drained(sink)
        assert [item[6] for item in database.attempts] == ["unavailable"] * 3 + ["recovered"]
        assert [item[6] for item in database.saved] == ["recovered"]
        assert sink.dropped == {"retry_exhausted": 1}
    output = capsys.readouterr().err
    assert "reason=retry_exhausted" in output and "private-host" not in output


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["acquire", "execute"])
async def test_timeout_bounds_pool_wait_and_query_without_losing_later_events(stage):
    async def acquire(attempt):
        if stage == "acquire" and attempt <= 3:
            await asyncio.Future()

    async def execute(args):
        if stage == "execute" and args[6] == "blocked":
            await asyncio.Future()

    database = Database(execute=execute, acquire=acquire)
    async with running_sink(database, ATTEMPT_TIMEOUT=0.01) as sink:
        sink.emit(record("blocked"))
        sink.emit(record("next"))
        await drained(sink)
        assert database.acquisitions == 4
        assert [item[6] for item in database.saved] == ["next"]
        assert sink.dropped == {"retry_exhausted": 1}


@pytest.mark.asyncio
async def test_full_queue_is_observable_without_recursive_log_storm(capsys):
    database = Database()
    async with running_sink(database) as sink:
        sink.queue = asyncio.Queue(maxsize=1)
        logging.getLogger().addHandler(sink)
        for _ in range(25):
            sink.emit(record("private-payload"))
        assert sink._pending == 1 and sink.queue.empty()
        await drained(sink)
        assert len(database.saved) == 1
        assert sink.dropped == {"queue_full": 24}
        assert capsys.readouterr().err.count("reason=queue_full") == 1
    output = capsys.readouterr().err
    assert "reason=shutdown_summary dropped=24" in output
    assert "private-payload" not in output


@pytest.mark.asyncio
async def test_shutdown_cancels_pending_query_and_accounts_for_queued_events(capsys):
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def execute(args):
        started.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    database = Database(execute=execute)
    sink = DatabaseEventSink(database)
    sink.SHUTDOWN_TIMEOUT = 0.01
    sink.emit(record("private-in-flight"))
    await asyncio.wait_for(started.wait(), 1)
    sink.emit(record("private-queued"))
    await asyncio.wait_for(sink.shutdown(), 1)
    assert cancelled.is_set() and sink.task.cancelled()
    assert sink.dropped == {"shutdown": 2} and sink.queue.empty()
    await asyncio.wait_for(sink.queue.join(), 0.1)
    # A producer already admitted before shutdown can finish formatting late.
    sink._pending += 1
    sink._enqueue({})
    sink.emit(record("private-late"))
    await asyncio.sleep(0)
    assert sink.queue.empty() and sink.dropped["shutdown"] == 3
    assert sink._pending == 0
    assert "private" not in capsys.readouterr().err


@pytest.mark.asyncio
async def test_closed_diagnostic_stream_does_not_kill_consumer(monkeypatch):
    class ClosedStream:
        def write(self, text):
            raise OSError("closed")

    async def execute(args):
        if args[6] == "invalid":
            raise ValueError("private-error")

    database = Database(execute=execute)
    async with running_sink(database) as sink:
        with monkeypatch.context() as patch:
            patch.setattr("sales_backend.services.system_events.sys.stderr", ClosedStream())
            sink.emit(record("invalid"))
            sink.emit(record("valid"))
            await drained(sink)
        assert [item[6] for item in database.saved] == ["valid"]


@pytest.mark.asyncio
async def test_only_owned_warnings_or_explicit_service_events_are_persisted():
    database = Database()
    async with running_sink(database) as sink:
        sink.emit(record("other-library", name="httpx"))
        sink.emit(record("ordinary-info", level=logging.INFO))
        started = record("service-start", level=logging.INFO)
        started.system_event = True
        sink.emit(started)
        await drained(sink)
        assert [item[6] for item in database.saved] == ["service-start"]


@pytest.mark.asyncio
@pytest.mark.parametrize("threaded", [False, True])
async def test_emit_bounds_scheduled_callbacks_before_a_stalled_loop_can_drain(monkeypatch, threaded):
    database = Database()
    async with running_sink(database) as sink:
        scheduled = 0
        schedule = sink.loop.call_soon_threadsafe

        def counted(callback, *args):
            nonlocal scheduled
            scheduled += 1
            return schedule(callback, *args)

        monkeypatch.setattr(sink.loop, "call_soon_threadsafe", counted)

        def emit_batch(size):
            for _ in range(size):
                sink.emit(record("private-pressure-payload"))

        if threaded:
            producers = [Thread(target=emit_batch, args=(1250,)) for _ in range(4)]
            for producer in producers:
                producer.start()
            for producer in producers:
                producer.join(timeout=2)
                assert not producer.is_alive()
        else:
            emit_batch(5000)  # No await: queue-side limiting alone is too late.
        assert scheduled == sink.queue.maxsize == sink._pending == 2000
        assert sink.queue.empty() and sink.dropped == {"queue_full": 3000}
        await drained(sink)
        assert len(database.saved) == 2000 and sink._pending == 0
        sink.emit(record("after-recovery"))
        await drained(sink)
        assert database.saved[-1][6] == "after-recovery" and sink._pending == 0


@pytest.mark.asyncio
async def test_failed_loop_schedule_releases_producer_admission(monkeypatch):
    database = Database()
    async with running_sink(database) as sink:
        def closed(*args):
            raise RuntimeError("loop closed")

        with monkeypatch.context() as patch:
            patch.setattr(sink.loop, "call_soon_threadsafe", closed)
            sink.emit(record("private-unscheduled"))
        assert sink._pending == 0 and sink.dropped == {"shutdown": 1}
        sink.emit(record("valid"))
        await drained(sink)
        assert [item[6] for item in database.saved] == ["valid"] and sink._pending == 0
