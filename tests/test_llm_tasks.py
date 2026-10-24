from filingscope.llm.extractor import build_messages, tasks_for
from filingscope.parse.sections import Section


def test_payment_only_when_rules_could_not_decide():
    assert tasks_for(6, "보통", None, "pending:llm") == ["payment"]
    assert tasks_for(6, "보통", None, "llm:claude-opus-5-5:extract-v2") == ["payment"]
    assert tasks_for(6, "보통", None, "rule:no_keyword") == []
    assert tasks_for(5, "보통", None, "rule:ci_mth") == []


def test_shareholder_common_stock_gets_discount_and_market_right_sale():
    assert tasks_for(1, "보통", "시장", "rule:ci_mth") == ["discount", "right_sale"]
    assert tasks_for(3, "보통", "장외", "rule:ci_mth") == ["discount"]
    assert tasks_for(2, "보통", "불가", "rule:ci_mth") == ["discount"]
    assert tasks_for(1, "우선", "시장", "rule:ci_mth") == []  # 우선주만 발행은 보류 (명세 5장)


def test_same_text_is_sent_once():
    s = Section("24번 글", False)
    msg = build_messages({"discount": s, "right_sale": s})[0]["content"]
    assert msg.count("24번 글") == 1 and "<discount_right_sale_글>" in msg
