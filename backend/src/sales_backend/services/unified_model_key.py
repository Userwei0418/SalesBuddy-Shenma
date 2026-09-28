"""Validate once, rotate the Agent host, then atomically activate locally.

The private journal supports compensation after a disconnect or process restart.
Secrets go to the fixed maintenance command via stdin, never argv or logs.
"""

import asyncio
import fcntl
import json
import os
import tempfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sales_backend.security.unified_model_key import credential, is_key_operator, read_state, validate_unified_endpoint
from sales_backend.services.model_api import ModelApiError, ModelApiService, legacy_snapshot, safe_test_error


def write_private_json(path, value):
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(prefix=".model-key-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class UnifiedModelKeyService:
    def __init__(self, settings, *, remote=None, probe=None):
        self.settings = settings
        self.remote = remote or self._remote
        self.probe = probe or self._probe

    @property
    def path(self):
        if not self.settings.unified_model_key_file:
            raise ModelApiError("统一模型密钥尚未由运维启用", 503)
        return Path(self.settings.unified_model_key_file)

    @property
    def journal(self):
        return self.path.with_suffix(".pending.json")

    def status(self, actor):
        state = read_state(self.settings) if self.settings.unified_model_key_file else None
        pending = bool(state and self.journal.exists())
        return {"managed": bool(self.settings.unified_model_key_file),
                "can_manage": bool(self.settings.unified_model_key_file) and is_key_operator(self.settings, actor.user_id),
                "configured": bool(state), "key_hint": "已配置 · 尾号 " + state["api_key_tail"] if state else "未配置",
                "revision": state["revision"] if state else 0,
                "updated_at": state.get("updated_at") if state else None,
                "rotation_status": "pending" if pending else "ready",
                "message": "上次更新未完成，请运维重新提交以恢复同步" if pending else "文字、语音和 Agent 共用运维统一密钥"}

    async def _remote(self, request):
        try:
            command = json.loads(self.settings.model_key_rotation_command_json)
            if not isinstance(command, list) or not command or not all(isinstance(arg, str) for arg in command):
                raise ValueError()
            process = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.PIPE,
                                                          stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                out, _ = await asyncio.wait_for(process.communicate(json.dumps(request).encode()), timeout=90)
            except BaseException:
                process.kill()
                await process.wait()
                raise
            result = json.loads(out)
            if process.returncode or result.get("status") not in {"prepared", "applied", "rolled_back", "unchanged"}:
                raise ValueError()
            return result
        except (OSError, ValueError, TimeoutError):
            raise ModelApiError("中台密钥同步未完成，当前配置已保留，请稍后重试", 503) from None

    async def _probe(self, key):
        validate_unified_endpoint(replace(self.settings, model_api_endpoint=""))
        candidate = replace(self.settings, senseaudio_api_key=key, unified_model_key_file="", max_retries=0,
                            model_api_endpoint="", model_api_metadata={})
        manager = ModelApiService(None, candidate)
        for purpose in ("text", "asr", "tts"):
            try:
                async with asyncio.timeout(45):
                    config = legacy_snapshot(candidate, purpose)
                    if purpose == "tts":
                        config["voice_id"] = "female_0033_b"
                    await manager._probe(purpose, candidate, config)
            except Exception as exc:
                raise ModelApiError({"text": "文字", "asr": "语音识别", "tts": "语音合成"}[purpose]
                                    + "验证失败：" + safe_test_error(exc), 422) from None

    async def _recover(self):
        if not self.journal.exists():
            return
        from sales_backend.security.runtime_credentials import read_private_file
        pending = json.loads(read_private_file(str(self.journal)))
        current = read_state(self.settings)
        if current.get("rotation_id") != pending["rotation_id"]:
            await self.remote({"action": "rollback", "rotation_id": pending["rotation_id"]})
        self.journal.unlink()

    async def rotate(self, actor, key, expected_revision):
        if not is_key_operator(self.settings, actor.user_id):
            raise ModelApiError("统一模型密钥仅允许运维维护账号更换", 403)
        descriptor = os.open(str(self.path) + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ModelApiError("其他运维正在更新密钥，请稍后刷新", 409) from None
            await self._recover()
            current = read_state(self.settings)
            if current["revision"] != expected_revision:
                raise ModelApiError("密钥配置已更新，请刷新后重试", 409)
            await self.probe(key)
            rotation_id = str(uuid4())
            next_state = {**credential(self.settings, key), "revision": current["revision"] + 1,
                          "rotation_id": rotation_id, "updated_by": str(actor.user_id),
                          "updated_at": datetime.now(timezone.utc).isoformat()}
            # Record intent before any remote change. A lost response remains recoverable.
            write_private_json(self.journal, {"rotation_id": rotation_id, "next_state": next_state})
            try:
                await self.remote({"action": "prepare", "rotation_id": rotation_id})
                await self.remote({"action": "apply", "rotation_id": rotation_id, "api_key": key})
                write_private_json(self.path, next_state)
            except BaseException:
                try:
                    # os.replace may have succeeded even if the following fsync
                    # failed. Never roll the Agent back after local activation.
                    if read_state(self.settings).get("rotation_id") != rotation_id:
                        await self.remote({"action": "rollback", "rotation_id": rotation_id})
                        self.journal.unlink()
                except Exception:
                    pass  # Keep the private journal; the next attempt must recover first.
                raise
            self.journal.unlink()
            return self.status(actor)
        finally:
            os.close(descriptor)
