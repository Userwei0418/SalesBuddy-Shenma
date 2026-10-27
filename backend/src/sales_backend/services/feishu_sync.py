"""Leased outbox consumer. Network calls are outside business transactions."""
import json
from contextlib import asynccontextmanager
from hashlib import sha256
from time import monotonic
from uuid import UUID

from sales_backend.db import json_value
from sales_backend import knowledge_availability
from sales_backend.domain.feishu_sync.cards import (
    knowledge_publication_card,
    notification_card,
    opportunity_update_card,
)
from sales_backend.domain.feishu_sync.config import COMMON_FIELDS, SOURCE_FIELDS, SyncConfig
from sales_backend.domain.feishu_sync.planning import SourceEvent, notification_plans
from sales_backend.domain.feishu_sync.projection import RELATIONS, encode_scalar, project
from sales_backend.integrations.feishu import FeishuClient, FeishuError
from sales_backend.repositories.feishu_sync import FeishuRepository
from sales_backend.security.runtime_credentials import CredentialCipher

TASK_MEASURES = frozenset({'active_count', 'completed_count', 'pending_review_count', 'ontime_completed_count'})
TASK_NOTICE_FIELDS = (
    'title', 'description', 'status', 'due_at', 'version_no', 'owner_id', 'owner_name',
    'creator_user_ref_id', 'original_dispatcher_name', 'batch_id', 'batch_name', 'source_code', 'target_position',
)


def task_notification_snapshot(event, raw):
    """Capture only current weekly import tasks; never infer recipients from its creator.

    The scoped SQL projection resolves active recipients and their departments.
    Re-reading this snapshot immediately before each send prevents a delayed or
    retried first-create card from announcing a cancelled, completed or moved task.
    """
    if (raw.get('excluded') or raw.get('deleted') or raw.get('deleted_at')
            or raw.get('data_kind') not in (None, 'production')
            or raw.get('status') not in {'pending_confirm', 'pending_execution'}
            or raw.get('source_code') != 'meeting_import'):
        return None
    try:
        if UUID(str(raw['id'])) != event['object_id']:
            return None
        # SQL exposes this identifier only when the linked import batch has
        # been confirmed. A direct/member task cannot borrow the notification.
        UUID(str(raw['batch_id']))
        version = raw['version_no']
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            return None
        recipients, names = raw['notification_recipient_ids'], raw['notification_recipient_names']
        departments = raw['notification_department_ids']
        if (not isinstance(recipients, list) or not recipients or not isinstance(names, list)
                or len(recipients) != len(names) or any(not isinstance(name, str) or not name for name in names)
                or not isinstance(departments, list)):
            return None
        pairs = sorted((str(UUID(str(person))), name) for person, name in zip(recipients, names, strict=True))
        if len({person for person, _ in pairs}) != len(pairs):
            return None
        owner = str(UUID(str(raw['owner_id']))) if raw.get('owner_id') else None
        if owner and [person for person, _ in pairs] != [owner]:
            return None
        if not owner and (raw['status'] != 'pending_confirm' or not raw.get('target_position')):
            return None
        snapshot = {key: raw.get(key) for key in TASK_NOTICE_FIELDS}
        snapshot.update(workspace_id=str(event['workspace_id']), task_id=str(event['object_id']),
                        notification_recipient_ids=[person for person, _ in pairs],
                        notification_recipient_names=[name for _, name in pairs],
                        notification_department_ids=sorted({str(UUID(str(value))) for value in departments}))
        return snapshot
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def update_snapshot(event):
    """Only a captured human change can qualify as an update notification.

    Legacy queue rows and reconciliation have no snapshot. Never reconstruct a
    difference from the current business row: it may already be several edits
    ahead by the time this event is consumed.
    """
    value = event.get("notification_snapshot")
    if value is None:
        return None
    snapshot = json_value(value)
    try:
        if (not isinstance(snapshot, dict) or snapshot.get("schema_version") != 1
                or event["object_kind"] != "opportunity" or event["first_formal_create"]):
            raise ValueError
        UUID(snapshot["change_id"])
        for key, event_key in (("workspace_id", "workspace_id"), ("opportunity_id", "object_id")):
            if str(snapshot[key]) != str(event[event_key]):
                raise ValueError
        changes = snapshot["changes"]
        if not isinstance(changes, list):
            raise ValueError
        if any(not isinstance(change, dict) or not isinstance(change.get("label"), str)
               or "before" not in change or "after" not in change for change in changes):
            raise ValueError
    except (KeyError, TypeError, ValueError, AttributeError):
        raise FeishuError("INVALID_UPDATE_SNAPSHOT") from None
    # Even a malformed/no-op audit must not create an empty update card.
    changes = [change for change in changes if change["before"] != change["after"]]
    return {**snapshot, "changes": changes} if changes else None


