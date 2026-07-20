# 系统构成图 (System Architecture) — 报告 1-2 / S1-2

## 分散多媒体知识库 总体构成

```mermaid
flowchart TB
  subgraph SRC["分散数据源 (Distributed Source-Nodes)"]
    A1["台风路径: 中央气象台(CMA·权威)<br/>+ 気象庁(JMA) + JTWC 官方实况"]
    A2["GDACS (UN/EC)<br/>热带气旋灾害事件 + 报道"]
    A3["Digital Typhoon (NII)<br/>卫星影像 + 灾情"]
    A4["Natural Earth<br/>国家/省 行政边界"]
    A5["GADM 4.1<br/>地级市 (admin-2)"]
    A6["受灾情报: 应急管理部灾情/消防庁/ReliefWeb<br/>+ GDELT 新闻检索"]
    A7["公共情报: 中央气象台预警/香港天文台/気象庁警報/应急响应"]
  end

  subgraph CRW["爬虫 / 元数据统合 (backend/crawler/sources/)"]
    B1["china/typhoon/cma.py (权威)<br/>japan/typhoon/jma*.py · usa/typhoon/jtwc*.py"]
    B2["gdacs.py 事件+报道"]
    B3["digital_typhoon.py"]
    B4["embed.py<br/>多语言向量化"]
    B6["naturalearth.py / gadm.py<br/>行政边界(含地级市)"]
    B7["enrich.py<br/>地理影响派生"]
    B8["load_disasters / load_public_info<br/>三级匹配挂到台风"]
    B5["pipeline.py 编排"]
  end

  subgraph DB["知识库 PostgreSQL 18 @ 10.0.10.20"]
    D1["typhoon (含 embedding)"]
    D2["track_point<br/>PostGIS geom (时空间核心)"]
    D3["affected_region 多边形"]
    D4["secondary_disaster 受灾情报<br/>PostGIS geom + embedding"]
    D8["public_info 公共情报<br/>PostGIS geom + embedding"]
    D5["media_asset 多媒体元数据"]
    D6["admin_region 行政边界<br/>国家/省/地级市"]
    D7["typhoon_region_impact / landfall<br/>地理影响事实"]
    DE["扩展: PostGIS + pgvector"]
  end

  subgraph API["后端 FastAPI (backend/)"]
    E1["属性查询 /typhoons"]
    E2["时空间查询 /search/spatiotemporal"]
    E3["语义查询 /search/semantic<br/>(台风+受灾情报+公共情报)"]
    E4["结合查询 /search/hybrid"]
    E5["GeoJSON 轨迹/灾害/影响区/登陆"]
    E6["地理影响聚合 /stats/*"]
    E7["公共情报 /public-info<br/>/typhoons/{id}/public-info"]
  end

  subgraph UI["前端 Vue3 + Leaflet (frontend/)"]
    F1["地图: 路径(强度着色)+灾害点+影响区+登陆点"]
    F2["时间轴回放 (走势)"]
    F3["语义联想检索框"]
    F4["详情: 强度曲线 + 受影响国家 + 灾害列表"]
    F5["统计(交互): 三级分级地图(国家/省/地级市)<br/>点击区域→画出相关台风路径 + 柱状图"]
  end

  A1-->B1-->B5
  A2-->B2-->B5
  A3-->B3-->B5
  A4-->B6-->B5
  A5-->B6
  A6-->B8
  A7-->B8
  B8-->B5
  B5-->B4
  B5-->B7
  B5-->DB
  B4-->DB
  B7-->DB
  DB-->API
  API-->UI
```

## 三层计算模型 (对应课程「時間的·空間的·意味的 選択·結合」)

```mermaid
flowchart LR
  Q["查询 Query"] --> S1
  subgraph 选择 Selection
    S1["空间选择<br/>ST_Intersects(bbox)"]
    S2["时间选择<br/>obs_time ∈ [from,to]"]
    S3["语义选择<br/>embedding cosine Top-K"]
  end
  S1 --> J["结合 Join<br/>时空 ∩ 语义"]
  S2 --> J
  S3 --> J
  J --> R["结果: 关联台风 + 次生灾害"]
```

- **空间/时间** → PostGIS：`track_point.geom` 上的 GiST 索引 + `obs_time` B-tree。
- **语义** → pgvector：`typhoon.embedding` / `secondary_disaster.embedding` /
  `public_info.embedding` 上的 IVFFlat 余弦索引。
- **结合** → `/search/hybrid`：先时空过滤候选，再按语义距离排序，实现意味的结合检索。

### 语义选择的三个附加环节

纯 Top-K 余弦排序对本知识库不够用，`/search/semantic` 在其之上还做三件事：

1. **意图判定**（`services/intent.py`）——「2019」「2306」「Hagibis」是查找而非描述，
   余弦距离对「2019 这个年份」没有概念，因此改走结构化列查询。
2. **相关度阈值**（`DEFAULT_MAX_DISTANCE = 0.60`）——余弦距离没有下界，不设阈值时
   Top-K 永远返回 K 条。实测本库：有意义的查询上限约 0.55，无关查询下限约 0.75。
3. **关键词臂**——裸地名（「浙江」「甘肃」）的向量距离过不了阈值，即使库中存有含该
   字串的记录。故对短查询（≤2 词且 ≤16 字符）并行做子串匹配，其命中排在向量结果之前。
   长查询不触发：逐词 AND 会退化成词袋匹配。

三层知识（台风 / 受灾情报 / 公共情报）各自独立检索，结果分段返回。
