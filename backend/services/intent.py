"""Query-intent detection for the search box.

Not every string a user types is a *semantic* question. "2019", "Hagibis",
"2306" are lookups — they have an exact answer in the structured columns, and
routing them through the embedding model produces confident-looking nonsense
(cosine distance has no notion of "the year 2019"). So we classify first and
only fall back to vector search when the query is genuinely descriptive.

Returned intent is one of:
  ``year``     a bare season year          -> filter typhoon.season_year
  ``intl_id``  a 4-digit WMO number        -> exact typhoon.intl_id
  ``name``     a short bare latin word     -> ILIKE on the name columns,
                                              semantic search still runs as backup
  ``semantic`` anything descriptive        -> vector search only
"""
from __future__ import annotations

import re

_YEAR = re.compile(r"^(1[89]\d{2}|20\d{2})$")
# WMO storm number: 2 digits of season + 2 of sequence, e.g. "2306" = 2023 #06.
_INTL_ID = re.compile(r"^\d{4}$")
# A single latin token with no descriptive punctuation — reads as a storm name.
_BARE_NAME = re.compile(r"^[A-Za-z][A-Za-z\-']{2,15}$")


def detect_intent(q: str) -> tuple[str, dict]:
    """Classify a raw query string. Returns ``(intent, params)``."""
    s = q.strip()
    # Allow "typhoon hagibis" / "台风 海贝思" to still read as a name lookup.
    stripped = re.sub(r"^(typhoon|台风|台風|颱風)\s+", "", s, flags=re.I).strip()

    if _YEAR.match(s):
        return "year", {"year": int(s)}
    if _INTL_ID.match(s):
        return "intl_id", {"intl_id": s}
    if _BARE_NAME.match(stripped) and stripped.lower() != "typhoon":
        return "name", {"name": stripped}
    return "semantic", {}
