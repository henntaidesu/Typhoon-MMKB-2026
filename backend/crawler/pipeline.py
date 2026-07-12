"""End-to-end ingest pipeline for the Typhoon MMKB.

Order:
  1. IBTrACS  -> typhoon + track_point + affected_region
  2. Secondary disasters (次生灾害) from official bulletins, matched to KB typhoons:
       GDACS / ReliefWeb (UN OCHA) / 中央气象台预警 (NMC) /
       应急管理部 (MEM) / 消防庁 (FDMA)
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
from sources import reliefweb, nmc_alarm, mem, fdma  # noqa: E402
import load  # noqa: E402
import embed as embed_mod  # noqa: E402


def _load_secondary(years: list[int]) -> None:
    """Ingest 次生灾害 from every official-bulletin source. Each is best-effort:
    one source failing never blocks the others."""

    def run_src(label: str, fn) -> None:
        try:
            recs = fn()
            n = load.load_disasters(recs)
            print(f"[pipeline] {label}: matched & loaded {n} disasters")
        except Exception as e:  # noqa: BLE001
            print(f"[pipeline] {label} failed: {e}")

    say = lambda m: print(f"[pipeline]{m}")  # noqa: E731

    # 2a. GDACS (per year, name-matched)
    def _gdacs():
        out = []
        for y in years:
            out += gdacs.parse_events(gdacs.fetch_tc_events(y))
        return out
    run_src("GDACS", _gdacs)

    # 2b. ReliefWeb official reports (per year, name-matched)
    run_src("ReliefWeb", lambda: reliefweb.collect(years, emit=say))

    # 2c. 中央气象台 预警 — current warnings, time/space-matched to active storms
    run_src("NMC 预警", lambda: nmc_alarm.collect(emit=say))

    # 2d. 应急管理部 灾情通报 — name/time-matched
    run_src("应急管理部", lambda: mem.collect(emit=say))

    # 2e. 消防庁 被害報 — exact intl_id match (台风第N号)
    run_src("消防庁", lambda: fdma.collect(emit=say))


def run(years: list[int], source: str = "last3years",
        with_digital_typhoon: bool = True) -> None:
    # 1. Tracks
    csv_path = ibtracs.download_csv(source)
    typhoons = ibtracs.parse(csv_path, years)
    n = load.load_typhoons(typhoons)
    print(f"[pipeline] loaded {n} typhoons ({len(years)} season(s))")

    # 2. Secondary disasters from official bulletins
    _load_secondary(years)

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
