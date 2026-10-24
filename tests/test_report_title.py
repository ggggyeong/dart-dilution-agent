"""제목 판정. 정답표 tests/fixtures/title_cases.csv의 모든 줄을 통과해야 한다 (unsure는 사용자 결정 대기)."""

import pytest

from filingscope.classify.answer_sheet import ANSWER_SHEET, load_answers
from filingscope.classify.report_title import classify_title

ANSWERS = load_answers()


def test_answer_sheet_exists():
    assert ANSWER_SHEET.exists(), "tests/fixtures/title_cases.csv 없음"
    assert ANSWERS


@pytest.mark.parametrize("answer", ANSWERS, ids=lambda a: a.report_nm)
def test_title_answer_sheet(answer):
    if answer.is_unsure:
        pytest.skip(f"unsure — 사용자 결정 대기: {answer.reason}")
    got = classify_title(answer.report_nm)
    assert (got.event_type, got.is_correction, got.report_tag, got.is_mixed) == answer.as_tuple(), answer.reason


# 요구사항에 직접 적힌 규칙
@pytest.mark.parametrize(
    "report_nm, event_type, is_correction, report_tag, is_mixed",
    [
        ("주요사항보고서(유상증자결정)", "rights_issue", False, None, False),
        ("주요사항보고서(전환사채권발행결정)", "cb", False, None, False),
        ("주요사항보고서(전환사채권 발행결정)", "cb", False, None, False),
        ("전환사채발행결정", "cb", False, None, False),
        ("주요사항보고서(유무상증자결정)", "rights_issue", False, None, True),
        ("[기재정정]주요사항보고서(유상증자결정)", "rights_issue", True, "기재정정", False),
        ("[첨부정정]주요사항보고서(전환사채권발행결정)", "cb", True, "첨부정정", False),
        ("[발행조건확정]주요사항보고서(유상증자결정)", "rights_issue", True, "발행조건확정", False),
        ("주요사항보고서(무상증자결정)", None, False, None, False),
        ("주요사항보고서(자기전환사채만기전취득결정)", None, False, None, False),
        ("주요사항보고서(교환사채권발행결정)", None, False, None, False),
        ("주요사항보고서(신주인수권부사채권발행결정)", None, False, None, False),
    ],
)
def test_title_rules(report_nm, event_type, is_correction, report_tag, is_mixed):
    got = classify_title(report_nm)
    assert (got.event_type, got.is_correction, got.report_tag, got.is_mixed) == (
        event_type, is_correction, report_tag, is_mixed,
    )
