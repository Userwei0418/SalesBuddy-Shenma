"""Bounded asynchronous journal sink; stdout remains available when PostgreSQL fails."""

import asyncio
import logging
import sys
import traceback
from collections import Counter
from threading import RLock
from time import monotonic
from uuid import UUID

import asyncpg

from sales_backend.db import DatabaseBusy
from sales_backend.domain.sanitization import redact_log
from sales_backend.repositories.operations_logs import OperationsLogRepository
from sales_backend.request_metadata import current_actor, request_metadata


class DatabaseEventSink(logging.Handler):
    MAX_ATTEMPTS = 3
    ATTEMPT_TIMEOUT = 5
    RETRY_DELAYS = (0.25, 1)
    SHUTDOWN_TIMEOUT = 5
    DIAGNOSTIC_INTERVAL = 30

    def __init__(self, database):
        super().__init__(logging.INFO)
        self.database = database
        self.loop = asyncio.get_running_loop()
        self.queue = asyncio.Queue(maxsize=2000)
        self.dropped = Counter()
        self.retry_count = 0
        self._accepting = True
        self._admission_lock = RLock()
        # Includes scheduled callbacks, queued events and the in-flight write.
        # The bound must apply before scheduling onto a possibly stalled loop.
        self._pending = 0
        self._reported = {}
        self.task = self.loop.create_task(self._run())

    def emit(self, record):
        if not self._accepting:
            return
        if not record.name.startswith("sales_backend") or (
            record.levelno < logging.WARNING and not getattr(record, "system_event", False)
        ):
            return
        with self._admission_lock:
            if not self._accepting:
                return
            if self._pending >= self.queue.maxsize:
                self._drop("queue_full")
                return
            self._pending += 1
        try:
            event = self._event(record)
        except Exception:
            # A malformed logging argument must not fail the business request.
            self._release()
            self._drop("invalid_event")
            return
        self._schedule(self._enqueue, event)

    @staticmethod
    def _text(value, limit):
        # PostgreSQL text rejects NUL and UTF-8 cannot encode lone surrogates.
        # Preserve their location as harmless text before redacting/truncating.
        value = str(value).replace("\x00", "\\0").encode("utf-8", errors="replace").decode("utf-8")
        return redact_log(value, limit)

    def _event(self, record):
        actor = getattr(record, "actor", None) or current_actor.get()
        try:
            request_id = str(UUID(str(getattr(record, "request_id", None) or request_metadata.get().request_id)))
        except (ValueError, TypeError):
            request_id = None
        return {
            # Invalid tenant attribution is rejected, never changed into a global event.
            "workspace_id": str(UUID(str(actor.workspace_id))) if actor else None,
            "actor_id": str(UUID(str(actor.user_id))) if actor else None,
            "request_id": request_id,
            "level": "ERROR"
            if record.levelno >= logging.ERROR
            else "WARN"
            if record.levelno >= logging.WARNING
            else "INFO",
            "module": self._text(record.name, 100),
            "event_type": self._text(
                getattr(record, "event_type", "exception" if record.exc_info else "service_event"), 100
            ),
            "detail": self._text(record.getMessage(), 8000),
            "stack": (
                self._text("".join(traceback.format_exception(*record.exc_info)), 32000) if record.exc_info else None
            ),
        }

    def _schedule(self, callback, value):
        try:
            self.loop.call_soon_threadsafe(callback, value)
        except RuntimeError:
            self._release()
            self._drop("shutdown")

    def _release(self):
        with self._admission_lock:
            self._pending -= 1

    def _diagnostic(self, reason, *, force=False):
        now = monotonic()
        if not force and reason in self._reported and now - self._reported[reason] < self.DIAGNOSTIC_INTERVAL:
            return
        self._reported[reason] = now
        try:
            # Fixed codes and aggregate counters only: no exception, actor or payload.
            # Do not use logging here: that would enqueue another failing event.
            sys.stderr.write(
                f"salesbuddy_system_event_sink reason={reason} dropped={sum(self.dropped.values())} "
                f"retries={self.retry_count} queued={self.queue.qsize()}\n"
            )
        except Exception:  # noqa: S110 -- Logging this error would recursively enter this sink.
            pass  # A closed standard stream must not crash request logging.

    def _drop(self, reason):
        with self._admission_lock:
            self.dropped[reason] += 1
            self._diagnostic(reason)

    def _enqueue(self, event):
        if not self._accepting:
            self._release()
            self._drop("shutdown")
        elif self.queue.full():
            self._release()
            self._drop("queue_full")
        else:
            self.queue.put_nowait(event)

    @staticmethod
    def _transient(exc):
        if isinstance(exc, (DatabaseBusy, TimeoutError, OSError, asyncpg.InterfaceError)):
            return True
        if isinstance(exc, asyncpg.PostgresError):
            state = exc.sqlstate or ""
            return state.startswith(("08", "40", "53")) or state in {"57014", "57P01", "57P02", "57P03"}
        return False

    async def _persist(self, repository, event):
        for attempt in range(self.MAX_ATTEMPTS):
            try:
                # Bound pool acquisition as well as the SQL operation.
                async with asyncio.timeout(self.ATTEMPT_TIMEOUT), self.database.connection() as connection:
                    await repository.append_system(connection, event)
                return
            except Exception as exc:
                if not self._transient(exc):
                    self._drop("permanent_failure")
                    return
                if attempt == self.MAX_ATTEMPTS - 1:
                    self._drop("retry_exhausted")
                    return
                self.retry_count += 1
                self._diagnostic("retrying")
                await asyncio.sleep(self.RETRY_DELAYS[attempt])

    async def _run(self):
        repository = OperationsLogRepository()
        while True:
            event = await self.queue.get()
            try:
                await self._persist(repository, event)
            except asyncio.CancelledError:
                self._drop("shutdown")
                raise
            finally:
                self.queue.task_done()
                self._release()

    async def shutdown(self):
        # Flush callbacks already submitted (including service_stop), then stop intake.
        await asyncio.sleep(0)
        with self._admission_lock:
            self._accepting = False
        try:
            await asyncio.wait_for(self.queue.join(), timeout=self.SHUTDOWN_TIMEOUT)
        except TimeoutError:
            pass
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        while not self.queue.empty():
            self.queue.get_nowait()
            self.queue.task_done()
            self._release()
            self._drop("shutdown")
        if self.dropped or self.retry_count:
            self._diagnostic("shutdown_summary", force=True)
        logging.getLogger().removeHandler(self)
