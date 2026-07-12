# 系统构成图 (System Architecture) — 报告 1-2 / S1-2

## 分散多媒体知识库 总体构成

```mermaid
flowchart TB
  subgraph SRC["分散数据源 (Distributed Source-Nodes)"]
    A1["IBTrACS (NOAA)<br/>西太平洋 best track"]
    A2["GDACS (UN/EC)<br/>热带气旋灾害事件"]
    A3["Digital Typhoon (NII)<br/>卫星影像 + 灾情"]
  end

  subgraph CRW["爬虫 / 元数据统合 (backend/crawler/)"]
    B1["ibtracs.py"]
    B2["gdacs.py"]
    B3["digital_typhoon.py"]
    B4["embed.py<br/>多语言向量化"]
    B5["pipeline.py 编排"]
  end

  subgraph DB["知识库 PostgreSQL 18 @ 10.0.10.20"]
    D1["typhoon (含 embedding)"]
    D2["track_point<br/>PostGIS geom (时空间核心)"]
    D3["affected_region 多边形"]
    D4["secondary_disaster<br/>PostGIS geom + embedding"]
    D5["media_asset 多媒体元数据"]
    DE["扩展: PostGIS + pgvector"]
  end

  subgraph API["后端 FastAPI (backend/)"]
    E1["属性查询 /typhoons"]
    E2["时空间查询 /search/spatiotemporal"]
    E3["语义查询 /search/semantic"]
    E4["结合查询 /search/hybrid"]
    E5["GeoJSON 轨迹/灾害/影响区"]
  end

  subgraph UI["前端 Vue3 + Leaflet (frontend/)"]
    F1["地图: 路径(强度着色)+灾害点+影响区"]
    F2["时间轴回放 (走势)"]
    F3["语义联想检索框"]
    F4["详情: 强度曲线 + 灾害列表"]
  end

  A1-->B1-->B5
  A2-->B2-->B5
  A3-->B3-->B5
  B5-->B4
  B5-->DB
  B4-->DB
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
- **语义** → pgvector：`typhoon.embedding` / `secondary_disaster.embedding` 上的 IVFFlat 余弦索引。
- **结合** → `/search/hybrid`：先时空过滤候选，再按语义距离排序，实现意味的结合检索。
