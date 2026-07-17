"""DB connection pool — read-only against trading tables (weatherbhn_dashboard
role: SELECT only on weather_bronze_kalshi_market_snapshots /
weather_position_exits_clean / weather_station_climatology /
weather_gold_city_day_features; full DML only on its own
weatherbhn_dashboard_journal table). Same DATABASE_URL / PG_* env-var
pattern used by every other BHN trading script (see trading_core.py,
cp4_kelly_sizer.py, etc.) for consistency."""
import os
import sys

import psycopg2
import psycopg2.extras
import psycopg2.pool

_pool: psycopg2.pool.ThreadedConnectionPool | None = None


def _build_dsn() -> str:
    db_url = os.environ.get('DATABASE_URL')
    if db_url:
        return db_url
    host = os.environ.get('PG_HOST', '/var/run/postgresql')
    port = os.environ.get('PG_PORT', '5432')
    db   = os.environ.get('PG_DB', 'eventhorizon')
    user = os.environ.get('PG_USER', 'weatherbhn_dashboard')
    pwd  = os.environ.get('PG_PASSWORD', '')
    if pwd:
        import urllib.parse
        return f'postgresql://{urllib.parse.quote(user)}:{urllib.parse.quote(pwd)}@{host}:{port}/{db}'
    # Peer-auth local socket connection (no password needed when the OS
    # user matches the PG role, or pg_hba.conf trusts this path).
    return f'postgresql://{user}@/{db}?host={host}'


def init_pool(minconn: int = 1, maxconn: int = 8) -> None:
    global _pool
    if _pool is not None:
        return
    try:
        _pool = psycopg2.pool.ThreadedConnectionPool(
            minconn, maxconn, _build_dsn(),
            cursor_factory=psycopg2.extras.RealDictCursor,
        )
    except Exception as e:
        sys.exit(f'ERROR: could not connect to Postgres: {e}')


def get_conn():
    if _pool is None:
        init_pool()
    return _pool.getconn()


def put_conn(conn) -> None:
    if _pool is not None:
        _pool.putconn(conn)


class conn_cursor:
    """Context manager: checkout a pooled connection + RealDictCursor,
    commit on clean exit, rollback + re-raise on exception, always return
    the connection to the pool."""

    def __enter__(self):
        self.conn = get_conn()
        self.cur = self.conn.cursor()
        return self.cur

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.conn.commit()
            else:
                self.conn.rollback()
        finally:
            self.cur.close()
            put_conn(self.conn)
        return False
