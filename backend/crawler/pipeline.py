"""End-to-end ingest pipeline for the Typhoon MMKB.

Order:
  1. IBTrACS  -> typhoon + track_point + affected_region
  1.5 Natural Earth -> admin_region reference boundaries (once)
  2. Secondary disasters (受灾情报 = damage that occurred), matched to KB typhoons:
       GDACS / ReliefWeb (UN OCHA) / 应急管理部 (MEM) / 消防庁 (FDMA)
  2b. Public information (公共情报 = warnings/advisories authorities announce):
       中央气象台预警 (NMC) / 香港天文台 (HKO) / 気象庁警報 (JMA)
  3. Digital Typhoon -> satellite media + damage records (per typhoon)
  4. embed    -> backfill semantic vectors (typhoons + disasters + public info)
  5. enrich   -> typhoon_region_impact + landfall (geographic impact)

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
from sources import reliefweb, nmc_alarm, mem, fdma, hko, jma_warning  # noqa: E402
from sources import naturalearth  # noqa: E402
import load  # noqa: E402
import embed as embed_mod  # noqa: E402
import enrich as enrich_mod  # noqa: E402


def _load_secondary(years: list[int]) -> None:
    """Ingest 受灾情报 (damage that occurred) + 公共情报 (public information the
    authorities announced) from every official source. Each is best-effort: one
    source failing never blocks the others."""

    say = lambda m: print(f"[pipeline]{m}")  # noqa: E731

    def run_disaster(label: str, fn) -> None:
        try:
            n = load.load_disasters(fn())
            print(f"[pipeline] {label}: matched & loaded {n} disasters (受灾情报)")
        except Exception as e:  # noqa: BLE001
            print(f"[pipeline] {label} failed: {e}")

    def run_public(label: str, fn) -> None:
        try:
            n = load.load_public_info(fn())
            print(f"[pipeline] {label}: matched & loaded {n} public-info (公共情报)")
        except Exception as e:  # noqa: BLE001
            print(f"[pipeline] {label} failed: {e}")

    # --- 2. 受灾情报 (damage that occurred) --------------------------------
    # 2a. GDACS (per year, name-matched)
    def _gdacs():
        out = []
        for y in years:
            out += gdacs.parse_events(gdacs.fetch_tc_events(y))
        return out
    run_disaster("GDACS", _gdacs)

    # 2b. ReliefWeb official situation reports (per year, name-matched)
    run_disaster("ReliefWeb", lambda: reliefweb.collect(years, emit=say))

    # 2c. 应急管理部 灾情通报 — name/time-matched
    run_disaster("应急管理部", lambda: mem.collect(emit=say))

    # 2d. 消防庁 被害報 — exact intl_id match (台风第N号)
    run_disaster("消防庁", lambda: fdma.collect(emit=say))

    # --- 2b. 公共情报 (warnings/advisories authorities announce) -----------
    # 2e. 中央气象台 预警 — current warnings, time/space-matched to active storms
    run_public("NMC 预警", lambda: nmc_alarm.collect(emit=say))

    # 2f. 香港天文台 警告 — current warnings, time/space-matched
    run_public("香港天文台", lambda: hko.collect(emit=say))

    # 2g. 気象庁 気象警報 — current 警報, time/space-matched by prefecture
    run_public("気象庁警報", lambda: jma_warning.collect(emit=say))

    # 2h. 应急管理部 应急响应 — national emergency-response announcements
    run_public("应急管理部响应", lambda: mem.collect_emergency(emit=say))

    # 2i. GDACS 报道 — the official GDACS report/news page per TC event (news)
    def _gdacs_news():
        out = []
        for y in years:
            out += gdacs.parse_news(gdacs.fetch_tc_events(y))
        return out
    run_public("GDACS 报道", _gdacs_news)


def run(years: list[int], source: str = "last3years",
        with_digital_typhoon: bool = True) -> None:
    # 1. Tracks
    csv_path = ibtracs.download_csv(source)
    typhoons = ibtracs.parse(csv_path, years)
    n = load.load_typhoons(typhoons)
    print(f"[pipeline] loaded {n} typhoons ({len(years)} season(s))")

    # 1.5 Reference admin boundaries (only if not yet loaded) — needed for the
    #     geographic impact enrichment in step 5.
    if load.admin_region_count() == 0:
        recs = naturalearth.parse()
        m = load.load_admin_regions(recs)
        print(f"[pipeline] loaded {m} admin regions (countries + provinces)")

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
    nt, nd, npub = embed_mod.backfill()
    print(f"[pipeline] embedded {nt} typhoons, {nd} disasters, {npub} public-info")

    # 5. Geographic impact enrichment (affected regions + landfalls)
    try:
        gt, glf = enrich_mod.backfill()
        print(f"[pipeline] geo-enriched {gt} typhoons, {glf} landfalls")
    except Exception as e:  # noqa: BLE001 — never let enrichment abort the run
        print(f"[pipeline] geo enrichment skipped: {e}")

    print("[pipeline] done.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, nargs="+", default=[2022, 2023, 2024])
    ap.add_argument("--source", default="last3years", choices=list(ibtracs.IBTRACS_URLS))
    ap.add_argument("--no-digital-typhoon", action="store_true")
    args = ap.parse_args()
    run(args.years, args.source, not args.no_digital_typhoon)
