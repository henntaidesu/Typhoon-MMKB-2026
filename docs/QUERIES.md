# 查询设置与执行 (Queries) — 报告 1-5 / S1-4

≥3 类知识处理查询，全部用 SQLAlchemy ORM 表达（无裸 SQL）。启动后端后可在
`http://localhost:8000/docs` 交互执行。

## 1. 属性查询 (Attribute Selection)
按年份 / 名称 / 强度筛选台风。
```
GET /typhoons?year=2023&min_wind=100
```
ORM: `select(Typhoon).where(Typhoon.season_year==2023, Typhoon.max_wind_kt>=100)`

## 2. 时空间查询 (Spatio-temporal Selection)
找在某时间窗内穿过某地理范围的台风。
```
GET /search/spatiotemporal?bbox=120,20,135,30&date_from=2023-07-01&date_to=2023-09-30
```
ORM: `func.ST_Intersects(TrackPoint.geom, func.ST_MakeEnvelope(...))` + `obs_time` 过滤。

## 3. 语义联想查询 (Semantic Associative Selection)
自然语言 → 向量 → pgvector 余弦 Top-K（跨中/日/英）。
```
POST /search/semantic
{ "q": "造成严重洪水和滑坡的强台风", "k": 10 }
```
ORM: `Typhoon.embedding.cosine_distance(qvec)` 排序取 Top-K。

## 4. 时空 × 语义 结合查询 (Spatio-temporal ∩ Semantic Join)
先时空过滤候选，再语义排序 —— 体现「意味的結合」。
```
GET /search/hybrid?q=storm surge coastal damage&bbox=120,20,135,30&date_from=2023-01-01&date_to=2023-12-31
```
ORM: 子查询取时空候选 `typhoon_id`，外层 `where(Typhoon.id.in_(...))` + 余弦排序。

## 5. 统计聚合 (Aggregation, 前端图表)
```
GET /search/stats   ->  { typhoons_by_year, disasters_by_type, totals }
```

---
### GeoJSON 输出（供 Leaflet 直接渲染）
- `GET /typhoons/{id}/track` → Feature(LineString)，属性含逐点风速/气压/时间
- `GET /typhoons/{id}/disasters` → FeatureCollection(Point)
- `GET /typhoons/{id}/affected-regions` → FeatureCollection(Polygon, `ST_AsGeoJSON`)
