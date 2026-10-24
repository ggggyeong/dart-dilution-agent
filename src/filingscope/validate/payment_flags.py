"""납입 방식 검사: LLM이 "현금"으로 판정했는데 현금 외 납입의 흔적이 있는 공시를 사람 확인 목록에 올린다.

- 대상자 이름에 "출자전환" 또는 "상계"가 있음 (예: 카프로 "하나은행(출자전환)")
- 발행가 산정 근거에 "2)"가 있음 — 대상자 무리마다 발행가가 다르다는 뜻 (예: 카프로 "1) 투자자 : 500원 … 2) 출자전환주주 : 2,000원")
  산정 근거 = 자유 서술의 "<6. 신주의 발행가격> … 산정근거" 머리말부터 다음 머리말("라)" · "③" · "(2)" 등)까지 + 7-3번 칸(DC_RATE_RS).
  괄호로 둘러싼 "(2)"는 다른 단계의 머리말이라 세지 않는다 (나인테크 · 차바이오텍 "(2) 금번 유상증자와 관련하여 …" 오탐).
"""

import re

from filingscope.parse.acode import DartDocument

ALLOTTEE_WORDS = ("출자전환", "상계")
_BASIS_HEAD = re.compile(r"발행\s*가(?:격|액)[^\n]{0,80}?산정\s*근거")
_NEXT_HEAD = re.compile(r"(?<![가-힣\w(])[가-하]\)|[①-⑳]|\(\d{1,2}\)")
_SECOND = re.compile(r"(?<![\d.\w(])2\)")
SPAN_MAX = 3000


def price_basis_text(doc: DartDocument) -> str:
    """발행가 산정 근거 글: 자유 서술의 해당 부분 + 7-3번 칸."""
    free = "\n".join(p.text for p in doc.body_paragraphs())
    parts = []
    for m in _BASIS_HEAD.finditer(free):
        nxt = _NEXT_HEAD.search(free, m.end())
        parts.append(free[m.start(): nxt.start() if nxt and nxt.start() - m.end() < SPAN_MAX else m.end() + SPAN_MAX])
    parts += [c.text for c in doc.body_cells() if c.code == "DC_RATE_RS" and not c.in_correction]
    return "\n".join(parts)


def payment_flags(doc: DartDocument, allottee_names: list[str]) -> list[str]:
    """걸린 이유 목록 (없으면 빈 목록)."""
    out = []
    hit = [n for n in allottee_names if n and any(w in n for w in ALLOTTEE_WORDS)]
    if hit:
        out.append("대상자 이름: " + ", ".join(hit))
    basis = price_basis_text(doc)
    m = _SECOND.search(basis)
    if m:
        out.append("발행가 산정 근거 '2)': …" + basis[max(0, m.start() - 20): m.end() + 60].replace("\n", " ") + "…")
    return out
