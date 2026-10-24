"""옛 사람 판정(manual/) 사례를 회귀 기대값으로 (⑨-2 4-1). 파이프라인만 돌린 DB 값과 비교한다. DB가 없으면 건너뛴다.
지금 파이프라인이 못 맞히는 사례는 xfail로 표시하고 이유를 적는다 (억지로 맞추지 않음 — 지시서 4-1-3)."""

import csv

import pytest

from filingscope.config import PROJECT_ROOT
from filingscope.db import connect

CASES = list(csv.DictReader((PROJECT_ROOT / "tests" / "fixtures" / "manual_cases.csv").open(encoding="utf-8")))
KNOWN_MISS = {
    "20250616000371": "payment-v4 불명: 현물 근거(현대바이오사이언스 주식 대용납입)는 같은 날 다른 공시(타법인주식양수결정)에만 있음",
    # 20250203000470 케이이엠텍 old_cb_shares: cb-v3(잔량표 줄 합)부터 통과 — 2026-10-22 xfail 뺌
    "20260914000244": "shares_before 자릿수 오기 — 파이프라인에 오기 판별 규칙 없음 (사람 판정 표시 없앰)",
}


@pytest.fixture(scope="module")
def conn():
    try:
        c = connect()
    except Exception:
        pytest.skip("DB 없음")
    yield c
    c.close()


@pytest.mark.parametrize("case", CASES, ids=[f"{c['rcept_no']}-{c['column']}" for c in CASES])
def test_manual_case(conn, case):
    if case["rcept_no"] in KNOWN_MISS:
        pytest.xfail(KNOWN_MISS[case["rcept_no"]])
    if case["table"] == "events":
        got = conn.execute("SELECT payment_type FROM events WHERE event_id = %s", (case["rcept_no"],)).fetchone()[0]
    elif case["table"] == "cb_values":
        got = conn.execute(f"SELECT {case['column']} FROM cb_values WHERE rcept_no = %s", (case["rcept_no"],)).fetchone()[0]
    else:
        pytest.skip(case["table"])
    assert str(got) == case["expected"]
