"""Narrow server-owned source attachment binding operations."""

from sales_backend.db import json_value


class KnowledgeSourceFilesRepository:
    async def get(self, connection, material_id):
        return json_value(await connection.fetchval("SELECT security.knowledge_source_files($1)", material_id))

    async def archive(self, connection, material_id, body):
        return json_value(
            await connection.fetchval(
                "SELECT security.knowledge_archive_source_files($1,$2,$3,$4,$5::jsonb)",
                material_id,
                body.expected_material_revision,
                body.expected_acl_revision,
                body.expected_source_revision,
                [a.model_dump(mode="json") for a in body.attachments],
            )
        )
