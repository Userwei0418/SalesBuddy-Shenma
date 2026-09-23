from __future__ import annotations

import json,math,re

from dataclasses import dataclass,field

from typing import Any

@dataclass(frozen=True)
class RunIds:
    task_id: str | None = None
    message_id: str | None = None
    conversation_id: str | None = None

class FdeError(RuntimeError):
    """Fixed error codes only; never expose upstream bodies/URLs/credentials.

    dispatch_started means a POST may have reached the platform, even if no task
    ID was received. It is NOT a statement about writes or retry safety.
    """

    def __init__(self, code: str, *, status: int | None = None, ids: RunIds | None = None):
        super().__init__(f"FDE {code}")
        self.code, self.status, self.ids = code, status, ids or RunIds()
        self.dispatch_started = False

def _strict_json(value: str) -> Any:
    def reject_constant(_):
        raise ValueError("non-finite JSON")

    def unique(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = item
        return result

    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("non-finite JSON")
        return number

    try:
        return json.loads(value, parse_constant=reject_constant, object_pairs_hook=unique, parse_float=finite_float)
    except (ValueError, RecursionError):
        raise FdeError("invalid_json") from None

@dataclass(frozen=True)
class FdeResult:
    # Neither result nor error carries a claimed/guessed execution revision.
    answer: str = field(repr=False)
    ids: RunIds
    input_tokens: int | None
    output_tokens: int | None

    def json_object(self) -> dict[str, Any]:
        # A single complete Markdown JSON fence is presentation, not extra
        # business content. Strip only that envelope; never extract a plausible
        # object from prose, multiple blocks, or an incomplete model answer.
        answer = self.answer.strip()
        fence = re.fullmatch(r"```(?:json)?[ \t]*\r?\n(.*)\r?\n```", answer, re.DOTALL | re.IGNORECASE)
        if fence:
            answer = fence.group(1)
        try:
            result = _strict_json(answer)
        except FdeError as error:
            error.ids = self.ids
            raise
        if not isinstance(result, dict):
            raise FdeError("expected_json_object", ids=self.ids)
        return result
