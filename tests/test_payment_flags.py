from filingscope.parse.acode import read_document
from filingscope.validate.payment_flags import payment_flags

DOC = """<?xml version="1.0" encoding="utf-8"?>
<DOCUMENT><DOCUMENT-NAME ACODE="11306">주요사항보고서(유상증자결정)</DOCUMENT-NAME><BODY><SECTION-1>
<P>나) 상기<6. 신주의 발행가격> 및 <7. 기준주가에 대한 할인율 또는 할증율> 산정근거 1) 투자자 : 1주당 금 오백원 {second}다) 본 유상증자의 효력발생일은 … 2) 다른 항목</P>
</SECTION-1></BODY></DOCUMENT>"""


def test_two_price_groups_flagged():
    doc = read_document(DOC.format(second="2) 출자전환주주 : 1주당 금 이천원 ").encode())
    assert any("2)" in r for r in payment_flags(doc, ["주식회사 티엠씨"]))


def test_single_price_group_not_flagged_even_if_later_item_has_2():
    doc = read_document(DOC.format(second="").encode())
    assert payment_flags(doc, ["주식회사 티엠씨"]) == []  # '다)' 뒤의 "2)"는 산정 근거 밖


def test_allottee_name_words():
    doc = read_document(DOC.format(second="").encode())
    assert payment_flags(doc, ["하나은행(출자전환)", "갑"]) == ["대상자 이름: 하나은행(출자전환)"]


def test_parenthesized_2_is_another_heading():
    """나인테크: "(2) 금번 유상증자와 관련하여 …"는 산정 근거의 두 번째 무리가 아니다."""
    doc = read_document(DOC.format(second="(2) 금번 유상증자와 관련하여 대표이사에게 위임 ").encode())
    assert payment_flags(doc, ["갑"]) == []
