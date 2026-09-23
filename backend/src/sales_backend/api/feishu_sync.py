"""Company-scoped operations console endpoints. Saving never enables external writes."""
from asyncpg import InsufficientPrivilegeError, InvalidParameterValueError
from fastapi import APIRouter, Depends, HTTPException

from sales_backend.api.dependencies import RequestIdentity, get_database, get_settings
from sales_backend.api.management_dependencies import get_management_identity
from sales_backend.config import Settings
from sales_backend.contracts.feishu_sync import FeishuAction, FeishuRecovery, SaveFeishuConfig
from sales_backend.db import Database, json_value
from sales_backend.repositories.feishu_sync import FeishuRepository
from sales_backend.security.runtime_credentials import CredentialCipher, RuntimeCredentialUnavailable

router = APIRouter(prefix="/api/v1/console/feishu-sync", tags=["Feishu synchronization"])
repository = FeishuRepository()


@router.get("")
async def read(identity: RequestIdentity = Depends(get_management_identity),
               database: Database = Depends(get_database)):
    async with database.transaction(identity.actor, readonly=True) as connection:
        row = await repository.config(connection, identity.actor.workspace_id)
        if not row:
            return {"configured": False, "enabled": False, "workspace_id": identity.actor.workspace_id}
        return {"configured": True, "enabled": row["enabled"], "config": json_value(row["settings"]),
                "has_credential": row["has_credential"], "validated_revision": row["validated_revision"],
                "validation_requested": row["validation_requested"], "last_error_code": row["last_error_code"]}


@router.put("")
async def save(body: SaveFeishuConfig, identity: RequestIdentity = Depends(get_management_identity),
               database: Database = Depends(get_database), settings: Settings = Depends(get_settings)):
    try:
        encrypted = None
        if body.app_secret is not None:
            secret = body.app_secret.get_secret_value()
            if not 1 <= len(secret) <= 4096:
                raise ValueError("应用凭证长度无效")
            cipher = CredentialCipher.from_file(settings.config_credential_keyring_file,
                                                settings.config_credential_key_id)
            encrypted = cipher.encrypt(identity.actor.workspace_id, secret)
        async with database.transaction(identity.actor) as connection:
            await repository.save(connection, identity.actor, body.config, body.expected_revision,
                                  migrate_target=body.migrate_target, new_credential=encrypted is not None)
            if encrypted:
                await repository.set_credential(connection, str(body.config.connection_id), encrypted)
    except FileExistsError as exc:
        raise HTTPException(409, str(exc)) from exc
    except InvalidParameterValueError as exc:
        raise HTTPException(422, "请先暂停同步，等待在途任务结束并处理结果未知的通知后再迁移") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RuntimeCredentialUnavailable as exc:
        raise HTTPException(503, "服务端安全凭证存储尚未配置") from exc
    return {"saved": True, "revision": body.config.revision, "enabled": False}


@router.post("/recover")
async def recover(body: FeishuRecovery, identity: RequestIdentity = Depends(get_management_identity),
                  database: Database = Depends(get_database)):
    try:
        async with database.transaction(identity.actor) as connection:
            row = await repository.config(connection, identity.actor.workspace_id, lock=True)
            if not row or row["revision"] != body.expected_revision:
                raise HTTPException(409, "配置版本已变化，请刷新")
            if row["enabled"]:
                raise HTTPException(422, "请先暂停同步并等待在途任务结束")
            await repository.recover(connection, row["id"], body)
    except InvalidParameterValueError as exc:
        raise HTTPException(422, "当前状态无法处理：请等待在途任务结束、核对未知通知并填写处理说明") from exc
    except InsufficientPrivilegeError as exc:
        raise HTTPException(404, "未找到可处理的同步事件") from exc
    return {"accepted": True}


@router.post("/{action}")
async def act(action: str, body: FeishuAction, identity: RequestIdentity = Depends(get_management_identity),
              database: Database = Depends(get_database)):
    if action not in {"validate", "enable", "pause", "initialize"}:
        raise HTTPException(404, "未知操作")
    async with database.transaction(identity.actor) as connection:
        row = await repository.config(connection, identity.actor.workspace_id, lock=True)
        if not row or row["revision"] != body.expected_revision:
            raise HTTPException(409, "配置版本已变化，请刷新")
        if action != "pause" and json_value(row["settings"]).get("direction", "system_to_base") != "system_to_base":
            raise HTTPException(422, "双向同步仅为配置预留，尚不支持校验或启用；请切换为单向同步并保存")
        if action != "pause" and not row["has_credential"]:
            raise HTTPException(422, "请先安全配置应用凭证")
        if action == "enable" and row["validated_revision"] != row["revision"]:
            raise HTTPException(422, "当前配置尚未通过连接和字段校验")
        if action == "initialize":
            await repository.initialize(connection, row["id"])
        else:
            await repository.action(connection, row["id"], action)
    return {"accepted": True, "action": action}


@router.get("/catalog")
async def catalog(identity: RequestIdentity = Depends(get_management_identity)):
    from sales_backend.domain.feishu_sync.config import COMMON_FIELDS, SOURCE_FIELDS
    labels = {"customer": "客户", "opportunity": "商机", "visit": "跟进记录", "partner": "合作伙伴",
              "task": "任务／待办", "demo_scene": "Demo／场景成果", "actual": "确收／回款实绩",
              "forecast": "商机季度预测（原始录入）", "period_actual_snapshot": "季度历史实绩（原始汇总）",
              "target": "经营目标", "member": "成员／部门", "contact": "客户联系人"}
    return {"objects": [{"key": kind, "label": labels[kind],
                         "fields": sorted((fields | COMMON_FIELDS) - {"system_id"})}
                        for kind, fields in SOURCE_FIELDS.items()]}


@router.get("/status")
async def sync_status(identity: RequestIdentity = Depends(get_management_identity),
                      database: Database = Depends(get_database)):
    async with database.transaction(identity.actor, readonly=True) as connection:
        return await repository.status(connection, identity.actor.workspace_id)
