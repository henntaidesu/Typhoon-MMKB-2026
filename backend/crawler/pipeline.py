"""End-to-end ingest pipeline for the Typhoon MMKB.

Order:
  1. IBTrACS  -> typhoon + track_point + affected_region
  2. GDACS    -> secondary_disaster (matched to KB typhoons)
  3. Digital Typhoon -> satellite media + damage records (per typhoon)
  4. embed    -> backfill semantic vectors

Idempotent: safe to re-run. Requires the DB to be initialized first
(python backend/init_db.py) and reachable.

Run:  conda activate MMKB && python crawler/pipeline.py --years 2022 2023 2024
"""
from __future__ import annotations

import argparse
import os
import sys

_HERE = os.path.dirname(__file__)
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..")))

from sources import ibtracs, gdacs, digital_typhoon  # noqa: E402
import load  # noqa: E402
import embed as embed_mod  # noqa: E402


def run(years: list[int], source: str = "last3years",
        with_digital_typhoon: bool = True) -> None:
    # 1. Tracks
    csv_path = ibtracs.download_csv(source)
    typhoons = ibtracs.parse(csv_path, years)
    n = load.load_typhoons(typhoons)
    print(f"[pipeline] loaded {n} typhoons ({len(years)} season(s))")

    # 2. GDACS secondary disasters
    total_dis = 0
    for y in years:
        try:
            feats = gdacs.fetch_tc_events(y)
            recs = gdacs.parse_events(feats)
            total_dis += load.load_disasters(recs)
        except Exception as e:  # noqa: BLE001
            print(f"[pipeline] GDACS {y} failed: {e}")
    print(f"[pipeline] loaded {total_dis} GDACS disasters (matched to KB)")

    # 3. Digital Typhoon media + damage (best-effort, per typhoon)
    if with_digital_typhoon:
        done = 0
        for rec in typhoons:
            try:
                dtid = digital_typhoon.dtid_for(rec.intl_id, rec.season_year)
                res = digital_typhoon.fetch_summary(dtid)
                load.load_media_and_damage(rec.intl_id, res)
                done += 1
            except Exception as e:  # noqa: BLE001
                print(f"[pipeline] DigitalTyphoon {rec.intl_id} skipped: {e}")
        print(f"[pipeline] processed Digital Typhoon for {done} typhoons")

    # 4. Embeddings
    nt, nd = embed_mod.backfill()
    print(f"[pipeline] embedded {nt} typhoons, {nd} disasters")
    print("[pipeline] done.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, nargs="+", default=[2022, 2023, 2024])
    ap.add_argument("--source", default="last3years", choices=list(ibtracs.IBTRACS_URLS))
    ap.add_argument("--no-digital-typhoon", action="store_true")
    args = ap.parse_args()
    run(args.years, args.source, not args.no_digital_typhoon)
