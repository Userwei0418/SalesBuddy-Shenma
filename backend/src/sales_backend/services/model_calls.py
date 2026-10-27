import asyncio
import logging
import asyncpg
from uuid import UUID, uuid4

from sales_backend.integrations.model_observer import response_usage
from sales_backend.services.ai_service_policy import AIServiceUnavailable
from sales_backend.observability import current_request_id
from sales_backend.repositories.model_calls import ModelCallRepository

logger = logging.getLogger(__name__)


class CallQuotaExceeded(RuntimeError):
    def __init__(self):
        super().__init__('公司 AI 或语音调用额度不足，请联系平台管理员调整额度')


class DatabaseModelObserver:
    def __init__(self, database, actor, operation, *, operation_id=None, run_id=None, provider="senseaudio"):
        self.database, self.actor, self.operation = database, actor, operation
        self.provider = provider
        self.operation_id, self.run_id = operation_id or str(uuid4()), run_id
        self.repository = ModelCallRepository()
        self._receipts = {}

    async def start(self, endpoint, model, metadata, attempt):
        try:
            request_id = str(UUID(current_request_id()))
        except ValueError:
            request_id = None
        # Admission/dispatch still require the current worker lease. Only usage
        # settlement may outlive that lease or the actor's business authority.
        async with self.database.transaction(self.actor) as connection:
            invocation_id = await self.repository.start(
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
            receipt = str(uuid4())
            try:
                await connection.fetchval('SELECT security.reserve_model_call($1::uuid,$2::uuid)', invocation_id, receipt)
            except asyncpg.PostgresError as exc:
                if exc.sqlstate == 'P7432':
                    raise AIServiceUnavailable() from None
                if exc.sqlstate == 'P7431':
                    raise CallQuotaExceeded() from None
                raise
            self._receipts[invocation_id] = receipt
        return invocation_id

    async def prepare_dispatch(self, invocation_id):
        async with self.database.transaction(self.actor) as connection:
            try:
                await connection.fetchval('SELECT security.dispatch_model_call($1::uuid,$2::uuid)',
                                          invocation_id, self._receipts[invocation_id])
            except asyncpg.PostgresError as exc:
                if exc.sqlstate == 'P7432':
                    raise AIServiceUnavailable() from None
                raise

    async def finish(self, invocation_id, response, error, *, response_metadata=None, network_sent=True):
        status = "cancelled" if isinstance(error, asyncio.CancelledError) else "failed" if error else "succeeded"
        usage = response_usage(response)
        for retry in range(3):
            try:
                async with self.database.connection() as connection:
                    async with connection.transaction():
                        await connection.fetchval('SELECT security.finish_model_call($1::uuid,$2::uuid,$3,$4::jsonb)',
                            invocation_id, self._receipts[invocation_id], network_sent,
                            {**usage, 'status': status, 'error_code': type(error).__name__ if error else None,
                             'response_metadata': response_metadata or {}})
                return
            except Exception:
                if retry == 2:
                    # The committed start remains visible as running/unknown. Do
                    # not fabricate tokens or retry a billed provider request.
                    logger.exception("model usage completion could not be persisted invocation=%s", invocation_id)
                    return
                await asyncio.sleep(0.1 * (retry + 1))
