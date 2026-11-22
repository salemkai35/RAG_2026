"""Accès à la base métier « contrats » (jamais à la base interne d'Open WebUI)."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator

from ..config import get_settings

_pool = None
_pool_lock = threading.Lock()


def _database_url() -> str:
    url = get_settings().database_url
    if not url:
        raise RuntimeError("CRAG_DATABASE_URL n'est pas défini.")
    return url


def connect():
    """Connexion directe (CLI, scripts)."""
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(_database_url(), row_factory=dict_row, autocommit=True, connect_timeout=10)


def _get_pool():
    """Pool partagé au niveau du module : il survit aux rechargements des Tools Open WebUI."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                from psycopg.rows import dict_row
                from psycopg_pool import ConnectionPool

                _pool = ConnectionPool(
                    _database_url(),
                    min_size=1,
                    max_size=get_settings().db_pool_max_size,
                    kwargs={"row_factory": dict_row, "autocommit": True, "connect_timeout": 10},
                    open=True,
                    name="contract_rag",
                )
    return _pool


@contextmanager
def connection() -> Iterator:
    """Connexion issue du pool (Tools Open WebUI, services)."""
    with _get_pool().connection() as conn:
        yield conn
