"""납입 방식 2단계 판정 (⑨-2, 프롬프트 prompts/payment_v4.md). 유상증자 · CB 하나의 지시문.

1단계(규칙, parse/rights_issue.noncash_signals)에서 비현금 신호가 걸린 공시만 온다. 입력은 원문 전체가 아니라
판단에 필요한 칸만 골라 "칸 이름: 값" 글로 만든다. 모델이 낸 근거 문장이 이 입력 안에 글자 그대로 없으면 값을 버린다.
"""

import re
from pathlib import Path
from typing import Literal, Optional

import anthropic
from pydantic import BaseModel

from filingscope.config import PROJECT_ROOT
from filingscope.parse.acode import DartDocument
from filingscope.parse.rights_issue import NONCASH_WORDS, _is_dash

PROMPT_VERSION = "payment-v4"
MODEL = "claude-opus-5-5"
SOURCE = f"llm:{MODEL}:{PROMPT_VERSION}"
PRICE = (4.00, 20.00)   # $/1M 입력 · 출력
EFFORT = "medium"   # 지시서는 temperature 0이지만 SDK 1.14 · 이 모델은 temperature를 받지 않음 → 이전 LLM 판정과 같은 effort
SYSTEM_PROMPT = (PROJECT_ROOT / "prompts" / "payment_v4.md").read_text(encoding="utf-8")
FUND_CODES = {"유상증자": ["FND_USE1", "FND_USE_SQ", "FND_USE2", "FND_USE_RD", "ANC_ACQ_AMT", "FND_USE3"],
              "CB": ["FND_USE1", "FND_USE_SQ", "FND_USE2", "FND_USE_RD", "ANC_ACQ_PRC", "FND_USE3"]}
PEOPLE = {"유상증자": ("THD_ASN_LST", ["PART", "RLT", "SLT_JDG", "MNTH", "ALL_CNT"]),
          "CB": ("CRP_ISSU", ["ISSU_NM", "RLT", "ISSU_SLT", "ISSU_HST", "ISSU_AMT"])}
FOOTNOTE = re.compile(r"^\s*[\(\[]?\s*주\s*1\s*\)")


class PaymentV4(BaseModel):
    payment_type: Literal["현금", "빚→주식", "현물", "섞임", "불명"]
    evidence: list[str]
    asset: Optional[str]
    debt: Optional[str]
    reason: str


def _cell_line(c) -> str:
    name = f"{c.code}({c.label})" if c.label else c.code
    return f"{name}: {c.text}"


def _rows(doc: DartDocument, group: str, codes: list[str] | None = None) -> list[str]:
    """부속 표 한 묶음을 줄마다 "칸: 값 / 칸: 값"으로. codes가 있으면 그 칸만."""
    rows: dict[tuple[int, int], list] = {}
    for c in doc.body_cells():
        if c.group == group and (codes is None or c.code in codes) and c.text.strip():
            rows.setdefault((c.table_idx, c.row_idx), []).append(c)
    out = []
    for key in sorted(rows):
        cells = rows[key]
        if all(_is_dash(c.text) for c in cells):
            continue
        out.append(" / ".join(f"{c.code}: {c.text}" for c in cells))
    return out


def _paragraphs(doc: DartDocument) -> list[str]:
    """신호 단어가 걸린 문단 + 바로 앞뒤 1개씩 + 각주 '주1)' 문단 (문서 순서)."""
    ps = [p.text for p in doc.body_paragraphs()]
    keep = set()
    for i, t in enumerate(ps):
        if any(w in t for w in NONCASH_WORDS):
            keep |= {i - 1, i, i + 1}
        if FOOTNOTE.match(t):
            keep.add(i)
    return [ps[i] for i in sorted(keep) if 0 <= i < len(ps)]


def build_input(doc: DartDocument, event_type: str, report_nm: str) -> str:
    first = lambda code: doc.first(code)  # noqa: E731
    L = ["[기본]", f"제목: {report_nm}"]
    if event_type == "유상증자":
        ci = first("CI_MTH")
        L.append(f"CI_MTH(증자 방식): {ci.text if ci else '-'}")
        L += ["", "[납입 자산]"] + [_cell_line(c) for code in ("PIN_EXT_YN", "PIN_VAL", "PAY_EXP_CNT") for c in [first(code)] if c]
    else:
        pym = first("PYM_DET")
        L.append(f"PYM_DET(납입 방법): {pym.text if pym else '-'}")
        L += ["", "[납입 자산]"] + _rows(doc, "TBL_PYM")
    L += ["", "[돈의 용도]"] + [_cell_line(c) for code in FUND_CODES[event_type] for c in [first(code)] if c]
    if event_type == "유상증자":
        L += [_cell_line(c) for c in doc.body_cells() if c.group == "THD_ASN_OBJ" and c.code == "OBJ" and not _is_dash(c.text)]
    L += _rows(doc, "TBL_FND2")
    group, codes = PEOPLE[event_type]
    L += ["", "[받는 사람]"] + _rows(doc, group, codes)
    L += ["", "[글]"] + _paragraphs(doc)
    return "\n".join(L)


def _norm(s: str) -> str:
    return " ".join(s.split())


def judge(client: anthropic.Anthropic, text: str) -> dict:
    """→ {payment_type, payment_evidence, reason, asset, debt, evidence_ok, raw_type, input_tokens, output_tokens, note}.
    근거 하나라도 입력에 없으면 payment_type None. 현물 · 빚→주식 · 섞임인데 asset/debt가 비면 불명."""
    r = client.beta.messages.parse(model=MODEL, max_tokens=4000, output_config={"effort": EFFORT}, system=SYSTEM_PROMPT,
                                   messages=[{"role": "user", "content": text}], output_format=PaymentV4)
    if r.stop_reason != "end_turn" or r.parsed_output is None:
        raise RuntimeError(f"stop_reason={r.stop_reason}")
    x = r.parsed_output
    src = _norm(text)
    ok = bool(x.evidence) and all(_norm(e) in src for e in x.evidence)
    ptype, note = x.payment_type, ""
    if ok and ((ptype == "현물" and not x.asset) or (ptype == "빚→주식" and not x.debt) or (ptype == "섞임" and not (x.asset or x.debt))):
        ptype, note = "불명", f"{x.payment_type}인데 asset/debt 없음 → 불명"
    if not ok:
        note = "근거가 입력에 없음 → 값 버림"
    prefix = (f"[자산: {x.asset}] " if x.asset else "") + (f"[빚: {x.debt}] " if x.debt else "")
    return {"payment_type": ptype if ok else None, "raw_type": x.payment_type, "payment_evidence": prefix + " / ".join(x.evidence),
            "asset": x.asset, "debt": x.debt, "reason": x.reason, "evidence_ok": ok, "note": note,
            "input_tokens": r.usage.input_tokens, "output_tokens": r.usage.output_tokens}
