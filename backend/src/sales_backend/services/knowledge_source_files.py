"""Explicit archive only: no source downloads, publication or parsing dispatch."""

from sales_backend.domain.knowledge import KnowledgeRuleError
from sales_backend.repositories.knowledge import KnowledgeRepository
from sales_backend.repositories.knowledge_source_files import KnowledgeSourceFilesRepository
from sales_backend.services.idempotency import execute_mutation


class KnowledgeSourceFilesService:
    def __init__(self, database):
        self.database = database
        self.knowledge = KnowledgeRepository()
        self.repository = KnowledgeSourceFilesRepository()

    async def get(self, actor, material_id):
        async with self.database.transaction(actor) as connection:
            return await self.repository.get(connection, material_id)

    async def archive(self, actor, key, material_id, body):
        if key is None:
            raise KnowledgeRuleError("KNOWLEDGE_IDEMPOTENCY_KEY_REQUIRED", "A request key is required")
        async with self.database.transaction(actor) as connection:
            await self.knowledge.lock(connection)

            async def authorize():
                current = await self.repository.get(connection, material_id)
                if not current["can_archive"]:
                    raise KnowledgeRuleError(
                        "KNOWLEDGE_SOURCE_ARCHIVE_UNAVAILABLE", "Source is not available for archive"
                    )

            await authorize()
            payload = body.model_dump(mode="json")
            payload["attachments"].sort(key=lambda a: a["source_token"])
            return await execute_mutation(
                connection,
                actor,
                key,
                f"knowledge.source_archive:{material_id}",
                payload,
                lambda: self.repository.archive(connection, material_id, body),
                authorize=authorize,
            )
