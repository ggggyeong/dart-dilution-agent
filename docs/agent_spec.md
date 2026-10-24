# Research Agent 명세 v1 (⑤)

> 2026-10-23: 현역 프롬프트 docs/agent_prompt_v6.md (v5에서 search_filing_text · 조항 답 문장 뺌). v1~v5 · agent_try10 · 평가 v1은 archive/2026-10-23_rag_v1/ (RAG 재구축 중).

> v1 (2026-10-19, 사용자 작성). 구현: `src/filingscope/agent/` (graph.py), 시스템 프롬프트 `docs/agent_prompt_v1.md` (코드가 그대로 읽음), 작동 확인 `scripts/agent_try10.py` → data/reports/agent/try_v1.{json,md}.

목적: 사용자 질문을 받아 MCP 도구를 골라 부르고, 결과를 근거와 함께 문장으로 답하는 단일 ReAct agent. 숫자는 도구가, agent는 도구 선택과 문장만. 판단 금지. 도구 호출 최대 3회.

## 0. 구조
LangGraph StateGraph 노드 2개 (llm ↔ tools) 루프. MCP 서버는 stdio 자식 프로세스. 모델 Sonnet 기본 (FILINGSCOPE_AGENT_MODEL). 기준일 FILINGSCOPE_AS_OF → MCP 서버 기본 as_of.

## 1. 입력 · 출력
`answer(question, as_of=None) -> {question, answer, tool_calls: [{name, args, result, ms(, error)}], plan, turns, model, prompt_version: "v1", usage: {input_tokens, output_tokens, cost_usd}}`

## 2. 시스템 프롬프트 v1
`docs/agent_prompt_v1.md` (명세 문구 그대로). 2026-10-19 사용자 지시로 [고르는 법]에 두 줄 추가 (명세 2장 최신): "주가 떨어지면 · 리픽싱 되면 · 최악" → dilution.worst_pct, 바닥가는 values.min_price · calc.floor_pct / "언제 전환 · 언제 상장 · 언제 풀려 · 만기" → schedule의 해당 label 날짜, 여러 건이면 전부.

## 3. 구현 규칙
도구 결과 JSON 그대로 (30,000자 넘으면 live_events의 values · allottees만 빼고, tool_calls.result에는 전체). 3회 초과 요청은 실행하지 않고 도구 없이 한 번 더 답하게 한 뒤 answer 끝에 "(도구 호출 한도)". MCP 오류는 예외로 올리지 않고 tool_calls에 error, answer에 "도구 오류: …". 멀티턴 없음.

## 4. 작동 확인 10문제 (채점 아님)
Q1-001 · Q1-002 · Q1-009 · Q2-003 · Q3-001 · Q3-030 · Q5-009 · Q6-020 · Q7-001 · Q7-021, FILINGSCOPE_AS_OF=2026-10-19.

## 6. 하지 않는 것
200문제 실행 · 채점 · 프롬프트 수정 (10문제 결과가 이상해도 보고만, 수정은 ⑥) · 멀티턴 · RAG · 도구 추가.

## 구현 메모 (2026-10-19)
- MCP 연결: langchain-mcp-adapters 대신 mcp SDK(2.3) 자체 stdio 클라이언트 (ClientSession + stdio_client). LLM은 Anthropic SDK를 LangGraph 노드에서 직접 호출 (langchain-anthropic 안 씀). 의존성 추가: langgraph.
- FILINGSCOPE_AS_OF 처리: `src/filingscope/mcp/server.py` apply_as_of() — 서버 시작 시 facts.cards._today를 그 날짜로 고정 (dilution · company_signals · 카드 company 기본 as_of 모두). answer(as_of=…)는 자식 프로세스 환경변수로 넘김.
- plan = LLM 응답에서 "계획"으로 시작하는 첫 줄. 답에서는 뺀다.
- answer()마다 MCP 서버를 새로 띄운다 (캐시 1.8초). 여러 문제는 answer_many(questions, as_of, on_result) — 서버 하나로 (질문마다 대화는 새로).
