"""DART 원문 XML 범용 읽기. 공시 종류(유상증자·CB 등)와 상관없이 쓴다.

- 코드가 붙은 칸: TE(ACODE, 값 입력 칸), TU(AUNIT + AUNITVALUE, 선택형 칸)
- 정정본 앞부분 '정정 신고'는 <CORRECTION> 요소로 감싸져 있다 → in_correction으로 표시
  단, 본문 섹션(SECTION-*)이 <CORRECTION> 안에 들어간 공시도 있다(2026년 유상증자 4건 등).
  그래서 "<CORRECTION> 안이면서 SECTION-* 밖"만 정정 신고로 본다 (받은 원문 전체에서 이 규칙이 성립함을 확인)
- 자유 서술: 코드가 붙은 칸 밖의 <P>
- 본문 글에 나오는 꺾쇠괄호("<증자결정 전>", "<개정 2018.>", "<Better ...")는 태그가 아니다. 그대로 두면 XML 해석기가
  태그로 착각해 표 줄이 깨진다(대상자 주식 수를 못 읽음). 그래서 DART가 실제로 쓰는 태그 이름(DART_TAGS)이 아닌 '<'는
  읽기 전에 글자(&lt;)로 바꾼다.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from lxml import etree

_SPACES = re.compile(r"\s+")
_PARSER = etree.XMLParser(recover=True, huge_tree=True)
# 받은 원문에서 실제로 쓰인 태그 이름 (2026-10-17 조사: 유상증자 원본 1,594건의 23개 — 모두 열기 수 = 닫기 수 —
# + 정정본의 CORRECTION). 전체 4,220개 파일에서 이 밖의 이름은 닫히지 않는 본문 글자뿐("<Better", "<TECH").
# 새 공시 종류(CB 등)를 읽기 전에 다시 조사해 빠진 태그가 없는지 확인한다.
DART_TAGS = frozenset({
    "DOCUMENT", "DOCUMENT-NAME", "FORMULA-VERSION", "COMPANY-NAME", "BODY", "TITLE", "SECTION-1", "LIBRARY", "PGBRK",
    "CORRECTION", "TABLE-GROUP", "TABLE", "COLGROUP", "COL", "THEAD", "TBODY", "TR", "TH", "TD", "TE", "TU", "P", "SPAN", "A",
})
_NON_TAG_LT = re.compile(rb"<(?![?!]|/?(?:" + b"|".join(sorted((t.encode() for t in DART_TAGS), key=len, reverse=True))
                         + rb")[\s>/])")


def escape_non_tags(raw: bytes) -> bytes:
    """DART 태그가 아닌 '<'를 글자(&lt;)로 바꾼다. XML 선언(<?)과 주석(<!)은 그대로."""
    return _NON_TAG_LT.sub(b"&lt;", raw)
CELL_TAGS = {"TD", "TH", "TE", "TU"}


def text_of(el) -> str:
    return _SPACES.sub(" ", "".join(el.itertext())).strip()


@dataclass(frozen=True)
class CodedCell:
    code: str  # ACODE 또는 AUNIT 값
    kind: str  # "ACODE" | "AUNIT"
    value: str | None  # AUNITVALUE (선택형 칸의 코드값)
    text: str  # 화면 글자
    label: str | None  # 같은 줄 앞쪽의 코드 없는 칸 글자 (화면 항목명)
    section: str | None  # SECTION-1 제목
    table_idx: int  # 문서 안 TABLE 순번
    row_idx: int  # 그 TABLE 안 TR 순번
    in_correction: bool
    group: str | None = None  # 속한 TABLE-GROUP의 ACLASS (⑨-2: 부속 표를 가려 읽기)


@dataclass(frozen=True)
class FreeText:
    text: str
    section: str | None
    in_table: bool  # 표 칸 안의 <P>인지
    in_correction: bool


@dataclass
class DartDocument:
    doc_code: str | None
    doc_name: str | None
    formula_version: str | None
    formula_adate: date | None
    company: str | None
    has_correction: bool
    cells: list[CodedCell] = field(default_factory=list)
    paragraphs: list[FreeText] = field(default_factory=list)

    def body_cells(self) -> list[CodedCell]:
        return [c for c in self.cells if not c.in_correction]

    def body_paragraphs(self) -> list[FreeText]:
        return [p for p in self.paragraphs if not p.in_correction]

    def first(self, code: str) -> CodedCell | None:
        return next((c for c in self.body_cells() if c.code == code), None)


def read_root(source: Path | bytes):
    raw = bytes(source) if isinstance(source, (bytes, bytearray)) else Path(source).read_bytes()
    return etree.fromstring(escape_non_tags(raw), _PARSER)


def doc_header(root) -> tuple[str | None, str | None, str | None, date | None, str | None]:
    """(doc_code, doc_name, formula_version, formula_adate, company)"""
    name = root.find(".//DOCUMENT-NAME")
    fv = root.find(".//FORMULA-VERSION")
    adate = None
    if fv is not None and fv.get("ADATE"):
        try:
            adate = datetime.strptime(fv.get("ADATE"), "%Y%m%d").date()
        except ValueError:
            pass
    return (
        name.get("ACODE") if name is not None else None,
        text_of(name) if name is not None else None,
        text_of(fv) if fv is not None else None,
        adate,
        text_of(root.find(".//COMPANY-NAME")) if root.find(".//COMPANY-NAME") is not None else None,
    )


def read_document(source: Path | bytes) -> DartDocument:
    root = read_root(source)
    doc_code, doc_name, version, adate, company = doc_header(root)
    doc = DartDocument(doc_code, doc_name, version, adate, company,
                       has_correction=root.find(".//CORRECTION") is not None)

    table_idx = -1
    tree = root.getroottree()
    row_pos: dict[str, int] = {}  # TR의 문서 내 경로 → 그 TABLE 안 순번 (lxml 요소는 id()가 매번 달라 경로로 구분)

    def walk(el, section, in_corr, in_coded, in_cell, cur_table, group=None):
        nonlocal table_idx
        tag = el.tag if isinstance(el.tag, str) else None
        if tag is None:
            return
        if tag == "TABLE-GROUP":
            group = (el.get("ACLASS") or "").strip() or group
        if tag == "CORRECTION":
            in_corr = True
        elif tag.startswith("SECTION-"):
            in_corr = False  # 본문 섹션은 <CORRECTION> 안에 있어도 본문
            if tag == "SECTION-1":
                title = el.find("TITLE")
                section = text_of(title) if title is not None else section
        elif tag == "TABLE":
            table_idx += 1
            cur_table = table_idx
            for i, tr in enumerate(el.iter("TR")):
                row_pos.setdefault(tree.getpath(tr), i)

        code, kind = (el.get("ACODE"), "ACODE") if el.get("ACODE") else (el.get("AUNIT"), "AUNIT")
        if code and tag in CELL_TAGS:
            tr = el.getparent()
            doc.cells.append(CodedCell(
                code=code, kind=kind, value=el.get("AUNITVALUE"), text=text_of(el),
                label=_row_label(el), section=section, table_idx=cur_table,
                row_idx=row_pos.get(tree.getpath(tr), -1) if tr is not None else -1, in_correction=in_corr, group=group,
            ))
            in_coded = True
        if tag == "P" and not in_coded:
            t = text_of(el)
            if t:
                doc.paragraphs.append(FreeText(t, section, in_cell, in_corr))
        for child in el:
            walk(child, section, in_corr, in_coded, in_cell or tag in CELL_TAGS, cur_table, group)

    walk(root, None, False, False, False, -1)
    return doc


def _row_label(cell) -> str | None:
    """같은 줄에서 이 칸 앞에 있는 코드 없는 칸들 중 가장 가까운 글자."""
    for prev in cell.itersiblings(preceding=True):  # 가까운 칸부터
        if isinstance(prev.tag, str) and prev.tag in CELL_TAGS and not (prev.get("ACODE") or prev.get("AUNIT")):
            t = text_of(prev)
            if t:
                return t
    return None
