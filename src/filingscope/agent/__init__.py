"""Research Agent (입구 2, 명세 docs/agent_spec.md ⑤): 질문 → MCP 도구 선택 · 호출 → 근거와 함께 답.

    from filingscope.agent import answer
    answer("엔투텍 희석 얼마나 돼?", as_of="2026-10-19")
"""

from filingscope.agent.graph import answer, answer_many

__all__ = ["answer", "answer_many"]
