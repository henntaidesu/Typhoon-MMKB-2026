"""应急管理部 (mem.gov.cn) — official Chinese disaster-situation bulletins (灾情通报).

The Ministry of Emergency Management publishes the authoritative post-event
灾情通报 for major typhoons: death/missing tolls, people affected/evacuated and
直接经济损失. There is no clean public API, so this is a best-effort HTML scraper
over the ministry's search/listing pages; it degrades to an empty list on any
layout change (the pipeline wraps it in try/except).

Each bulletin is matched to a KB typhoon by the storm name it quotes (台风“…”),
falling back to time/space when the name is absent.

  NOTE: LISTING_URLS below target the ministry's typhoon/flood news channels.
  Verify them against the live site if results come back empty — the CMS paths
  change occasionally.

Offline test:  python crawler/sources/mem.py --preview
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timezone

import httpx

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from crawler.sources.disaster_common import (  # noqa: E402
    DisasterRec, classify_type, extract_casualties, extract_loss_usd,
    extract_typhoon_name,
)

BASE = "https://www.mem.gov.cn"
# Site full-text search (returns HTML). Keyword targets typhoon disaster bulletins.
SEARCH_URL = "https://www.mem.gov.cn/was5/web/search"
_H = {"User-Agent": "Mozilla/5.0", "Referer": BASE}

_KEYWORDS = ("台风", "洪涝", "风暴潮", "滑坡", "泥石流")
# Only keep bulletins that read like an impact report, not routine notices.
_IMPACT_HINT = ("灾情", "死亡", "失踪", "受灾", "经济损失", "转移", "倒塌")

# <a href="...">title</a>  — coarse anchor harvest from a listing/search page.
_RE_ANCHOR = re.compile(r'<a[^>]+href="([^"#]+)"[^>]*>\s*(.*?)\s*</a>', re.I | re.S)
_RE_TAG = re.compile(r"<[^>]+>")
_RE_DATE = re.compile(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})")


def _get(url: str, params: dict | None = None) -> str:
    r = httpx.get(url, headers=_H, params=params, timeout=45.0, follow_redirects=True)
    r.raise_for_status()
    b = r.content
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("gbk", "replace")


def _clean(html: str) -> str:
    return _RE_TAG.sub(" ", html or "").replace("&nbsp;", " ").strip()


def _abs(url: str) -> str:
    if url.startswith("http"):
        return url
    return BASE + ("" if url.startswith("/") else "/") + url


def _search(keyword: str) -> str:
    """Full-text search HTML for one keyword (best-effort; empty string on fail)."""
    params = {"channelid": "215308", "searchword": keyword}  # 站内搜索 channel
    try:
        return _get(SEARCH_URL, params)
    except Exception:  # noqa: BLE001
        return ""


def _harvest_links(html: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for href, inner in _RE_ANCHOR.findall(html):
        title = _clean(inner)
        if len(title) < 6 or href in seen:
            continue
        if not any(k in title for k in _KEYWORDS):
            continue
        seen.add(href)
        out.append((_abs(href), title))
    return out


def _parse_dt_from(text: str) -> datetime | None:
    m = _RE_DATE.search(text or "")
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
    except ValueError:
        return None


def _to_rec(url: str, title: str, fetch_body: bool, emit) -> DisasterRec | None:
    body = ""
    if fetch_body:
        try:
            body = _clean(_get(url))[:4000]
        except Exception as e:  # noqa: BLE001
            emit(f"  MEM 正文跳过（{e}）")
    text = f"{title}\n{body}"
    if not any(h in text for h in _IMPACT_HINT):
        return None
    return DisasterRec(
        typhoon_name=extract_typhoon_name(text),
        event_time=_parse_dt_from(text),
        disaster_type=classify_type(text, default="casualty"),
        casualties=extract_casualties(text),
        economic_loss_usd=extract_loss_usd(text),
        description=f"[应急管理部] {title}"[:800],
        source="MEM",
        source_url=url,
    )


def collect(fetch_body: bool = True, emit=lambda m: None) -> list[DisasterRec]:
    links: list[tuple[str, str]] = []
    seen: set[str] = set()
    for kw in ("台风 灾情", "台风 洪涝"):
        html = _search(kw)
        for url, title in _harvest_links(html):
            if url in seen:
                continue
            seen.add(url)
            links.append((url, title))
    recs: list[DisasterRec] = []
    for url, title in links:
        rec = _to_rec(url, title, fetch_body, emit)
        if rec:
            recs.append(rec)
    emit(f"  应急管理部: {len(links)} candidate bulletins -> {len(recs)} disaster records")
    return recs


def _preview() -> None:
    recs = collect(fetch_body=True, emit=lambda m: print(f"[mem]{m}"))
    for r in recs[:12]:
        print(f"  {r.typhoon_name or '(?)':10s} {r.disaster_type:12s} "
              f"deaths={r.casualties} loss={r.economic_loss_usd} | {r.description[:60]}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true")
    args = ap.parse_args()
    if args.preview:
        _preview()
    else:
        print("Use --preview for offline fetch; DB load runs via crawler/pipeline.py")
