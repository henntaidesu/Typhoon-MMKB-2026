-- ===========================================================================
-- 台風 MMKB — サーバサイド DB オブジェクト定義
--   トリガ 3 種 / 関数 3 種 / 主要クエリ 5 種
--
--   実行:  psql -h 10.0.10.20 -U postgres -d MMKB -f docs/db_objects.sql
--   ORM(models.py) が作るテーブル・索引の *上に* 載せる層。冪等 (何度でも実行可)。
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- 1. トリガ関数 #1 — 空間整合性 (lat/lon → PostGIS geom の自動生成)
--    緯度経度だけを INSERT しても geom が必ず埋まることを DB 側で保証する。
--    アプリ側 (crawler) の書き忘れ・外部 SQL 投入・Navicat 手入力すべてに効く。
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_sync_geom() RETURNS trigger AS $$
BEGIN
    -- lon/lat が両方あり、geom が未設定 or 座標がずれている場合のみ作り直す
    IF NEW.lon IS NOT NULL AND NEW.lat IS NOT NULL THEN
        IF NEW.geom IS NULL
           OR ST_X(NEW.geom) IS DISTINCT FROM NEW.lon
           OR ST_Y(NEW.geom) IS DISTINCT FROM NEW.lat THEN
            NEW.geom := ST_SetSRID(ST_MakePoint(NEW.lon, NEW.lat), 4326);
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION fn_sync_geom() IS
    '点ジオメトリ列 geom を lon/lat から自動生成する共通トリガ関数 (SRID 4326)';

-- 同じ関数を 4 つの点テーブルで再利用する (多態トリガ)
DROP TRIGGER IF EXISTS trg_track_point_geom        ON track_point;
DROP TRIGGER IF EXISTS trg_secondary_disaster_geom ON secondary_disaster;
DROP TRIGGER IF EXISTS trg_public_info_geom        ON public_info;
DROP TRIGGER IF EXISTS trg_landfall_geom           ON landfall;

CREATE TRIGGER trg_track_point_geom
    BEFORE INSERT OR UPDATE OF lon, lat ON track_point
    FOR EACH ROW EXECUTE FUNCTION fn_sync_geom();

CREATE TRIGGER trg_secondary_disaster_geom
    BEFORE INSERT OR UPDATE OF lon, lat ON secondary_disaster
    FOR EACH ROW EXECUTE FUNCTION fn_sync_geom();

CREATE TRIGGER trg_public_info_geom
    BEFORE INSERT OR UPDATE OF lon, lat ON public_info
    FOR EACH ROW EXECUTE FUNCTION fn_sync_geom();

CREATE TRIGGER trg_landfall_geom
    BEFORE INSERT OR UPDATE OF lon, lat ON landfall
    FOR EACH ROW EXECUTE FUNCTION fn_sync_geom();


-- ---------------------------------------------------------------------------
-- 2. トリガ関数 #2 — 集約メタデータの自動ロールアップ
--    track_point を投入すると親 typhoon の
--      max_wind_kt / min_pressure_hpa / start_time / end_time
--    が自動的に広がる。文レベル (STATEMENT) + 遷移テーブルなので、
--    13 万点の一括 COPY でも UPDATE は台風数ぶんの 1 回で済む。
--    GREATEST/LEAST は PostgreSQL では NULL を無視するため初回投入も安全。
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_rollup_typhoon_intensity() RETURNS trigger AS $$
BEGIN
    UPDATE typhoon t
       SET max_wind_kt      = GREATEST(t.max_wind_kt,      s.max_wind),
           min_pressure_hpa = LEAST   (t.min_pressure_hpa, s.min_pres),
           start_time       = LEAST   (t.start_time,       s.min_time),
           end_time         = GREATEST(t.end_time,         s.max_time)
      FROM (
            SELECT typhoon_id,
                   MAX(wind_kt)      AS max_wind,
                   MIN(pressure_hpa) AS min_pres,
                   MIN(obs_time)     AS min_time,
                   MAX(obs_time)     AS max_time
              FROM new_points
             GROUP BY typhoon_id
           ) s
     WHERE t.id = s.typhoon_id;
    RETURN NULL;   -- AFTER STATEMENT トリガの戻り値は無視される
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION fn_rollup_typhoon_intensity() IS
    '経路点の投入に応じて親台風の最大風速・最低気圧・発生/消滅時刻を更新する';

