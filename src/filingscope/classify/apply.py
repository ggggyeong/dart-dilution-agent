"""b001_filings의 제목 종류 전체를 현재 분류 코드로 다시 판정해 title_classifications에 쓴다. API 호출 없음."""

import psycopg

from filingscope.classify.report_title import CLASSIFIER_VERSION, TitleInfo, classify_title
from filingscope.db.filings import distinct_titles, replace_title_classifications


def reclassify_all(conn: psycopg.Connection) -> dict[str, TitleInfo]:
    results = {nm: classify_title(nm) for nm in distinct_titles(conn)}
    replace_title_classifications(conn, results, CLASSIFIER_VERSION)
    return results
