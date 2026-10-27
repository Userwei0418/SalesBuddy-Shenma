"""Company terminology and durable ASR identities; no supplier credentials."""
from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCENES = ('customer_create', 'visit_entry', 'management_task', 'chatbi', 'demo_scene', 'visit_import')
LIMITS = dict(max_items=200, max_term_length=64, max_remark_length=200,
              max_effective_words=50, max_effective_characters=2000)
Category = Literal['product', 'company', 'industry', 'other']
Scene = Literal['all', 'customer_create', 'visit_entry', 'management_task', 'chatbi', 'demo_scene', 'visit_import']


class AsrError(ValueError):
    def __init__(self, code, status=409):
        self.code, self.status_code = code, status
        super().__init__(code)


class HotwordItem(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: UUID | None = None
    term: str = Field(min_length=1, max_length=64)
    category: Category = 'other'
    scenes: list[Scene] = Field(default_factory=lambda: ['all'], min_length=1, max_length=6)
    enabled: bool = True
    remark: str = Field(default='', max_length=200)
    # Returned metadata may be round-tripped; the server never trusts it.
    updated_at: str | None = None
    updated_by: str | None = None


class HotwordSave(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: int = Field(ge=0)
    operation_id: UUID
    items: list[HotwordItem] = Field(max_length=200)
    reason: str = Field(default='', max_length=200)


class HotwordRestore(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: int = Field(ge=0)
    source_version: int = Field(ge=1)
    operation_id: UUID


class AsrBudgetPolicy(BaseModel):
    model_config = ConfigDict(extra='forbid')
    daily_seconds: int | None = Field(default=None, ge=1, le=8640000)
    monthly_seconds: int | None = Field(default=None, ge=1, le=259200000)
    user_daily_seconds: int | None = Field(default=None, ge=1, le=86400)
    company_concurrency: int = Field(default=2, ge=1, le=4)
    user_concurrency: int = Field(default=2, ge=1, le=2)


class AsrCapacityRelease(BaseModel):
    model_config = ConfigDict(extra='forbid')
    owner_id: UUID
    evidence: str = Field(min_length=10, max_length=500)
    resolution_kind: Literal['provider_finished', 'local_transport_closed'] = 'provider_finished'
    provider_finished: Literal[True] | None = None
    local_transport_closed: Literal[True] | None = None

    @model_validator(mode='after')
    def confirmed_basis(self):
        if self.resolution_kind == 'provider_finished':
            valid = self.provider_finished is True and self.local_transport_closed is None
        else:
            valid = self.local_transport_closed is True and self.provider_finished is None
        if not valid:
            raise ValueError('请确认所选核对依据')
        return self


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def normalized_items(items):
    if len(items) > LIMITS['max_items']:
        raise AsrError('ASR_HOTWORD_LIMIT_EXCEEDED', 422)
    seen, ids, result = set(), set(), []
    for value in items:
        value = value.model_dump(mode='json') if isinstance(value, HotwordItem) else dict(value)
        term = unicodedata.normalize('NFC', value['term']).strip()
        scenes = sorted(set(value.get('scenes', ['all'])))
        if (not 1 <= len(term) <= 64 or any(unicodedata.category(c).startswith('C') or c in ',，' for c in term)
                or not scenes or set(scenes) - {'all', *SCENES} or ('all' in scenes and len(scenes) != 1)):
            raise AsrError('ASR_HOTWORD_INVALID', 422)
        name = term.casefold()
        if name in seen or (value.get('id') and value['id'] in ids):
            raise AsrError('ASR_HOTWORD_DUPLICATE', 422)
        seen.add(name)
        ids.add(value.get('id'))
        result.append({k: v for k, v in {**value, 'term': term, 'scenes': scenes}.items()
                       if k not in {'updated_at', 'updated_by'}})
    for scene in ('all', *SCENES):
        words = selected_words(result, scene)
        if len(words) > 50 or len(','.join(words)) > 2000:
            raise AsrError('ASR_HOTWORD_LIMIT_EXCEEDED', 422)
    return result


def selected_words(items, scene):
    return tuple(i['term'] for i in items if i.get('enabled', True)
                 and ('all' in i['scenes'] or scene in i['scenes']))


def model_info(settings):
    from urllib.parse import urlsplit
    # Explicit known protocol/provider/model only. Model names alone are not a capability grant.
    endpoint = settings.model_api_endpoint or settings.senseaudio_base_url
    host = (urlsplit(endpoint).hostname or '').lower()
    supported = ((host == 'senseaudio.cn' or host.endswith('.senseaudio.cn'))
                 and settings.asr_model.startswith('senseaudio-asr-lite-1.5')
                 and (not settings.model_api_endpoint or settings.model_api_metadata.get('connection_purpose') == 'asr'))
    return {'supported': supported, 'available': bool(settings.senseaudio_api_key), 'name': settings.asr_model}


def configuration_fingerprint(settings):
    # No credentials, prompt bodies or arbitrary metadata in persisted snapshots.
    metadata = settings.model_api_metadata
    return digest({'model': settings.asr_model, 'base': settings.senseaudio_base_url,
                   'endpoint': settings.model_api_endpoint,
                   'version': {k: metadata.get(k) for k in ('connection_version', 'connection_mode',
                              'ai_service_source', 'source_version', 'runtime_version', 'source')}})
