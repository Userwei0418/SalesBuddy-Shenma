"""Leased outbox consumer. Network calls are outside business transactions."""
import json
from contextlib import asynccontextmanager
from hashlib import sha256
from time import monotonic
from uuid import UUID

from sales_backend.db import json_value
from sales_backend.domain.feishu_sync.cards import notification_card
from sales_backend.domain.feishu_sync.config import COMMON_FIELDS, SOURCE_FIELDS, SyncConfig
from sales_backend.domain.feishu_sync.planning import SourceEvent, notification_plans
from sales_backend.domain.feishu_sync.projection import RELATIONS, encode_scalar, project
from sales_backend.integrations.feishu import FeishuClient, FeishuError
from sales_backend.repositories.feishu_sync import FeishuRepository
from sales_backend.security.runtime_credentials import CredentialCipher


class FeishuSyncService:
    def __init__(self, settings, *, client_factory=FeishuClient, reuse_client=False):
        self.settings, self.client_factory = settings, client_factory
        self.repository = FeishuRepository()
        self.reuse_client = reuse_client
        self._cached_client = None
        self._comparison_schemas = {}

    def client(self, row):
        cipher = CredentialCipher.from_file(self.settings.config_credential_keyring_file,
                                            self.settings.config_credential_key_id)
        secret = cipher.decrypt(str(row["workspace_id"]), row["encryption_key_id"], bytes(row["ciphertext"]))
        config = SyncConfig.model_validate(json_value(row["settings"]) | {"enabled": row["enabled"]})
        return config, self.client_factory(config.app_id, secret)

    async def close(self):
        cached, self._cached_client = self._cached_client, None
        self._comparison_schemas.clear()
        if cached:
            await cached[3].close()

    @asynccontextmanager
    async def _client(self, row):
        if not self.reuse_client:
            config, client = self.client(row)
            try:
                yield config, client
            finally:
                await client.close()
            return
        # One entry per sequential worker, never shared between workers or
        # credentials. Schema is fetched afresh before each remote write.
        key = (str(row["id"]), str(row["workspace_id"]), row["revision"],
               row["credential_updated_at"], row["encryption_key_id"])
        cached = self._cached_client
        if cached is None or cached[0] != key or monotonic() >= cached[1]:
            await self.close()
            config, client = self.client(row)
            cached = self._cached_client = (key, monotonic() + 600, config, client)
        yield cached[2], cached[3]

    async def validate(self, connection, row):
        await self.close()  # Validation always uses fresh credentials and schema.
        client = None
        error = None
        try:
            config, client = self.client(row)
            if config.direction != "system_to_base":
                raise FeishuError("SYNC_DIRECTION_UNSUPPORTED")
            if not any(m.enabled for m in config.mappings.values()):
                raise ValueError("NO_ENABLED_MAPPINGS")
            for mapping in config.mappings.values():
                if not mapping.enabled:
                    continue
                fields = await client.fields(config.base_token, mapping.table_id)
                if fields.get(mapping.id_field_id, {}).get("type") != 1:
                    raise ValueError("SYSTEM_ID_REQUIRES_TEXT")
                for source, target in mapping.fields.items():
                    field = fields.get(target)
                    if not field or field.get("type") not in {1, 2, 3, 4, 5, 7, 13, 18, 21}:
                        raise ValueError("FIELD_MISSING_OR_UNSUPPORTED")
                    if field["type"] in {18, 21}:
                        parent = config.mappings.get(RELATIONS.get(source))
                        if (not parent or not parent.enabled
                                or parent.table_id != field.get("property", {}).get("table_id")):
                            raise ValueError("LINK_TARGET_MISMATCH")
            # Resource visibility only; does not create a record or send a message.
            if config.notification.enabled:
                chats = {config.notification.default_chat_id}
                if config.notification.routing_mode == "department_routes":
                    chats.update(chat for route in config.notification.routes for chat in route.chat_ids)
                for chat in chats:
                    await client.request("GET", f"/im/v1/chats/{chat}")
        except Exception as exc:
            error = exc.code if isinstance(exc, FeishuError) else "CONFIG_VALIDATION_FAILED"
        finally:
            if client:
                await client.close()
        await self.repository.validation_result(connection, row, error)

    async def handle(self, connection, event, row):
        async with self._client(row) as (config, client):
            if config.direction != "system_to_base":
                raise FeishuError("SYNC_DIRECTION_UNSUPPORTED")
            kind = event["object_kind"]
            if kind == "refresh":
                await self.repository.reconcile(connection, event["connection_id"])
                return
            mapping = config.mappings.get(kind)
            if not mapping or not mapping.enabled:
                return
            await self.repository.guard(connection, event, config.revision)
            raw = await self.repository.source(connection, event)
            values = project(kind, raw, SOURCE_FIELDS[kind] | COMMON_FIELDS)
            if values is None:
                return
            linked_records = {}
            for source, parent_kind in RELATIONS.items():
                if source not in mapping.fields or not values.get(source):
                    continue
                value = values[source]
                ids = value if isinstance(value, (list, tuple)) else [value]
                linked_records[source] = []
                for object_id in dict.fromkeys(ids):
                    parent = await self.repository.mapped(
                        connection, config.connection_id, parent_kind, UUID(str(object_id)))
                    linked_records[source].append(parent["record_id"] if parent else None)
            fingerprint = sha256(json.dumps({
                "version": 1, "app": config.app_id, "base": config.base_token,
                "mapping": mapping.model_dump(mode="json"),
                "values": {key: values.get(key) for key in {"system_id", "record_status", *mapping.fields} - {"synced_at"}},
                "links": linked_records,
            }, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            mapped = await self.repository.mapped(connection, config.connection_id, kind, event["object_id"])
            same_target = mapped and mapped["table_id"] == mapping.table_id
            force = event.get("force_remote_check", False) or event.get("attempts", 1) > 1
            if same_target and not force and mapped.get("source_fingerprint") == fingerprint:
                await self.repository.guard(connection, event, config.revision)
                await self.notify(connection, event, config, values, raw, client, record_id=mapped["record_id"])
                return
            # A cached schema is used ONLY to compare legacy acknowledged hashes.
            # Every actual write re-reads schema, preventing field-name reuse from
            # writing into an unrelated field after a remote rename.
            schema_key = (str(config.connection_id), config.revision, mapping.table_id)
            legacy = same_target and not force and not mapped.get("source_fingerprint")
            schema = self._comparison_schemas.get(schema_key) if legacy else None
            cached_schema = schema is not None
            if schema is None:
                schema = await client.fields(config.base_token, mapping.table_id)
                if len(self._comparison_schemas) >= 24:
                    self._comparison_schemas.clear()
                self._comparison_schemas[schema_key] = schema
            fields = await self.project_fields(connection, event, config, mapping, values, raw, schema, linked_records)
            digest = sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            if legacy and mapped["projection_hash"] == digest:
                await self.repository.guard(connection, event, config.revision)
                await self.repository.remember(connection, event, mapping.table_id, mapped["record_id"],
                                               digest, fingerprint)
                await self.notify(connection, event, config, values, raw, client, record_id=mapped["record_id"])
                return
            if legacy and cached_schema:
                schema = await client.fields(config.base_token, mapping.table_id)
                self._comparison_schemas[schema_key] = schema
                fields = await self.project_fields(connection, event, config, mapping, values, raw, schema, linked_records)
                digest = sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            record_id = await client.find_record(config.base_token, mapping.table_id,
                                                schema[mapping.id_field_id]["field_name"], values["system_id"])
            await self.repository.guard(connection, event, config.revision)
            if record_id is None and (raw.get("deleted") or raw.get("deleted_at") or raw.get("excluded")):
                await self.notify(connection, event, config, values, raw, client)
                return  # Never create remote records for missing or out-of-scope data.
            # The worker already holds the object lock. Independent objects may
            # write concurrently; explicit provider conflicts use queue backoff.
            await self.repository.guard(connection, event, config.revision)
            if record_id is None:
                # UUIDv4-shaped stable key, required by Feishu's create API.
                key = sha256(f"{config.connection_id}:{kind}:{event['object_id']}".encode()).digest()[:16]
                record_id = await client.create_record(
                    config.base_token, mapping.table_id, fields, UUID(bytes=key, version=4))
            else:
                # Repair remote edits even when the source hash is unchanged.
                await client.update_record(config.base_token, mapping.table_id, record_id, fields)
            await self.repository.remember(connection, event, mapping.table_id, record_id, digest, fingerprint)
            await self.notify(connection, event, config, values, raw, client, record_id=record_id)

    async def project_fields(self, connection, event, config, mapping, values, raw, schema, linked_records):
        if mapping.id_field_id not in schema:
            raise FeishuError("ID_FIELD_MISSING")
        fields = {schema[mapping.id_field_id]["field_name"]: values["system_id"]}
        for source, target in mapping.fields.items():
            if (raw.get("deleted") or raw.get("deleted_at") or raw.get("excluded")) and source != "record_status":
                continue
            if target not in schema:
                raise FeishuError("FIELD_MISSING")
            definition, value = schema[target], values.get(source)
            if definition["type"] in {18, 21}:
                if value:
                    linked = linked_records.get(source)
                    if not linked or any(record_id is None for record_id in linked):
                        raise FeishuError("DEPENDENCY_PENDING", retryable=True)
                    fields[definition["field_name"]] = linked
                else:
                    fields[definition["field_name"]] = []
            else:
                fields[definition["field_name"]] = encode_scalar(value, definition["type"])
        return fields

    async def notify(self, connection, event, config, values, raw, client, *, record_id=None):
        await self.repository.guard(connection, event, config.revision)
        existing = await self.repository.deliveries(connection, event["id"])
        if values["record_status"] != "有效" or raw.get("excluded") or raw.get("deleted"):
            for delivery in existing:
                if delivery["status"] == "pending":
                    await self.repository.delivery_state(connection, delivery["dedupe_key"], "failed",
                                                         error="SOURCE_NO_LONGER_ELIGIBLE")
            return  # Keep sent receipts and unknown results; never send frozen ineligible content.
        if not existing and not event["notification_planned"]:
            department = raw.get("owner_team_id") or raw.get("recorder_team_id") or raw.get("creator_team_id")
            source = SourceEvent(event["id"], event["workspace_id"], event["object_kind"], event["object_id"],
                event["first_formal_create"] and values["record_status"] == "有效", event["historical"], True,
                (UUID(department),) if department else ())
            card = notification_card(event["object_kind"], values, config, record_id)
            plans = notification_plans(config, source)
            # A disabled notification configuration must not consume the
            # one-time planning marker.  Otherwise a business record created
            # while notifications are paused can never notify after the
            # configuration is enabled; it would look synced but silently
            # lose its first-create card.
            if plans:
                async with connection.transaction():
                    for plan in plans:
                        await self.repository.freeze_delivery(connection, plan, event["workspace_id"], card)
                    await self.repository.mark_planned(connection, event)
                existing = await self.repository.deliveries(connection, event["id"])
        for delivery in existing:
            if delivery["status"] in {"sent", "failed"}:
                continue
            if delivery["status"] == "unknown":
                raise FeishuError("MESSAGE_RESULT_UNKNOWN")
            # Crash after send starts must never blindly resend after the provider's 1-hour dedupe window.
            await self.repository.guard(connection, event, config.revision)
            await self.repository.delivery_state(connection, delivery["dedupe_key"], "unknown")
            try:
                message_id = await client.send_card(delivery["chat_id"], json_value(delivery["payload"]),
                                                    delivery["dedupe_key"][:50])
            except FeishuError as exc:
                if not exc.unknown:
                    await self.repository.delivery_state(connection, delivery["dedupe_key"], "pending", error=exc.code)
                raise
            await self.repository.delivery_state(connection, delivery["dedupe_key"], "sent", message_id=message_id)
