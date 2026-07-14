"""应急管理部 (mem.gov.cn) — official Chinese disaster-situation bulletins (灾情通报).

The Ministry of Emergency Management publishes the authoritative post-event
灾情通报 for major typhoons: death/missing tolls, people affected/evacuated and
直接经济损失. There is no clean public API, so this is a best-effort crawler over
the ministry's disaster-news listing channels; it degrades to an empty list on
any layout change (the pipeline wraps it in try/except).

Approach (rewritten 2026-07): the ministry's TRS full-text search endpoint
(/was5/web/search) returns a 404 stub for guessed channelids, so instead we walk
the **灾害事故信息** listing channel (xw/zhsgxx/) — the aggregated feed of
disaster & emergency-response bulletins — page by page (index.shtml,
index_1.shtml …). Each article link matches ``tYYYYMMDD_<id>.shtml``; the date is
taken from that filename (reliable) and the storm name / impact figures are mined
from the title + body. Only bulletins that read like an impact report (灾情 /
死亡 / 受灾 / 经济损失 / 转移 …) are kept.

Each bulletin is matched to a KB typhoon by the storm name it quotes (台风“…”),
falling back to time/space when the name is absent.

Offline test:  python crawler/sources/china/disaster/government/mem.py --preview
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx

_BACKEND = os.path.dirname(os.path.abspath(__file__))
while os.path.basename(_BACKEND) != "backend" and os.path.dirname(_BACKEND) != _BACKEND:
    _BACKEND = os.path.dirname(_BACKEND)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from crawler.sources._shared.disaster_common import (  # noqa: E402
    DisasterRec, classify_type, extract_casualties, extract_loss_usd,
    extract_typhoon_name,
)
from crawler.sources._shared.public_common import INFO_EVACUATION, PublicInfoRec  # noqa: E402

BASE = "https://www.mem.gov.cn"
# 灾害事故信息 — the aggregated disaster / emergency-response bulletin channel.
# (Add more listing roots here if a wider sweep is wanted; each is paginated.)
LISTING_URLS = ("https://www.mem.gov.cn/xw/zhsgxx/",)
_H = {"User-Agent": "Mozilla/5.0", "Referer": BASE}

# Titles must mention a typhoon-related hazard to be a candidate at all.
_KEYWORDS = ("台风", "洪涝", "风暴潮", "滑坡", "泥石流", "防台", "防汛")
# A bulletin that reads like an impact report (受灾情报 = damage that occurred).
_IMPACT_HINT = ("灾情", "死亡", "失踪", "受灾", "经济损失", "转移", "倒塌", "遇难", "被困")
# A bulletin that reads like an emergency-response / deployment announcement
# (公共情报 = what the authority is doing about it), the bulk of this channel.
_RESPONSE_HINT = ("应急响应", "响应级别", "工作组", "调度", "部署", "指导", "启动", "预警")

# <a href="…tYYYYMMDD_<id>.shtml">title</a> — the article-link shape on a listing.
_RE_ARTICLE = re.compile(
    r'<a[^>]+href="([^"#]*t(\d{8})_\d+\.s?html?)"[^>]*>\s*(.*?)\s*</a>', re.I | re.S
)
_RE_TAG = re.compile(r"<[^>]+>")
# A trailing publish stamp the listing appends after the title, e.g.
# "…四级应急响应 2026-07-11 12:28" — stripped so the title is just the headline.
_RE_TRAIL_DATE = re.compile(r"\s*20\d{2}[-/]\d{1,2}[-/]\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?\s*$")
# How many listing pages to walk per channel (index.shtml + index_1…N.shtml).
_MAX_PAGES = 6


def _get(url: str) -> str:
    r = httpx.get(url, headers=_H, timeout=45.0, follow_redirects=True)
    r.raise_for_status()
    b = r.content
    # mem.gov.cn serves UTF-8; fall back to GBK only if that fails.
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("gbk", "replace")


def _clean(html: str) -> str:
    return _RE_TAG.sub(" ", html or "").replace("&nbsp;", " ").replace("　", " ").strip()


def _page_urls(listing: str) -> list[str]:
    """The listing page + its paginated siblings index_1.shtml … index_N.shtml."""
    root = listing if listing.endswith("/") else listing + "/"
    urls = [root]  # index.shtml is served at the bare directory
    urls += [urljoin(root, f"index_{i}.shtml") for i in range(1, _MAX_PAGES)]
    return urls


def _date_from_name(yyyymmdd: str) -> datetime | None:
    try:
        return datetime.strptime(yyyymmdd, "%Y%m%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _harvest_links(html: str, page_url: str) -> list[tuple[str, str, datetime | None]]:
    """(absolute_url, title, date) for every typhoon-related article on a page."""
    out: list[tuple[str, str, datetime | None]] = []
    for href, ymd, inner in _RE_ARTICLE.findall(html):
        title = _RE_TRAIL_DATE.sub("", _clean(inner))
        if len(title) < 6:
            continue
        if not any(k in title for k in _KEYWORDS):
            continue
        out.append((urljoin(page_url, href), title, _date_from_name(ymd)))
    return out


# Per-run body cache so disaster + emergency collectors don't double-fetch.
_BODY_CACHE: dict[str, str] = {}


def _body(url: str, fetch_body: bool, emit) -> str:
    if not fetch_body:
        return ""
    if url not in _BODY_CACHE:
        try:
            _BODY_CACHE[url] = _clean(_get(url))[:4000]
        except Exception as e:  # noqa: BLE001
            emit(f"  MEM 正文跳过（{e}）")
            _BODY_CACHE[url] = ""
    return _BODY_CACHE[url]


def _gather(emit) -> list[tuple[str, str, datetime | None]]:
    """(url, title, date) for every typhoon-related bulletin across the listing
    channels + their paginated pages."""
    candidates: list[tuple[str, str, datetime | None]] = []
    seen: set[str] = set()
    for listing in LISTING_URLS:
        for page_url in _page_urls(listing):
            try:
                html = _get(page_url)
            except Exception as e:  # noqa: BLE001
                emit(f"  MEM 列表跳过 {page_url}（{e}）")
                continue
            found = _harvest_links(html, page_url)
            for url, title, dt in found:
                if url in seen:
                    continue
                seen.add(url)
                candidates.append((url, title, dt))
            if not found:
                break  # ran past the last populated page for this channel
    return candidates


def collect(fetch_body: bool = True, emit=lambda m: None) -> list[DisasterRec]:
    """受灾情报 — only bulletins that quote actual damage (灾情 / 死亡 / 受灾 /
    经济损失 …). Post-event 灾情通报 with casualty or loss figures land here."""
    candidates = _gather(emit)
    recs: list[DisasterRec] = []
    for url, title, dt in candidates:
        text = f"{title}\n{_body(url, fetch_body, emit)}"
        if not any(h in text for h in _IMPACT_HINT):
            continue
        recs.append(DisasterRec(
            typhoon_name=extract_typhoon_name(text),
            event_time=dt,
            disaster_type=classify_type(text, default="casualty"),
            casualties=extract_casualties(text),
            economic_loss_usd=extract_loss_usd(text),
            description=f"[应急管理部] {title}"[:800],
            source="MEM",
            source_url=url,
        ))
    emit(f"  应急管理部灾情: {len(candidates)} candidates -> {len(recs)} disaster records (受灾情报)")
    return recs


def collect_emergency(fetch_body: bool = False, emit=lambda m: None) -> list[PublicInfoRec]:
    """公共情报 — the emergency-response / deployment announcements that make up
    the bulk of this channel (国家救灾应急响应, 防汛防台风响应级别, 派出工作组 …).
    These are what the authority *announces it is doing*, not a damage toll, so
    they belong in public_info (info_type=evacuation). Title-only by default —
    the response action is already in the headline."""
    candidates = _gather(emit)
    recs: list[PublicInfoRec] = []
    for url, title, dt in candidates:
        text = f"{title}\n{_body(url, fetch_body, emit)}"
        if not any(h in text for h in _RESPONSE_HINT):
            continue
        # Skip the pure damage-toll bulletins; those are handled by collect().
        if any(h in title for h in _IMPACT_HINT):
            continue
        recs.append(PublicInfoRec(
            info_type=INFO_EVACUATION,
            category=classify_type(text, default=None),
            agency="应急管理部",
            title=title[:400],
            event_time=dt,
            description=f"[应急管理部] {title}"[:800],
            source="MEM",
            source_url=url,
            typhoon_name=extract_typhoon_name(text),
        ))
    emit(f"  应急管理部应急响应: {len(candidates)} candidates -> {len(recs)} public-info (公共情报)")
    return recs


def _preview() -> None:
    dis = collect(fetch_body=True, emit=lambda m: print(f"[mem]{m}"))
    for r in dis[:8]:
        print(f"  灾情 {r.typhoon_name or '(?)':8s} {r.disaster_type:10s} "
              f"{r.event_time.date() if r.event_time else '?'} "
              f"deaths={r.casualties} loss={r.economic_loss_usd} | {r.description[:44]}")
    pub = collect_emergency(emit=lambda m: print(f"[mem]{m}"))
    for r in pub[:10]:
        print(f"  应急 {r.category or '-':10s} "
              f"{r.event_time.date() if r.event_time else '?'} | {r.description[:52]}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true")
    args = ap.parse_args()
    if args.preview:
        _preview()
    else:
        print("Use --preview for offline fetch; DB load runs via crawler/pipeline.py")
