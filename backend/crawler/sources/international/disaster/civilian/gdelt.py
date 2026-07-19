"""GDELT DOC 2.0 — per-typhoon news / official-media search for 受灾情报.

Where GDACS / ReliefWeb give *structured* disaster bulletins, GDELT indexes the
world's news — including official media (新华网 / 人民网 / 央视 / NHK / 政府公报
and thousands more) — behind ONE free, key-less JSON API. We use it to answer
"what did the news say happened where this typhoon passed": death tolls,
flooding, inundation, which place was hit.

Flow (driven by a user-selected typhoon):
  1. build a query from the storm's name variants (EN / 中文 / 日本語), restricted
     to the storm's date window (damage keywords are applied locally in step 2 —
     GDELT caps query length, see `build_query`);
  2. GDELT ArtList returns matching articles (title, url, domain, seendate, …),
     and headlines without a damage keyword are dropped;
  3. each article's title is mined for casualties / loss (reusing the shared
     regex extractors) and its place name is resolved to a coordinate against the
     local admin_region gazetteer (services.geocode) so it lands on the map;
  4. each becomes a SecondaryDisaster keyed to the selected typhoon by intl_id
     (an exact match — no name-guessing, since the user picked the storm).

API:  https://api.gdeltproject.org/api/v2/doc/doc   (mode=ArtList, format=json)
DOC 2.0 coverage begins ~2017; older storms simply return nothing.

Offline test:
  python -m crawler.sources.international.disaster.civilian.gdelt --preview \
      --name Bavi --name-cn 巴威 --start 2020-08-24 --end 2020-08-28
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx

_BACKEND = os.path.dirname(os.path.abspath(__file__))
while os.path.basename(_BACKEND) != "backend" and os.path.dirname(_BACKEND) != _BACKEND:
    _BACKEND = os.path.dirname(_BACKEND)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from crawler.sources._shared.disaster_common import (  # noqa: E402
    DisasterRec, classify_type, extract_casualties, extract_loss_usd,
)

API = "https://api.gdeltproject.org/api/v2/doc/doc"

# Damage terms in EN / 中文 / 日本語 — at least one must co-occur with the storm
# name for an article to count as 受灾情报 (rather than a mere forecast mention).
_DAMAGE_TERMS = (
    "flood", "flooding", "landslide", "casualties", "death", "killed", "damage",
    "evacuat", "inundat", "storm surge",
    "洪水", "内涝", "山洪", "滑坡", "泥石流", "死亡", "遇难", "伤亡", "受灾",
    "被淹", "损失", "倒塌", "停电", "转移",
    "浸水", "洪水", "土砂", "被害", "死者", "行方不明", "冠水", "決壊", "避難",
)

# News accrues before landfall (warnings) and for up to two weeks after (tolls,
# reconstruction) — widen the storm's own window to catch it.
_PAD_BEFORE = timedelta(days=2)
_PAD_AFTER = timedelta(days=14)


def _gdelt_dt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S")


def _quote(term: str) -> str:
    """Wrap a term in quotes so multi-word / CJK names stay one phrase."""
    return f'"{term}"' if (" " in term or any(ord(c) > 127 for c in term)) else term


def _or_group(terms) -> str:
    """Join terms with OR. GDELT rejects parentheses around a *single* term
    ("Parentheses may only be used around OR'd statements."), so only wrap the
    group when it actually holds more than one term."""
    parts = [_quote(t) for t in terms if t]
    joined = " OR ".join(parts)
    return f"({joined})" if len(parts) > 1 else joined


def build_query(names: list[str]) -> str:
    """`name` or `(name1 OR name2 …)` — the storm's name variants, nothing else.

    The damage filter deliberately does NOT go into the query. GDELT caps how
    long a query may be ("Your query was too short or too long."), and OR-ing the
    thirty-odd EN/中文/日本語 damage terms blows straight past it, so every search
    came back empty. Filtering the returned headlines locally via
    `is_damage_related` has no length ceiling and is strictly more expressive.

    Parentheses are only added for a real OR — GDELT rejects them around a single
    term ("Parentheses may only be used around OR'd statements.")."""
    return _or_group(names)


def is_damage_related(title: str) -> bool:
    """True when a headline mentions actual impact rather than a mere forecast.

    Applied to GDELT results locally (see `build_query`). EN terms match
    case-insensitively; CJK terms are plain substrings."""
    low = title.lower()
    return any((t.lower() in low) if t.isascii() else (t in title)
               for t in _DAMAGE_TERMS)


def fetch_articles(query: str, start: datetime, end: datetime,
                   maxrecords: int = 75, timeout: float = 60.0,
                   retries: int = 3, backoff: float = 6.0,
                   emit=lambda m: None) -> list[dict]:
    """GDELT ArtList articles for `query` within [start, end].

    GDELT rate-limits to roughly one request per 5 s per IP and answers 429 when
    exceeded, so a 429 is retried after a short wait (backoff) rather than
    failing the whole crawl. Other network errors raise; the caller skips."""
    params = {
        "query": query,
        "mode": "ArtList",
        "format": "json",
        "maxrecords": str(max(1, min(maxrecords, 250))),
        "sort": "DateDesc",
        "startdatetime": _gdelt_dt(start),
        "enddatetime": _gdelt_dt(end),
    }
    with httpx.Client(timeout=timeout, follow_redirects=True,
                      headers={"User-Agent": "typhoon-mmkb/0.1"}) as c:
        for attempt in range(1, retries + 1):
            r = c.get(API, params=params)
            if r.status_code == 429:
                if attempt < retries:
                    emit(f"  GDELT 触发限流(429)，{backoff:.0f}s 后重试（{attempt}/{retries - 1}）…")
                    time.sleep(backoff)
                    continue
                # Persistent rate-limit (GDELT allows ~1 request / 5 s per IP):
                # don't abort the crawl — report it and return nothing this run.
                emit("  GDELT 持续限流(429)，本次未取到文章；请稍后（约 1 分钟）再试。")
                return []
            r.raise_for_status()
            body = r.text.strip()
            if not body:
                return []
            # GDELT reports query errors as HTTP 200 with a plain-text body (e.g.
            # "Parentheses may only be used around OR'd statements."). Silently
            # treating that as "no results" hides a broken query behind a
            # successful-looking "0 篇文章", so surface it instead.
            if not body.startswith("{"):
                emit(f"  GDELT 拒绝了检索式：{body[:200]}")
                return []
            try:
                return r.json().get("articles", []) or []
            except Exception:  # noqa: BLE001 — malformed JSON means no results
                emit("  GDELT 返回了无法解析的响应，本次未取到文章。")
                return []
    return []


def _parse_seendate(s: str | None) -> datetime | None:
    if not s:
        return None
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%d%H%M%S"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def parse_articles(articles: list[dict], intl_id: str) -> list[DisasterRec]:
    """Turn GDELT articles into geocoded, storm-attributed DisasterRecs.

    Import of the geocoder is deferred so this module can be imported (and
    unit-tested for query building) without a live DB."""
    from services import geocode  # noqa: E402  (deferred: needs the DB)

    recs: list[DisasterRec] = []
    seen_urls: set[str] = set()
    for a in articles:
        url = a.get("url")
        title = (a.get("title") or "").strip()
        if not url or not title or url in seen_urls:
            continue
        seen_urls.add(url)
        domain = a.get("domain") or "GDELT"
        ts = _parse_seendate(a.get("seendate"))
        hit = geocode.geocode(title)
        recs.append(DisasterRec(
            intl_id=intl_id,
            disaster_type=classify_type(title),
            description=f"[{domain}] {title}"[:800],
            source="GDELT",
            event_time=ts,
            casualties=extract_casualties(title),
            economic_loss_usd=extract_loss_usd(title),
            source_url=url,
            lat=hit.lat if hit else None,
            lon=hit.lon if hit else None,
            region_name=hit.region_name if hit else None,
        ))
    return recs


def collect(typhoon: dict, maxrecords: int = 75, emit=lambda m: None) -> list[DisasterRec]:
    """Search GDELT for news about one typhoon and return geocoded DisasterRecs.

    `typhoon` is a plain dict: {intl_id, name, name_cn, name_jp, start_time,
    end_time, season_year}. Name variants are OR-ed; the date window is the
    storm's own window padded, or the whole season if the window is unknown."""
    intl_id = typhoon["intl_id"]
    names = [n for n in (typhoon.get("name"), typhoon.get("name_cn"),
                         typhoon.get("name_jp")) if n]
    if not names:
        emit(f"  {intl_id}: 无可用台风名，无法检索。")
        return []

    start = typhoon.get("start_time")
    end = typhoon.get("end_time")
    if start is None or end is None:
        year = typhoon.get("season_year") or datetime.now(timezone.utc).year
        start = start or datetime(year, 1, 1, tzinfo=timezone.utc)
        end = end or datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    win_start = start - _PAD_BEFORE
    win_end = end + _PAD_AFTER

    query = build_query(names)
    emit(f"  GDELT 检索：{'/'.join(names)}  窗口 {win_start:%Y-%m-%d}…{win_end:%Y-%m-%d}")
    try:
        articles = fetch_articles(query, win_start, win_end,
                                  maxrecords=maxrecords, emit=emit)
    except Exception as e:  # noqa: BLE001
        emit(f"  GDELT 获取失败（{e}）")
        return []

    # The damage filter runs here rather than in the query — see `build_query`.
    hits = [a for a in articles if is_damage_related(a.get("title") or "")]
    recs = parse_articles(hits, intl_id)
    located = sum(1 for r in recs if r.lat is not None)
    emit(f"  GDELT：{len(articles)} 篇文章 → {len(hits)} 篇含受灾关键词 "
         f"→ {len(recs)} 条灾情（{located} 条已定位）")
    return recs


def _preview(name: str, name_cn: str | None, name_jp: str | None,
             start: str, end: str) -> None:
    st = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    en = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
    names = [n for n in (name, name_cn, name_jp) if n]
    query = build_query(names)
    print(f"[gdelt] query: {query}")
    arts = fetch_articles(query, st - _PAD_BEFORE, en + _PAD_AFTER, emit=print)
    hits = [a for a in arts if is_damage_related(a.get("title") or "")]
    print(f"[gdelt] {len(arts)} articles → {len(hits)} with a damage keyword")
    for a in hits[:15]:
        print(f"  {a.get('seendate','?')}  {a.get('domain','?'):22s} | {(a.get('title') or '')[:80]}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--name-cn", default=None)
    ap.add_argument("--name-jp", default=None)
    ap.add_argument("--start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD")
    ap.add_argument("--preview", action="store_true")
    args = ap.parse_args()
    if args.preview:
        _preview(args.name, args.name_cn, args.name_jp, args.start, args.end)
    else:
        print("Use --preview for an offline fetch; DB load runs via the 数据源 page.")
