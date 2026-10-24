"""실제 판정용 LLM에 줄 입력: 원문에서 과제별로 필요한 부분만 자른다 (명세 6장).

- 납입 방식 (제3자배정): extract-v3부터 원문 전체 (payment_full). 아래 잘라 넣는 방식(payment_section)은 extract-v1~v2.
  extract-v1~v2: 20번 등 자유 서술(코드 없는 <P>), 목적 표 OBJ, 4번 자금 용도, 13번 현물출자 칸(PIN_*),
  17번 면제 사유 RPT_SBM, 대상자 이름 · 관계. 정관 RULE 칸은 넣지 않는다.
- 할인율 · 권리 매도 기간 (주주배정): 자유 서술 전체(코드 없는 <P>), 길이 제한 없음.
  24번 "기타 투자판단에 참고할 사항" 머리말은 대부분 코드 칸(ISS_FUNC) 안에 있어 <P>에서 찾을 수 없고,
  자유 서술이 사실상 24번 글이다 (주주배정 45건: 2,470~7,400자).
  소제목 단위로 자르면 하위 번호에서 끊겨 정답 문장이 빠졌다 (extract-v1, 리츠 서식 포함 7건).
"""

from dataclasses import dataclass
from pathlib import Path

from filingscope.parse.acode import DartDocument, read_document

FUND = [("FND_USE1", "시설자금"), ("FND_USE_SQ", "영업양수자금"), ("FND_USE2", "운영자금"), ("FND_USE_RD", "채무상환자금"),
        ("ANC_ACQ_AMT", "타법인 증권 취득자금"), ("FND_USE3", "기타자금")]


@dataclass
class Section:
    text: str
    fallback: bool  # 머리말을 못 찾아 더 넓게 넣었는가


def _free_text(doc: DartDocument) -> str:
    return "\n".join(p.text for p in doc.body_paragraphs())


def payment_section(doc: DartDocument) -> Section:
    parts = []
    funds = [(lab, doc.first(code).text) for code, lab in FUND if doc.first(code) is not None]
    if funds:
        parts.append("[4. 자금조달의 목적] " + " / ".join(f"{lab}: {v}" for lab, v in funds))
    pin = [f"{c.label or c.code}: {c.text}" for c in doc.body_cells() if c.code.startswith("PIN_")]
    if pin:
        parts.append("[13. 현물출자] " + " / ".join(pin))
    rpt = doc.first("RPT_SBM")
    if rpt is not None:
        parts.append(f"[17. 증권신고서 제출 면제 사유] {rpt.text}")
    obj = [c.text for c in doc.body_cells() if c.code == "OBJ"]
    if obj:
        parts.append("[제3자배정 목적] " + " ".join(obj))
    rows: dict[tuple[int, int], dict[str, str]] = {}
    for c in doc.body_cells():
        if c.code in ("PART", "RLT") and c.row_idx >= 0:
            rows.setdefault((c.table_idx, c.row_idx), {})[c.code] = c.text
    if rows:
        parts.append("[대상자] " + "; ".join(f"{r.get('PART', '')} ({r.get('RLT', '-')})" for _, r in sorted(rows.items())))
    parts.append("[기타 투자판단에 참고할 사항 등 자유 서술]\n" + _free_text(doc))  # 길이 제한 없음 (8,000자 제한 때 2023~2025년 10건이 잘림)
    return Section("\n".join(parts), False)


def discount_section(doc: DartDocument) -> Section:
    return Section(_free_text(doc), False)


def right_sale_section(doc: DartDocument) -> Section:
    return Section(_free_text(doc), False)


def payment_full(path: Path) -> Section:
    """납입 방식 입력 (extract-v3~): 원문 전체. 잘라 넣으면 대상자 표의 거래내역 칸 · 연도별 자금 사용 표 · 같은 날 다른 공시
    언급 같은 단서가 빠진다 (2023~2025 새 표본: 아스트, 페니트리움바이오)."""
    from filingscope.llm.answer_key import document_text
    return Section(document_text(path), False)


def sections_for(path: Path, tasks: list[str]) -> dict[str, Section]:
    doc = read_document(path)
    fn = {"discount": discount_section, "right_sale": right_sale_section}
    return {t: payment_full(path) if t == "payment" else fn[t](doc) for t in tasks}
