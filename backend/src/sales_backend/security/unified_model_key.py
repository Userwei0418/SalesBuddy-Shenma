"""One encrypted deployment credential; no workspace can override it."""

import base64
import json
from dataclasses import replace
from urllib.parse import urlsplit

from sales_backend.security.runtime_credentials import (
    CredentialCipher,
    RuntimeCredentialUnavailable,
    read_private_file,
)

DEPLOYMENT_AAD = "00000000-0000-0000-0000-000000000000"


def read_state(settings):
    if not settings.unified_model_key_file:
        return None
    try:
        state = json.loads(read_private_file(settings.unified_model_key_file))
        if type(state["revision"]) is not int or state["revision"] < 1:
            raise ValueError()
        return state
    except (KeyError, TypeError, ValueError):
        raise RuntimeCredentialUnavailable() from None


def decrypt_state(settings, state):
    try:
        cipher = CredentialCipher.from_file(settings.config_credential_keyring_file, settings.config_credential_key_id)
        return cipher.decrypt(DEPLOYMENT_AAD, state["encryption_key_id"],
                              base64.b64decode(state["api_key_ciphertext"], validate=True))
    except (KeyError, TypeError, ValueError):
        raise RuntimeCredentialUnavailable() from None


def credential(settings, key):
    cipher = CredentialCipher.from_file(settings.config_credential_keyring_file, settings.config_credential_key_id)
    result = cipher.encrypt(DEPLOYMENT_AAD, key)
    return {**result, "api_key_ciphertext": base64.b64encode(result["api_key_ciphertext"]).decode()}


def validate_unified_endpoint(settings):
    """Do not send the deployment credential to a historical company endpoint."""
    endpoint = settings.model_api_endpoint or settings.senseaudio_base_url
    try:
        target = urlsplit(endpoint)
        if (not endpoint or any(char.isspace() for char in endpoint)
                or target.scheme != "https" or target.hostname != "api.senseaudio.cn"
                or target.port not in (None, 443) or target.username is not None
                or target.password is not None or target.fragment):
            raise ValueError()
    except (AttributeError, TypeError, ValueError):
        raise RuntimeCredentialUnavailable() from None


def effective_settings(settings):
    if not settings.unified_model_key_file or settings.model_api_metadata.get("connection_mode") == "disabled":
        return settings
    validate_unified_endpoint(settings)
    return replace(settings, senseaudio_api_key=decrypt_state(settings, read_state(settings)))


def is_key_operator(settings, user_id):
    return str(user_id) in {value.strip() for value in settings.model_key_operator_ids.split(",") if value.strip()}
