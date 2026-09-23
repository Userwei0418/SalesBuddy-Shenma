"""Durable connection and leased queue; business projection is a separate adapter."""
from uuid import uuid4

from sales_backend.db import json_value


class FeishuRepository:
    async def config(self, connection, workspace_id, *, lock=False):
        row = await connection.fetchrow(
            "SELECT *,security.has_feishu_credential(id) AS has_credential FROM config.feishu_connection "  # noqa: S608
            "WHERE workspace_id=$1::uuid" + (" FOR UPDATE" if lock else ""), str(workspace_id))  # noqa: S608
        return dict(row) if row else None

    async def save(self, connection, actor, config, expected_revision, *, migrate_target=False, new_credential=False):
        # Serializes first insert too, before a connection row exists.
        await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))",
                                 f"feishu-config:{actor.workspace_id}")
        old = await self.config(connection, actor.workspace_id, lock=True)
        if (old["revision"] if old else 0) != expected_revision:
            raise FileExistsError("配置版本已变化，请刷新")
        if str(config.workspace_id) != actor.workspace_id or config.revision != expected_revision + 1:
            raise ValueError("公司或配置版本不匹配")
        if config.enabled:
            raise ValueError("配置先保存并验证，再单独启用")
        if old:
            prior = json_value(old["settings"])
            if str(old["id"]) != str(config.connection_id):
                raise ValueError("不能替换已有连接身份")
            target_changed = prior.get("base_token") != config.base_token or prior.get("app_id") != config.app_id
            for key, before in prior.get("mappings", {}).items():
                after = config.mappings.get(key)
                target_changed |= after is None or (
                    before["table_id"], before["id_field_id"]) != (after.table_id, after.id_field_id)
            if target_changed:
                if not migrate_target:
                    raise ValueError("目标已变化，请暂停同步并勾选确认迁移")
                if old["enabled"]:
                    raise ValueError("迁移前必须先暂停同步")
                if prior.get("app_id") != config.app_id and not new_credential:
                    raise ValueError("更换应用时必须填写新应用凭证")
                await connection.execute("SELECT security.prepare_feishu_migration($1::uuid)",
                                         str(config.connection_id))
        await connection.execute("""INSERT INTO config.feishu_connection
          (id,workspace_id,revision,settings,updated_by) VALUES($1::uuid,$2::uuid,$3,$4::jsonb,$5::uuid)
          ON CONFLICT(workspace_id) DO UPDATE SET revision=excluded.revision,settings=excluded.settings,
          updated_by=excluded.updated_by,updated_at=clock_timestamp(),enabled=false,
          validated_revision=NULL,validation_requested=false,last_error_code=NULL""",
          str(config.connection_id), actor.workspace_id, config.revision, config.model_dump(mode="json"), actor.user_id)

    async def guard(self, connection, event, revision):
        valid = await connection.fetchval("""SELECT EXISTS (
          SELECT 1 FROM ops.feishu_event e JOIN config.feishu_connection c ON c.id=e.connection_id
          WHERE e.id=$1 AND e.lease_token=$2 AND e.status='running'
          AND e.locked_until>clock_timestamp() AND c.enabled AND c.revision=$3
          AND c.validated_revision=c.revision)""", event["id"], event["lease_token"], revision)
        if not valid:
            raise RuntimeError("SYNC_LEASE_OR_CONFIG_CHANGED")

    async def schedule_reconcile(self, connection):
        # Compatibility entry point: periodic full scans are retired. Durable
        # business events and explicit administrator repair are the only sources.
        return None

    async def claim(self, connection, *, prefer_history=False):
        return await connection.fetchrow("""WITH candidate AS (
          SELECT e.id FROM ops.feishu_event e JOIN config.feishu_connection c ON c.id=e.connection_id
          WHERE c.enabled AND c.validated_revision=c.revision
          AND ((e.status IN ('pending','failed') AND e.available_at<=clock_timestamp())
            OR (e.status='running' AND e.locked_until<clock_timestamp()))
          AND NOT EXISTS(SELECT 1 FROM ops.feishu_event prior WHERE prior.connection_id=e.connection_id
            AND prior.object_kind=e.object_kind AND prior.object_id=e.object_id
            AND prior.sequence_no<e.sequence_no AND prior.status NOT IN ('succeeded','dead_letter'))
          ORDER BY CASE WHEN $2::boolean THEN 0
            WHEN e.queue_origin='business' THEN 0
            WHEN EXISTS(SELECT 1 FROM ops.feishu_event live
              WHERE live.connection_id=e.connection_id AND live.object_kind=e.object_kind
                AND live.object_id=e.object_id AND live.queue_origin='business'
                AND live.status NOT IN ('succeeded','dead_letter')) THEN 0
            ELSE 1 END, e.sequence_no FOR UPDATE OF e,c SKIP LOCKED LIMIT 1
        ) UPDATE ops.feishu_event e SET status='running',attempts=attempts+1,
          lease_token=$1::uuid,locked_until=clock_timestamp()+interval '120 seconds'
          FROM candidate WHERE e.id=candidate.id RETURNING e.*""", str(uuid4()), prefer_history)

    async def finish(self, connection, event, *, error=None, retryable=False, retry_after=0):
        state = "succeeded" if error is None else (
            "failed" if retryable and event["attempts"] < 10 else "dead_letter")
        delay = max(retry_after, min(1800, 2 ** min(event["attempts"], 10)))
        status = await connection.execute("""UPDATE ops.feishu_event SET status=$3,error_code=$4,
          lease_token=NULL,locked_until=NULL,available_at=clock_timestamp()+make_interval(secs=>$5),
          completed_at=CASE WHEN $3 IN ('succeeded','dead_letter') THEN clock_timestamp() ELSE NULL END
          WHERE id=$1 AND lease_token=$2 AND status='running' AND locked_until>clock_timestamp()""",
          event["id"], event["lease_token"], state, error, delay)
        if status != "UPDATE 1":
            raise RuntimeError("SYNC_LEASE_LOST")

    async def remember(self, connection, event, table_id, record_id, digest, source_fingerprint=None):
        await connection.execute("""INSERT INTO ops.feishu_record_map
          (connection_id,workspace_id,object_kind,object_id,table_id,record_id,projection_hash,source_fingerprint)
          VALUES($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT(connection_id,object_kind,object_id)
          DO UPDATE SET table_id=excluded.table_id,record_id=excluded.record_id,
          projection_hash=excluded.projection_hash,source_fingerprint=excluded.source_fingerprint,
          updated_at=clock_timestamp()""", event["connection_id"], event["workspace_id"],
          event["object_kind"], event["object_id"], table_id, record_id, digest, source_fingerprint)

    async def freeze_delivery(self, connection, plan, workspace_id, card):
        await connection.execute("""INSERT INTO ops.feishu_delivery
          (dedupe_key,event_id,connection_id,workspace_id,config_revision,chat_id,payload)
          VALUES($1,$2,$3,$4,$5,$6,$7::jsonb) ON CONFLICT(dedupe_key) DO NOTHING""",
          plan.dedupe_key, plan.event_id, plan.connection_id, workspace_id,
          plan.config_revision, plan.chat_id, card)

    async def set_credential(self, connection, connection_id, encrypted):
        await connection.execute("SELECT security.set_feishu_credential($1::uuid,$2,$3)", connection_id,
                                 encrypted["api_key_ciphertext"], encrypted["encryption_key_id"])

    async def action(self, connection, connection_id, action):
        if action == "validate":
            await connection.execute("UPDATE config.feishu_connection SET enabled=false,validation_requested=true,"
                                     "validated_revision=NULL,last_error_code=NULL WHERE id=$1", connection_id)
        else:
            await connection.execute("UPDATE config.feishu_connection SET enabled=$2 WHERE id=$1",
                                     connection_id, action == "enable")

    async def worker_config(self, connection, connection_id=None, *, validation=False):
        if validation:
            return await connection.fetchrow("""SELECT c.*,k.ciphertext,k.encryption_key_id,
              k.updated_at AS credential_updated_at
              FROM config.feishu_connection c JOIN security.feishu_credential k ON k.connection_id=c.id
              WHERE c.validation_requested ORDER BY c.updated_at LIMIT 1""")
        return await connection.fetchrow("""SELECT c.*,k.ciphertext,k.encryption_key_id,
              k.updated_at AS credential_updated_at
          FROM config.feishu_connection c JOIN security.feishu_credential k ON k.connection_id=c.id
          WHERE c.id=$1 AND c.enabled AND c.validated_revision=c.revision""", connection_id)

    async def validation_result(self, connection, row, error):
        await connection.execute("""UPDATE config.feishu_connection SET validation_requested=false,
          validated_revision=CASE WHEN $3::text IS NULL THEN revision ELSE NULL END,last_error_code=$3
          WHERE id=$1 AND revision=$2 AND EXISTS (SELECT 1 FROM security.feishu_credential k
            WHERE k.connection_id=$1 AND k.updated_at=$4)""",
          row["id"], row["revision"], error, row["credential_updated_at"])

    async def source(self, connection, event):
        return json_value(await connection.fetchval("SELECT ops.feishu_source($1,$2,$3)",
                          event["connection_id"], event["object_kind"], event["object_id"]))

    async def reconcile(self, connection, connection_id):
        return await connection.fetchval("SELECT ops.feishu_reconcile($1)", connection_id)

    async def mapped(self, connection, connection_id, kind, object_id):
        return await connection.fetchrow("SELECT * FROM ops.feishu_record_map "
            "WHERE connection_id=$1 AND object_kind=$2 AND object_id=$3", connection_id, kind, object_id)

    async def deliveries(self, connection, event_id):
        return await connection.fetch("SELECT * FROM ops.feishu_delivery WHERE event_id=$1 ORDER BY chat_id", event_id)

    async def mark_planned(self, connection, event):
        status = await connection.execute("UPDATE ops.feishu_event SET notification_planned=true "
            "WHERE id=$1 AND lease_token=$2 AND status='running' AND locked_until>clock_timestamp()",
            event["id"], event["lease_token"])
        if status != "UPDATE 1":
            raise RuntimeError("SYNC_LEASE_LOST")

    async def delivery_state(self, connection, key, status, *, message_id=None, error=None):
        await connection.execute("""UPDATE ops.feishu_delivery SET status=$2,message_id=$3,error_code=$4,
          sent_at=CASE WHEN $2='sent' THEN clock_timestamp() ELSE sent_at END WHERE dedupe_key=$1""",
          key, status, message_id, error)

    async def heartbeat(self, connection, event):
        status = await connection.execute("""UPDATE ops.feishu_event
          SET locked_until=clock_timestamp()+interval '120 seconds'
          WHERE id=$1 AND lease_token=$2 AND status='running' AND locked_until>clock_timestamp()""",
          event["id"], event["lease_token"])
        if status != "UPDATE 1":
            raise RuntimeError("SYNC_LEASE_LOST")

    async def try_lock(self, connection, key):
        return await connection.fetchval("SELECT pg_try_advisory_lock(hashtextextended($1,0))", key)

    async def unlock(self, connection, key):
        await connection.execute("SELECT pg_advisory_unlock(hashtextextended($1,0))", key)

    async def status(self, connection, workspace_id):
        rows = await connection.fetch("SELECT status,count(*) AS count,max(completed_at) AS last_completed "
            "FROM ops.feishu_event WHERE workspace_id=$1::uuid GROUP BY status", workspace_id)
        errors = await connection.fetch("SELECT id,object_kind,error_code,status,created_at FROM ops.feishu_event "
            "WHERE workspace_id=$1::uuid AND status IN ('failed','dead_letter') "
            "ORDER BY created_at DESC LIMIT 20", workspace_id)
        unknown = await connection.fetch("SELECT dedupe_key,event_id,chat_id,created_at FROM ops.feishu_delivery "
            "WHERE workspace_id=$1::uuid AND status='unknown' ORDER BY created_at LIMIT 50", workspace_id)
        return {"counts": [dict(row) for row in rows], "errors": [dict(row) for row in errors],
                "unknown": [dict(row) for row in unknown]}

    async def recover(self, connection, connection_id, body):
        await connection.execute("SELECT security.feishu_recover($1,$2,$3,$4,$5)", connection_id,
                                 body.event_id, body.action, body.delivery_key, body.note)

    async def initialize(self, connection, connection_id):
        return await connection.fetchval("SELECT security.feishu_initialize($1)", connection_id)
