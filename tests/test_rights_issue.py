"""유상증자 코드 칸 추출 (명세 v1). 케이에스인더스트리 정답 XML + 작은 단위 테스트."""

from datetime import date
from decimal import Decimal

from conftest import FIXTURES_DIR
from filingscope.parse.acode import CodedCell, read_document
from filingscope.parse.rights_issue import extract, lockup_period, to_date, to_int, to_number

KS = FIXTURES_DIR / "ks_rights_issue_20261015000184.xml"


def test_ks_third_party():
    r = extract(read_document(KS), "주요사항보고서(유상증자결정)")
    v = r.values
    assert r.ci_mth == 6 and r.group == "제3자배정"
    assert v["new_shares"] == 4170141 and v["shares_before"] == 14796480
    assert v["price"] == 1199 and v["price_status"] == "확정"
    assert v["discount_rate"] == Decimal("9.95")  # 칸에는 -9.95, 발행가 < 기준주가라 할인(양수)
    assert v["fund_operating"] == 2399999059 and v["fund_debt"] == 2600000000
    assert v["fund_facility"] == 0 and v["fund_other"] == 0  # "-"는 0
    assert v["listing_date"] == date(2026, 11, 9) and v["participation"] == "불가"
    assert [(a.name, a.shares, a.lockup) for a in r.allottees] == [("(주)이메디슨", 4170141, "1년")]
    # 정관 RULE 칸의 '출자전환'은 자유 서술이 아니라 걸리지 않음 (⑨-2: 신호 없음 = rule:no_signal)
    assert (r.payment_type, r.payment_source) == ("현금", "rule:no_signal")


def test_number_and_date_parsing():
    assert to_number("1,199") == 1199 and to_number("-9.95") == Decimal("-9.95")
    assert to_number("-") is None and to_number("-", dash_zero=True) == 0
    assert to_int("4,170,141") == 4170141
    cell = lambda value, text: CodedCell("X", "AUNIT", value, text, None, None, 0, 0, False)  # noqa: E731
    assert to_date(cell("20261109", "2026년 11월 09일")) == date(2026, 11, 9)
    assert to_date(cell(None, "2026년 8월 6일")) == date(2026, 8, 6)
    assert to_date(cell(None, "-")) is None


def test_lockup_period():
    assert lockup_period("사모발행 (1년간 보호예수)") == "1년"
    assert lockup_period("6개월 보호예수") == "6개월"
    assert lockup_period("-") is None


# ---------- 규칙 v2 (30건 검토 반영) ----------

def _doc(cells: str, paragraphs: str = "") -> object:
    xml = f"""<?xml version="1.0" encoding="utf-8"?><DOCUMENT><DOCUMENT-NAME ACODE="11306">주요사항보고서(유상증자결정)</DOCUMENT-NAME>
    <BODY><SECTION-1><TITLE>유상증자 결정</TITLE><TABLE><TBODY>{cells}</TBODY></TABLE>{paragraphs}</SECTION-1></BODY></DOCUMENT>"""
    return read_document(xml.encode())


def _row(code: str, text: str, value: str | None = None, kind: str = "ACODE") -> str:
    if kind == "AUNIT":
        return f'<TR><TD>x</TD><TU AUNIT="{code}" AUNITVALUE="{value}">{text}</TU></TR>'
    return f'<TR><TD>x</TD><TE ACODE="{code}">{text}</TE></TR>'


def test_third_party_discount_null_when_no_standard_price():
    """기준주가가 '-'(거래정지 등)면 할인율 null. 기준주가가 있으면 '-'는 0."""
    base = _row("CI_MTH", "제3자배정증자", "6", "AUNIT") + _row("CST_ISS_VAL", "500") + _row("DC_RATE", "-")
    assert extract(_doc(base + _row("CST_STD_VAL", "-")), "x").values["discount_rate"] is None
    assert extract(_doc(base + _row("CST_STD_VAL", "600")), "x").values["discount_rate"] == 0


def test_mixed_dash_and_number_uses_number():
    cells = _row("CI_MTH", "일반공모증자", "5", "AUNIT") + _row("DC_RATE", "- 20")
    assert extract(_doc(cells), "x").values["discount_rate"] == 20


def test_ci2_right_sale_not_possible():
    cells = _row("CI_MTH", "주주우선공모증자", "2", "AUNIT") + _row("PRE_CST_VAL", "8,520")
    v = extract(_doc(cells), "x").values
    assert v["right_sale"] == "불가" and v["shares_per_share"] is None


def test_payment_rules_v2():
    third = _row("CI_MTH", "제3자배정증자", "6", "AUNIT")
    # 현물출자 칸이 Y여도 확정하지 않고 LLM
    r = extract(_doc(third + _row("PIN_EXT_YN", "예", "Y", "AUNIT")), "x")
    assert (r.payment_type, r.payment_source) == (None, "pending:llm") and "PIN_EXT_YN=Y" in r.noncash_words
    # 단어가 있으면 오탐 문구라도 LLM이 판단
    r = extract(_doc(third, "<P>현금으로 납입하거나 또는 채권을 상계할 수 있음</P>"), "x")
    assert r.payment_source == "pending:llm" and r.noncash_words == ["상계"]
    # 단어가 하나도 없으면 현금
    assert extract(_doc(third, "<P>운영자금 조달</P>"), "x").payment_type == "현금"
    # 제목 (출자전환) → 빚→주식
    pub = _row("CI_MTH", "주주배정증자", "1", "AUNIT")
    r = extract(_doc(pub), "주요사항보고서(유상증자결정)(출자전환)")
    assert (r.payment_type, r.payment_source) == ("빚→주식", "rule:title")


def test_stock_type_preferred_only_reads_pst():
    """⑨-2 1번: 우선주만 발행이면 비우지 않고 기타주식 칸(PST_*)에서 채운다. 기준주가 PST_STD_VAL이 없으면 할인율 NULL."""
    third = (_row("CI_MTH", "제3자배정증자", "6", "AUNIT") + _row("CST_CNT", "-") + _row("PST_CNT", "2,495,500")
             + _row("BFR_CST_CNT", "10,000,000") + _row("PST_ISS_VAL", "5,009") + _row("DC_RATE", "4.6") + _row("FND_USE2", "12,500,000,000"))
    v = extract(_doc(third), "x").values
    assert v["stock_type"] == "우선"
    assert v["new_shares"] == 2495500 and v["price"] == 5009 and v["price_status"] == "확정" and v["discount_rate"] is None
    assert v["shares_before"] == 10000000 and v["fund_operating"] == 12500000000  # 공시 전체 사실은 남김


def test_stock_type_common_and_mixed():
    assert extract(_doc(_row("CI_MTH", "x", "6", "AUNIT") + _row("CST_CNT", "100") + _row("PST_CNT", "-")), "x").values["stock_type"] == "보통"
    assert extract(_doc(_row("CI_MTH", "x", "6", "AUNIT") + _row("CST_CNT", "100") + _row("PST_CNT", "50")), "x").values["stock_type"] == "혼합"
