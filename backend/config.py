"""Central configuration for the Typhoon MMKB backend and crawler.

All components (crawler / backend / embedding) share this config so the
database connection and embedding model are defined in exactly one place.

Nothing is hard-coded here: settings live in ``conf.ini`` next to this file.
Each value can additionally be overridden at runtime by an environment
variable. Resolution order for every setting is:

    environment variable  >  conf.ini  >  built-in fallback default
"""
from __future__ import annotations

import os
from configparser import ConfigParser
from pathlib import Path
from urllib.parse import quote_plus

# --- Load conf.ini ----------------------------------------------------------
# Located next to this module so it works regardless of the current directory.
# The path itself can be redirected with MMKB_CONF_FILE.
CONF_FILE = Path(os.getenv("MMKB_CONF_FILE", Path(__file__).with_name("conf.ini")))

_parser = ConfigParser(interpolation=None)  # interpolation off: '%' in passwords is literal
if CONF_FILE.is_file():
    _parser.read(CONF_FILE, encoding="utf-8")


def _get(section: str, option: str, env: str, default: str) -> str:
    """Return env var if set, else conf.ini value, else the fallback default."""
    env_val = os.getenv(env)
    if env_val is not None:
        return env_val
    return _parser.get(section, option, fallback=default)


# --- Database (PostgreSQL 18, with PostGIS + pgvector) -----------------------
DB_HOST = _get("database", "host", "MMKB_DB_HOST", "10.0.10.20")
DB_PORT = int(_get("database", "port", "MMKB_DB_PORT", "5432"))
DB_USER = _get("database", "user", "MMKB_DB_USER", "postgres")
DB_PASSWORD = _get("database", "password", "MMKB_DB_PASSWORD", "")
DB_NAME = _get("database", "name", "MMKB_DB_NAME", "typhoon_mmkb")

# SQLAlchemy URL using the psycopg (v3) driver, which is what the MMKB env has.
# NOTE: password is URL-encoded because it may contain reserved chars like '@'.
DATABASE_URL = (
    f"postgresql+psycopg://{DB_USER}:{quote_plus(DB_PASSWORD)}"
    f"@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)

# A separate URL pointing at the default 'postgres' database, used only to
# CREATE the target database if it does not yet exist.
MAINTENANCE_URL = (
    f"postgresql+psycopg://{DB_USER}:{quote_plus(DB_PASSWORD)}"
    f"@{DB_HOST}:{DB_PORT}/postgres"
)

# --- Semantic layer ---------------------------------------------------------
# Multilingual model so JP / EN / CN text share one 384-dim semantic space.
EMBEDDING_MODEL = _get(
    "embedding", "model", "MMKB_EMBEDDING_MODEL",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
)
EMBEDDING_DIM = int(_get("embedding", "dim", "MMKB_EMBEDDING_DIM", "384"))

# --- Spatial reference ------------------------------------------------------
SRID = int(_get("spatial", "srid", "MMKB_SRID", "4326"))  # WGS84 lat/lon

# --- External data-source credentials ---------------------------------------
# ReliefWeb API v2 requires a *pre-approved* appname since 2025-11-01 (an
# arbitrary one is rejected with HTTP 403). Register a domain/app name with
# ReliefWeb, then set it here or via the RELIEFWEB_APPNAME env var. Left at the
# placeholder default, the ReliefWeb source simply skips itself.
RELIEFWEB_APPNAME = _get("reliefweb", "appname", "RELIEFWEB_APPNAME", "")
