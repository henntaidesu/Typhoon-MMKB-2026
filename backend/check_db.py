"""Quick connectivity + capability check for the MMKB database.

Run:  conda activate MMKB && python backend/check_db.py
Prints a clean report (decodes GBK server errors) so you can confirm the
server-side setup in docs/SERVER_SETUP.md is complete.
"""
from __future__ import annotations

import psycopg

from config import DB_HOST, DB_PORT, DB_USER, DB_PASSWORD


def _decode(e: Exception) -> str:
    try:
        return str(e).encode("latin1", "ignore").decode("gbk", "ignore")
    except Exception:
        return str(e)


def main() -> None:
    print(f"connecting to {DB_HOST}:{DB_PORT} as {DB_USER} ...")
    try:
        conn = psycopg.connect(
            host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD,
            dbname="postgres", connect_timeout=8,
        )
    except Exception as e:  # noqa: BLE001
        print("  [FAIL] cannot connect:", _decode(e)[:200])
        print("  -> fix pg_hba.conf (see docs/SERVER_SETUP.md step 1)")
        return

    cur = conn.cursor()
    ver = cur.execute("SELECT version()").fetchone()[0]
    print("  [OK] connected:", ver[:50])
    avail = cur.execute(
        "SELECT name FROM pg_available_extensions WHERE name IN ('postgis','vector') ORDER BY name"
    ).fetchall()
    avail = [r[0] for r in avail]
    for ext in ("postgis", "vector"):
        state = "AVAILABLE" if ext in avail else "MISSING (install on server)"
        print(f"  extension {ext:8s}: {state}")
    dbs = [r[0] for r in cur.execute(
        "SELECT datname FROM pg_database WHERE datistemplate=false").fetchall()]
    print("  databases:", dbs)
    conn.close()
    if set(avail) >= {"postgis", "vector"}:
        print("\nReady. Next: python backend/init_db.py")
    else:
        print("\nExtensions missing. See docs/SERVER_SETUP.md steps 2-3.")


if __name__ == "__main__":
    main()
