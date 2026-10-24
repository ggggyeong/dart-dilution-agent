"""단일 ReAct agent: LangGraph StateGraph 노드 2개 (llm ↔ tools) 루프, 도구 호출 최대 3회.

- LLM: Anthropic SDK 직접 (기본 Sonnet 5.5, FILINGSCOPE_AGENT_MODEL로 교체). 시스템 프롬프트는 docs/agent_prompt_{버전}.md를 그대로 읽음
  (기본 v9, FILINGSCOPE_AGENT_PROMPT로 교체).
- MCP: mcp SDK의 stdio 클라이언트로 `python -m filingscope.mcp`를 자식 프로세스로 띄움. as_of를 주면 FILINGSCOPE_AS_OF로 넘김.
- 도구 결과 JSON은 그대로 LLM에 넣는다. 너무 크면(SLIM_CHARS) live_events의 values · allottees만 빼고 넣고, tool_calls.result에는 전체를 기록.
- 4번째 호출 요청은 실행하지 않고 "도구 호출 한도" tool_result를 준 뒤 도구 없이 한 번 더 답하게 하고, answer 끝에 "(도구 호출 한도)".
- MCP 오류는 예외로 올리지 않고 tool_calls에 error, answer에 "도구 오류: …".
- 프롬프트 캐시 (2026-10-24 fix2): tools 목록 끝 · system 끝에 cache_control(ephemeral). 비용 = 입력 + 캐시 쓰기 × 1.25 + 캐시 읽기 × 0.1 + 출력
  (입력 단가 기준 배수, 5분 캐시). usage에 cache_write_tokens · cache_read_tokens.
"""

import asyncio
import contextlib
import json
import os
import sys
import time
from typing import Any, TypedDict

import anthropic
from langgraph.graph import END, StateGraph
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from filingscope.config import PROJECT_ROOT, get_settings

PROMPT_VERSION = "v9"  # 기본 (2026-10-24 v9 = 마지막 단계, 장 번호 · 용어표 · flag 공시별 · 신호 공시 당시 · 비교 말; v8 = v7 위쪽 그대로 · [답]만 다시 씀, 시험 운행 fix1; 본 실행 전 fix2에서 rehab 줄만 "회생 절차 중인 회사의 발행이에요."로; v7 = v6 + search_filing_text · 조항 · flag 문장, docs/cc_task_eval_v2_prep.md A; v6 = v5에서 옛 search_filing_text 문장 뺌; v1~v5는 archive/2026-10-23_rag_v1/docs/). FILINGSCOPE_AGENT_PROMPT로 바꿈 (docs/agent_prompt_{버전}.md)


def prompt_version() -> str:
    return os.environ.get("FILINGSCOPE_AGENT_PROMPT", PROMPT_VERSION)


def prompt_path():
    return PROJECT_ROOT / "docs" / f"agent_prompt_{prompt_version()}.md"
DEFAULT_MODEL = "claude-sonnet-5-5"
PRICE = {"claude-sonnet-5-5": (2.00, 10.00), "claude-opus-5-5": (4.00, 20.00), "claude-haiku-5-5": (0.10, 0.50)}
MAX_TOOL_CALLS = 3
SLIM_CHARS = 30000
LIMIT_NOTE = "(도구 호출 한도)"
CACHE_WRITE_X, CACHE_READ_X = 1.25, 0.1   # 입력 단가 대비 (5분 캐시 쓰기 · 읽기)
CACHE = {"type": "ephemeral"}


def model_name() -> str:
    return os.environ.get("FILINGSCOPE_AGENT_MODEL", DEFAULT_MODEL)


class State(TypedDict, total=False):
    messages: list[dict]
    tool_calls: list[dict]
    plan: str | None
    turns: int
    input_tokens: int
    output_tokens: int
    cache_write_tokens: int
    cache_read_tokens: int
    force_answer: bool
    capped: bool
    tool_errors: list[str]


def _slim(result: Any) -> Any:
    """크면 live_events의 values · allottees를 뺀다 (calc · signals · schedule 등은 그대로)."""
    if not isinstance(result, dict) or len(json.dumps(result, ensure_ascii=False)) <= SLIM_CHARS:
        return result
    if "live_events" in result:
        result = {**result, "live_events": [{k: v for k, v in e.items() if k not in ("values", "allottees")} for e in result["live_events"]]}
    return result


def _plan_of(text: str) -> str | None:
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("계획"):
            return s
    return None


def _strip_plan(text: str) -> str:
    return "\n".join(ln for ln in text.splitlines() if not ln.strip().startswith("계획")).strip()


