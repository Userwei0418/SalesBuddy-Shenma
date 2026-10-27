"""Publication, anonymous questionnaire gates, and scoped analytics."""

import hashlib
import json
import secrets
from uuid import UUID

from sales_backend.db import json_value
from sales_backend.repositories.knowledge import KnowledgeRepository
from sales_backend.repositories.knowledge_distribution import DistributionRepository
from sales_backend.services.idempotency import execute_mutation


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


async def enqueue_distribution(connection, **event):
    # Implemented by the Feishu slice. Missing wiring is an explicit failed
    # transaction, never a successful publish with silently dropped sync.
    from sales_backend.services.knowledge_distribution_sync import enqueue_knowledge_distribution_event

    return await enqueue_knowledge_distribution_event(connection, **event)


class DistributionService:
    def __init__(self, database, *, enqueue=enqueue_distribution):
        self.database = database
        self.repository = DistributionRepository()
        self.knowledge = KnowledgeRepository()
        self.enqueue = enqueue

    async def read(self, actor, method, *args):
        async with self.database.transaction(actor, readonly=True, isolation="repeatable_read") as connection:
            await self.knowledge.require_feature(connection, "read")
            if method in {"shares", "statistics", "responses"}:
                query = args[0]
                if query.scope == "all" and not await self.repository.admin(connection):
                    raise PermissionError("KNOWLEDGE_DISTRIBUTION_DENIED")
                if query.scope == "self" and query.sales_user_id not in (None, UUID(actor.user_id)):
                    raise PermissionError("KNOWLEDGE_DISTRIBUTION_DENIED")
            return await getattr(self.repository, method)(connection, *args)

    async def mutate(self, actor, key, operation, payload, perform, authorize):
        if key is None:
            raise ValueError("KNOWLEDGE_IDEMPOTENCY_KEY_REQUIRED")
        async with self.database.transaction(actor) as connection:
            await self.knowledge.lock(connection)

            async def auth():
                await self.knowledge.require_feature(connection, "read")
                await authorize(connection)

            await auth()
            return await execute_mutation(
                connection, actor, key, operation, payload, lambda: perform(connection), authorize=auth
            )

    async def publish(self, actor, key, material_id, body):
        payload = body.model_dump(mode="json")
        # Preserve pre-existing idempotency receipts for the default behavior.
        # Only an explicit silent publication adds a new payload field.
        if body.notify:
            payload.pop("notify")

        async def authorize(connection):
            if not await self.repository.admin(connection):
                raise PermissionError("KNOWLEDGE_DISTRIBUTION_DENIED")
            await self.knowledge.require_material(connection, material_id, "manage")

        async def perform(connection):
            before = await self.repository.publication(connection, material_id)
            publication_id = await connection.fetchval("SELECT security.kd_publish($1,$2::jsonb)", material_id, payload)
            result = await self.repository.publication(connection, material_id)
            if not body.enabled:
                kind = "withdrawn"
            elif not before.get("revision", 0):
                kind = "published"
            elif str(before.get("version_id")) == str(body.version_id):
                kind = "metadata_updated"
            else:
                kind = "version_published"
            await self.enqueue(
                connection,
                workspace_id=UUID(actor.workspace_id),
                material_id=material_id,
                publication_id=publication_id,
                version_id=body.version_id,
                event_kind=kind,
                revision=result["revision"],
                actor_id=UUID(actor.user_id),
            )
            return result

        return await self.mutate(
            actor, key, f"knowledge.distribution.publish:{material_id}", payload, perform, authorize
        )

    async def share(self, actor, key, material_id, body):
        async def authorize(connection):
            await self.knowledge.require_material(connection, material_id)
            if not await connection.fetchval("SELECT security.kd_publication_visible($1)", body.publication_id):
                raise LookupError("KNOWLEDGE_SHARE_UNAVAILABLE")
            matches = await connection.fetchval(
                "SELECT material_id=$2 FROM knowledge.distribution_publication WHERE id=$1",
                body.publication_id,
                material_id,
            )
            if not matches:
                raise LookupError("KNOWLEDGE_SHARE_UNAVAILABLE")

        async def perform(connection):
            token = secrets.token_urlsafe(32)
            sid = await connection.fetchval(
                "SELECT security.kd_create_share($1,$2,$3)", material_id, body.publication_id, digest(token)
            )
            return dict(
                await self.repository.share(connection, sid),
                token=token,
                share_path="/web/knowledge-share.html#token=" + token,
            )

        result = await self.mutate(
            actor, key, f"knowledge.distribution.share:{material_id}", body.model_dump(mode="json"), perform, authorize
        )
        # A stored idempotent response never revives a revoked link.
        async with self.database.transaction(actor, readonly=True) as connection:
            current = await self.repository.share(connection, UUID(str(result["share_id"])))
            if current["revoked_at"] is not None:
                raise LookupError("KNOWLEDGE_SHARE_UNAVAILABLE")
        return result

    async def revoke(self, actor, key, share_id, body):
        async def authorize(connection):
            await self.repository.share(connection, share_id)

        async def perform(connection):
            await connection.fetchval("SELECT security.kd_revoke($1,$2)", share_id, body.expected_revoked)
            return await self.repository.share(connection, share_id)

        return await self.mutate(
            actor, key, f"knowledge.distribution.revoke:{share_id}", body.model_dump(mode="json"), perform, authorize
        )

    async def public(self, method, token, *, browser_id=None, body=None, key=None, grant=None, file_id=None):
        # No ActorContext, tenant supplied by caller, authorization_refresh, or
        # arbitrary query method exists on this anonymous path.
        async with self.database.connection() as connection:
            async with connection.transaction():
                if method == "metadata":
                    result = await connection.fetchval(
                        "SELECT security.kd_public_metadata($1,$2)", digest(token), digest(browser_id)
                    )
                elif method == "response":
                    if key is None:
                        raise ValueError("KNOWLEDGE_IDEMPOTENCY_KEY_REQUIRED")
                    payload = body.model_dump(mode="json")
                    fingerprint = digest(json.dumps(payload, sort_keys=True, ensure_ascii=False))
                    result = await connection.fetchval(
                        "SELECT security.kd_public_response($1,$2,$3,$4,$5,$6::jsonb,$7)",
                        digest(token),
                        digest(body.browser_id),
                        key,
                        fingerprint,
                        body.questionnaire_version,
                        body.answers.model_dump(),
                        secrets.token_urlsafe(32),
                    )
                elif method in {"cover", "preview", "download"}:
                    result = await connection.fetchrow(
                        "SELECT * FROM security.kd_public_file($1,$2,$3,$4)",
                        digest(token),
                        digest(grant or ""),
                        file_id,
                        method,
                    )
                else:
                    raise ValueError("Unsupported public operation")
                return json_value(result) if method in {"metadata", "response"} else dict(result)
