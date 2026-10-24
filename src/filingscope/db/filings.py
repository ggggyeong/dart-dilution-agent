import psycopg

from filingscope.classify.report_title import TitleInfo
from filingscope.models.filing import B001Filing

_B001_COLUMNS = ("rcept_no", "corp_code", "corp_name", "stock_code", "corp_cls", "report_nm", "rcept_dt")


def insert_b001_filings(conn: psycopg.Connection, filings: list[B001Filing]) -> int:
    """새로 들어간 줄 수를 돌려준다. 이미 있는 rcept_no는 건너뛴다."""
    if not filings:
        return 0
    sql = (
        f"INSERT INTO b001_filings ({', '.join(_B001_COLUMNS)}) "
        f"VALUES ({', '.join(['%s'] * len(_B001_COLUMNS))}) "
        "ON CONFLICT (rcept_no) DO NOTHING"
    )
    inserted = 0
    with conn.cursor() as cur:
        for f in filings:
            cur.execute(sql, tuple(getattr(f, c) for c in _B001_COLUMNS))
            inserted += cur.rowcount
    return inserted


def distinct_titles(conn: psycopg.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT DISTINCT report_nm FROM b001_filings ORDER BY 1")]


def replace_title_classifications(
    conn: psycopg.Connection, results: dict[str, TitleInfo], version: str
) -> None:
    """판정 결과 전체를 바꿔 쓴다. title_filings 뷰도 이 결과를 따라 바뀐다."""
    with conn.transaction():
        conn.execute("DELETE FROM title_classifications")
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO title_classifications
                   (report_nm, event_type, is_correction, report_tag, is_mixed, classifier_version)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                [(nm, t.event_type or "none", t.is_correction, t.report_tag, t.is_mixed, version)
                 for nm, t in results.items()],
            )


def insert_collect_log(conn: psycopg.Connection, **row) -> None:
    cols = list(row)
    conn.execute(
        f"INSERT INTO collect_log ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
        tuple(row.values()),
    )