def _blocks(content) -> list[dict]:
    out = []
    for b in content:
        if b.type == "text":
            out.append({"type": "text", "text": b.text})
        elif b.type == "tool_use":
            out.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
    return out


def cached_request_parts(system: str, tool_defs: list[dict]) -> tuple[list[dict], list[dict]]:
    """system · tools에 cache_control을 붙인 요청 조각 (tools → system 순서로 앞부분이 캐시됨). 원본 tool_defs는 안 바꾼다."""
    sys_blocks = [{"type": "text", "text": system, "cache_control": CACHE}]
    tools = [dict(t) for t in tool_defs]
    if tools:
        tools[-1]["cache_control"] = CACHE
    return sys_blocks, tools


def cost_usd(model: str, it: int, ot: int, cw: int = 0, cr: int = 0) -> float:
    pin, pout = PRICE.get(model, (0.0, 0.0))
    return round((it * pin + cw * pin * CACHE_WRITE_X + cr * pin * CACHE_READ_X + ot * pout) / 1e6, 4)


def build_graph(llm: anthropic.AsyncAnthropic, session: ClientSession, tool_defs: list[dict], system: str, model: str):
    sys_blocks, cached_tools = cached_request_parts(system, tool_defs)

    async def llm_node(state: State) -> State:
        kw = dict(model=model, max_tokens=2000, system=sys_blocks, messages=state["messages"])
        if not state.get("force_answer"):
            kw["tools"] = cached_tools
        resp = await llm.messages.create(**kw)
        blocks = _blocks(resp.content)
        text = "\n".join(b["text"] for b in blocks if b["type"] == "text")
        upd: State = {"messages": state["messages"] + [{"role": "assistant", "content": blocks}],
                      "turns": state.get("turns", 0) + 1,
                      "input_tokens": state.get("input_tokens", 0) + resp.usage.input_tokens,
                      "output_tokens": state.get("output_tokens", 0) + resp.usage.output_tokens,
                      "cache_write_tokens": state.get("cache_write_tokens", 0) + (getattr(resp.usage, "cache_creation_input_tokens", 0) or 0),
                      "cache_read_tokens": state.get("cache_read_tokens", 0) + (getattr(resp.usage, "cache_read_input_tokens", 0) or 0)}
        if state.get("plan") is None and _plan_of(text):
            upd["plan"] = _plan_of(text)
        return upd

    async def tools_node(state: State) -> State:
        last = state["messages"][-1]["content"]
        results, log, errors = [], list(state.get("tool_calls", [])), list(state.get("tool_errors", []))
        force, capped = state.get("force_answer", False), state.get("capped", False)
        for b in (x for x in last if x["type"] == "tool_use"):
            if len(log) >= MAX_TOOL_CALLS:
                results.append({"type": "tool_result", "tool_use_id": b["id"], "is_error": True,
                                "content": f"도구 호출 한도({MAX_TOOL_CALLS}회)를 넘어 실행하지 않았어요. 지금까지 결과로 답하세요."})
                force = capped = True
                continue
            t0 = time.time()
            try:
                r = await session.call_tool(b["name"], b["input"])
                body = json.loads(r.content[0].text) if r.content else {}
                if r.is_error:
                    raise RuntimeError(r.content[0].text if r.content else "is_error")
                log.append({"name": b["name"], "args": b["input"], "result": body, "ms": int((time.time() - t0) * 1000)})
                results.append({"type": "tool_result", "tool_use_id": b["id"],
                                "content": json.dumps(_slim(body), ensure_ascii=False, default=str)})
            except Exception as e:  # MCP 오류는 예외로 올리지 않는다
                msg = f"{type(e).__name__}: {e}"[:300]
                log.append({"name": b["name"], "args": b["input"], "result": None, "error": msg, "ms": int((time.time() - t0) * 1000)})
                errors.append(msg)
                results.append({"type": "tool_result", "tool_use_id": b["id"], "is_error": True, "content": f"도구 오류: {msg}"})
        if len(log) >= MAX_TOOL_CALLS:
            pass  # 3회째까지는 실행. 그다음 요청이 오면 위에서 막는다
        return {"messages": state["messages"] + [{"role": "user", "content": results}], "tool_calls": log,
                "force_answer": force, "capped": capped, "tool_errors": errors}

    def route(state: State) -> str:
        last = state["messages"][-1]
        wants = any(b["type"] == "tool_use" for b in last["content"])
        return "tools" if wants else END

    g = StateGraph(State)
    g.add_node("llm", llm_node)
    g.add_node("tools", tools_node)
    g.set_entry_point("llm")
    g.add_conditional_edges("llm", route, {"tools": "tools", END: END})
    g.add_edge("tools", "llm")
    return g.compile()


