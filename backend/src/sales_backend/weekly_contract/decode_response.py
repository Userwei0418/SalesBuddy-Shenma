"""Strictly decode one Agent JSON object; optional single JSON fence is tolerated.

This is a backend integration helper, not deployed business code.
Never extract arbitrary brace fragments from prose or silently repair JSON.
"""
import json
import re


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key: " + key)
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("Non-finite JSON number: " + value)


def decode_response(raw):
    if not isinstance(raw, str):
        raise ValueError("Agent response must be text")
    text = raw.strip()
    match = re.fullmatch(r"```(?:json)?[ \t]*\r?\n([\s\S]*?)\r?\n```", text, re.IGNORECASE)
    if match:
        text = match.group(1)
    result = json.loads(text, object_pairs_hook=_object, parse_constant=_reject_constant)
    if not isinstance(result, dict):
        raise ValueError("Agent response must contain one JSON object")
    return result
