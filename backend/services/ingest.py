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
        "description": "官方实况路径：每个台风实际观测走过的逐点轨迹。留空=抓取本季"
                       "（含活跃台风，实时更新）；填年份可批量收集历史数据。",
        "params": [
            {"name": "years", "type": "years",
             "label": "年份(逗号分隔/范围如2015-2024,留空=本季实况)", "default": ""},
        ],
    },
    {
        "key": "jma",
        "name": "日本气象厅 · 实况",
        "provider": "JMA / RSMC 东京",
        "kind": "台风实况",
        "description": "西北太平洋 WMO 指定官方机构 (RSMC)。抓取当前活跃台风的"
                       "「実況 / Analysis」定位作为 JMA 官方实况点。",
        "params": [],
    },
    {
        "key": "jtwc",
        "name": "JTWC · 实况",
        "provider": "JTWC 美国联合台风警报中心",
        "kind": "台风实况",
        "description": "读取 JTWC 实时警报，解析当前西太平洋活跃台风的最新定位。"
                       "尽力解析：无活跃台风或警报已撤时可能无数据（属正常）。",
        "params": [],
    },
    {
        "key": "full",
        "name": "全部官方实况源",
        "provider": "CMA + JMA + JTWC",
        "kind": "一键实况",
        "description": "依次执行三家官方机构的实况路径抓取并生成语义向量，"
                       "同一台风按编号自动合并各家的实况点。",
        "params": [],
    },
    {
        "key": "gdacs",
        "name": "GDACS 灾害事件",
        "provider": "GDACS",
        "kind": "次生灾害",
        "description": "全球灾害预警系统的热带气旋事件，按名称+年份匹配到已入库的"
                       "台风，生成次生灾害记录（风暴潮 / 风灾等）。",
        "depends": "cma",
        "params": [
            {"name": "years", "type": "years", "label": "年份(逗号分隔)",
             "default": "2026"},
        ],
    },
    {
        "key": "digital_typhoon",
        "name": "Digital Typhoon",
        "provider": "NII 情報学研究所",
        "kind": "卫星影像 / 灾情",
        "description": "为库内每个台风抓取代表性卫星云图与灾情/伤亡文本"
                       "（日文页面，尽力解析，缺失自动跳过）。",
        "depends": "cma",
        "params": [],
    },
]

_KEYS = {s["key"] for s in SOURCES}


# --- Job registry -----------------------------------------------------------
_lock = threading.Lock()
_active: str | None = None
_state: dict[str, dict] = {
    s["key"]: {"status": "idle", "message": "", "log": [],
               "counts": {}, "started_at": None, "finished_at": None}
    for s in SOURCES
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
    from crawler.sources import cma
    from crawler import load, embed as embed_mod

    years = params.get("years") or None
    emit(f"获取 CMA 中央气象台台风列表（{years or '本季实况'}）…")
    storms = cma.fetch_storms(years=years, emit=emit)
    emit(f"解析到 {len(storms)} 个台风的实况路径，写入数据库 …")
    nty, npt = load.load_agency_storms(storms, agency="CMA", authoritative=True)
    emit(f"已写入 {nty} 个台风 / {npt} 个实况点，生成语义向量 …")
    a, _ = embed_mod.backfill()
    emit(f"向量化完成：新增 {a}")
    return {"台风": nty, "实况点": npt, "新增向量": a}


def _run_jma(params: dict, emit) -> dict:
    from crawler.sources import jma
    from crawler import load, embed as embed_mod

    emit("获取 JMA 当前活跃台风 …")
    storms = jma.fetch_storms(emit=emit)
    emit(f"解析到 {len(storms)} 个台风的 JMA 实况点，写入 …")
    nty, npt = load.load_agency_storms(storms, agency="JMA", authoritative=False)
    a, _ = embed_mod.backfill()
    emit(f"完成：{nty} 台风 / {npt} 实况点")
    return {"台风": nty, "实况点": npt}


def _run_jtwc(params: dict, emit) -> dict:
    from crawler.sources import jtwc
    from crawler import load, embed as embed_mod

    emit("获取 JTWC 实时警报 …")
    storms = jtwc.fetch_storms(emit=emit)
    emit(f"解析到 {len(storms)} 个台风的 JTWC 定位，写入 …")
    nty, npt = load.load_agency_storms(storms, agency="JTWC", authoritative=False)
    a, _ = embed_mod.backfill()
    emit(f"完成：{nty} 台风 / {npt} 实况点")
    return {"台风": nty, "实况点": npt}


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


def _run_full(params: dict, emit) -> dict:
    emit("========== 1/3 CMA 实况 ==========")
    c1 = _run_cma(params, emit)
    emit("========== 2/3 JMA 实况 ==========")
    c2 = _run_jma(params, emit)
    emit("========== 3/3 JTWC 实况 ==========")
    c3 = _run_jtwc(params, emit)
    return {"台风": c1.get("台风"), "CMA实况点": c1.get("实况点"),
            "JMA实况点": c2.get("实况点"), "JTWC实况点": c3.get("实况点")}


RUNNERS = {
    "cma": _run_cma,
    "jma": _run_jma,
    "jtwc": _run_jtwc,
    "full": _run_full,
    "gdacs": _run_gdacs,
    "digital_typhoon": _run_digital_typhoon,
    "ibtracs": _run_ibtracs,
}
