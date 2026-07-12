# 台风多媒体知识库 · Typhoon MMKB

西太平洋台风的**分散多媒体知识库**，具备**时空间计算(PostGIS)**与**语义计算(pgvector)**能力。
课程：マルチメディア知識ベース構成論 (Musashino University, Spring 2026) · Subject-1 · 选题 (8) Disasters。

```
爬虫(IBTrACS/GDACS/Digital Typhoon) → PostgreSQL(PostGIS+pgvector) → FastAPI 后端 → Vue3+Leaflet 前端
```

## 组件

| 目录 | 说明 |
|---|---|
| `backend/` | FastAPI + SQLAlchemy ORM。`models.py` 用 ORM 数据库对象定义全部表；`init_db.py` 建库/扩展/表；`routers/` 提供台风/轨迹/灾害(GeoJSON)与语义/时空/混合查询。 |
| `backend/crawler/` | `sources/ibtracs.py` 轨迹、`sources/gdacs.py` 次生灾害、`sources/digital_typhoon.py` 卫星影像+灾情；`embed.py` 生成向量；`pipeline.py` 一键编排。 |
| `frontend/` | Vue3 + Vite + Pinia + Leaflet。地图路径(强度着色)、时间轴回放、次生灾害标记、语义联想检索框、强度曲线与详情面板。 |
| `docs/` | 服务器准备、构成图、数据结构、查询示例（报告素材）。 |

## 快速开始

前置：conda 环境 `MMKB` (Python 3.12)，数据库 `10.0.10.20` 已装 PostGIS+pgvector 且允许本机连接
（见 `docs/SERVER_SETUP.md`）。

```bash
conda activate MMKB

# 1. 安装依赖
pip install -r backend/requirements.txt -r backend/crawler/requirements.txt

# 2. 建库（ORM：扩展 + 表）
python backend/check_db.py          # 确认服务器就绪
python backend/init_db.py

# 3. 抓取 + 入库 + 向量化
python backend/crawler/pipeline.py --years 2022 2023 2024

# 4. 启动后端
cd backend && uvicorn main:app --reload --port 8000   # http://localhost:8000/docs

# 5. 启动前端（另开终端）
cd frontend && npm install && npm run dev              # http://localhost:5173
```

## 三类以上知识处理查询（对应 S1-4 / 记述内容 1-5）

1. **属性查询** `GET /typhoons?year=2023&min_wind=100`
2. **时空间查询** `GET /search/spatiotemporal?bbox=120,20,135,30&date_from=2023-07-01&date_to=2023-09-30`
3. **语义联想查询** `POST /search/semantic {"q":"造成严重洪水的强台风","k":10}`
4. **时空×语义 结合查询** `GET /search/hybrid?q=storm surge damage&bbox=120,20,135,30`

## 验证

见 `docs/` 与各模块的 `--preview`（离线自测）：
`python backend/crawler/sources/ibtracs.py --preview --years 2023`、
`python backend/crawler/sources/gdacs.py --preview --years 2023`。
