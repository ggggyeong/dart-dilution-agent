"""Research Agent의 API 없는 부분: 계획 줄 처리 · 큰 결과 줄이기."""

from filingscope.agent.graph import SLIM_CHARS, _plan_of, _slim, _strip_plan


def test_plan_line_extracted_and_removed():
    text = "계획: get_company_dilution → compare_event\n엔투텍은 30.4%예요.\n근거: 접수번호 1"
    assert _plan_of(text) == "계획: get_company_dilution → compare_event"
    assert _strip_plan(text) == "엔투텍은 30.4%예요.\n근거: 접수번호 1"
    assert _plan_of("계획 없이 답") == "계획 없이 답" and _plan_of("답만") is None


def test_slim_drops_values_only_when_big():
    small = {"live_events": [{"values": {"a": 1}, "calc": {"x": 1}}]}
    assert _slim(small) == small
    big = {"live_events": [{"values": {"pad": "x" * SLIM_CHARS}, "allottees": [], "calc": {"x": 1}, "signals": []}], "schedule": []}
    assert _slim(big) == {"live_events": [{"calc": {"x": 1}, "signals": []}], "schedule": []}


def test_cached_request_parts_and_cost():
    """fix2: system · 마지막 tool에 cache_control, 원본 tool 목록은 안 바뀜. 비용 = 입력 + 쓰기 1.25 + 읽기 0.1 + 출력."""
    from filingscope.agent.graph import cached_request_parts, cost_usd
    tools = [{"name": "a", "input_schema": {}}, {"name": "b", "input_schema": {}}]
    sys_blocks, cached = cached_request_parts("프롬프트", tools)
    assert sys_blocks == [{"type": "text", "text": "프롬프트", "cache_control": {"type": "ephemeral"}}]
    assert "cache_control" not in cached[0] and cached[1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in tools[1]
    # Sonnet 5.5 입력 $2 · 출력 $10 /M: 1M 입력 + 1M 쓰기 + 1M 읽기 + 0.1M 출력 = 2 + 2.5 + 0.2 + 1
    assert cost_usd("claude-sonnet-5-5", 1_000_000, 100_000, 1_000_000, 1_000_000) == 5.7
