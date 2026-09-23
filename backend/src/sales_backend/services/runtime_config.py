from __future__ import annotations

import logging
from dataclasses import dataclass, replace

from sales_backend.config import Settings
from sales_backend.db import Database
from sales_backend.domain.agent import ActorContext

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RuntimeConfiguration:
    settings: Settings
    prompt_overrides: dict[str, str]
    business_policy: dict | None = None


async def _load_provider_configuration(
    database: Database,
    actor: ActorContext,
    defaults: Settings,
    *, purpose="text",
) -> RuntimeConfiguration:
    from sales_backend.repositories.admin import AgentRuntimeConfigRepository
    from sales_backend.security.runtime_credentials import decrypt_credential

    async with database.transaction(actor, readonly=True) as connection:
        from sales_backend.repositories.model_api import ModelApiRepository
        from sales_backend.services.model_api import connection_settings
        binding = await ModelApiRepository().current(connection, actor, purpose)
        row = await AgentRuntimeConfigRepository().current(connection, actor)
        if binding and binding["config_snapshot"]["mode"] != "inherit":
            key = await decrypt_credential(connection, binding, defaults)
            settings = connection_settings(defaults, actor, purpose, binding["config_snapshot"], key,
                                           binding["version_no"])
            prompts = (row or {}).get("prompt_overrides") if row and row["enabled"] else {}
            return RuntimeConfiguration(settings, prompts if isinstance(prompts, dict) else {})
        if not row or not row["enabled"]:
            return RuntimeConfiguration(defaults, {})
        api_key = await decrypt_credential(connection, row, defaults)
    configured = replace(
        defaults,
        senseaudio_base_url=str(row["provider_base_url"] or defaults.senseaudio_base_url).rstrip("/"),
        senseaudio_api_key=str(api_key or defaults.senseaudio_api_key),
        llm_model=str(row["llm_model"] or defaults.llm_model),
        asr_model=str(row["asr_model"] or defaults.asr_model),
        tts_model=str(row["tts_model"] or defaults.tts_model),
    )
    prompts = row["prompt_overrides"] if isinstance(row["prompt_overrides"], dict) else {}
    return RuntimeConfiguration(
        configured,
        {str(key): str(value) for key, value in prompts.items() if str(value).strip()},
    )


def apply_execution_policy(settings, snapshot):
    from sales_backend.domain.company_rules import AgentExecutionPolicy
    policy = AgentExecutionPolicy(**snapshot["definition"])
    updates = {"agent_execution_policy": snapshot}
    if policy.override_budget:
        updates.update(agent_inference_platform_seconds=policy.platform_seconds,
                       agent_inference_total_seconds=policy.total_seconds)
    return replace(settings, **updates)


async def load_runtime_configuration(database, actor, defaults, *, capability=None, purpose="text"):
    from sales_backend.domain.company_rules import TECHNICAL_CAPABILITIES
    from sales_backend.repositories.company_rules import CompanyRulesRepository

    runtime = await _load_provider_configuration(database, actor, defaults, purpose=purpose)
    if capability not in TECHNICAL_CAPABILITIES:
        return runtime
    # Resolve the published business policy exactly once for this invocation.
    # Failure must not silently run with different company judgement criteria.
    async with database.transaction(actor, readonly=True) as connection:
        business_policy = await CompanyRulesRepository().active(connection, "agent_business." + capability)
    if business_policy.get("code") != "agent_business." + capability:
        raise ValueError("业务规则与当前能力不一致")
    try:
        async with database.transaction(actor, readonly=True) as connection:
            snapshot = await CompanyRulesRepository().active(connection, "agent_execution." + capability)
        settings = apply_execution_policy(runtime.settings, snapshot)
    except Exception:
        # Do not accidentally re-enable a disabled platform when the rule store is unavailable.
        logger.exception("Agent execution policy unavailable; selecting original API")
        settings = replace(runtime.settings, agent_execution_policy={
            "code": "agent_execution." + capability, "source": "unavailable",
            "definition": {"strategy": "direct_only"},
        })
    return RuntimeConfiguration(settings, runtime.prompt_overrides, business_policy)
