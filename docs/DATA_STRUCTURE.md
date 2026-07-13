# 知识库数据结构定义 (Knowledge Base Data Structure) — 报告 1-3 / 1-4 / S1-1

以 SQLAlchemy ORM 数据库对象定义（见 `backend/models.py`），每张表兼具
**空间/时间特征列**（PostGIS `geom` + 时间戳）与**语义特征列**（pgvector `embedding`）。

## ER 关系

```mermaid
erDiagram
  TYPHOON ||--o{ TRACK_POINT : has
  TYPHOON ||--o{ AFFECTED_REGION : has
  TYPHOON ||--o{ SECONDARY_DISASTER : triggers
  TYPHOON ||--o{ MEDIA_ASSET : has
  TYPHOON ||--o{ TYPHOON_REGION_IMPACT : affects
  TYPHOON ||--o{ LANDFALL : makes
  ADMIN_REGION ||--o{ TYPHOON_REGION_IMPACT : "impacted by"
  ADMIN_REGION ||--o{ LANDFALL : "struck by"

  TYPHOON {
    int id PK
    string intl_id "如 2306"
    string name
    int season_year
    string category
    float max_wind_kt
    float min_pressure_hpa
    datetime start_time
    datetime end_time
    text summary_text "语义文本"
    vector embedding "384维"
  }
  TRACK_POINT {
    int id PK
    int typhoon_id FK
    datetime obs_time "时间"
    geometry geom "POINT/4326 空间"
    float lat
    float lon
    float wind_kt
    float pressure_hpa
    string grade
  }
  AFFECTED_REGION {
    int id PK
    int typhoon_id FK
    geometry geom "MULTIPOLYGON 影响范围"
    string impact_type
  }
  SECONDARY_DISASTER {
    int id PK
    int typhoon_id FK
    string disaster_type "flood|landslide|storm_surge|casualty|..."
    geometry geom "POINT/4326"
    datetime event_time
    int casualties
    float economic_loss_usd
    text description
    string region_name "受影响国家/省(源上报)"
    vector embedding "384维"
  }
  MEDIA_ASSET {
    int id PK
    int typhoon_id FK
    string kind "satellite|photo|map"
    text url
    text caption
  }
  ADMIN_REGION {
    int id PK
    string ne_id "Natural Earth 主键"
    string name
    string iso_a2
    string iso_a3
    int admin_level "0国家/1省县"
    string country "父国(admin-1分组)"
    geometry geom "MULTIPOLYGON/4326"
  }
  TYPHOON_REGION_IMPACT {
    int id PK
    int typhoon_id FK
    int admin_region_id FK
    bool passed_over "轨迹穿过"
    bool landfall "区域内有登陆"
    bool within_corridor "在影响走廊内"
    float min_distance_deg
    float max_wind_kt
    datetime landfall_time
  }
  LANDFALL {
    int id PK
    int typhoon_id FK
    int admin_region_id FK "最细区域(省优先)"
    string country "反规范化"
    datetime landfall_time
    float lat
    float lon
    float wind_kt
    geometry geom "POINT/4326"
  }
```

## 地理影响层 (Geographic Impact — 新增)

`ADMIN_REGION` 是参考行政边界，分三级（`admin_level` = 0 国家 / 1 省 / 2 地级市）：
- **国家 + 省**：**Natural Earth (public domain)**，`backend/crawler/sources/naturalearth.py`。
- **地级市（admin-2）**：**GADM 4.1（学术用途）**，`backend/crawler/sources/gadm.py`——中国 368 个地级市
  （含省份 `parent_name` 与中文名）+ 周边国家的二级行政区。
两者都经 `load.load_admin_regions` 幂等入库（按 `ne_id`）。

`backend/crawler/enrich.py` 把每个台风的**主机构轨迹**（CMA→JMA→JTWC 优先，避免多机构
重复计数）与 `ADMIN_REGION` 做 PostGIS 空间连接，派生两张事实表：
- `TYPHOON_REGION_IMPACT`：台风影响了哪些国家/省（穿过 / 走廊内 / 登陆 + 距离 + 近区峰值风速）。
- `LANDFALL`：由海入陆的离散登陆事件（`ST_Contains` 逐点判国 → outside→inside 跳变），
  登陆点吸附到海岸线，归属到最细行政区（省优先）。

这两张表回答「某台风影响了哪些国家」与「某区域被台风登陆了多少次」，
是 `/stats/*` 与 `/typhoons/{id}/countries`、`/landfalls` 的数据来源。

## 元数据 / 语义计算设计 (Metadata for Semantic Computing)

每个「知识单元」(台风、灾害事件) 由结构化元数据生成一段**可读多语言描述**
(`typhoon_summary()` / `disaster_summary()`，见 `backend/services/embedding.py`)，
再由多语言模型 `paraphrase-multilingual-MiniLM-L12-v2` 映射为 **384 维向量**，
JP/EN/CN 共享同一语义空间。检索时把自然语言查询投影到同一空间，用余弦距离做
**意味联想选择**——即课程 Coral / 意味計算 的工程实现。

> 已离线验证跨语言语义一致性：
> "severe flooding typhoon" 与 日文「洪水と土砂災害をもたらした台風」余弦相似度 ≈ **0.82**，
> 与中文「造成严重洪水和滑坡的强台风」≈ **0.73**，与无关句「a quiet day at the beach」≈ **0.10**。

## 数据示例 (Data Example — 记述内容 1-3)

来自 IBTrACS 2023 的真实解析结果（`--preview` 验证）：

| intl_id | name | year | category | max_wind_kt | min_pres | 轨迹点数 |
|---|---|---|---|---|---|---|
| 2321 | Mawar | 2023 | TY5 | 165 | 891 | 133 |
| 2333 | Doksuri | 2023 | TY4 | 130 | 928 | 87 |
| 2335 | Khanun | 2023 | TY4 | 125 | 924 | 133 |
