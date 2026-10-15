"""Durable, owner-scoped upload intentions. No filename/hash lookup across users."""

from sales_backend.repositories.authorization_checks import require_permission


class UploadIntentConflict(ValueError):
    pass


class UploadStatusUnavailable(RuntimeError):
    pass


SELECT = """SELECT i.id::text,i.request_key::text,i.current_attempt_id::text,i.state,
 i.filename,i.file_size,i.content_sha256,i.registered_import_id::text,
 v.status AS import_status
 FROM activity.visit_upload_intent i LEFT JOIN activity.visit_import v
 ON v.id=i.registered_import_id AND v.workspace_id=i.workspace_id
 AND v.created_by_user_ref_id=i.actor_id
 WHERE i.workspace_id=common.current_workspace_id() AND i.actor_id=common.current_user_ref_id()
 AND i.request_key=$1::uuid"""


def receipt(row, *, replayed=True):
    result = {"upload_intent_id": row["request_key"], "state": row["state"], "filename": row["filename"]}
    if row["state"] == "registered":
        if not row["registered_import_id"] or row["import_status"] not in {"queued", "processing", "succeeded", "failed"}:
            raise UploadStatusUnavailable("上传登记状态暂不可核实，请保留文件稍后查回")
        return {**result, "id": row["registered_import_id"], "status": row["import_status"], "replayed": replayed}
    return {**result, "id": None, "status": None, "retryable": row["state"] == "retryable_failure",
            "retry_after_seconds": 2}


async def lookup(connection, key, *, lock=False):
    await require_permission(connection, "visit.upload")
    row = await connection.fetchrow(SELECT + (" FOR UPDATE OF i" if lock else ""), str(key))
    return dict(row) if row else None


async def claim(connection, actor, key, upload, location):
    await require_permission(connection, "visit.upload")
    created = await connection.fetchval(
        """INSERT INTO activity.visit_upload_intent
        (workspace_id,actor_id,request_key,filename,file_size,content_sha256,current_attempt_id)
        VALUES($1::uuid,$2::uuid,$3::uuid,$4,$5,$6,$7::uuid)
        ON CONFLICT(workspace_id,actor_id,request_key) DO NOTHING RETURNING id::text""",
        actor.workspace_id, actor.user_id, str(key), upload.filename, upload.size, location["sha256"], upload.import_id,
    )
    current = await lookup(connection, key, lock=True)
    if current is None:
        raise UploadStatusUnavailable("无法核实上传意图，请保留文件稍后查回")
    if (current["filename"], current["file_size"], current["content_sha256"]) != (
        upload.filename, upload.size, location["sha256"],
    ):
        raise UploadIntentConflict("该上传标识已用于不同文件，请恢复原文件或新建录入")
    if not created and current["state"] != "retryable_failure":
        return False, receipt(current)
    await connection.execute(
        """INSERT INTO activity.visit_upload_attempt
        (id,intent_id,workspace_id,actor_id,quota_receipt,staging_path,storage_driver,storage_profile,storage_key)
        VALUES($1::uuid,$2::uuid,$3::uuid,$4::uuid,$5::uuid,$6,$7,$8,$9)""",
        upload.import_id, current["id"], actor.workspace_id, actor.user_id, upload.quota_receipt,
        str(upload.path), location["driver"], location["profile"], location["key"],
    )
    if not created:
        await connection.execute(
            "UPDATE activity.visit_upload_intent SET current_attempt_id=$2::uuid,state='processing' "
            "WHERE id=$1::uuid", current["id"], upload.import_id,
        )
    return True, None


async def lock_registration(connection, key, upload):
    # Match the existing storage reconciliation/trigger lock order. Never hold
    # an intent lock and then wait for a quota lock held by the maintenance path.
    await connection.execute("SELECT security.lock_visit_upload_quota()")
    current = await lookup(connection, key, lock=True)
    if current is None or current["current_attempt_id"] != upload.import_id:
        raise UploadStatusUnavailable("本次上传已转入恢复，请先查回原意图")
    if current["state"] == "registered":
        return receipt(current)
    if current["state"] not in {"processing", "uncertain"}:
        raise UploadStatusUnavailable("上传尝试已结束，请按原意图重新查询")
    return None


async def complete(connection, key, upload):
    await connection.execute(
        "UPDATE activity.visit_upload_intent SET state='registered',registered_import_id=$2::uuid "
        "WHERE request_key=$1::uuid AND workspace_id=common.current_workspace_id() "
        "AND actor_id=common.current_user_ref_id() AND current_attempt_id=$2::uuid", str(key), upload.import_id,
    )
    await connection.execute("UPDATE activity.visit_upload_attempt SET state='registered',finished_at=clock_timestamp() "
                             "WHERE id=$1::uuid", upload.import_id)


async def finish_failure(connection, key, upload, *, cleanup_complete):
    current = await lookup(connection, key, lock=True)
    if not current or current["current_attempt_id"] != upload.import_id or current["state"] in {"registered", "retryable_failure"}:
        return
    state = "retryable_failure" if cleanup_complete else "uncertain"
    await connection.execute(
        "UPDATE activity.visit_upload_attempt SET state=$2,finished_at=clock_timestamp() WHERE id=$1::uuid",
        upload.import_id, state,
    )
    await connection.execute("UPDATE activity.visit_upload_intent SET state=$2 WHERE id=$1::uuid", current["id"], state)


async def reconcile_attempt(connection, allocation, *, registered):
    """Called with quota/allocation locks and the freshly checked original actor.

    Historical allocations have no intent. V162-only maintenance stays usable;
    the existence check never fabricates an intent for a historical upload.
    """
    if not await connection.fetchval("SELECT to_regclass('activity.visit_upload_attempt') IS NOT NULL"):
        return
    key = await connection.fetchval(
        """SELECT i.request_key::text FROM activity.visit_upload_attempt a
        JOIN activity.visit_upload_intent i ON i.id=a.intent_id
        WHERE a.id=$1::uuid AND a.workspace_id=$2::uuid AND a.actor_id=$3::uuid""",
        str(allocation["import_id"]), str(allocation["workspace_id"]), str(allocation["actor_id"]),
    )
    if key is None:
        return
    from types import SimpleNamespace

    upload = SimpleNamespace(import_id=str(allocation["import_id"]))
    current = await lookup(connection, key, lock=True)
    if not current or current["current_attempt_id"] != upload.import_id:
        # A completed prior attempt cannot register after a new attempt started.
        if registered:
            raise UploadStatusUnavailable("旧上传尝试已结束，禁止恢复为另一份材料")
        return
    if current["state"] == "registered":
        receipt(current)
        return
    if registered:
        if current["state"] not in {"processing", "uncertain"}:
            raise UploadStatusUnavailable("上传清理已完成，禁止恢复旧尝试")
        await complete(connection, key, upload)
    else:
        await finish_failure(connection, key, upload, cleanup_complete=True)
