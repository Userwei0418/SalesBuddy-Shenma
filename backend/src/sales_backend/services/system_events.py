"""Bounded asynchronous journal sink; stdout remains available when PostgreSQL fails."""

import asyncio
import logging
import traceback
from uuid import UUID

from sales_backend.domain.sanitization import redact_log
from sales_backend.repositories.operations_logs import OperationsLogRepository
from sales_backend.request_metadata import current_actor, request_metadata


class DatabaseEventSink(logging.Handler):
    def __init__(self, database):
        super().__init__(logging.INFO)
        self.database = database
        self.loop = asyncio.get_running_loop()
        self.queue = asyncio.Queue(maxsize=2000)
        self.task = self.loop.create_task(self._run())

    def emit(self, record):
        if not record.name.startswith("sales_backend") or (
            record.levelno < logging.WARNING and not getattr(record, "system_event", False)
        ):
            return
        actor = getattr(record, "actor", None) or current_actor.get()
        meta = request_metadata.get()
        try:
            request_id = str(UUID(getattr(record, "request_id", None) or meta.request_id))
        except (ValueError, TypeError):
            request_id = None
        event = {
            "workspace_id": actor.workspace_id if actor else None,
            "actor_id": actor.user_id if actor else None,
            "request_id": request_id,
            "level": "ERROR"
            if record.levelno >= logging.ERROR
            else "WARN"
            if record.levelno >= logging.WARNING
            else "INFO",
            "module": record.name[:100],
            "event_type": getattr(record, "event_type", "exception" if record.exc_info else "service_event"),
            "detail": redact_log(record.getMessage(), 8000),
            "stack": redact_log("".join(traceback.format_exception(*record.exc_info))) if record.exc_info else None,
        }
        self.loop.call_soon_threadsafe(self._enqueue, event)

    def _enqueue(self, event):
        if not self.queue.full():
            self.queue.put_nowait(event)

    async def _run(self):
        repository = OperationsLogRepository()
        while True:
            event = await self.queue.get()
            try:
                while True:
                    try:
                        async with self.database.connection() as connection:
                            await repository.append_system(connection, event)
                        break
                    except Exception:
                        # Do not log this failure through ourselves recursively.
                        await asyncio.sleep(2)
            finally:
                self.queue.task_done()

    async def shutdown(self):
        await asyncio.sleep(0)
        try:
            await asyncio.wait_for(self.queue.join(), timeout=5)
        except TimeoutError:
            pass
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        logging.getLogger().removeHandler(self)
