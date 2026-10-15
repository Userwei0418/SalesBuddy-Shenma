"""Platform sharing is an upper bound, independent of company RBAC and modules."""
from dataclasses import fields, replace


class AIServiceUnavailable(RuntimeError):
    def __init__(self):
        super().__init__('本公司未获准使用平台共享 AI 服务，请由公司管理员配置本公司的模型接口和密钥')


async def shared_allowed(connection):
    policy = await connection.fetchval('SELECT security.ai_service_policy()')
    # Missing, malformed or inaccessible policy never grants platform access.
    return isinstance(policy, dict) and policy.get('shared_enabled') is True


def legacy_owned(row, purpose):
    model = {'text': 'llm_model', 'asr': 'asr_model', 'tts': 'tts_model'}[purpose]
    return bool(row and row.get('enabled') and
                (row.get('api_key_ciphertext') or row.get('has_api_key')) and
                str(row.get('provider_base_url') or '').strip() and str(row.get(model) or '').strip())


def purpose_owned(row):
    # Old custom candidates could copy a platform credential. Do not relabel
    # those as company-owned merely because they are now stored in its row.
    return bool(row and row.get('api_key_ciphertext') and row.get('credential_owned') is True)


def restrict_shared_agents(settings, allowed):
    if allowed:
        return settings
    updates = {f.name: '' for f in fields(settings)
               if f.name.startswith('agent_fde_') and f.name.endswith('_api_key')}
    updates['agent_execution_policy'] = {'source': 'platform-sharing-disabled',
                                       'definition': {'strategy': 'direct_only'}}
    return replace(settings, **updates)


def source_settings(settings, *, allowed, source, purpose, version=0, test_id=None):
    metadata = {**settings.model_api_metadata, 'ai_service_source': source,
                'shared_ai_allowed': allowed, 'connection_purpose': purpose,
                'connection_version': version}
    if test_id is not None:
        metadata['connection_test_id'] = str(test_id)
    return restrict_shared_agents(replace(settings, model_api_metadata=metadata), allowed)
