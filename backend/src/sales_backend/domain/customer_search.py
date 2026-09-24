"""Name-only phonetic projection. Never cache directory membership or authorization."""

from functools import lru_cache
import re
import unicodedata

from pypinyin import Style, lazy_pinyin


def phonetic_query(query: str | None) -> str:
    value = unicodedata.normalize("NFKC", query or "").lower().strip()
    # Chinese, punctuation-only and mixed Chinese queries keep literal matching.
    return re.sub(r"[\s'’-]", "", value) if re.fullmatch(r"[a-z0-9\s'’-]+", value) else ""


@lru_cache(maxsize=50000)
def name_search_keys(name: str) -> tuple[str, str]:
    value = unicodedata.normalize("NFKC", name).lower()
    def compact(parts):
        return "".join(re.findall(r"[a-z0-9]+", "".join(parts)))
    return (compact(lazy_pinyin(value)), compact(lazy_pinyin(value, style=Style.FIRST_LETTER)))


def matching_names(names: list[str], query: str) -> list[str]:
    # Called off the event loop. The fresh, authorized names are supplied for
    # every request; caching only a pure name transform cannot retain old access.
    return [name for name in names if any(query in key for key in name_search_keys(name))]
