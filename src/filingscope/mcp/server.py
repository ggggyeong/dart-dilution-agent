"""stdio MCP 서버 (도구 5개, docs/mcp_spec.md v2). 도구 설명(description)은 명세 문구 그대로 (agent가 이걸 보고 고른다).
5번째 search_filing_text는 새 RAG (rag_chunks section · topics, docs/cc_task_mcp_v2.md A). 옛 RAG v1 도구는 archive/2026-10-23_rag_v1/."""

import os
import sys
from datetime import date
from typing import Annotated, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from filingscope.facts import cards
from filingscope.mcp import text_search, tools

DESC = {
    "get_event": "공시 1건의 값 · 계산 · 신호. 접수번호로 조회. 유상증자 · CB 모두.",
    "get_company_dilution": "회사 1곳의 살아 있는 유상증자 · CB를 모아 최대 희석률 · 일정 · 회사 신호를 계산. 회사명 또는 corp_code. "
                            "결과의 live_events가 살아 있는 공시, company.events는 전체 이력.",
    "compare_event": "공시 1건의 희석률이 같은 종류 · 같은 시장 공시 전체에서 몇 백분위인지. '큰 편인가'에 답할 때. "
                     "희석률이 없는 공시는 no_value.",
    "search_events": "조건으로 공시 목록 찾기. 회사 · 기간 · 종류 · 희석률 범위 · 신호. 최대 20건. 글 내용 검색은 안 됨.",
    "search_filing_text": "공시 1건의 조항 원문을 주제별로 가져온다. 숫자 계산은 하지 않는다. 주주배정 유상증자와 CB만 지원한다.\n"
                          "주제:\n"
                          "- 청약절차: 주주배정 유상증자에서 확정 발행가를 정하는 방법, 초과청약, 실권주 · 청약 미달 주식 처리\n"
                          "- 리픽싱: 주가가 내려갈 때 CB 전환가액을 낮추는 조건과 조정 주기\n"
                          "- 콜: 회사(또는 회사가 정한 사람)가 투자자에게서 CB를 되사오는 권리\n"
                          "- 풋: 투자자가 만기 전에 회사에 원금을 돌려달라고 하는 권리\n"
                          "- 전체: 어느 주제인지 확실하지 않을 때. 그 공시의 조항 원문 전부",
}

# 인자 설명 (agent가 inputSchema로 받는다). 평문 Annotated[타입, "설명"]은 이 SDK에서 스키마에 안 들어가 Field(description=)로.
RceptNo = Annotated[str, Field(description="접수번호 14자리")]
Company = Annotated[str, Field(description="회사명 또는 corp_code 8자리")]
AsOf = Annotated[str | None, Field(description="기준일 YYYY-MM-DD. 비우면 서버 기준일")]
DateArg = Annotated[str | None, Field(description="YYYY-MM-DD. 연도만 알면 01-01 / 12-31")]
Dilution = Annotated[float | None, Field(description="희석률 (%)")]
Signal = Annotated[
    Literal["non_cash", "major_holder", "debt_heavy", "halted", "refixing", "old_bonds", "public_single"] | None,
    Field(description="신호 하나. non_cash=현금 아닌 납입 · major_holder=최대주주 배정 · debt_heavy=채무상환 50% 이상 · "
                      "halted=거래정지 중 · refixing=리픽싱 있음 · old_bonds=예전 사채 잔량 있음 · public_single=공모인데 한 곳이 전액")]
Limit = Annotated[int, Field(description="최대 건수")]
OrderBy = Annotated[Literal["rcept_dt", "dilution"], Field(description="정렬. rcept_dt=접수일 최신순(기본), dilution=희석률 큰 순(NULL은 뒤). 정렬한 뒤 최대 건수만큼")]
SearchCompany = Annotated[str | None, Field(description="회사명 또는 corp_code 8자리")]
EventType = Annotated[Literal["유상증자", "CB"] | None, Field(description="공시 종류")]
TextRceptNo = Annotated[str, Field(description="공시 접수번호 14자리. 모르면 search_events나 get_company_dilution으로 먼저 찾는다.")]
TextTopic = Annotated[Literal["청약절차", "리픽싱", "콜", "풋", "전체"], Field(description="아래 설명 참고")]
Question = Annotated[str, Field(description="사용자 질문 원문 그대로. 조각 순서를 정하는 데만 쓴다.")]
server = MCPServer(name="filingscope", instructions="DART 유상증자 · CB 사실 카드 도구. 숫자는 사실 카드 그대로, 판단은 하지 않는다.")
CACHE: tools.Cache | None = None


def cache() -> tools.Cache:
    global CACHE
    if CACHE is None:
        CACHE = tools.Cache.build()
    return CACHE


@server.tool(name="get_event", description=DESC["get_event"])
def get_event(rcept_no: RceptNo) -> dict:
    return tools.get_event(rcept_no)


@server.tool(name="get_company_dilution", description=DESC["get_company_dilution"])
def get_company_dilution(company: Company, as_of: AsOf = None) -> dict:
    return tools.get_company_dilution(company, as_of)


@server.tool(name="compare_event", description=DESC["compare_event"])
def compare_event(rcept_no: RceptNo) -> dict:
    return tools.compare_event(rcept_no, cache())


@server.tool(name="search_events", description=DESC["search_events"])
def search_events(company: SearchCompany = None, type: EventType = None, date_from: DateArg = None,
                  date_to: DateArg = None, min_dilution: Dilution = None, max_dilution: Dilution = None,
                  signal: Signal = None, limit: Limit = 20, order_by: OrderBy = "rcept_dt") -> dict:
    return tools.search_events(cache(), company, type, date_from, date_to, min_dilution, max_dilution, signal, limit, order_by)


@server.tool(name="search_filing_text", description=DESC["search_filing_text"])
def search_filing_text(rcept_no: TextRceptNo, topic: TextTopic, question: Question) -> dict:
    return text_search.search_filing_text(rcept_no, topic, question)


def apply_as_of() -> str | None:
    """FILINGSCOPE_AS_OF(YYYY-MM-DD)가 있으면 서버의 '오늘'을 그 날로 고정한다 — dilution · company_signals ·
    사실 카드의 company 기본 as_of가 모두 이 날짜를 쓴다 (평가에서 2026-10-19 고정용)."""
    v = os.environ.get("FILINGSCOPE_AS_OF")
    if v:
        d = date.fromisoformat(v)
        cards._today = lambda: d
    return v


def main():
    as_of = apply_as_of()  # 캐시보다 먼저
    if as_of:
        print(f"filingscope MCP: 기준일 {as_of} (FILINGSCOPE_AS_OF)", file=sys.stderr)
    c = cache()  # 시작 시 사실 카드 전부 계산 (stdout은 MCP 통신용이라 stderr로 기록)
    print(f"filingscope MCP: 사실 카드 {len(c.cards):,}건 캐시, {c.seconds}초", file=sys.stderr)
    server.run("stdio")
