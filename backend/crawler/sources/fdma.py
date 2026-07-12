"""消防庁 (FDMA, www.fdma.go.jp) — Japan's official typhoon damage reports (被害報).

Japan's Fire and Disaster Management Agency issues the authoritative national
被害状況 for each landfalling typhoon: 死者・行方不明・負傷者 and 住家被害. Reports
are indexed on the 災害情報 page and reference each storm by its Japanese sequence
number (台風第N号), which equals the WMO storm number — so a report for 台風第7号 in
2023 maps directly to KB typhoon intl_id "2307". That gives an exact intl_id
match instead of fuzzy name matching.

Bodies are mostly PDFs, so impact figures are mined from the HTML index titles
(best-effort); the report link is preserved for reference. Degrades to an empty
list on layout change (pipeline wraps it in try/except).

Offline test:  python crawler/sources/fdma.py --preview
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
    DisasterRec, classify_type, extract_casualties,
)

BASE = "https://www.fdma.go.jp"
INDEX_URL = "https://www.fdma.go.jp/disaster/info/"
_H = {"User-Agent": "Mozilla/5.0", "Referer": BASE}

_RE_ANCHOR = re.compile(r'<a[^>]+href="([^"#]+)"[^>]*>\s*(.*?)\s*</a>', re.I | re.S)
_RE_TAG = re.compile(r"<[^>]+>")
_RE_TC_NUM = re.compile(r"台風第?\s*(\d{1,2})\s*号")
_RE_DATE = re.compile(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})")
_RE_REIWA = re.compile(r"令和\s*(\d{1,2})\s*年")  # 令和N年 -> 2018 + N
_RE_HEISEI = re.compile(r"平成\s*(\d{1,2})\s*年")  # 平成N年 -> 1988 + N
_RE_YEAR = re.compile(r"(?:^|[/\D])(20\d{2})(?:[/年]|$)")  # bare 西暦 in title or URL path


def _get(url: str) -> str:
    r = httpx.get(url, headers=_H, timeout=45.0, follow_redirects=True)
    r.raise_for_status()
    b = r.content
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("shift_jis", "replace")


def _clean(html: str) -> str:
    return _RE_TAG.sub(" ", html or "").replace("&nbsp;", " ").strip()


def _abs(url: str) -> str:
    if url.startswith("http"):
        return url
    return BASE + ("" if url.startswith("/") else "/") + url


def _year_of(*texts: str) -> int | None:
    """Resolve the calendar year from a Japanese title/URL: full date, 令和/平成
    era, or a bare 西暦 in the title or URL path."""
    for text in texts:
        if not text:
            continue
        m = _RE_DATE.search(text)
        if m:
            return int(m.group(1))
        m = _RE_REIWA.search(text)
        if m:
            return 2018 + int(m.group(1))
        m = _RE_HEISEI.search(text)
        if m:
            return 1988 + int(m.group(1))
        m = _RE_YEAR.search(text)
        if m:
            return int(m.group(1))
    return None


def parse_index(html: str) -> list[DisasterRec]:
    recs: list[DisasterRec] = []
    seen: set[str] = set()
    for href, inner in _RE_ANCHOR.findall(html):
        title = _clean(inner)
        m = _RE_TC_NUM.search(title)
        if not m or href in seen:
            continue
        seen.add(href)
        num = int(m.group(1))
        year = _year_of(title, href)
        intl_id = f"{year % 100:02d}{num:02d}" if year else None
        ts = None
        dm = _RE_DATE.search(title) or _RE_DATE.search(href)
        if dm:
            try:
                ts = datetime(int(dm.group(1)), int(dm.group(2)), int(dm.group(3)), tzinfo=timezone.utc)
            except ValueError:
                ts = None
        recs.append(DisasterRec(
            intl_id=intl_id,
            season_year=year,
            event_time=ts,
            disaster_type=classify_type(title, default="casualty"),
            casualties=extract_casualties(title),
            description=f"[消防庁 災害情報] {title}"[:800],
            source="FDMA",
            source_url=_abs(href),
        ))
    return recs


def collect(emit=lambda m: None) -> list[DisasterRec]:
    try:
        html = _get(INDEX_URL)
    except Exception as e:  # noqa: BLE001
        emit(f"  消防庁 索引获取失败（{e}）")
        return []
    recs = parse_index(html)
    emit(f"  消防庁 災害情報: -> {len(recs)} 台风被害報 records")
    return recs


def _preview() -> None:
    recs = collect(emit=lambda m: print(f"[fdma]{m}"))
    for r in recs[:15]:
        print(f"  {r.intl_id or '(?)':6s} {r.disaster_type:12s} "
              f"deaths={r.casualties} | {r.description[:60]}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true")
    args = ap.parse_args()
    if args.preview:
        _preview()
    else:
        print("Use --preview for offline fetch; DB load runs via crawler/pipeline.py")
