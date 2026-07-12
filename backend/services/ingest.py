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
SOURCES = [
    {
        "key": "ibtracs",
        "name": "IBTrACS 最佳路径",
        "provider": "NOAA / NCEI",
        "kind": "台风路径",
        "description": "西北太平洋热带气旋最佳路径：台风本体、逐点路径、"
                       "以及由路径推导的影响走廊。是整个知识库的核心数据源。",
        "params": [
            {"name": "variant", "type": "select", "label": "数据集",
             "options": ["last3years", "WP", "since1980"], "default": "last3years"},
            {"name": "years", "type": "years", "label": "年份(逗号分隔,留空=全部)",
             "default": ""},
        ],
    },
    {
        "key": "gdacs",
        "name": "GDACS 灾害事件",
        "provider": "GDACS",
        "kind": "次生灾害",
        "description": "全球灾害预警系统的热带气旋事件，按名称+年份匹配到已入库的"
                       "台风，生成次生灾害记录（风暴潮 / 风灾等）。",
        "depends": "ibtracs",
        "params": [
            {"name": "years", "type": "years", "label": "年份(逗号分隔)",
             "default": "2023,2024"},
        ],
    },
    {
        "key": "digital_typhoon",
        "name": "Digital Typhoon",
        "provider": "NII 情報学研究所",
        "kind": "卫星影像 / 灾情",
        "description": "为库内每个台风抓取代表性卫星云图与灾情/伤亡文本"
                       "（日文页面，尽力解析，缺失自动跳过）。",
        "depends": "ibtracs",
        "params": [],
    },
    {
        "key": "full",
        "name": "完整入库流程",
        "provider": "IBTrACS + GDACS + Digital Typhoon",
        "kind": "一键全量",
        "description": "依次执行三个数据源并生成语义向量，一步建成知识库。",
        "params": [
            {"name": "variant", "type": "select", "label": "IBTrACS 数据集",
             "options": ["last3years", "WP", "since1980"], "default": "last3years"},
            {"name": "years", "type": "years", "label": "年份(逗号分隔)",
             "default": "2022,2023,2024"},
        ],
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
    emit("========== 1/3 IBTrACS ==========")
    c1 = _run_ibtracs(params, emit)
    emit("========== 2/3 GDACS ==========")
    c2 = _run_gdacs(params, emit)
    emit("========== 3/3 Digital Typhoon ==========")
    c3 = _run_digital_typhoon(params, emit)
    return {**c1, **c2, **c3}


RUNNERS = {
    "ibtracs": _run_ibtracs,
    "gdacs": _run_gdacs,
    "digital_typhoon": _run_digital_typhoon,
    "full": _run_full,
}
