"""Administrator-owned direct model connections, without credentials in snapshots."""

from __future__ import annotations

import ipaddress
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

PURPOSES = {
    "text": {
        "label": "文字模型与业务兜底",
        "protocol": "chat_completions",
        "path": "/v1/chat/completions",
        "impact": "通用文字处理，以及业务智能体未启用中台或中台失败后的原接口调用",
    },
    "asr": {
        "label": "语音转文字",
        "protocol": "audio_transcriptions",
        "path": "/v1/audio/transcriptions",
        "impact": "录音转写、语音输入和拜访音频文件导入",
    },
    "tts": {
        "label": "语音合成（预留）",
        "protocol": "senseaudio_tts",
        "path": "/v1/t2a_v2",
        "impact": "当前没有启用业务调用入口；仅保存和测试语音合成接口",
    },
}
Purpose = Literal["text", "asr", "tts"]


def public_endpoint(value: str) -> str:
    """Structural validation; the transport also resolves and pins public IPs."""
    try:
        u = urlsplit(value)
        if (
            u.scheme != "https"
            or not u.hostname
            or u.username is not None
            or u.password is not None
            or u.query
            or u.fragment
            or any(c.isspace() or ord(c) < 32 for c in value)
            or "\\" in value
            or not u.path
            or u.path == "/"
            or u.port == 0
        ):
            raise ValueError()
        hostname = u.hostname.lower().rstrip(".")
        if hostname in {"localhost", "metadata.google.internal"} or hostname.endswith(
            (".localhost", ".local", ".internal")
        ):
            raise ValueError()
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            if "." not in hostname:
                raise ValueError()
        else:
            if not address.is_global:
                raise ValueError()
    except (ValueError, TypeError):
        raise ValueError("请填写公网 HTTPS 完整接口地址，不含账号、查询参数或片段") from None
    return value


class ConnectionSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    mode: Literal["custom", "inherit", "disabled"] = "custom"
    provider_name: str = Field(default="", max_length=80)
    endpoint_url: str = Field(default="", max_length=500)
    protocol: Literal["chat_completions", "audio_transcriptions", "senseaudio_tts"]
    model: str = Field(default="", max_length=200)
    timeout_seconds: int = Field(default=30, ge=1, le=120)
    max_retries: int = Field(default=1, ge=0, le=3)
    voice_id: str = Field(default="", max_length=100)

    @model_validator(mode="after")
    def connection(self):
        if self.endpoint_url:
            public_endpoint(self.endpoint_url)
        if self.mode == "custom":
            if not self.provider_name or not self.model:
                raise ValueError("请填写服务商名称和模型名称")
            public_endpoint(self.endpoint_url)
            if self.protocol == "senseaudio_tts" and not self.voice_id:
                raise ValueError("语音合成需要服务商支持的音色编号")
        return self


class ConnectionTest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_version: int = Field(ge=0, le=2147483645)
    configuration: ConnectionSnapshot
    api_key: SecretStr | None = Field(default=None, min_length=8, max_length=2000, repr=False)
    restored_from_version: int | None = Field(default=None, ge=1, le=2147483645)

    @field_validator("api_key")
    @classmethod
    def clean_key(cls, value):
        if value is not None and (
            value.get_secret_value().strip() != value.get_secret_value()
            or any(ord(c) < 33 or ord(c) == 127 for c in value.get_secret_value())
        ):
            raise ValueError("密钥不能包含空白或控制字符")
        return value


class ConnectionPublish(BaseModel):
    model_config = ConfigDict(extra="forbid")
    test_id: UUID
    expected_version: int = Field(ge=0, le=2147483645)
