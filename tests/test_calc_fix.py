"""희석률 계산 검증 3단계 (docs/calc_criteria.md v1) — 가짜 데이터 단위 테스트. DB 없이 돈다. rcept_no는 모두 가상 값."""

from datetime import date

from filingscope.facts.cards import combine, holder_kind, unit_mismatch
from filingscope.parse.cb import old_cb_shares, period_end


def card(no, typ, dt, sb, new=None, conv=None, old=None, worst=None, preferred=False):
    return {"rcept_no": no, "type": typ, "rcept_dt": dt,
            "values": {"shares_before": sb, "new_shares": new, "conv_shares": conv, "old_cb_shares": old},
            "calc": {"worst_shares": worst if worst is not None else conv}, "flags": {"preferred_only": preferred}}


# ---- 1-1 우선주
def test_preferred_only_left_out_of_sum():
    live = [card("R1", "유상증자", "2026-01-01", 1000, new=100),
            card("R2", "유상증자", "2026-02-01", 1000, new=500, preferred=True)]
    d = combine(live, None, False)
    assert d["new_shares"] == 100 and d["max_pct"] == 9.1          # 100 / 1100
    assert d["basis_rcept_no"] == "R2"                              # 기준은 우선주 공시여도 그 shares_before(보통주)


def test_preferred_only_alone_is_null():
    d = combine([card("R1", "유상증자", "2026-01-01", 1000, new=500, preferred=True)], None, False)
    assert d["max_pct"] is None and d["worst_pct"] is None and d["basis_rcept_no"] == "R1"


# ---- 1-2 major_holder · new_major_holder
def test_holder_kind():
    assert holder_kind("최대주주") == "major"
    assert holder_kind("최대주주 본인") == "major"
    assert holder_kind("변경 예정최대주주") == "new"
    assert holder_kind("변경후 최대주주") == "new"
    assert holder_kind("최대주주 변경예정") == "new"
    for r in ("최대주주의 특수관계인", "최대주주의 최대주주", "변경 예정 최대주주의 특수관계인", "회사의 최대주주", "", None):
        assert holder_kind(r) is None


# ---- 1-3 기준 주식 수 = 가장 최근 살아 있는 공시
def test_basis_is_latest_filing_any_type():
    live = [card("C1", "CB", "2026-01-10", 1000, conv=50, old=30),
            card("R1", "유상증자", "2026-03-01", 1100, new=100)]
    d = combine(live, "C1", False)
    assert (d["shares_before"], d["basis_rcept_no"], d["basis_date"]) == (1100, "R1", "2026-03-01")
    assert d["new_shares"] == 180                                   # 100 + 50 + old 30 (기준 CB 것)


def test_same_day_cb_adds_conv_only():
    live = [card("C1", "CB", "2026-01-10", 1000, conv=50, old=30), card("C2", "CB", "2026-01-10", 1000, conv=20, old=99)]
    assert combine(live, "C2", False)["new_shares"] == 20 + 99 + 50


# ---- 1-4 전환 기간 끝난 잔량표 줄
def test_old_cb_rows_after_conversion_end_dropped():
    rows = [("1,000", "2020년 08월 13일 ~ 2024년 08월 13일", "제1회 CB"),   # 공시일 전에 끝남 → 뺌
            ("2,000", "2023.01.01 ~ 2026.12.31", "제2회 CB"),
            ("300", "-", None)]                                           # 종료일 없음 → 포함
    assert old_cb_shares(rows, date(2025, 1, 1)) == (2300, "줄 합 (전환 끝난 1줄 뺌)")
    assert old_cb_shares(rows, None) == (3300, "줄 합")          # 공시일 모르면 빼지 않음
    assert old_cb_shares([("1,000", "2020-01-01 ~ 2024-08-12", None)], date(2024, 8, 13)) == (0, "줄 합 (전환 끝난 1줄 뺌)")


def test_old_cb_rows_exchangeable_dropped():
    """교환사채(EB)는 자기주식 교환이라 새 주식이 안 생김 → 뺌. 신주인수권부사채(BW)는 넣음."""
    rows = [("200", "2025.01.01 ~ 2028.01.01", "33회 사모 신주인수권부사채"),
            ("600", "2026.01.01 ~ 2027.01.01", "34회 사모교환사채"),
            ("50", None, "제5회 EB"),
            ("70", None, "제6회 BW")]
    assert old_cb_shares(rows, date(2025, 2, 1)) == (270, "줄 합 (교환사채 2줄 뺌)")
    assert old_cb_shares([("600", None, "교환 사채")], None) == (0, "줄 합 (교환사채 1줄 뺌)")


def test_period_end():
    assert period_end("2020년 08월 13일 ~ 2024년 08월 13일") == date(2024, 8, 13)
    assert period_end("2024-08-13") is None and period_end(None) is None