def knowledge_notification_snapshot(event, raw):
    """A current, eligible publication is required even for a frozen retry."""
    value = event.get("notification_snapshot")
    if value is None:
        return None
    snapshot = json_value(value)
    try:
        if (snapshot["schema_version"] != 1 or snapshot["event_kind"] not in {"published", "version_published"}
                or bool(event["first_formal_create"]) != (snapshot["event_kind"] == "published")):
            raise ValueError
        for field, event_field in (("change_id", "id"), ("publication_id", "id"),
                ("workspace_id", "workspace_id"), ("material_id", "object_id")):
            if UUID(str(snapshot[field])) != event[event_field]:
                raise ValueError
        UUID(str(snapshot["version_id"]))
        if raw.get("excluded") or raw.get("deleted"):
            return None
        # Metadata revisions supersede the public link, but retain the original
        # formal notice. SQL derives this identity from the immutable publish
        # audit and cuts it at a withdrawal, missing audit or another version.
        for field, current_field in (
            ("publication_id", "notification_publication_id"),
            ("publication_revision", "notification_publication_revision"),
            ("version_id", "version_id"),
        ):
            if str(raw.get(current_field)) != str(snapshot[field]):
                return None
    except (KeyError, TypeError, ValueError, AttributeError):
        raise FeishuError("INVALID_KNOWLEDGE_SNAPSHOT") from None
    return snapshot


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
            for kind, mapping in config.mappings.items():
                if kind == "knowledge" and not knowledge_availability.KNOWLEDGE_AVAILABLE:
                    continue
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
                await client.find_record(config.base_token, mapping.table_id,
                    fields[mapping.id_field_id]["field_name"],
                    f"__salesbuddy_validation__:{config.connection_id}")
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
        if event["object_kind"] == "knowledge":
            from sales_backend.knowledge_availability import require_knowledge_available
            require_knowledge_available()
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
            if kind == "knowledge" and config.knowledge_web_url and not raw.get("excluded"):
                base = config.knowledge_web_url + "#/pages/knowledge/index?material_id=" + str(event["object_id"])
                raw = {**raw, "internal_url": base, "share_entry_url": base + "&distribution=1"}
            values = project(kind, raw, SOURCE_FIELDS[kind] | COMMON_FIELDS | set(mapping.fields))
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
                # Task tombstones now clear aggregate measures. Invalidate the
                # old acknowledged fingerprint so a retry repairs legacy rows.
                "version": 2 if kind == "task" else 1, "app": config.app_id, "base": config.base_token,
                "mapping": mapping.model_dump(mode="json"),
                "values": {key: values.get(key)
                           for key in {"system_id", "record_status", *mapping.fields} - {"synced_at"}},
                "links": linked_records,
            }, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            mapped = await self.repository.mapped(connection, config.connection_id, kind, event["object_id"])
            same_target = mapped and mapped["table_id"] == mapping.table_id
            force = event.get("force_remote_check", False) or event.get("attempts", 1) > 1
            if same_target and not force and mapped.get("source_fingerprint") == fingerprint:
                await self.repository.guard(connection, event, config.revision)
                # This outcome only accelerates silent, already acknowledged
                # reconciliation. A business/notification event keeps its normal
                # delivery path even when its source fingerprint is unchanged.
                silent_reconcile = (
                    event.get("queue_origin") == "reconcile" and event.get("historical") is True
                    and event.get("first_formal_create") is False
                    and event.get("notification_planned") is False
                    and event.get("notification_snapshot") is None
                )
                await self.notify(connection, event, config, values, raw, client, record_id=mapped["record_id"])
                no_deliveries = silent_reconcile and not await self.repository.deliveries(connection, event["id"])
                return "source_unchanged" if no_deliveries else None
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
                fields = await self.project_fields(
                    connection, event, config, mapping, values, raw, schema, linked_records)
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
            archived = bool(raw.get("deleted") or raw.get("deleted_at") or raw.get("excluded"))
            clear_measure = archived and event['object_kind'] == 'task' and source in TASK_MEASURES
            if archived and source != "record_status" and not clear_measure and event["object_kind"] != "knowledge":
                continue
            if target not in schema:
                raise FeishuError("FIELD_MISSING")
            definition, value = schema[target], 0 if clear_measure else values.get(source)
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
        task_source = task_notification_snapshot(event, raw) if event['object_kind'] == 'task' else None
        knowledge_source = knowledge_notification_snapshot(event, raw) if event['object_kind'] == 'knowledge' else None
        if (values["record_status"] != "有效" or raw.get("excluded") or raw.get("deleted")
                or (event['object_kind'] == 'task' and task_source is None)
                or (event['object_kind'] == 'knowledge' and knowledge_source is None)):
            for delivery in existing:
                if delivery["status"] == "pending":
                    await self.repository.delivery_state(connection, delivery["dedupe_key"], "failed",
                                                         error="SOURCE_NO_LONGER_ELIGIBLE")
            return  # Keep sent receipts and unknown results; never send frozen ineligible content.
        snapshot = update_snapshot(event) if event["object_kind"] == "opportunity" else None
        change_id = UUID((knowledge_source or snapshot)["change_id"]) if (knowledge_source or snapshot) else None
        allowed = (event["object_kind"] in config.notification.on_create if event["first_formal_create"]
                   else bool(change_id) and event["object_kind"] in config.notification.on_update)
        if not config.notification.enabled or not allowed or event["historical"]:
            for delivery in existing:
                if delivery["status"] == "pending":
                    await self.repository.delivery_state(connection, delivery["dedupe_key"], "failed",
                                                         error="NOTIFICATION_DISABLED")
            return  # Revocation also applies to frozen pending cards, without resending unknown results.
        if not existing and not event["notification_planned"]:
            department = (snapshot.get("department_id") if snapshot else
                          raw.get("owner_team_id") or raw.get("recorder_team_id") or raw.get("creator_team_id"))
            departments = (tuple(UUID(value) for value in task_source['notification_department_ids'])
                          if task_source else (UUID(str(department)),) if department else ())
            source = SourceEvent(event["id"], event["workspace_id"], event["object_kind"], event["object_id"],
                event["first_formal_create"] and values["record_status"] == "有效", event["historical"], True,
                departments, change_id=change_id)
            card_values = ({**values, 'notification_recipient_names': task_source['notification_recipient_names']}
                           if task_source else values)
            card = (knowledge_publication_card(knowledge_source, config, record_id) if knowledge_source else
                    opportunity_update_card(snapshot, config, record_id) if snapshot else
                    notification_card(event["object_kind"], card_values, config, record_id))
            frozen = ({'schema_version': 1, 'kind': 'task_create', 'source': task_source, 'card': card}
                      if task_source else {'schema_version': 1, 'kind': 'knowledge_publication',
                          'source': knowledge_source, 'card': card} if knowledge_source else card)
            plans = notification_plans(config, source)
            # A disabled notification configuration must not consume the
            # one-time planning marker.  Otherwise a business record created
            # while notifications are paused can never notify after the
            # configuration is enabled; it would look synced but silently
            # lose its first-create card.
            if plans:
                async with connection.transaction():
                    await self.repository.lock_lease(connection, event)
                    for plan in plans:
                        await self.repository.freeze_delivery(connection, plan, event["workspace_id"], frozen)
                    await self.repository.mark_planned(connection, event)
                existing = await self.repository.deliveries(connection, event["id"])
        for delivery in existing:
            if delivery["status"] in {"sent", "failed"}:
                if delivery["status"] == "sent" and event["object_kind"] == "weekly_report" and record_id:
                    await self.refresh_weekly_receipt(connection, event, config, client, record_id)
                continue
            if delivery["status"] == "unknown":
                raise FeishuError("MESSAGE_RESULT_UNKNOWN")
            # Crash after send starts must never blindly resend after the provider's 1-hour dedupe window.
            await self.repository.guard(connection, event, config.revision)
            frozen = json_value(delivery['payload'])
            if event['object_kind'] == 'task':
                current = await self.repository.source(connection, event)
                current_snapshot = task_notification_snapshot(event, current)
                if (not isinstance(frozen, dict) or frozen.get('schema_version') != 1
                        or frozen.get('kind') != 'task_create' or current_snapshot is None
                        or current_snapshot != frozen.get('source') or not isinstance(frozen.get('card'), dict)):
                    await self.repository.delivery_state(connection, delivery['dedupe_key'], 'failed',
                                                         error='SOURCE_CHANGED_BEFORE_NOTIFICATION')
                    continue
                frozen = frozen['card']
                await self.repository.guard(connection, event, config.revision)
            if event['object_kind'] == 'knowledge':
                current = await self.repository.source(connection, event)
                current_snapshot = knowledge_notification_snapshot(event, current)
                if (current_snapshot is None or not isinstance(frozen, dict)
                        or frozen.get('kind') != 'knowledge_publication'
                        or frozen.get('source') != current_snapshot or not isinstance(frozen.get('card'), dict)):
                    await self.repository.delivery_state(connection, delivery['dedupe_key'], 'failed',
                                                         error='SOURCE_CHANGED_BEFORE_NOTIFICATION')
                    continue
                frozen = frozen['card']
                await self.repository.guard(connection, event, config.revision)
            await self.repository.delivery_state(connection, delivery["dedupe_key"], "unknown")
            try:
                message_id = await client.send_card(delivery["chat_id"], frozen,
                                                    delivery["dedupe_key"][:50])
            except FeishuError as exc:
                if not exc.unknown:
                    await self.repository.delivery_state(connection, delivery["dedupe_key"], "pending", error=exc.code)
                raise
            await self.repository.delivery_state(connection, delivery["dedupe_key"], "sent", message_id=message_id)
            if event["object_kind"] == "weekly_report" and record_id:
                await self.refresh_weekly_receipt(connection, event, config, client, record_id)

    async def refresh_weekly_receipt(self, connection, event, config, client, record_id):
        """Refresh delivery receipt fields after a sent card, including retries."""
        mapping = config.mappings.get("weekly_report")
        if not mapping or not mapping.enabled:
            return
        refreshed = await self.repository.source(connection, event)
        refreshed_values = project("weekly_report", refreshed,
                                  SOURCE_FIELDS["weekly_report"] | COMMON_FIELDS)
        schema = await client.fields(config.base_token, mapping.table_id)
        refreshed_fields = await self.project_fields(connection, event, config, mapping,
                                                      refreshed_values, refreshed, schema, {})
        await self.repository.guard(connection, event, config.revision)
        await client.update_record(config.base_token, mapping.table_id, record_id, refreshed_fields)
        fingerprint = sha256(json.dumps({
            "version": 1, "app": config.app_id, "base": config.base_token,
            "mapping": mapping.model_dump(mode="json"),
            "values": {key: refreshed_values.get(key) for key in {"system_id", "record_status", *mapping.fields} - {"synced_at"}},
            "links": {},
        }, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        digest = sha256(json.dumps(refreshed_fields, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        await self.repository.remember(connection, event, mapping.table_id, record_id, digest, fingerprint)
