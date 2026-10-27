"""Only current-authority, database-fenced ingestion writes."""

from uuid import UUID

from sales_backend.repositories.knowledge import KnowledgeRepository


class KnowledgeIngestionRepository:
    async def version(self, c, material_id, version_id):
        await KnowledgeRepository().require_material(c, material_id, "read")
        row = await c.fetchrow(
            """SELECT v.*,m.revision AS material_revision,m.latest_version_id,
          security.knowledge_ingestion_allowed(m.id,v.id) AS can_process,
          (SELECT count(*) FROM knowledge.version_file f WHERE f.version_id=v.id) AS file_count
          FROM knowledge.version v JOIN knowledge.material m ON m.id=v.material_id
          WHERE v.material_id=$1 AND v.id=$2 AND v.state<>'deleted' """,
            material_id,
            version_id,
        )
        if row is None:
            raise LookupError("KNOWLEDGE_VERSION_NOT_FOUND")
        return dict(row)

    async def current(self, c, material_id, version_id):
        row = await c.fetchrow(
            """SELECT i.*,
         (SELECT count(*) FROM knowledge.ingestion_file f
           WHERE f.ingestion_id=i.id AND f.status='parsed') AS processed_files,
         (SELECT count(*) FROM knowledge.ingestion_file f WHERE f.ingestion_id=i.id) AS total_files,
         (SELECT status FROM ops.job j WHERE j.id=i.job_id) AS job_status
         FROM knowledge.ingestion i WHERE material_id=$1 AND version_id=$2""",
            material_id,
            version_id,
        )
        return dict(row) if row else None

    async def start(self, c, actor, material_id, version_id, revision, configuration):
        return await c.fetchval(
            "SELECT security.knowledge_ingestion_start($1,$2,$3,$4,$5,$6,$7::jsonb)",
            material_id,
            version_id,
            revision,
            configuration["origin"],
            configuration["dataset"],
            configuration["fingerprint"],
            actor.model_dump(mode="json"),
        )

    async def task(self, c, ingestion_id):
        row = await c.fetchrow("SELECT * FROM knowledge.ingestion WHERE id=$1", UUID(str(ingestion_id)))
        if row is None:
            raise PermissionError("KNOWLEDGE_ACCESS_DENIED")
        return dict(row)

    async def files(self, c, ingestion_id):
        rows = await c.fetch(
            """SELECT s.*,f.filename,f.media_type,f.sha256,f.size_bytes
          FROM knowledge.ingestion_file s JOIN knowledge.file f ON f.id=s.file_id AND f.workspace_id=s.workspace_id
          WHERE ingestion_id=$1 ORDER BY s.file_id""",
            UUID(str(ingestion_id)),
        )
        return [dict(row) for row in rows]

    async def content(self, c, ingestion_id, file_id):
        return await c.fetchval(
            """SELECT f.content FROM knowledge.ingestion_file s JOIN knowledge.file f
          ON f.id=s.file_id AND f.workspace_id=s.workspace_id WHERE s.ingestion_id=$1 AND s.file_id=$2""",
            UUID(str(ingestion_id)),
            file_id,
        )

    async def step(self, c, ingestion_id, action, file_id=None, document_id=None, chunks=None, error=None):
        await c.execute(
            "SELECT security.knowledge_ingestion_step($1,$2,$3,$4,$5,$6)",
            UUID(str(ingestion_id)),
            action,
            file_id,
            document_id,
            chunks,
            error,
        )
