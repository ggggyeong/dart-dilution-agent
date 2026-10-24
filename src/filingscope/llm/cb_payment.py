"""CB 납입 방식 판정 (명세 docs/cb_spec.md 2-3): 13번 납입방법이 "기타"인 CB만.

유상증자 납입 방식 판정(extract-v3: Opus 5.5, effort medium, 같은 지시문 · 출력)을 그대로 쓰고,
지시문의 대상 낱말만 CB로 바꾼다 ("신주 대금" → "사채 인수 대금" 등). 정답표용도 같은 방식으로 바꾼다.
입력: 부속 표 TBL_PYM(납입자산 · 상세 내역 · 평가방법). 표가 없으면 본문 자유 서술("기타 투자판단에 참고할 사항" 글).
"""

from pathlib import Path

from filingscope.llm import answer_key as ak
from filingscope.llm import extractor as ex
from filingscope.parse.acode import read_document, read_root, text_of
from filingscope.parse.sections import Section

PROMPT_VERSION = "cb-v2"  # v2: 매매대금 상계 = 현물 규칙 (사용자 2026-10-17). v1: 정답표 20건 16/20
SOURCE = f"llm:{PROMPT_VERSION}"
MODEL = "claude-opus-5-5"


def _cb(prompt: str, pairs: list[tuple[str, str]]) -> str:
    for a, b in pairs:
        assert a in prompt, a
        prompt = prompt.replace(a, b)
    return prompt


_WORDS = [("신주 대금을 무엇으로 내는지", "전환사채 인수 대금(사채 납입 대금)을 무엇으로 내는지"),
          ("같은 증자에서", "같은 사채 발행에서"),
          ("과거 다른 증자의 내역", "과거 다른 증자 · 사채의 내역"),
          ("이번 증자의 납입 방식", "이번 사채의 납입 방식")]
SYSTEM_PROMPT = _cb(ex.SYSTEM_PROMPT.split("\n\n[할인율 discount]")[0], [
    ("유상증자 공시(주요사항보고서)의 원문 전체 또는 잘라낸 부분", "전환사채(CB) 발행결정 공시(주요사항보고서)에서 잘라낸 부분"),
    *_WORDS])
KEY_SYSTEM_PROMPT = _cb(ak.SYSTEM_PROMPT.split("\n\n[할인율 discount]")[0], [
    ("유상증자 공시(주요사항보고서) 원문", "전환사채(CB) 발행결정 공시(주요사항보고서) 원문"),
    ("신주 대금을 무엇으로 내는지", "전환사채 인수 대금(사채 납입 대금)을 무엇으로 내는지"),
    ("같은 증자에서", "같은 사채 발행에서"),
    ("- 불명: 원문으로 판단할 수 없음\n", "- 불명: 원문으로 판단할 수 없음\n상계·대용납입이라도 상계되는 채권이 같은 거래에서 자산(주식·부동산 등)을 사며 생긴 매매대금이면 현물, 그 전부터 있던 빚(대여금·차입금·예전 사채 원금)이면 빚→주식.\n")])


def payment_input(path: Path) -> tuple[str, str]:
    """(입력 글, 출처). TBL_PYM 표가 있으면 표를 줄마다 ' | '로, 없으면 자유 서술 전체."""
    root = read_root(path)
    tg = next((g for g in root.iter("TABLE-GROUP") if (g.get("ACLASS") or "").strip() == "TBL_PYM"), None)
    if tg is not None:
        rows = []
        for tr in tg.iter("TR"):
            cells = [text_of(c) for c in tr if isinstance(c.tag, str) and c.tag in ("TD", "TH", "TE", "TU")]
            if any(cells):
                rows.append(" | ".join(cells))
        return "[납입자산 표]\n" + "\n".join(rows), "TBL_PYM"
    doc = read_document(path)
    return "[기타 투자판단에 참고할 사항 등 자유 서술]\n" + "\n".join(p.text for p in doc.body_paragraphs()), "본문"


def judge(client, path: Path) -> dict:
    text, src = payment_input(path)
    row = {"input_source": src, "error": ""}
    try:
        out, usage, _ = ex.extract(client, MODEL, {"payment": Section(text, False)}, SYSTEM_PROMPT)
    except Exception as e:
        row["error"] = f"{type(e).__name__}: {e}"[:300]
        return row
    x = out.payment
    row.update(payment_type=x.payment_type, payment_indirect=x.indirect, payment_evidence=x.evidence,
               payment_reason=x.reason, payment_evidence_ok=ak.evidence_in_text(x.evidence, text),
               input_tokens=usage.input_tokens, output_tokens=usage.output_tokens)
    return row


def label(client, path: Path) -> dict:
    """정답표: 원문 전체, effort high."""
    text = ak.document_text(path)
    row = {"error": ""}
    try:
        out, usage, _ = ak.label(client, text, ["payment"], KEY_SYSTEM_PROMPT)
    except Exception as e:
        row["error"] = f"{type(e).__name__}: {e}"[:300]
        return row
    x = out.payment
    row.update(payment_type=x.payment_type, payment_evidence=x.evidence, payment_reason=x.reason,
               payment_evidence_ok=ak.evidence_in_text(x.evidence, text),
               input_tokens=usage.input_tokens, output_tokens=usage.output_tokens)
    return row
