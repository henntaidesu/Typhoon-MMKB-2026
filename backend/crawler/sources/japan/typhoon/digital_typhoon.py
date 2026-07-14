"""Digital Typhoon (NII) supplementary source.

For a given typhoon international id (e.g. '2306' -> Digital Typhoon id
'202306') fetch the summary page and extract:
  - a representative satellite cloud image URL (multimedia asset), and
  - best-effort damage / casualty figures from the page (Japanese pages).

Digital Typhoon markup changes over time, so extraction is defensive and
degrades gracefully (returns whatever it can find).

Offline test:  python crawler/sources/japan/typhoon/digital_typhoon.py --preview --id 2306 --year 2023
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup

_BACKEND = os.path.dirname(os.path.abspath(__file__))
while os.path.basename(_BACKEND) != "backend" and os.path.dirname(_BACKEND) != _BACKEND:
    _BACKEND = os.path.dirname(_BACKEND)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

BASE = "http://agora.ex.nii.ac.jp"
SUMMARY = BASE + "/digital-typhoon/summary/wnp/s/{dtid}.html.en"


@dataclass
class DTResult:
    dtid: str
    title: str | None
    image_url: str | None
    damage_text: str | None
    casualties: int | None


def dtid_for(intl_id: str, year: int) -> str:
    """'2306' + 2023 -> '202306' (Digital Typhoon storm id)."""
    num = intl_id[-2:]
    return f"{year}{num}"


def fetch_summary(dtid: str) -> DTResult:
    url = SUMMARY.format(dtid=dtid)
    with httpx.Client(timeout=45.0, follow_redirects=True,
                      headers={"User-Agent": "typhoon-mmkb/0.1 (academic)"}) as c:
        r = c.get(url)
        r.raise_for_status()
        html = r.text
    soup = BeautifulSoup(html, "lxml")

    title = soup.title.get_text(strip=True) if soup.title else None

    # Representative image: first sizeable <img> whose src looks like a typhoon image.
    image_url = None
    for img in soup.find_all("img"):
        src = img.get("src", "")
        if any(k in src for k in ("/summary/", "/globe/", "/wnp/", "image")) and src.endswith((".jpg", ".png")):
            image_url = src if src.startswith("http") else BASE + src
            break

    # Best-effort damage figures: look for 'dead', 'killed', 'casualt', '死者', '行方不明'.
    text = soup.get_text(" ", strip=True)
    casualties = None
    m = re.search(r"(\d[\d,]*)\s*(?:dead|killed|deaths|fatalities|死者|人が死亡)", text, re.I)
    if m:
        casualties = int(m.group(1).replace(",", ""))
    damage_text = None
    dm = re.search(r"([^.]{0,120}(?:damage|flood|landslide|被害|洪水|土砂)[^.]{0,120})", text, re.I)
    if dm:
        damage_text = dm.group(1).strip()

    return DTResult(dtid=dtid, title=title, image_url=image_url,
                    damage_text=damage_text, casualties=casualties)


def _preview(intl_id: str, year: int) -> None:
    dtid = dtid_for(intl_id, year)
    try:
        res = fetch_summary(dtid)
    except Exception as e:  # noqa: BLE001
        print(f"[digital_typhoon] {dtid} fetch failed: {e}")
        return
    print(f"[digital_typhoon] dtid={dtid}")
    print("  title     :", res.title)
    print("  image_url :", res.image_url)
    print("  casualties:", res.casualties)
    print("  damage    :", res.damage_text)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True, help="typhoon intl id, e.g. 2306")
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--preview", action="store_true")
    args = ap.parse_args()
    if args.preview:
        _preview(args.id, args.year)
    else:
        print("Use --preview for offline fetch; DB load runs via crawler/pipeline.py")
