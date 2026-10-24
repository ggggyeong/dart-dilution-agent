"""사실 카드 명세 7장 테스트 10개 (손 계산값, 비교노트 · 명세). DB가 없으면 건너뛴다."""

from datetime import date

import pytest

from filingscope.db import connect
from filingscope.facts import cb_facts, dilution, rights_facts

AS_OF = date(2026, 10, 19)  # 명세 작성일 (dilution의 '살아 있는 공시' 기준)


@pytest.fixture(scope="module")
def conn():
    try:
        c = connect()
    except Exception:
        pytest.skip("DB 없음")
    yield c
    c.close()


def names(card):
    return {s["name"] for s in card["signals"]}


def sig(card, name):
    return next(s for s in card["signals"] if s["name"] == name)


def test_koiz_cb(conn):
    c = cb_facts("20260522000636", conn=conn)
    assert c["calc"]["dilution_pct"] == 5.8
    assert c["signals"] == []                     # refixing · old_bonds 없음


def test_entutech_cb(conn):
    c = cb_facts("20260820000443", conn=conn)
    k = c["calc"]
    assert (k["dilution_pct"], k["worst_pct"], k["total_pct"], k["floor_pct"]) == (5.4, 7.6, 17.2, 70.0)
    assert {"refixing", "old_bonds", "public_single"} <= names(c)
    assert c["flags"]["date_flag"]


def test_hengsheng_cb(conn):
    c = cb_facts("20260213002703", conn=conn)
    k = c["calc"]
    assert (k["dilution_pct"], k["worst_pct"], k["total_pct"]) == (6.6, 9.1, 13.8)
    assert {"refixing", "old_bonds"} <= names(c)


def test_aptamer_cb(conn):
    c = cb_facts("20250909000002", conn=conn)
    assert c["flags"]["shares_before_missing"] is True
    assert (c["calc"]["dilution_pct"], c["calc"]["worst_pct"], c["calc"]["total_pct"]) == (None, None, None)
    # calc_criteria v1 D-6 (2026-10-22): "변경 예정최대주주"는 major_holder가 아니라 new_major_holder flag (명세 7장 v1.3)
    assert "major_holder" not in names(c) and c["flags"]["new_major_holder"] is True
    assert c["allottees"][0]["relation"] == "변경 예정최대주주"


def test_vuno_rights(conn):
    c = rights_facts("20260828001315", conn=conn)
    assert c["calc"]["dilution_pct"] == 31.0
    assert round(c["calc"]["raised"] / 1e8, 1) == 313.7    # 약 313.7억
    assert "debt_heavy" in names(c) and sig(c, "debt_heavy")["debt_ratio"] == 0.64


def test_neon_rights(conn):
    c = rights_facts("20260706000282", conn=conn)
    assert c["calc"]["dilution_pct"] == 59.1
    assert "major_holder" in names(c) and "debt_heavy" not in names(c)


def test_ast_rights(conn):
    c = rights_facts("20240722000152", conn=conn)
    assert sig(c, "non_cash")["payment_type"] == "빚→주식"


def test_kapro_rights(conn):
    c = rights_facts("20240328001867", conn=conn)
    assert sig(c, "non_cash")["payment_type"] == "섞임"


def test_entutech_dilution(conn):
    """기준 CB 19회(…443) + 같은 날 18회(…442, 19회 잔량표에 없음)의 conv_shares (old_cb_shares는 기준 CB 것만)."""
    d = dilution("01105621", AS_OF, conn=conn)
    cbs = {e["rcept_no"] for e in d["live_events"] if e["type"] == "CB"}
    assert cbs == {"20260820000443", "20260820000442"}
    assert d["dilution"]["max_pct"] == 30.4
    assert d["dilution"]["basis_rcept_no"] == "20260820000443"


def test_koiz_dilution(conn):
    corp = cb_facts("20260522000636", conn=conn)["corp_code"]
    assert dilution(corp, AS_OF, conn=conn)["dilution"]["max_pct"] == 5.8


# ---- fix1 (2026-10-24, 시험 운행 뒤)

def test_neon_cb_same_basis_pairs(conn):
    """이번 공시만(dilution · worst)과 예전 잔량 포함(total · total_worst)이 같은 기준끼리 짝. 시험 운행에서 total 12.0 > worst 11.0을 섞어 답함."""
    k = cb_facts("20250115000342", conn=conn)["calc"]
    assert (k["dilution_pct"], k["worst_pct"], k["total_pct"], k["total_worst_pct"]) == (7.9, 11.0, 12.0, 14.8)
    assert k["dilution_pct"] <= k["worst_pct"] and k["total_pct"] <= k["total_worst_pct"]


def test_price_pending_display():
    from filingscope.facts.cards import price_pending_display
    d = date(2026, 10, 19)
    assert price_pending_display({"price_status": "예정", "price_confirm_date": "2026-11-04"}, d) == "(예정 발행가 기준, 2026-11-04 확정)"
    assert price_pending_display({"price_status": "예정", "price_confirm_date": "2026-10-19"}, d) == "(예정 발행가 기준, 2026-10-19 확정)"
    assert price_pending_display({"price_status": "예정", "price_confirm_date": None}, d) == "(예정 발행가 기준)"
    assert price_pending_display({"price_status": "예정", "price_confirm_date": "2026-10-06"}, d) == "(예정 발행가 기준, 확정 공시 미반영)"
    assert price_pending_display({"price_status": "확정", "price_confirm_date": None}, d) is None


def test_aroot_events_status_matches_live(conn):
    """company.events status = live 기준 (에이루트: 2026-06-15 유상증자 2건은 상장 끝 → 끝남, CB만 진행)."""
    d = dilution(cb_facts("20260519000315", conn=conn)["corp_code"], AS_OF, conn=conn)
    assert {e["rcept_no"]: e["status"] for e in d["company"]["events"]} == {
        "20260519000315": "진행", "20260615000422": "끝남", "20260615000432": "끝남"}


def test_neon_events_status_residual(conn):
    """fix2: 전환 기간이 남은 이전 CB는 "잔량 반영" (뉴온 2024-01 · 2024-03 CB → 기준 CB 잔량표로 합산), 상장 끝난 유상증자는 "끝남"."""
    d = dilution(cb_facts("20250115000342", conn=conn)["corp_code"], AS_OF, conn=conn)
    st = {e["rcept_no"]: e["status"] for e in d["company"]["events"]}
    assert st["20250115000342"] == st["20250115000341"] == "진행"
    assert st["20240126000455"] == st["20240305000509"] == "잔량 반영"
    assert st["20260706000282"] == "끝남"
