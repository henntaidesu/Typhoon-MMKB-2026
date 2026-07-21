-- ===========================================================================
-- Navicat で「そのまま」実行できる主要クエリ集
--   バインド変数 (:qvec など) は一切使わない。
--   意味検索のクエリベクトルは、DB 内に既にある embedding を代用する。
--   本番 (FastAPI) では embed('自然言語') の結果が :qvec に束縛される。
-- ===========================================================================


-- ---------------------------------------------------------------------------
-- Q1 : 属性選択 (Attribute Selection)
--   → 2302 Mawar / 2315 Bolaven / 2309 Saola (SuperTY)
-- ---------------------------------------------------------------------------
SELECT intl_id, name, category, max_wind_kt
  FROM typhoon
 WHERE season_year = 2023
   AND max_wind_kt >= 100
 ORDER BY max_wind_kt DESC;


-- ---------------------------------------------------------------------------
-- Q2 : 時空間選択 (Spatio-temporal Selection)
--   → 2306 Khanun が 147 点 (約1か月) 同海域に停滞
-- ---------------------------------------------------------------------------
SELECT t.intl_id, t.name, COUNT(*) AS hits
  FROM typhoon t
  JOIN track_point tp ON tp.typhoon_id = t.id
 WHERE ST_Intersects(tp.geom, ST_MakeEnvelope(120, 20, 135, 30, 4326))
   AND tp.obs_time BETWEEN '2023-07-01' AND '2023-09-30'
 GROUP BY 1, 2
 ORDER BY hits DESC;


-- ---------------------------------------------------------------------------
-- Q3 : 意味的選択 (Semantic Selection)
--   台風 Gloria(1999) のベクトルを問合せベクトルに使う。
--   → 9922 Gloria が距離 0、続いて他年の Gloria 群が並ぶ。
-- ---------------------------------------------------------------------------
SELECT * FROM fn_semantic_typhoons(
         (SELECT embedding FROM typhoon WHERE intl_id = '9922'), 10);

-- 関数を使わず素の SQL で書くとこうなる (等価)
SELECT t.intl_id, t.name, t.season_year,
       (t.embedding <=> q.qvec)::double precision AS distance
  FROM typhoon t,
       (SELECT embedding AS qvec FROM typhoon WHERE intl_id = '9922') q
 WHERE t.embedding IS NOT NULL
 ORDER BY t.embedding <=> q.qvec
 LIMIT 10;


-- ---------------------------------------------------------------------------
-- Q4 : 時空間 × 意味 の結合 (/search/hybrid の中心的な問合せ)
--   ① 時空間 (GiST) で候補を絞り、② その少数行にだけ意味計算を適用する。
-- ---------------------------------------------------------------------------
WITH q AS (   -- 本番は embed('高潮による沿岸被害') を束縛する箇所
    SELECT embedding AS qvec
      FROM typhoon
     WHERE embedding IS NOT NULL
     ORDER BY id
     LIMIT 1
)
SELECT t.intl_id, t.name, t.season_year,
       t.embedding <=> q.qvec AS distance
  FROM typhoon t, q
 WHERE t.id IN (                          -- ① 時空間選択で候補を作る
        SELECT tp.typhoon_id
          FROM track_point tp
         WHERE ST_Intersects(tp.geom,
                 ST_MakeEnvelope(120, 20, 135, 30, 4326))
           AND tp.obs_time BETWEEN '2023-01-01' AND '2023-12-31')
   AND t.embedding IS NOT NULL
 ORDER BY t.embedding <=> q.qvec          -- ② 意味距離で順序付け
 LIMIT 10;


-- ---------------------------------------------------------------------------
-- ストアド関数の呼び出し例
-- ---------------------------------------------------------------------------
-- 時空間選択: 東シナ海の矩形 × 2023 年夏
SELECT * FROM fn_typhoons_in_bbox(120, 20, 135, 30,
                                  '2023-07-01', '2023-09-30');

-- 地域上陸回数: id 3 = China (国) / id 173 = 広東省
SELECT fn_region_landfall_count(3)   AS china,
       fn_region_landfall_count(173) AS guangdong;
