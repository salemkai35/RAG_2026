"""Migrations SQL versionnées et minimales (sans dépendance externe).

Les fichiers ``migrations/NNN_nom.sql`` sont appliqués dans l'ordre, une seule
fois, chacun dans sa transaction. ``{{EMBEDDING_DIM}}`` est remplacé par la
dimension configurée.
"""

from __future__ import annotations

from importlib import resources

from ..config import get_settings

_TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     text PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT now()
);
"""


def available_migrations() -> list[tuple[str, str]]:
    folder = resources.files("contract_rag.db") / "migrations"
    items = sorted(p.name for p in folder.iterdir() if p.name.endswith(".sql"))
    return [(name.removesuffix(".sql"), (folder / name).read_text(encoding="utf-8")) for name in items]


def render(sql: str) -> str:
    return sql.replace("{{EMBEDDING_DIM}}", str(get_settings().embedding_dim))


def applied_migrations(conn) -> set[str]:
    conn.execute(_TRACKING_TABLE)
    conn.commit()
    return {row["version"] for row in conn.execute("SELECT version FROM schema_migrations")}


def upgrade(conn) -> list[str]:
    done = applied_migrations(conn)
    newly_applied = []
    for version, sql in available_migrations():
        if version in done:
            continue
        with conn.transaction():
            conn.execute(render(sql))
            conn.execute("INSERT INTO schema_migrations(version) VALUES (%s)", (version,))
        newly_applied.append(version)
    return newly_applied


def status(conn) -> list[tuple[str, bool]]:
    done = applied_migrations(conn)
    return [(version, version in done) for version, _ in available_migrations()]