DROP TRIGGER IF EXISTS trg_track_point_rollup ON track_point;
CREATE TRIGGER trg_track_point_rollup
    AFTER INSERT ON track_point
    REFERENCING NEW TABLE AS new_points
    FOR EACH STATEMENT EXECUTE FUNCTION fn_rollup_typhoon_intensity();


-- ---------------------------------------------------------------------------
-- 3. トリガ関数 #3 — 意味ベクトルの失効検知 (semantic staleness)
--    要約文の材料になる属性が変わったら summary_text と embedding を NULL に戻す。
--    crawler/embed.py は "WHERE embedding IS NULL" を再埋め込み対象とするので、
--    「属性が変われば意味ベクトルも自動的に貼り直される」保証が DB 側で完結する。
--    WHEN 句により、値が実際に変化した UPDATE だけが発火する。
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_mark_embedding_stale() RETURNS trigger AS $$
BEGIN
    NEW.summary_text := NULL;
    NEW.embedding    := NULL;    -- 再埋め込みキューに戻す印
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION fn_mark_embedding_stale() IS
    '意味要約の材料属性が変化した知識単位の embedding を失効させ再埋め込みを促す';

DROP TRIGGER IF EXISTS trg_typhoon_embedding_stale ON typhoon;
CREATE TRIGGER trg_typhoon_embedding_stale
    BEFORE UPDATE OF name, name_jp, name_cn, category, season_year,
                     max_wind_kt, min_pressure_hpa, start_time, end_time
    ON typhoon
    FOR EACH ROW
    WHEN (   OLD.name             IS DISTINCT FROM NEW.name
          OR OLD.category         IS DISTINCT FROM NEW.category
          OR OLD.season_year      IS DISTINCT FROM NEW.season_year
          OR OLD.max_wind_kt      IS DISTINCT FROM NEW.max_wind_kt
          OR OLD.min_pressure_hpa IS DISTINCT FROM NEW.min_pressure_hpa
          OR OLD.start_time       IS DISTINCT FROM NEW.start_time
          OR OLD.end_time         IS DISTINCT FROM NEW.end_time)
    EXECUTE FUNCTION fn_mark_embedding_stale();

-- 受灾情报側も同じ関数を再利用 (description / 位置が変われば失効)
DROP TRIGGER IF EXISTS trg_disaster_embedding_stale ON secondary_disaster;
CREATE TRIGGER trg_disaster_embedding_stale
    BEFORE UPDATE OF description, disaster_type, region_name, casualties
    ON secondary_disaster
    FOR EACH ROW
    WHEN (   OLD.description   IS DISTINCT FROM NEW.description
          OR OLD.disaster_type IS DISTINCT FROM NEW.disaster_type
          OR OLD.region_name   IS DISTINCT FROM NEW.region_name
          OR OLD.casualties    IS DISTINCT FROM NEW.casualties)
    EXECUTE FUNCTION fn_mark_embedding_stale();


-- ===========================================================================
-- 関数 (ストアド関数) — 「選択」三種を DB 側の API として公開する
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- 関数 #1 — 意味的選択 (Semantic Selection)
--   自然言語クエリを 384 次元に射影したベクトルを受け取り、
--   pgvector のコサイン距離で Top-K を返す。IVFFlat 索引 + probes で再現率を確保。
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_semantic_typhoons(
    p_qvec  vector(384),
    p_k     int DEFAULT 10
)
RETURNS TABLE (
    id           int,
    intl_id      varchar,
    name         varchar,
    season_year  int,
    max_wind_kt  double precision,
    summary_text text,
    distance     double precision
) AS $$
    SELECT t.id, t.intl_id, t.name, t.season_year, t.max_wind_kt, t.summary_text,
           (t.embedding <=> p_qvec)::double precision AS distance
      FROM typhoon t
     WHERE t.embedding IS NOT NULL
     ORDER BY t.embedding <=> p_qvec    -- <=> = コサイン距離 (IVFFlat 索引が効く)
     LIMIT p_k;
