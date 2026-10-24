"""번호 붙은 SQL 파일(migrations/NNN_*.sql) 중 아직 적용 안 된 것만 순서대로 실행한다.

    uv run python -m filingscope.db.migrate
"""

from pathlib import Path

import psycopg

from filingscope.db import connect

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def pending(conn: psycopg.Connection) -> list[Path]:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version     text        PRIMARY KEY,
            applied_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    return [p for p in sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql")) if p.stem not in applied]


def migrate(conn: psycopg.Connection) -> list[str]:
    """적용한 파일 이름 목록을 돌려준다. 파일 하나가 실패하면 그 파일은 통째로 되돌린다."""
    done = []
    for path in pending(conn):
        with conn.transaction():
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (path.stem,))
        done.append(path.stem)
    return done


def main() -> None:
    with connect() as conn:
        conn.autocommit = True  # 파일마다 transaction()으로 묶는다
        done = migrate(conn)
    print("적용: " + (", ".join(done) if done else "없음 (모두 최신)"))


if __name__ == "__main__":
    main()
