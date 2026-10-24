"""DB: 접속, 스키마."""

import psycopg

from filingscope.config import get_settings


def connect() -> psycopg.Connection:
    return psycopg.connect(get_settings().postgres_conninfo, connect_timeout=5)
