"""Data-source ingest orchestration for the '数据源' page.

Wraps the existing crawler (IBTrACS / GDACS / Digital Typhoon) so each source
can be triggered independently from the web UI and its progress polled.

Design: a crawl is long-running (network + embedding model), so it runs in a
background daemon thread. A small in-process registry tracks per-source status
and a rolling log. Only one crawl runs at a time (they share the DB and the
embedding model), so a second request while one is active gets HTTP 409.

This registry is in-memory: it is per-process state for a dev server, not a
durable job queue. It resets when the backend restarts.
"""
from __future__ import annotations

import os
import sys
import threading
import traceback
from datetime import datetime, timezone

# Make backend/ importable so 'crawler.*', 'db', 'models' resolve regardless of
# the process working directory.
_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


# --- Source catalogue (drives the cards shown in the UI) --------------------
# Track sources are the official agencies' ACTUAL (实况) real-time tracks, not
# post-season best-track. CMA is the authoritative/richest; JMA & JTWC enrich
# the same typhoon (matched by WMO number) with their own official fixes.
SOURCES = [
    {
        "key": "cma",
        "name": "中央气象台 · 实况路径",
        "provider": "CMA 中国气象局 (typhoon.nmc.cn)",
        "kind": "台风实况路径",
        "params": [],
    },
    {
        "key": "jma",
        "name": "日本气象厅 · 最佳路径",
        "provider": "JMA / RSMC 东京",
        "kind": "台风路径(官方)",
        "depends": "cma",
        "params": [],
    },
    {
        "key": "jtwc",
        "name": "JTWC · 最佳路径",
        "provider": "JTWC 美国联合台风警报中心",
        "kind": "台风路径(官方)",
        "depends": "cma",
        "params": [],
    },
    {
        "key": "gdacs",
        "name": "GDACS 灾害事件",
        "provider": "GDACS",
        "kind": "次生灾害",
        "depends": "cma",
        "params": [
            {"name": "years", "type": "years", "label": "年份(逗号分隔)",
             "default": "2026"},
        ],
    },
    {
        "key": "naturalearth",
        "name": "Natural Earth 行政边界",
        "provider": "Natural Earth (public domain)",
        "kind": "基础地理边界",
        "params": [],
    },
    {
        "key": "gadm",
        "name": "GADM 地级市边界",
        "provider": "GADM 4.1 (学术用途)",
        "kind": "基础地理边界",
        "depends": "naturalearth",
        "params": [],
    },
    {
        "key": "geo_impact",
        "name": "地理影响分析",
        "provider": "派生：轨迹 × 行政边界",
        "kind": "地理影响分析",
        "depends": "naturalearth",
        "params": [],
    },
    {
        "key": "digital_typhoon",
        "name": "Digital Typhoon",
        "provider": "NII 情報学研究所",
        "kind": "卫星影像 / 灾情",
        "depends": "cma",
        "params": [],
    },
]

# "update" is a runnable action (refresh active typhoons) but not a source card.
_KEYS = {s["key"] for s in SOURCES} | {"update"}


