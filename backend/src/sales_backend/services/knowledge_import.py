"""Source-bound import into the ordinary ACL-protected knowledge directory."""

from collections import Counter
from uuid import UUID

from sales_backend.contracts.knowledge_import import imported_contribution
from sales_backend.db import json_value
from sales_backend.repositories.knowledge import KnowledgeRepository
from sales_backend.services.idempotency import execute_mutation


class KnowledgeImportService:
    def __init__(self, database):
        self.database = database
        self.repository = KnowledgeRepository()

    async def run(self, actor, key, body):
        if str(body.workspace_id) != str(actor.workspace_id):
            raise PermissionError("KNOWLEDGE_IMPORT_ACCESS_DENIED")
        if not body.dry_run and key is None:
            raise ValueError("KNOWLEDGE_IDEMPOTENCY_KEY_REQUIRED")
        contributions = [imported_contribution(body, record).model_dump(mode="json") for record in body.records]
        payload = body.model_dump(mode="json")
        # Semantic grants cannot vary with process-local frozenset ordering.
        for grant in payload["grants"]:
            grant["actions"] = sorted(grant["actions"])
        payload["grants"].sort(key=lambda g: (g["subject_kind"], g["subject_id"] or ""))
        grants = payload["grants"]
        async with self.database.transaction(actor) as connection:
            await self.repository.lock(connection)

            async def authorize():
                await connection.fetchval(
                    "SELECT security.knowledge_import_authorize($1,$2,$3::jsonb)",
                    body.workspace_id,
                    body.owner_user_id,
                    grants,
                )

            await authorize()

            async def perform():
                items = []
                for record, content in zip(body.records, contributions, strict=True):
                    value = await connection.fetchval(
                        "SELECT security.knowledge_import_record($1,$2,$3::jsonb,$4,$5,$6,$7,$8::jsonb,$9)",
                        body.workspace_id,
                        body.owner_user_id,
                        grants,
                        body.base_id,
                        body.table_id,
                        record.record_id,
                        body.source_revision,
                        content,
                        body.dry_run,
                    )
                    items.append(json_value(value))
                counts = Counter(item["outcome"] for item in items)
                return {
                    "workspace_id": str(body.workspace_id),
                    "base_id": body.base_id,
                    "table_id": body.table_id,
                    "source_revision": body.source_revision,
                    "dry_run": body.dry_run,
                    "items": items,
                    "counts": {kind: counts[kind] for kind in ("created", "updated", "unchanged", "conflict")},
                }

            if body.dry_run:
                return await perform()
            result = await execute_mutation(
                connection,
                actor,
                key,
                "knowledge.source_import",
                payload,
                perform,
                authorize=authorize,
            )
            # A receipt is not current permission; never return a revoked material on replay.
            for item in result["items"]:
                if item["material_id"]:
                    await self.repository.require_material(connection, UUID(item["material_id"]), "manage")
            return result
