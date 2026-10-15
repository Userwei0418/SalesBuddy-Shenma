"""Secret-free configuration facts, not a connectivity or decryption attestation."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class ConfigurationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IdentityChecks(ConfigurationModel):
    administrator_ready: bool
    activation_complete: bool
    permissions_ready: bool


class ConfigurationModule(ConfigurationModel):
    code: Literal["ai", "feishu", "visit"]
    enabled: bool
    effective: bool
    expires_at: AwareDatetime | None


class ModelConnectionFacts(ConfigurationModel):
    purpose: Literal["text", "asr", "tts"]
    mode: Literal["custom", "inherit", "disabled", "invalid"]
    version_no: int | None = Field(ge=1)
    source: Literal["tenant_purpose", "tenant_legacy", "instance_default", "disabled", "invalid"]
    legacy_version_no: int | None = Field(ge=0)
    fields_present: bool
    credential_saved: bool
    credential_owned: bool
    service_authorized: bool


class FeishuConnectionFacts(ConfigurationModel):
    mapping_fields_present: bool
    configured: bool
    revision: int | None = Field(ge=1)
    enabled: bool
    validated: bool
    validation_pending: bool
    credential_saved: bool
    fields_present: bool
    direction_supported: bool
    enabled_mappings: int = Field(ge=0)


class AIServicePolicy(ConfigurationModel):
    workspace_id: UUID
    version_no: int = Field(ge=1)
    shared_enabled: bool


class IntegrationConfigurationCheck(ConfigurationModel):
    code: Literal['text', 'asr', 'tts', 'feishu']
    enabled: bool
    required: bool
    configuration_ready: bool
    issues: list[Literal['purpose_disabled', 'invalid_configuration', 'source_not_authorized',
                         'fields_missing', 'credential_missing', 'shared_service_not_configured',
                         'connection_missing', 'direction_unsupported', 'mapping_missing', 'mapping_fields_missing']]


class ConfigurationReadiness(ConfigurationModel):
    kind: Literal['static_configuration']
    required_ready: bool
    checks: list[IntegrationConfigurationCheck] = Field(min_length=4, max_length=4)


class TenantConfigurationStatus(ConfigurationModel):
    configuration_readiness: ConfigurationReadiness
    ai_service: AIServicePolicy
    workspace_id: UUID
    version_no: int = Field(ge=1)
    sampled_at: AwareDatetime
    identity_checks: IdentityChecks
    modules: list[ConfigurationModule] = Field(min_length=3, max_length=3)
    model_connections: list[ModelConnectionFacts] = Field(min_length=3, max_length=3)
    feishu: FeishuConnectionFacts
