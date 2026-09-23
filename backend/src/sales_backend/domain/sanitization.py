import re


def redact_log(value: str, limit: int = 32000) -> str:
    text = str(value)
    text = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", text)
    text = re.sub(r"(\w+://)[^\s/:@]+:[^\s@]+@", r"\1[REDACTED]@", text)
    text = re.sub(
        r"""(?i)(["']?(?:password|secret|refresh_token|access_token|api_key|authorization|credential|token)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;}]+)""",
        r"\1[REDACTED]",
        text,
    )
    return text[:limit]
