from datetime import date

from filingscope.parse.cb import date_flag, issue_method, old_cb_shares


def test_issue_method_rules():
    assert issue_method("해외 무기명식 이권부무보증 사모 전환사채", "16,000,000", "사모") == "해외"
    assert issue_method("무기명식 무보증사모 전환사채", "3,942,000,000", "사모") == "해외"   # FRN_AMT 값 있음 (파이온엑스)
    assert issue_method("무기명식 이권부 무보증 사모 전환사채", "-", "사모") == "사모"         # AMT_UNIT USD만 있는 경우
    assert issue_method("무기명식 무보증 공모 전환사채", "-", "공모") == "공모"


def test_old_cb_shares_rows_only():
    """cb-v3: 소계는 안 쓰고 잔량표 줄 합 (아센디오처럼 '-' 줄은 건너뜀)."""
    assert old_cb_shares([("1,308,900", None, None), ("11,379,941", None, None)], date(2026, 1, 1)) == (12688841, "줄 합")
    assert old_cb_shares([("4,687,500", None, None), ("-", None, None)], date(2026, 1, 1)) == (4687500, "줄 합")     # 아센디오
    assert old_cb_shares([("-", None, None), ("-", None, None)], date(2026, 1, 1)) == (0, "없음")


def test_date_flag():
    d = date
    assert date_flag(d(2026, 8, 20), d(2026, 8, 21), d(2027, 8, 21), d(2029, 8, 21)) is None
    assert date_flag(d(2026, 8, 20), d(2029, 8, 21), d(2027, 8, 21), d(2029, 8, 21)) == "pay_date>conv_start,pay_date=maturity"   # ⑨-2
    assert date_flag(None, d(2026, 1, 1), d(2026, 1, 1), None) == "pay_date=conv_start"
