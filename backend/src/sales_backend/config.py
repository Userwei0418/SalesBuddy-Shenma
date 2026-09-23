from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache


@dataclass(frozen=True, slots=True)
class Settings:
    app_env: str
    database_url: str
    senseaudio_base_url: str
    senseaudio_api_key: str = field(repr=False)
    asr_model: str
    tts_model: str
    llm_model: str
    timeout_seconds: float
    max_retries: int
    access_token_secret: str
    access_token_issuer: str
    access_token_audience: str
    access_token_minutes: int
    refresh_token_days: int
    auth_mode: str
    demo_workspace: str
    database_min_pool_size: int
    database_max_pool_size: int
    worker_poll_seconds: float
    worker_lock_seconds: int
    worker_id: str
    config_credential_key_id: str = ""
    config_credential_keyring_file: str = ""
    config_credential_legacy_key_file: str = ""
    worker_import_concurrency: int = 1
    worker_interactive_concurrency: int = 2
    worker_review_concurrency: int = 1
    worker_database_max_pool_size: int = 8
    circuit_failure_threshold: int = 5
    circuit_recovery_seconds: float = 25.0
    agent_platform_bindings_json: str = "{}"
    agent_fde_pilot_json: str = "{}"
    agent_fde_pilot_path: str = ""
    agent_fde_base_url: str = ""
    agent_fde_ca_bundle: str = ""
    agent_fde_chatbi_id: str = ""
    agent_fde_chatbi_api_key: str = field(default="", repr=False)
    agent_fde_battle_map_id: str = ""
    agent_fde_battle_map_api_key: str = field(default="", repr=False)
    agent_fde_opportunity_id: str = ""
    agent_fde_opportunity_api_key: str = field(default="", repr=False)
    agent_fde_personal_risks_id: str = ""
    agent_fde_personal_risks_api_key: str = field(default="", repr=False)
    agent_fde_visit_quality_id: str = ""
    agent_fde_visit_quality_api_key: str = field(default="", repr=False)
    agent_fde_visit_entry_id: str = ""
    agent_fde_visit_entry_api_key: str = field(default="", repr=False)
    agent_fde_today_tasks_id: str = ""
    agent_fde_today_tasks_api_key: str = field(default="", repr=False)
    agent_fde_operating_report_id: str = ""
    agent_fde_operating_report_api_key: str = field(default="", repr=False)
    agent_fde_customer_advice_id: str = ""
    agent_fde_customer_advice_api_key: str = field(default="", repr=False)
    agent_fde_opportunity_advice_id: str = ""
    agent_fde_opportunity_advice_api_key: str = field(default="", repr=False)
    agent_fde_visit_advice_id: str = ""
    agent_fde_visit_advice_api_key: str = field(default="", repr=False)
    agent_fde_competency_review_id: str = ""
    agent_fde_competency_review_api_key: str = field(default="", repr=False)
    agent_today_tasks_acceptance_path: str = ""
    agent_today_tasks_acceptance_target: str = ""
    model_api_endpoint: str = ""
    model_api_metadata: dict = field(default_factory=dict, repr=False)
    agent_execution_policy: dict = field(default_factory=dict, repr=False)
    agent_inference_total_seconds: float = 45.0
    agent_inference_platform_seconds: float = 12.0

    @property
    def model_gateway_configured(self) -> bool:
        return bool(self.senseaudio_api_key)

    def require_model_gateway(self) -> None:
        if not self.senseaudio_api_key:
            raise RuntimeError("当前用途的模型接口未配置或已停用")

    def require_auth(self) -> None:
        self.require_auth_mode()
        if len(self.access_token_secret) < 32:
            raise RuntimeError("ACCESS_TOKEN_SECRET must contain at least 32 characters")

    def require_auth_mode(self) -> None:
        if self.auth_mode not in {"password", "demo"}:
            raise RuntimeError("AUTH_MODE must be password or demo")
        if self.app_env.lower() == "production" and self.auth_mode != "password":
            raise RuntimeError("Production requires AUTH_MODE=password")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    app_env = os.getenv("APP_ENV", "development").strip().lower()
    auth_mode = os.getenv("AUTH_MODE", "").strip().lower()
    if app_env == "production" and auth_mode != "password":
        # Absence must not silently choose an authentication strategy in production.
        raise RuntimeError("Production requires explicit AUTH_MODE=password")
    settings = Settings(
        app_env=app_env,
        database_url=os.getenv("DATABASE_URL", ""),
        senseaudio_base_url=os.getenv("SENSEAUDIO_BASE_URL", "https://api.senseaudio.cn").rstrip("/"),
        senseaudio_api_key=os.getenv("SENSEAUDIO_API_KEY", ""),
        asr_model=os.getenv("SENSEAUDIO_ASR_MODEL", "senseaudio-asr-lite-1.5-260319"),
        tts_model=os.getenv("SENSEAUDIO_TTS_MODEL", "senseaudio-tts-1.5-260319"),
        llm_model=os.getenv("SENSEAUDIO_LLM_MODEL", "senseaudio-s2-lite"),
        timeout_seconds=float(os.getenv("SENSEAUDIO_TIMEOUT_SECONDS", "30")),
        max_retries=int(os.getenv("SENSEAUDIO_MAX_RETRIES", "3")),
        access_token_secret=os.getenv("ACCESS_TOKEN_SECRET", ""),
        access_token_issuer=os.getenv("ACCESS_TOKEN_ISSUER", "sales-saas"),
        access_token_audience=os.getenv("ACCESS_TOKEN_AUDIENCE", "sales-mini-program"),
        access_token_minutes=int(os.getenv("ACCESS_TOKEN_MINUTES", "30")),
        refresh_token_days=int(os.getenv("REFRESH_TOKEN_DAYS", "30")),
        auth_mode=auth_mode or "password",
        demo_workspace=os.getenv("DEMO_WORKSPACE", "demo-sales-workspace"),
        database_min_pool_size=int(os.getenv("DATABASE_MIN_POOL_SIZE", "1")),
        database_max_pool_size=int(os.getenv("DATABASE_MAX_POOL_SIZE", "8")),
        worker_poll_seconds=float(os.getenv("WORKER_POLL_SECONDS", "1")),
        worker_lock_seconds=int(os.getenv("WORKER_LOCK_SECONDS", "180")),
        worker_id=os.getenv("WORKER_ID", "sales-worker-1"),
        worker_import_concurrency=int(os.getenv("WORKER_IMPORT_CONCURRENCY", "1")),
        worker_interactive_concurrency=int(os.getenv("WORKER_INTERACTIVE_CONCURRENCY", "2")),
        worker_review_concurrency=int(os.getenv("WORKER_REVIEW_CONCURRENCY", "1")),
        worker_database_max_pool_size=int(os.getenv("WORKER_DATABASE_MAX_POOL_SIZE", "8")),
        circuit_failure_threshold=int(os.getenv("SENSEAUDIO_CIRCUIT_FAILURES", "5")),
        circuit_recovery_seconds=float(os.getenv("SENSEAUDIO_CIRCUIT_RECOVERY_SECONDS", "25")),
        config_credential_key_id=os.getenv("CONFIG_CREDENTIAL_KEY_ID", ""),
        config_credential_keyring_file=os.getenv("CONFIG_CREDENTIAL_KEYRING_FILE", ""),
        config_credential_legacy_key_file=os.getenv("CONFIG_CREDENTIAL_LEGACY_KEY_FILE", ""),
        agent_platform_bindings_json=os.getenv("AGENT_PLATFORM_BINDINGS_JSON", "{}"),
        agent_fde_pilot_json=os.getenv("AGENT_FDE_PILOT_JSON", "{}"),
        agent_fde_pilot_path=os.getenv("AGENT_FDE_PILOT_PATH", ""),
        agent_fde_base_url=os.getenv("AGENT_FDE_BASE_URL", ""),
        agent_fde_ca_bundle=os.getenv("AGENT_FDE_CA_BUNDLE", ""),
        agent_fde_chatbi_id=os.getenv("AGENT_FDE_CHATBI_ID", ""),
        agent_fde_chatbi_api_key=os.getenv("AGENT_FDE_CHATBI_API_KEY", ""),
        agent_fde_battle_map_id=os.getenv("AGENT_FDE_BATTLE_MAP_ID", ""),
        agent_fde_battle_map_api_key=os.getenv("AGENT_FDE_BATTLE_MAP_API_KEY", ""),
        agent_fde_opportunity_id=os.getenv("AGENT_FDE_OPPORTUNITY_ID", ""),
        agent_fde_opportunity_api_key=os.getenv("AGENT_FDE_OPPORTUNITY_API_KEY", ""),
        agent_fde_personal_risks_id=os.getenv("AGENT_FDE_PERSONAL_RISKS_ID", ""),
        agent_fde_personal_risks_api_key=os.getenv("AGENT_FDE_PERSONAL_RISKS_API_KEY", ""),
        agent_fde_visit_quality_id=os.getenv("AGENT_FDE_VISIT_QUALITY_ID", ""),
        agent_fde_visit_quality_api_key=os.getenv("AGENT_FDE_VISIT_QUALITY_API_KEY", ""),
        agent_fde_visit_entry_id=os.getenv("AGENT_FDE_VISIT_ENTRY_ID", ""),
        agent_fde_visit_entry_api_key=os.getenv("AGENT_FDE_VISIT_ENTRY_API_KEY", ""),
        agent_fde_today_tasks_id=os.getenv("AGENT_FDE_TODAY_TASKS_ID", ""),
        agent_fde_today_tasks_api_key=os.getenv("AGENT_FDE_TODAY_TASKS_API_KEY", ""),
        agent_fde_operating_report_id=os.getenv("AGENT_FDE_OPERATING_REPORT_ID", ""),
        agent_fde_operating_report_api_key=os.getenv("AGENT_FDE_OPERATING_REPORT_API_KEY", ""),
        agent_fde_customer_advice_id=os.getenv("AGENT_FDE_CUSTOMER_ADVICE_ID", ""),
        agent_fde_customer_advice_api_key=os.getenv("AGENT_FDE_CUSTOMER_ADVICE_API_KEY", ""),
        agent_fde_opportunity_advice_id=os.getenv("AGENT_FDE_OPPORTUNITY_ADVICE_ID", ""),
        agent_fde_opportunity_advice_api_key=os.getenv("AGENT_FDE_OPPORTUNITY_ADVICE_API_KEY", ""),
        agent_fde_visit_advice_id=os.getenv("AGENT_FDE_VISIT_ADVICE_ID", ""),
        agent_fde_visit_advice_api_key=os.getenv("AGENT_FDE_VISIT_ADVICE_API_KEY", ""),
        agent_fde_competency_review_id=os.getenv("AGENT_FDE_COMPETENCY_REVIEW_ID", ""),
        agent_fde_competency_review_api_key=os.getenv("AGENT_FDE_COMPETENCY_REVIEW_API_KEY", ""),
        agent_today_tasks_acceptance_path=os.getenv("AGENT_TODAY_TASKS_ACCEPTANCE_PATH", ""),
        agent_today_tasks_acceptance_target=os.getenv("AGENT_TODAY_TASKS_ACCEPTANCE_TARGET", ""),
        agent_inference_total_seconds=float(os.getenv("AGENT_INFERENCE_TOTAL_SECONDS", "45")),
        agent_inference_platform_seconds=float(os.getenv("AGENT_INFERENCE_PLATFORM_SECONDS", "12")),
    )
    settings.require_auth_mode()
    return settings