# ---- 3 단위 불일치 (방향 기준)
def test_unit_mismatch_decrease():
    live = [card("A", "유상증자", "2026-01-01", 30_000_000, new=1), card("B", "CB", "2026-05-01", 3_000_000, conv=1)]
    assert unit_mismatch(live, []) == [("A", "B", "감소")]
    assert unit_mismatch([card("A", "CB", "2026-01-01", 1000), card("B", "CB", "2026-02-01", 700)], []) == []   # 1.5배 안 됨


def test_unit_mismatch_increase_vs_issued():
    live = [card("A", "CB", "2024-07-01", 2_800_000, conv=100_000), card("B", "CB", "2026-09-01", 25_000_000, conv=1)]
    assert unit_mismatch(live, [("2024-07-01", 100_000)]) == [("A", "B", "증가")]
    # 그 사이 증자로 설명되면 안 걸림: 2.8M + 12M × 1.2 ≥ 6M
    small = [card("A", "CB", "2024-07-01", 2_800_000), card("B", "CB", "2026-09-01", 6_000_000)]
    assert unit_mismatch(small, [("2025-01-01", 12_000_000)]) == []
    # B 날짜의 공시는 안 셈 (A 날짜 ≤ d < B 날짜)
    assert unit_mismatch(small, [("2026-09-01", 12_000_000)]) == [("A", "B", "증가")]


def test_mismatch_uses_latest_filing_only():
    live = [card("A", "유상증자", "2026-01-01", 30_000_000, new=5_000_000),
            card("B", "CB", "2026-05-01", 3_000_000, conv=300_000, old=200_000, worst=400_000),
            card("C", "CB", "2026-05-01", 3_000_000, conv=100_000)]
    d = combine(live, "C", True)
    assert d["basis_rcept_no"] == "C" and d["new_shares"] == 100_000   # C 1건만 (old 없음), 같은 날 B 안 넣음


def test_old_cb_rows_same_day_sibling_dropped():
    """cb-v4: 같은 날 공시한 형제 CB(잔액 · 전환가액이 같은 줄)는 dilution()에서 따로 더해지므로 old_cb_shares에서 뺌."""
    rows = [("4,687,500", "2025.01.10 ~ 2026.12.10", "45", "6,000,000,000", "1,280"),      # 형제 A
            ("4,687,500", "2025.01.10 ~ 2026.12.10", "46", "6,000,000,000", "1,280"),      # 형제 B (같은 조건)
            ("5,194,805", "2026.07.25 ~ 2028.06.25", "제17회", "10,000,000,000", "1,925")]  # 형제 아님 (금액 같아도 가액 다름)
    dropped = []
    sib = [(6_000_000_000, 1280), (6_000_000_000, 1280), (10_000_000_000, 1519)]
    assert old_cb_shares(rows, date(2023, 11, 23), sib, dropped) == (5194805, "줄 합 (형제 CB 2줄 뺌)")
    assert dropped == ["45", "46"]
    assert old_cb_shares(rows, date(2023, 11, 23)) == (14569805, "줄 합")       # 형제 없으면 그대로


def test_old_cb_sibling_needs_same_series_number():
    """우연히 금액 · 전환가액이 같은 옛 CB는 회차 숫자가 형제와 다르면 남김. 줄에 회차 숫자가 없으면 금액 · 가액만으로 뺌."""
    rows = [("1,000", None, "제1회차 사모전환사채", "5,000,000,000", "4,925"),     # 옛 CB, 형제 3회와 금액 · 가액만 같음
            ("5,000", None, "제54회 사모전환사채", "2,500,000,000", "500"),       # 옛 CB
            ("5,000", None, "제56회 사모전환사채", "2,500,000,000", "500"),       # 같은 날 형제 56회
            ("7,000", None, "전환사채", "20,000,000,000", "11,668")]              # 회차 숫자 없음 → 금액 · 가액으로
    sib = [(5_000_000_000, 4925, "3"), (2_500_000_000, 500, "56"), (20_000_000_000, 11668, "5")]
    dropped = []
    assert old_cb_shares(rows, None, sib, dropped) == (6000, "줄 합 (형제 CB 2줄 뺌)")
    assert dropped == ["제56회 사모전환사채", "전환사채"]


# ---- 알림 금액 표시 (코드가 원 → 억 · 만, 2026-10-22)
def test_won_display():
    from filingscope.facts.cards import share_pct, won_display
    assert won_display(119_988_000_000) == "1,199.9억"      # 펩트론 (LLM이 120.0억으로 씀)
    assert won_display(62_000_000_000) == "620억"           # 나노신소재 (LLM이 62억으로 씀)
    assert won_display(2_999_000_000) == "29.99억"          # 100억 미만 · 소수 2자리에서 딱 떨어짐
    assert won_display(31_374_000_000) == "313.7억"         # 100억 이상은 1자리
    assert won_display(100_000_000) == "1억" and won_display(99_990_000) == "9,999만"
    assert won_display(12_345_678) == "1,235만"
    assert won_display(0) is None and won_display(None) is None
    assert share_pct(20_000_000_000, 31_374_000_000) == 64 and share_pct(0, 10) is None and share_pct(5, None) is None
