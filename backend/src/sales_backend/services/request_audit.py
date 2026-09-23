import logging

from sales_backend.repositories.operations_logs import OperationsLogRepository

logger = logging.getLogger(__name__)


async def record_http_operation(database, identity, method, path, status, *, export_count=None, filters=None):
    if database is None or identity is None:
        return
    try:
        async with database.transaction(identity.actor) as connection:
            await OperationsLogRepository().record_request(
                connection,
                identity.actor,
                method=method,
                path=path,
                status=status,
                export_count=export_count,
                filters=filters,
                authenticated_actor=identity.authenticated_profile.context if identity.authenticated_profile else None,
            )
    except Exception:
        # Durable business changes remain audited in their original transaction;
        # if this separate request receipt fails, the system journal records it.
        logger.exception("request audit receipt failed method=%s path=%s", method, path)
