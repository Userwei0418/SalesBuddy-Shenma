"""Only static API-process configuration presence crosses into platform SQL."""
from urllib.parse import urlsplit


def shared_presence(settings):
    result = {purpose: False for purpose in ('text', 'asr', 'tts')}
    if settings is None:
        return result
    try:
        base = urlsplit(settings.senseaudio_base_url)
        valid = (base.scheme == 'https' and bool(base.hostname) and not base.username
                 and not base.password and not base.query and not base.fragment
                 and bool(settings.senseaudio_api_key.strip()))
        # Parse malformed ports as well, without returning the URL or exception.
        base.port
    except (ValueError, AttributeError):
        return result
    for purpose, model in [('text', 'llm_model'), ('asr', 'asr_model'), ('tts', 'tts_model')]:
        result[purpose] = bool(valid and str(getattr(settings, model, '') or '').strip())
    return result
