"""⑨-2 파서 수정 (docs/cc_task_verify_fix.md) — 지시서에 적힌 rcept_no · 기대값. 원문(data/raw)이 없으면 건너뛴다."""

from datetime import date
from decimal import Decimal

import pytest

from filingscope.config import DATA_DIR
from filingscope.parse.acode import read_document
from filingscope.parse.cb import date_flag, extract as cb_extract
from filingscope.parse.rights_issue import extract, norm_relation

RAW = DATA_DIR / "raw" / "document"


def mk(cells: str, paragraphs: str = ""):
    """작은 가짜 공시 (tests/test_rights_issue.py와 같은 모양)."""
    xml = f"""<?xml version="1.0" encoding="utf-8"?><DOCUMENT><DOCUMENT-NAME ACODE="11306">x</DOCUMENT-NAME>
    <BODY><SECTION-1><TITLE>x</TITLE><TABLE><TBODY>{cells}</TBODY></TABLE>{paragraphs}</SECTION-1></BODY></DOCUMENT>"""
    return read_document(xml.encode())


def _row(code: str, text: str, value: str | None = None, kind: str = "ACODE") -> str:
    if kind == "AUNIT":
        return f'<TR><TD>x</TD><TU AUNIT="{code}" AUNITVALUE="{value}">{text}</TU></TR>'
    return f'<TR><TD>x</TD><TE ACODE="{code}">{text}</TE></TR>'


def _doc(no: str):
    files = sorted((RAW / no).glob("*.xml")) if (RAW / no).exists() else []
    if not files:
        pytest.skip(f"원문 없음 {no}")
    # 본문 파일: documents.main_file과 같게 가장 큰 xml (부속 파일보다 큼)
    return read_document(max(files, key=lambda p: p.stat().st_size))


@pytest.mark.parametrize("no", ["20230822000274", "20240613000079", "20241230000631", "20251020000294", "20260213002858"])
def test_third_party_undecided_price(no):
    """2번: 3자배정 발행가 미확정 → 예정 (price 값은 그대로)."""
    v = extract(_doc(no), "주요사항보고서(유상증자결정)").values
    assert v["price_status"] == "예정" and v["price"] is not None


@pytest.mark.parametrize("no, want", [("20231005000055", Decimal("0.8434273916")), ("20260508000511", Decimal("0.3224838999"))])
def test_ci2_shares_per_share(no, want):
    """5번: 주주우선공모(ci 2) 1주당 배정주식수는 글에서, 소수점 자릿수 유지. right_sale은 불가 그대로."""
    x = extract(_doc(no), "주요사항보고서(유상증자결정)")
    assert x.ci_mth == 2 and x.values["shares_per_share"] == want and x.values["right_sale"] == "불가"


@pytest.mark.parametrize("no, word", [("20230706000056", "현물"), ("20230828000483", "현물"), ("20250530002705", "채무상환"),
                                      ("20251208000254", "상계"), ("20241121000382", "PIN_EXT_YN=Y")])
def test_payment_stage1_signal(no, word):
    """3번 1단계: 이 공시들은 비현금 신호가 걸려 LLM 대기 (증자 방식으로 미리 현금 단정하지 않음)."""
    x = extract(_doc(no), "주요사항보고서(유상증자결정)")
    assert x.payment_source == "pending:llm" and word in x.noncash_words


def test_relation_normalized():
    """6번: 공백 제거 후 -, 없음, 해당없음, 해당사항없음, 무 → NULL."""
    assert [norm_relation(t) for t in ("-", "없 음", "해당 없음", "해당사항 없음", "무", "최대주주")] == [None] * 5 + ["최대주주"]


def test_date_flag_pay_maturity():
    """6번: pay_date ≥ maturity 쌍."""
    d = date
    assert date_flag(None, d(2029, 1, 1), None, d(2029, 1, 1)) == "pay_date=maturity"
    assert date_flag(None, d(2030, 1, 1), None, d(2029, 1, 1)) == "pay_date>maturity"


def test_cb_exempt_reason_dash_is_null():
    """6번: CB exempt_reason "-" → NULL, 신호 없으면 현금 rule:no_signal."""
    x = cb_extract(mk(_row("RPT_SBM", "-") + _row("PYM_DET", "현금", "1", "AUNIT")))
    assert x.values["exempt_reason"] is None and (x.payment_type, x.payment_source) == ("현금", "rule:no_signal")


def test_acquire_target_undecided_is_null():
    """6번: 3자배정 acquire_target에 "미정" → NULL."""
    v = extract(mk(_row("CI_MTH", "제3자배정증자", "6", "AUNIT") + _row("CST_CNT", "100") + _row("FND2_ISS", "미정")), "x").values
    assert v["acquire_target"] is None