$$ LANGUAGE sql STABLE
   SET ivfflat.probes = 10;   -- 関数単位の GUC 設定: 再現率を確保する

COMMENT ON FUNCTION fn_semantic_typhoons(vector, int) IS
    '意味的選択: クエリベクトルとのコサイン距離で台風を Top-K 検索する';


-- ---------------------------------------------------------------------------
-- 関数 #2 — 時空間的選択 (Spatio-temporal Selection)
--   矩形 (bbox) × 時間窓を通過した台風を返す。
--   ST_Intersects は track_point.geom の GiST 索引が効く。
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_typhoons_in_bbox(
    p_min_lon double precision,
    p_min_lat double precision,
    p_max_lon double precision,
    p_max_lat double precision,
    p_from    timestamptz DEFAULT NULL,
    p_to      timestamptz DEFAULT NULL
)
RETURNS TABLE (
    id           int,
    intl_id      varchar,
    name         varchar,
    season_year  int,
    max_wind_kt  double precision,
    hit_points   bigint,
    first_hit    timestamptz
) AS $$
    SELECT t.id, t.intl_id, t.name, t.season_year, t.max_wind_kt,
           COUNT(*) AS hit_points,
           MIN(tp.obs_time) AS first_hit
      FROM typhoon t
      JOIN track_point tp ON tp.typhoon_id = t.id
     WHERE ST_Intersects(
               tp.geom,
               ST_MakeEnvelope(p_min_lon, p_min_lat, p_max_lon, p_max_lat, 4326))
       AND (p_from IS NULL OR tp.obs_time >= p_from)
       AND (p_to   IS NULL OR tp.obs_time <= p_to)
     GROUP BY t.id, t.intl_id, t.name, t.season_year, t.max_wind_kt
     ORDER BY MIN(tp.obs_time) DESC;
$$ LANGUAGE sql STABLE;

COMMENT ON FUNCTION fn_typhoons_in_bbox(double precision, double precision,
    double precision, double precision, timestamptz, timestamptz) IS
    '時空間的選択: 指定矩形を指定期間内に通過した台風を返す (GiST 索引利用)';


-- ---------------------------------------------------------------------------
-- 関数 #3 — 地理影響の集計 (上陸頻度)
--   行政区 (国 / 省 / 地級市) が台風上陸を何回受けたかを数える。
--   admin_level=0 は非正規化した国名で、1/2 は ST_Contains の空間包含で数えるため、
--   上陸点がどの粒度に紐付いていても正しく集計できる。
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_region_landfall_count(
    p_region_id int,
    p_min_year  int DEFAULT NULL,
    p_max_year  int DEFAULT NULL
) RETURNS bigint AS $$
DECLARE
    v_level int;
    v_name  varchar;
    v_count bigint;
BEGIN
    SELECT admin_level, name INTO v_level, v_name
      FROM admin_region WHERE id = p_region_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION '行政区 id=% が存在しません', p_region_id;
    END IF;

    IF v_level = 0 THEN                       -- 国: 非正規化列で高速に数える
        SELECT COUNT(*) INTO v_count
          FROM landfall l
          JOIN typhoon t ON t.id = l.typhoon_id
         WHERE l.country = v_name
           AND (p_min_year IS NULL OR t.season_year >= p_min_year)
           AND (p_max_year IS NULL OR t.season_year <= p_max_year);
    ELSE                                      -- 省/地級市: 空間包含で数える
        SELECT COUNT(*) INTO v_count
          FROM landfall l
          JOIN typhoon t      ON t.id = l.typhoon_id
          JOIN admin_region r ON r.id = p_region_id
         WHERE ST_Contains(r.geom, l.geom)
           AND (p_min_year IS NULL OR t.season_year >= p_min_year)
           AND (p_max_year IS NULL OR t.season_year <= p_max_year);
    END IF;

    RETURN COALESCE(v_count, 0);
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION fn_region_landfall_count(int, int, int) IS
    '地理影響集計: 指定行政区が受けた台風上陸回数 (粒度に応じ名称一致/空間包含)';
