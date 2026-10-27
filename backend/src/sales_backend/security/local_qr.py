"""QR rendering is local; credentials and company entry data never leave the process."""

import io
import re
from urllib.parse import urlsplit

import segno


def company_entry_scene(code: str) -> str | None:
    """A short safe c= value; historical delimiters/non-ASCII need a future mapping."""
    return "c=" + code if re.fullmatch(r"[A-Za-z0-9._-]{1,30}", code) else None


def qr_svg(value: str) -> str:
    output = io.BytesIO()
    segno.make(value, error="m", micro=False).save(
        output,
        kind="svg",
        scale=5,
        border=4,
        xmldecl=False,
    )
    return output.getvalue().decode("utf-8")


def company_web_origin(settings, request_origin: str) -> str | None:
    """Use a configured deployment origin or an explicit local development loopback.

    A production Host / forwarded-host header can never choose a QR destination.
    """
    configured = getattr(settings, "public_web_origin", "").strip()
    local = getattr(settings, "app_env", "").lower() in {"development", "local", "test"}
    value = configured or (request_origin if local else "")
    try:
        parsed = urlsplit(value)
        _ = parsed.port  # Validate malformed/out-of-range ports without ever sending a request.
    except ValueError:
        return None
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or any(character.isspace() for character in value)
        or "\\" in value
    ):
        return None
    loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme != "https" and not (local and loopback and parsed.scheme == "http"):
        return None
    if not configured and not (local and loopback):
        return None
    # Retain the validated explicit port (if any) and IPv6 brackets from the origin.
    return f"{parsed.scheme}://{parsed.netloc}"
