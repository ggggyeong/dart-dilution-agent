"""사실 카드: DB의 원문 값을 읽어 계산하고 신호를 켜고 JSON 한 장으로 만든다 (명세 docs/facts_spec.md).

알림(입구 1)과 MCP 도구(입구 2)가 이 함수를 그대로 쓴다. LLM 없음, 새 DB 칸 없음, 판단 없음.
"""

from filingscope.facts.cards import cb_facts, company_signals, dilution, rights_facts

__all__ = ["rights_facts", "cb_facts", "company_signals", "dilution"]
