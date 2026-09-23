"""Independent maintenance loops; a broken monitor never blocks business job claims."""

import asyncio
import logging

from sales_backend.repositories.maintenance import MaintenanceRepository

logger = logging.getLogger("sales_backend.worker.maintenance")


class WorkerMaintenance:
    INTERVALS = {
        "ai_usage": 60,
        "log_retention": 86400,
        "competency_schedule": 300,
        "priority_reminders": 1800,
    }
    RETRY_SECONDS = 30
    TIMEOUT_SECONDS = 10

    def __init__(self, database):
        self.database = database
        self.repository = MaintenanceRepository()

    async def run_once(self, name: str) -> int:
        try:
            async with asyncio.timeout(self.TIMEOUT_SECONDS):
                async with self.database.connection() as connection:
                    await self.repository.run(connection, name)
        except Exception:
            logger.exception(
                "worker maintenance failed: %s", name,
                extra={"event_type": "worker_maintenance_failed"},
            )
            return self.RETRY_SECONDS
        return self.INTERVALS[name]

    async def _loop(self, name: str) -> None:
        while True:
            delay = await self.run_once(name)
            await asyncio.sleep(delay)

    async def run_forever(self) -> None:
        async with asyncio.TaskGroup() as group:
            for name in self.INTERVALS:
                group.create_task(self._loop(name), name=f"maintenance:{name}")
