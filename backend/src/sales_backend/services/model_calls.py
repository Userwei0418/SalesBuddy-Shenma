import asyncio
import logging
from uuid import UUID, uuid4

from sales_backend.db import set_request_context
from sales_backend.integrations.model_observer import response_usage
from sales_backend.observability import current_request_id
from sales_backend.repositories.model_calls import ModelCallRepository

logger = logging.getLogger(__name__)


class DatabaseModelObserver:
    def __init__(self, database, actor, operation, *, operation_id=None, run_id=None, provider="senseaudio"):
        self.database, self.actor, self.operation = database, actor, operation
        self.provider = provider
        self.operation_id, self.run_id = operation_id or str(uuid4()), run_id
        self.repository = ModelCallRepository()

    async def start(self, endpoint, model, metadata, attempt):
        try:
            request_id = str(UUID(current_request_id()))
        except ValueError:
            request_id = None
        # Accounting survives a worker lease expiring after the provider has billed
        # a call. Business result transactions still enforce their own job lease.
        async with self.database.connection() as connection:
            async with connection.transaction():
                await set_request_context(connection, self.actor)
                return await self.repository.start(
                    connection,
                    self.actor,
                    operation=self.operation,
                    operation_id=self.operation_id,
                    run_id=self.run_id,
                    endpoint=endpoint,
                    model=model,
                    metadata=metadata,
                    attempt=attempt,
                    request_id=request_id,
                    provider=("direct_api" if metadata.get("connection_mode") == "custom" else self.provider),
                )

    async def finish(self, invocation_id, response, error, *, response_metadata=None):
        status = "cancelled" if isinstance(error, asyncio.CancelledError) else "failed" if error else "succeeded"
        usage = response_usage(response)
        for retry in range(3):
            try:
                async with self.database.connection() as connection:
                    async with connection.transaction():
                        await set_request_context(connection, self.actor)
                        await self.repository.finish(
                            connection, invocation_id, usage, status, type(error).__name__ if error else None,
                            response_metadata=response_metadata,
                        )
                return
            except Exception:
                if retry == 2:
                    # The committed start remains visible as running/unknown. Do
                    # not fabricate tokens or retry a billed provider request.
                    logger.exception("model usage completion could not be persisted invocation=%s", invocation_id)
                    return
                await asyncio.sleep(0.1 * (retry + 1))
