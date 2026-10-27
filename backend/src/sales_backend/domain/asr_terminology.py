"""Bounded spelling hints and formatting for a selected company snapshot.

Hints use the supplier's existing plain hotwords contract, not a phoneme or
replacement protocol. Formatting may join identical letters but never substitutes
letters, guesses a similar-sounding word, or changes the supplier's raw payload.
"""
from __future__ import annotations

import re

from sales_backend.domain.asr import LIMITS

VERSION = 2
NORMALIZATION_VERSION = 1
_SHORT_ACRONYM = re.compile(r'[A-Z]{2,6}')
_NORMALIZABLE_ACRONYM = re.compile(r'[A-Z]{3,6}')
# Horizontal spacing only: never join letters across a line or sentence.
_HORIZONTAL_SPACE = r'[ \t\u00a0\u1680\u2000-\u200a\u202f\u205f\u3000]'
_HORIZONTAL_SPACES = re.compile(_HORIZONTAL_SPACE + '+')
# Consume a whole run before checking boundaries. Matching a permitted acronym
# directly would incorrectly normalize the ACV substring of "X A C V".
_LETTER_RUN = re.compile(r'[A-Z](?:' + _HORIZONTAL_SPACE + r'+[A-Z])+')
_IDENTIFIER_EDGE = re.compile(r'[A-Za-z0-9_.-]')


def provider_words(words) -> tuple[str, ...]:
    """Keep validated standard words first, then add same-letter spelling hints.

    The caller supplies only its frozen, authorized company/scene words. No
    global vocabulary, remarks, similar-sounding aliases or inferred words are
    used. Existing standard words always take priority over optional hints.
    """
    result = list(dict.fromkeys(words))
    seen = set(result)
    characters = len(','.join(result))
    for word in tuple(result):
        if not _SHORT_ACRONYM.fullmatch(word):
            continue
        spelled = ' '.join(word)
        added_characters = len(spelled) + (1 if result else 0)
        if (spelled in seen or len(result) >= LIMITS['max_effective_words']
                or characters + added_characters > LIMITS['max_effective_characters']):
            continue
        result.append(spelled)
        seen.add(spelled)
        characters += added_characters
    return tuple(result)


def normalize_acronyms(text: str, words) -> tuple[str, list[dict]]:
    """Join same-letter spelling only for the caller's frozen standard words.

    Two-letter sequences remain untouched because they are commonly coordinates
    or enumerations. Identifier, dotted and hyphenated contexts are left alone.
    Changes contain offsets into the original text, not the progressively edited
    output. The caller is responsible for retaining that original and its raw
    provider response alongside the formatted text.
    """
    allowed = {word for word in words if _NORMALIZABLE_ACRONYM.fullmatch(word)}
    if not allowed:
        return text, []
    pieces, changes, previous_end = [], [], 0
    for match in _LETTER_RUN.finditer(text):
        start, end = match.span()
        before = match.group()
        after = _HORIZONTAL_SPACES.sub('', before)
        if (after not in allowed
                or (start and _IDENTIFIER_EDGE.fullmatch(text[start - 1]))
                or (end < len(text) and _IDENTIFIER_EDGE.fullmatch(text[end]))):
            continue
        pieces.extend((text[previous_end:start], after))
        changes.append({'start': start, 'end': end, 'before': before, 'after': after})
        previous_end = end
    pieces.append(text[previous_end:])
    return ''.join(pieces), changes