def _new_state(question: str) -> State:
    return {"messages": [{"role": "user", "content": question}], "tool_calls": [], "plan": None, "turns": 0,
            "input_tokens": 0, "output_tokens": 0, "cache_write_tokens": 0, "cache_read_tokens": 0,
            "force_answer": False, "capped": False, "tool_errors": []}


@contextlib.asynccontextmanager
async def _graph(as_of: str | None):
    """MCP 서버를 한 번 띄우고 그 세션으로 만든 그래프를 돌려준다 (answer · answer_many가 같이 쓴다)."""
    model = model_name()
    system = prompt_path().read_text(encoding="utf-8")
    env = {**os.environ, **({"FILINGSCOPE_AS_OF": as_of} if as_of else {})}
    params = StdioServerParameters(command=sys.executable, args=["-m", "filingscope.mcp"], env=env, cwd=str(PROJECT_ROOT))
    llm = anthropic.AsyncAnthropic(api_key=get_settings().anthropic_api_key.get_secret_value(), max_retries=3, timeout=180.0)
    with open(os.devnull, "w") as devnull:
        async with stdio_client(params, errlog=devnull) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                exclude = {x.strip() for x in os.environ.get("FILINGSCOPE_AGENT_EXCLUDE_TOOLS", "").split(",") if x.strip()}
                tool_defs = [{"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
                             for t in listed.tools if t.name not in exclude]   # 평가 기준선용 (예: v3는 search_filing_text 없이)
                yield build_graph(llm, session, tool_defs, system, model)


def _error_state(question: str, e: BaseException) -> State:
    """연결 실패 등은 예외로 올리지 않고 tool_calls · answer에 남긴다."""
    msg = f"{type(e).__name__}: {e}"[:300]
    s = _new_state(question)
    s["tool_calls"] = [{"name": None, "args": {}, "result": None, "error": msg, "ms": 0}]
    s["tool_errors"] = [msg]
    return s


async def _answer(question: str, as_of: str | None) -> dict:
    try:
        async with _graph(as_of) as graph:
            state = await graph.ainvoke(_new_state(question), {"recursion_limit": 20})
    except Exception as e:
        state = _error_state(question, e)
    return _result(question, state)


async def _answer_many(questions: list[str], as_of: str | None, on_result) -> list[dict]:
    out = []
    try:
        async with _graph(as_of) as graph:
            for q in questions:
                try:
                    state = await graph.ainvoke(_new_state(q), {"recursion_limit": 20})
                except Exception as e:  # 한 문제 실패가 나머지를 멈추지 않게
                    state = _error_state(q, e)
                r = _result(q, state)
                out.append(r)
                if on_result:
                    on_result(r)
    except Exception as e:  # 서버 연결 실패: 남은 문제 전부 오류로
        for q in questions[len(out):]:
            r = _result(q, _error_state(q, e))
            out.append(r)
            if on_result:
                on_result(r)
    return out


def _result(question: str, state: State) -> dict:
    model = model_name()
    final = ""
    for m in reversed(state["messages"]):
        if m["role"] == "assistant":
            final = "\n".join(b["text"] for b in m["content"] if b["type"] == "text")
            break
    text = _strip_plan(final)
    if state.get("tool_errors") and not text:
        text = "도구 오류: " + state["tool_errors"][-1]
    elif state.get("tool_errors"):
        text += "\n도구 오류: " + "; ".join(state["tool_errors"])
    if state.get("capped"):
        text += f"\n{LIMIT_NOTE}"
    it, ot = state.get("input_tokens", 0), state.get("output_tokens", 0)
    cw, cr = state.get("cache_write_tokens", 0), state.get("cache_read_tokens", 0)
    return {"question": question, "answer": text, "tool_calls": state.get("tool_calls", []), "plan": state.get("plan") or "",
            "turns": state.get("turns", 0), "model": model, "prompt_version": prompt_version(),
            "usage": {"input_tokens": it, "output_tokens": ot, "cache_write_tokens": cw, "cache_read_tokens": cr,
                      "cost_usd": cost_usd(model, it, ot, cw, cr)}}


def answer(question: str, as_of: str | None = None) -> dict:
    """질문 1개. MCP 서버를 띄우고 끝나면 닫는다."""
    return asyncio.run(_answer(question, as_of))


def answer_many(questions: list[str], as_of: str | None = None, on_result=None) -> list[dict]:
    """질문 여러 개를 MCP 서버 하나로 (캐시 1번). 질문마다 대화는 새로 시작 (멀티턴 아님). on_result(r)로 진행 표시."""
    return asyncio.run(_answer_many(questions, as_of, on_result))
