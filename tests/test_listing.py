"""A 거르기 판정 (가짜 시세)."""

from datetime import date

from filingscope.classify.listing import check_listing, prior_trading_days

CAL = [date(2026, 2, d) for d in (2, 3, 4, 5, 6, 9, 10)]


def snap(table: dict):
    """table: {(day, market): {code: volume}}"""
    return lambda d, m, c: table.get((d, m), {}).get(c)


def test_prior_days_exclude_filing_date():
    assert prior_trading_days(CAL, date(2026, 2, 9)) == [date(2026, 2, 6)]  # 기본: 직전 1거래일 (주말 건너뜀)
    assert prior_trading_days(CAL, date(2026, 2, 3)) == [date(2026, 2, 2)]
    assert prior_trading_days(CAL, date(2026, 2, 9), n=5) == CAL[:5]


def test_traded_passes_even_if_some_days_zero():
    days = CAL[:3]
    t = {(days[0], "KOSDAQ"): {"111111": 0}, (days[1], "KOSDAQ"): {"111111": 500}, (days[2], "KOSDAQ"): {"111111": 0}}
    r = check_listing("111111", days, snap(t))
    assert r.passed and r.market == "KOSDAQ" and r.listed_days == 3 and r.traded_days == 1


def test_suspended_dropped():
    days = CAL[:3]
    t = {(d, "KOSPI"): {"005030": 0} for d in days}
    r = check_listing("005030", days, snap(t))
    assert not r.passed and r.reason == "거래정지" and r.market == "KOSPI"


def test_konex_and_not_listed_and_no_code():
    days = CAL[:2]
    t = {(d, "KONEX"): {"222222": 100} for d in days}
    assert check_listing("222222", days, snap(t)).reason == "코넥스"
    assert check_listing("033180", days, snap(t)).reason == "공시 당시 미상장"
    assert check_listing(None, days, snap(t)).reason == "비상장(종목코드 없음)"
