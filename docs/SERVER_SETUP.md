# 服务器端准备清单 —— 10.0.10.20 (PostgreSQL 18.4, Windows)

> 本机(开发机)IP = `10.0.10.10`，数据库在另一台机器 `10.0.10.20`。
> 以下三步需在 **10.0.10.20 本机**（RDP/控制台）以管理员完成。做完后回到开发机运行 `python backend/init_db.py`。

## 1. 允许开发机连接（pg_hba.conf）

现象：从 `10.0.10.10` 连接被拒 —— `no pg_hba.conf entry for host "10.0.10.10"`。

在 `10.0.10.20` 上编辑 **`<PGDATA>\pg_hba.conf`**（一般是 `C:\Program Files\PostgreSQL\18\data\pg_hba.conf`），加入一行（放在文件靠后即可）：

```
# 允许 LAN 网段以密码认证连接
host    all    all    10.0.10.0/24    scram-sha-256
```

> 若服务器认证用的是 md5，则把 `scram-sha-256` 换成 `md5`。
> 确认 `postgresql.conf` 里 `listen_addresses = '*'`（之前能连上，说明多半已是）。

生效（任选其一）：
- SQL：`SELECT pg_reload_conf();`
- 或在服务里重启 **postgresql-x64-18** 服务。

验证（在开发机）：`python backend/check_db.py` 应能连上。

## 2. 安装 pgvector（语义/向量检索）

用 **“x64 Native Tools Command Prompt for VS”**（需 Visual Studio Build Tools）：

```bat
set "PGROOT=C:\Program Files\PostgreSQL\18"
cd %TEMP%
git clone --branch v0.8.0 https://github.com/pgvector/pgvector.git
cd pgvector
nmake /F Makefile.win
nmake /F Makefile.win install
```

## 3. 安装 PostGIS（时空间检索）

优先用 **EDB Application Stack Builder**（开始菜单 → PostgreSQL 18 → Stack Builder）→
`Spatial Extensions → PostGIS x.x Bundle for PostgreSQL 18`，按向导安装。
（若 StackBuilder 尚无 PG18 条目，去 OSGeo / EnterpriseDB 下载与 **PG18** 匹配的 PostGIS Windows bundle 安装。）

## 4. 完成后（开发机）

```bash
conda activate MMKB
python backend/init_db.py     # 建库 typhoon_mmkb + 建 postgis/vector 扩展 + 建表
```

预期输出包含：`extension 'postgis' ready`、`extension 'vector' ready`、`tables: [...]`。

---

### 备选方案（若 PG18 的 PostGIS/pgvector 预编译包一时难获取）
系统代码已按“可切换存储后端”设计的余地：可在 `backend/config.py` 加开关，
将向量存 `float[]`、相似度在 Python 端计算，经纬度用数值列 + `cube/earthdistance`
做球面距离，影响区多边形存 GeoJSON 文本。功能等价、无需服务器改动。
（当前默认走 PostGIS+pgvector 原方案。）
