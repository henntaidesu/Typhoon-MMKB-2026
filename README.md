# 台风多媒体知识库 · Typhoon MMKB

西太平洋台风的**分散多媒体知识库**，具备**时空间计算(PostGIS)**与**语义计算(pgvector)**能力。
课程：マルチメディア知識ベース構成論 (Musashino University, Spring 2026) · Subject-1 · 选题 (8) Disasters。

```
爬虫(IBTrACS/GDACS/Digital Typhoon) → PostgreSQL(PostGIS+pgvector) → FastAPI 后端 → Vue3+Leaflet 前端
```

情报分两层，各自带 PostGIS 几何 + pgvector 语义向量、都挂在台风下：
- **受灾情报 (`secondary_disaster`)** — *已发生的损失*：死伤 / 经济损失 / 洪涝·滑坡·风暴潮事件（GDACS 事件、应急管理部灾情通报、消防庁被害報、ReliefWeb 情报）。
- **公共情报 (`public_info`)** — *当局公开发布的信息*：官方预警·警报、避难·应急响应、报道（中央气象台预警、香港天文台、気象庁警報、应急管理部应急响应、GDACS 报道）。

## 组件

| 目录 | 说明 |
|---|---|
| `backend/` | FastAPI + SQLAlchemy ORM。`models.py` 用 ORM 数据库对象定义全部表；`init_db.py` 建库/扩展/表；`routers/` 提供台风/轨迹/灾害/公共情报(GeoJSON)与语义/时空/混合查询。 |
| `backend/crawler/` | `sources/ibtracs.py` 轨迹、`sources/gdacs.py` 次生灾害、`sources/digital_typhoon.py` 卫星影像+灾情；**受灾情报**(应急管理部/消防庁/ReliefWeb) 与 **公共情报**(中央气象台预警/香港天文台/気象庁警報/GDACS报道) 分别入库；`embed.py` 生成向量；`pipeline.py` 一键编排。 |
| `frontend/` | Vue3 + Vite + Pinia + Leaflet。地图路径(强度着色)、时间轴回放、次生灾害标记、语义联想检索框、强度曲线与详情面板。 |
| `docs/` | 服务器准备、构成图、数据结构、查询示例（报告素材）。 |

