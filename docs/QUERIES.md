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
GET /search/stats   ->  { typhoons_by_year, disasters_by_type, top_countries, total_typhoons, total_disasters, total_landfalls }
```

## 6. 地理影响查询 (Geographic Impact — 新增)
时空间计算的直接应用：把轨迹归属到真实行政边界，回答「影响了哪些国家」「某区域被登陆多少次」。

```
GET /stats/by-country
  -> [{admin_region_id, iso_a3, country, typhoon_count, landfall_count}]   # 各国受影响台风数 + 登陆次数
GET /stats/by-region?level=1|2&country=CN&min_year=&max_year=
  -> [{admin_region_id, name, country, parent_name, landfall_count, impact_count}]  # 某省/地级市被登陆多少次
GET /typhoons/{id}/countries
  -> [{name, iso_a3, admin_level, passed_over, landfall, within_corridor, min_distance_deg, max_wind_kt, landfall_time}]
GET /stats/region/{admin_region_id}/tracks?landfall_only=
  -> FeatureCollection(LineString)   # 影响该区域的所有台风路径（交互式：点击区域即在地图上画出）
```
数据来自 `typhoon_region_impact` / `landfall`（`backend/crawler/enrich.py` 用
`ST_Intersects` / `ST_DWithin` / `ST_Contains` 派生；主机构轨迹归一避免重复计数）。
支持三级粒度：`level=0` 国家、`level=1` 省、`level=2` 地级市（GADM）。示例真实结果：
登陆次数 China 600 > Philippines 527 > Japan 284；中国省份 广东 217 > 海南 121 > 福建 111；
中国地级市 海南 94 > 湛江 42 > 福州 26 > 茂名 25 > 阳江 22。

---
### GeoJSON 输出（供 Leaflet 直接渲染）
- `GET /typhoons/{id}/track` → Feature(LineString)，属性含逐点风速/气压/时间
- `GET /typhoons/{id}/disasters` → FeatureCollection(Point)
- `GET /typhoons/{id}/affected-regions` → FeatureCollection(Polygon, `ST_AsGeoJSON`)
- `GET /typhoons/{id}/landfalls` → FeatureCollection(Point)，登陆点(时间/风速/等级)
- `GET /stats/landfall-geojson?level=0|1&bbox=` → FeatureCollection(Polygon)，
  每个行政区带 `landfall_count`/`impact_count`，直接驱动前端「统计」页的分级(choropleth)地图
