from dataclasses import replace

import pytest

from sales_backend.services.configuration_readiness import shared_presence
from tests.test_senseaudio import settings


@pytest.mark.parametrize('change', [
    {'senseaudio_api_key': ''}, {'senseaudio_api_key': '   '},
    {'senseaudio_base_url': 'https://hidden.invalid:not-a-port'},
    {'senseaudio_base_url': 'https://private-user:private-secret@hidden.invalid'},
    {'senseaudio_base_url': 'https://hidden.invalid?key=private-secret'},
    {'senseaudio_base_url': 'not-an-endpoint'},
])
def test_missing_or_malformed_instance_is_not_ready_and_never_returns_credentials(change):
    result = shared_presence(replace(settings(), **change))
    assert result == {'text': False, 'asr': False, 'tts': False}
    assert 'private' not in str(result)


def test_only_available_purposes_are_reported_without_service_details():
    result = shared_presence(replace(settings(), senseaudio_api_key='synthetic',
                                    asr_model='', tts_model=''))
    assert result == {'text': True, 'asr': False, 'tts': False}
    assert shared_presence(None) == {'text': False, 'asr': False, 'tts': False}