# --- Job registry -----------------------------------------------------------
_lock = threading.Lock()
_active: str | None = None
_state: dict[str, dict] = {
    key: {"status": "idle", "message": "", "log": [],
          "counts": {}, "started_at": None, "finished_at": None}
    for key in _KEYS
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode(e: Exception) -> str:
    """Recover GBK-encoded PostgreSQL messages from a zh-CN server."""
    try:
        return str(e).encode("latin1", "ignore").decode("gbk", "ignore")
    except Exception:
        return str(e)


def active() -> str | None:
    return _active


def get_status(key: str) -> dict:
    return _state.get(key, {})


def all_status() -> dict:
    return _state


def list_sources() -> list[dict]:
    """Catalogue + live status merged, for the cards."""
    out = []
    for s in SOURCES:
        st = _state[s["key"]]
        out.append({**s, "state": {
            "status": st["status"], "message": st["message"],
            "counts": st["counts"], "started_at": st["started_at"],
            "finished_at": st["finished_at"],
        }})
    return out


def start(key: str, params: dict | None) -> None:
    """Kick off a crawl in a background thread. Raises KeyError (unknown source)
    or RuntimeError (another crawl already running)."""
    global _active
    if key not in _KEYS:
        raise KeyError(key)
    with _lock:
        if _active is not None:
            raise RuntimeError(f"已有任务正在运行：{_active}，请等待其完成。")
        _active = key
        _state[key] = {"status": "running", "message": "启动中…", "log": [],
                       "counts": {}, "started_at": _now(), "finished_at": None}
    threading.Thread(target=_run, args=(key, params or {}), daemon=True).start()


def _run(key: str, params: dict) -> None:
    global _active
    st = _state[key]

    def emit(msg: str) -> None:
        st["log"].append(msg)
        del st["log"][:-100]  # keep last 100 lines
        st["message"] = msg
        print(f"[ingest:{key}] {msg}")

    try:
        counts = RUNNERS[key](params, emit)
        st["counts"] = counts or {}
        st["status"] = "done"
        st["message"] = "完成"
    except Exception as e:  # noqa: BLE001
        st["status"] = "error"
        st["message"] = _decode(e)
        emit("ERROR: " + _decode(e))
        traceback.print_exc()
    finally:
        st["finished_at"] = _now()
        with _lock:
            _active = None


# --- Per-source runners -----------------------------------------------------
def _run_cma(params: dict, emit) -> dict:
    """Full incremental CMA crawl in one run. Fetches EVERY typhoon in scope
    (years param, or all history 1949→now when empty) that is not already in the
    DB, plus refreshes active/just-ended storms. Already-fetched ended storms are
    skipped, so re-running stays cheap (only active ones re-download)."""
    from datetime import datetime, timezone

    from crawler.sources import cma
    from crawler import load, embed as embed_mod

    years = params.get("years") or None
    cur_year = datetime.now(timezone.utc).year
    if years:
        scope = sorted({int(y) for y in years}, reverse=True)
    else:
        scope = list(range(cur_year, cma.EARLIEST_YEAR - 1, -1))  # all history, newest first

    status_map = load.cma_status_map()  # already-fetched CMA typhoons -> is_active
    to_fetch: list[dict] = []
    refresh = 0
    seen: set[str] = set()

    emit(f"规划抓取范围：{scope[0]}…{scope[-1]}" if len(scope) > 1 else f"抓取 {scope[0]}")
    for y in scope:
        roster = cma.fetch_current_roster(emit) if y == cur_year else cma.fetch_year_roster(y, emit)
        for entry in roster:
            iid = entry["intl_id"]
            if iid in seen:
                continue
            seen.add(iid)
            st = status_map.get(iid, "absent")
            if entry["active"] or st is True:
                to_fetch.append(entry)      # active / just-ended -> always refresh
                refresh += 1
            elif st is False:
                continue                    # ended and already fetched -> skip
            else:
                to_fetch.append(entry)      # new storm (no per-run cap)

    if not to_fetch:
        emit("没有需要抓取的台风（已全部抓取完毕）。")
        return {"本次处理": 0, "库内CMA台风": len(status_map)}

    emit(f"本次将处理 {len(to_fetch)} 个台风（含刷新 {refresh}，新增 {len(to_fetch) - refresh}），逐个拉取实况 …")
    loaded = 0
    for i, entry in enumerate(to_fetch, 1):
        emit(f"  ({i}/{len(to_fetch)}) {entry['intl_id']} {entry['name'] or ''} …")
        try:
            storm = cma.fetch_view(entry)
            if storm is None:
                continue
            load.load_agency_storms([storm], agency="CMA", authoritative=True)
        except Exception as e:  # noqa: BLE001 — one bad storm must not abort the run
            emit(f"    跳过 {entry['intl_id']}（{e}）")
            continue
        loaded += 1

    emit("生成语义向量 …")
    a, _ = embed_mod.backfill()

    total = len(load.cma_status_map())
    emit(f"本次处理 {loaded} 个（刷新 {refresh}，新增历史 {loaded - refresh}）；库内共 {total} 个 CMA 台风。已全部抓取完毕。")
    return {"本次处理": loaded, "刷新": refresh, "新增历史": loaded - refresh, "库内CMA台风": total}


def _run_jma(params: dict, emit) -> dict:
    """JMA official best-track (historical, keyed by WMO number → merges onto the
    same typhoon as CMA) plus the current active storm's realtime 実況 point.
    Incremental: ended storms already carrying JMA points are skipped."""
    from datetime import datetime, timezone

    from crawler.sources import jma, jma_besttrack
    from crawler import load, embed as embed_mod

    years = params.get("years") or None
    cur_year = datetime.now(timezone.utc).year

    bt = jma_besttrack.fetch_storms(years=years, emit=emit)
    status = load.agency_status_map("JMA")
    to_load = [s for s in bt if status.get(s.intl_id) is not False]  # skip fetched+ended
    emit(f"JMA 最佳路径：共 {len(bt)}，需写入 {len(to_load)} …")
    nty = npt = 0
    for i in range(0, len(to_load), 100):
        chunk = to_load[i:i + 100]
        a, b = load.load_agency_storms(chunk, agency="JMA", authoritative=False)
        nty += a; npt += b
        emit(f"  已写入 {min(i + 100, len(to_load))}/{len(to_load)} …")

    if not years or cur_year in {int(y) for y in years}:
        emit("获取 JMA 当前活跃台风实况 …")
        rt = jma.fetch_storms(emit=emit)
        if rt:
            load.load_agency_storms(rt, agency="JMA", authoritative=False)

    emit("生成语义向量 …")
    embed_mod.backfill()
    total = len(load.agency_status_map("JMA"))
    emit(f"JMA 完成：写入 {nty} 台风 / {npt} 点；库内 JMA 台风 {total}。")
    return {"JMA台风": nty, "JMA点": npt, "库内JMA": total}


def _run_jtwc(params: dict, emit) -> dict:
    """JTWC official best-track (historical) matched to known typhoons by name,
    plus the current active storm's realtime fix. Years already carrying JTWC
    points are skipped (except the current year)."""
    from datetime import datetime, timezone

    from crawler.sources import jtwc, jtwc_besttrack
    from crawler import load, embed as embed_mod

    years = params.get("years")
    cur_year = datetime.now(timezone.utc).year
    if years:
        scope = sorted({int(y) for y in years})
    else:
        scope = list(range(jtwc_besttrack.EARLIEST_YEAR, cur_year + 1))

    done_seasons = load.agency_seasons("JTWC")
    nty = npt = unresolved = 0
    for y in scope:
        if y in done_seasons and y != cur_year:
            continue  # this season's JTWC tracks already loaded
        storms = jtwc_besttrack.fetch_storms([y], emit=emit)
        batch = []
        for s in storms:
            # Prefer a name match; fall back to JTWC's number-based key (already
            # set as s.intl_id) when the storm has one and it exists in the KB.
            iid = load.resolve_intl_id(s.name, s.season_year)
            if not iid and s.intl_id and load.typhoon_exists(s.intl_id):
                iid = s.intl_id
            if not iid:
                unresolved += 1
                continue
            s.intl_id = iid
            batch.append(s)
        if batch:
            a, b = load.load_agency_storms(batch, agency="JTWC", authoritative=False)
            nty += a; npt += b
        emit(f"  JTWC {y}: 写入 {len(batch)} 个轨迹")

    if not years or cur_year in scope:
        emit("获取 JTWC 当前活跃台风定位 …")
        rt = jtwc.fetch_storms(emit=emit)
        if rt:
            load.load_agency_storms(rt, agency="JTWC", authoritative=False)

    emit("生成语义向量 …")
    embed_mod.backfill()
    total = len(load.agency_status_map("JTWC"))
    emit(f"JTWC 完成：写入 {nty} / 点 {npt}；未匹配(名称对不上) {unresolved}；库内 JTWC {total}。")
    return {"JTWC台风": nty, "JTWC点": npt, "未匹配": unresolved, "库内JTWC": total}


def _run_ibtracs(params: dict, emit) -> dict:
    from crawler.sources import ibtracs
    from crawler import load, embed as embed_mod

    variant = params.get("variant") or "last3years"
    years = params.get("years") or None
    emit(f"下载 IBTrACS 数据集（{variant}）…")
    csv_path = ibtracs.download_csv(variant)
    emit("解析 CSV …")
    recs = ibtracs.parse(csv_path, years)
    emit(f"解析到 {len(recs)} 个台风，写入数据库 …")
    n = load.load_typhoons(recs)
    emit(f"已写入 {n} 个台风，生成语义向量 …")
    nt, nd = embed_mod.backfill()
    emit(f"向量化完成：台风 {nt}，灾害 {nd}")
    return {"台风": n, "新增向量": nt}


def _run_gdacs(params: dict, emit) -> dict:
    from crawler.sources import gdacs
    from crawler import load, embed as embed_mod

    years = params.get("years") or [datetime.now(timezone.utc).year]
    total = 0
    for y in years:
        emit(f"获取 GDACS {y} 年热带气旋事件 …")
        feats = gdacs.fetch_tc_events(y)
        recs = gdacs.parse_events(feats)
        n = load.load_disasters(recs)
        total += n
        emit(f"{y} 年：{len(feats)} 个事件 → 匹配入库 {n} 条")
    emit("生成语义向量 …")
    nt, nd = embed_mod.backfill()
    emit(f"向量化完成：灾害 {nd}")
    return {"灾害": total, "新增向量": nd}


def _run_digital_typhoon(params: dict, emit) -> dict:
    from sqlalchemy import select

    from crawler.sources import digital_typhoon
    from crawler import load, embed as embed_mod
    from db import SessionLocal
    from models import Typhoon

    with SessionLocal() as s:
        items = [(t.intl_id, t.season_year)
                 for t in s.scalars(select(Typhoon)) if t.season_year]
    if not items:
        emit("知识库暂无台风，请先运行 IBTrACS。")
        return {"处理": 0}
    done = 0
    for intl_id, year in items:
        try:
            dtid = digital_typhoon.dtid_for(intl_id, year)
            res = digital_typhoon.fetch_summary(dtid)
            load.load_media_and_damage(intl_id, res)
            done += 1
            emit(f"{intl_id}：已处理（{done}/{len(items)}）")
        except Exception as e:  # noqa: BLE001
            emit(f"{intl_id}：跳过（{e}）")
    emit("生成语义向量 …")
    nt, nd = embed_mod.backfill()
    return {"处理": done, "新增向量": nd}


def _run_naturalearth(params: dict, emit) -> dict:
    """Load Natural Earth admin-0 countries + admin-1 provinces into the
    reference table (idempotent by ne_id)."""
    from crawler.sources import naturalearth
    from crawler import load

    emit("下载并解析 Natural Earth 行政边界（国家 + 省/县）…")
    recs = naturalearth.parse()
    n0 = sum(1 for r in recs if r.admin_level == 0)
    n1 = sum(1 for r in recs if r.admin_level == 1)
    emit(f"解析到 {len(recs)} 个区域（{n0} 国家 / {n1} 省），写入数据库 …")
    n = load.load_admin_regions(recs)
    emit(f"完成：库内共 {load.admin_region_count()} 个行政区域。")
    return {"国家": n0, "省/县": n1, "库内区域": load.admin_region_count()}


def _run_gadm(params: dict, emit) -> dict:
    """Load GADM level-2 (prefecture / 地级市) boundaries for the WP landfall
    countries into admin_region (admin_level=2). Idempotent by ne_id."""
    from crawler.sources import gadm
    from crawler import load

    emit("下载并解析 GADM 地级市边界（中国 地级市 + 周边国家）…")
    recs = gadm.parse()
    emit(f"解析到 {len(recs)} 个地级市/二级行政区，写入数据库 …")
    n = load.load_admin_regions(recs)
    emit(f"完成：写入 {n} 个二级行政区；库内区域共 {load.admin_region_count()}。")
    return {"地级市/二级区": n, "库内区域": load.admin_region_count()}


def _run_geo_impact(params: dict, emit) -> dict:
    """Derive per-typhoon affected regions + landfall events by spatially joining
    tracks against the admin_region boundaries. Incremental (skips already-done)."""
    from crawler import load, enrich

    if load.admin_region_count() == 0:
        raise RuntimeError("行政边界为空：请先运行「Natural Earth 行政边界」。")
    force = bool(params.get("force"))
    emit("计算台风地理影响与登陆点（轨迹 × 行政边界）…")
    nt, nlf = enrich.backfill(force=force)
    emit(f"完成：处理 {nt} 个台风，识别 {nlf} 次登陆。")
    return {"处理台风": nt, "登陆次数": nlf}


def _run_update(params: dict, emit) -> dict:
    """Fast refresh of only the currently active (进行中) typhoons — re-pulls the
    live CMA track for each active storm plus the current JMA/JTWC realtime fix.
    Does NOT rescan history, so it returns in seconds."""
    from crawler.sources import cma, jma, jtwc
    from crawler import load, embed as embed_mod

    emit("获取当前活跃台风 …")
    active = [e for e in cma.fetch_current_roster(emit) if e["active"]]
    if not active:
        emit("当前没有进行中的台风，无需更新。")
        return {"进行中": 0}

    n = 0
    for e in active:
        emit(f"  刷新 {e['intl_id']} {e['name'] or ''} …")
        storm = cma.fetch_view(e)
        if storm is not None:
            load.load_agency_storms([storm], agency="CMA", authoritative=True)
            n += 1

    for name, mod, agency in (("JMA", jma, "JMA"), ("JTWC", jtwc, "JTWC")):
        try:
            rt = mod.fetch_storms(emit=emit)
            if rt:
                load.load_agency_storms(rt, agency=agency, authoritative=False)
        except Exception as ex:  # noqa: BLE001
            emit(f"  {name} 实时跳过（{ex}）")

    emit("生成语义向量 …")
    embed_mod.backfill()
    emit(f"更新完成：刷新了 {n} 个进行中台风。")
    return {"进行中刷新": n}


RUNNERS = {
    "cma": _run_cma,
    "jma": _run_jma,
    "jtwc": _run_jtwc,
    "gdacs": _run_gdacs,
    "naturalearth": _run_naturalearth,
    "gadm": _run_gadm,
    "geo_impact": _run_geo_impact,
    "digital_typhoon": _run_digital_typhoon,
    "ibtracs": _run_ibtracs,
    "update": _run_update,
}
