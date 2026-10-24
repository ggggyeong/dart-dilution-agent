"""유상증자 원문 XML 구조 조사 (파서·분류 코드 아님).

    uv run python scripts/survey_xml_structure.py

결과: data/reports/xml_structure_rights_issue.md, data/reports/xml_view/*.html
양식 = 증자방식(CI_MTH) × 양식 버전(FORMULA-VERSION) × 원본/정정
"""

import html
import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from filingscope.config import DATA_DIR
from filingscope.db import connect
from filingscope.parse.acode import DartDocument, read_document, read_root
from filingscope.parse.xml_view import render_html

RAW = DATA_DIR / "raw" / "document"
OUT_MD = DATA_DIR / "reports" / "xml_structure_rights_issue.md"
VIEW_DIR = DATA_DIR / "reports" / "xml_view"

DOC_TYPES = {"11306": "주요사항보고서(유상증자)", "11308": "주요사항보고서(유무상증자)", "80518": "유상증자 결정(비표준)"}
KEY_CODES = ["PIN_EXT_YN", "CST_STD_VAL", "DC_RATE", "PRE_CST_VAL", "CST_ISS_VAL", "PART", "ALL_BS_DT"]
KEY_LABELS = {"PIN_EXT_YN": "현물출자", "CST_STD_VAL": "기준주가", "DC_RATE": "할인율", "PRE_CST_VAL": "예정발행가",
              "CST_ISS_VAL": "확정발행가", "PART": "대상자", "ALL_BS_DT": "신주배정기준일"}
NONCASH_WORDS = ["출자전환", "상계", "현물", "대용", "회생"]


@dataclass
class Doc:
    rcept_no: str
    corp_name: str
    rcept_dt: object
    is_correction: bool
    is_mixed: bool
    main_file: str
    title: str
    doc: DartDocument

    @property
    def year(self) -> str:
        return str(self.rcept_dt)[:4]

    @property
    def ci(self) -> str:
        c = self.doc.first("CI_MTH")
        if c is None:
            return "(CI_MTH 없음)"
        return f"{c.value} {c.text}" if c.value else f"? {c.text}"

    @property
    def version(self) -> str:
        return f"{self.doc.formula_version} ({self.doc.formula_adate})"

    @property
    def dtype(self) -> str:
        return f"{self.doc.doc_code} {DOC_TYPES.get(self.doc.doc_code, '')}".strip()

    @property
    def kind(self) -> str:
        return "정정" if self.is_correction else "원본"

    @property
    def codes(self) -> set[str]:
        return {c.code for c in self.doc.body_cells()}


def load(conn) -> list[Doc]:
    rows = conn.execute(
        """SELECT d.rcept_no, f.corp_name, f.rcept_dt, f.is_correction, f.is_mixed, d.main_file, f.report_nm
           FROM documents d JOIN title_filings f USING (rcept_no)
           WHERE f.event_type = 'rights_issue' AND d.main_file IS NOT NULL ORDER BY d.rcept_no""").fetchall()
    return [Doc(*r, read_document(RAW / r[0] / r[5])) for r in rows]


def md_table(headers, rows) -> list[str]:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(str(x).replace("|", "\\|") for x in r) + " |" for r in rows]
    return out


def code_labels(docs: list[Doc]) -> dict[str, str]:
    c = defaultdict(Counter)
    for d in docs:
        for cell in d.doc.body_cells():
            if cell.label:
                c[cell.code][cell.label[:30]] += 1
    return {k: v.most_common(1)[0][0] for k, v in c.items()}


def fmt_codes(codes, labels, counts=None, n=None) -> str:
    items = []
    for code in sorted(codes):
        s = f"`{code}`"
        if labels.get(code):
            s += f" {labels[code]}"
        if counts is not None:
            s += f" ({counts[code]}/{n})"
        items.append(s)
    return ", ".join(items) if items else "-"


def snippet(text: str, word: str, width: int = 50) -> str:
    i = text.find(word)
    return ("…" if i > width else "") + text[max(0, i - width): i + len(word) + width] + ("…" if i + len(word) + width < len(text) else "")


def diff_versions(docs: list[Doc], labels) -> list[str]:
    """같은 증자방식끼리 버전 순서대로 칸 코드 합집합 비교. 이름이 바뀐 것으로 보이는 것 = 없어진 코드와 새 코드의 항목명이 같음."""
    out = []
    by_ci = defaultdict(lambda: defaultdict(set))
    for d in docs:
        by_ci[f"{d.dtype} · {d.ci}"][d.version] |= d.codes
    for ci in sorted(by_ci):
        versions = sorted(by_ci[ci])
        if len(versions) < 2:
            out.append(f"- **{ci}**: 버전 1개 ({versions[0]}) — 비교 대상 없음")
            continue
        out.append(f"- **{ci}**")
        for a, b in zip(versions, versions[1:]):
            new, gone = by_ci[ci][b] - by_ci[ci][a], by_ci[ci][a] - by_ci[ci][b]
            renamed = [(g, n) for g in gone for n in new if labels.get(g) and labels.get(g) == labels.get(n)]
            out.append(f"  - {a} → {b}: 새로 생김 {fmt_codes(new, labels)} / 없어짐 {fmt_codes(gone, labels)}"
                       + (f" / 이름 바뀐 것으로 보임 {', '.join(f'`{g}`→`{n}`' for g, n in renamed)}" if renamed else ""))
    return out


def main() -> None:
    with connect() as conn:
        docs = load(conn)
        fetched = conn.execute("""SELECT count(*) FROM title_filings WHERE event_type='rights_issue' AND rcept_dt >= '2026-01-01'""").fetchone()[0]
        failed_rows = conn.execute("""SELECT DISTINCT l.rcept_no, f.report_tag FROM collect_log l JOIN title_filings f USING (rcept_no)
                                     WHERE l.endpoint='document.xml' AND NOT l.ok
                                     AND l.rcept_no NOT IN (SELECT rcept_no FROM documents)""").fetchall()
        failed = len(failed_rows)
        multi = conn.execute("""SELECT rcept_no, main_file, files FROM documents WHERE jsonb_array_length(files) > 1""").fetchall()
    new = [d for d in docs if d.year == "2026"]
    old = [d for d in docs if d.year != "2026"]
    labels = code_labels(docs)
    L = ["# 유상증자 원문 XML 구조 조사", "",
         f"- 2026년 유상증자 공시 {fetched}건 중 원문 확보 {len(new)}건 (받기 실패 {failed}건)",
         f"- 2023~2025 비교 표본 {len(old)}건",
         "- 양식 = 증자방식(CI_MTH) × 양식 버전(FORMULA-VERSION) × 원본/정정. 칸 코드는 **본문**(정정 신고 부분 제외) 기준", ""]

    # 문서코드
    L += ["## 1. 문서코드", ""]
    dc = Counter((d.doc.doc_code, d.doc.doc_name) for d in new)
    L += md_table(["문서코드", "문서 이름", "건수"], [(c, n, k) for (c, n), k in dc.most_common()])
    odd = [d for d in new if d.doc.doc_code != "11306"]
    L += ["", f"문서코드가 11306이 아닌 공시: **{len(odd)}건**"]
    if odd:
        L += md_table(["rcept_no", "회사", "문서코드", "문서 이름", "증자방식"],
                      [(d.rcept_no, d.corp_name, d.doc.doc_code, d.doc.doc_name, d.ci) for d in odd])
    L += ["", f"ZIP 안 파일이 2개 이상인 공시: {len(multi)}건 (본문은 `{{rcept_no}}.xml`, 나머지는 첨부)"]
    att = Counter((f.get("doc_code"), f.get("doc_name")) for _, main, files in multi for f in files if f["name"] != main)
    if multi:
        L += ["본문이 아닌 파일:"] + md_table(["문서코드", "문서 이름", "개수"], [(c, n, k) for (c, n), k in att.most_common(15)])

    # 양식 버전
    L += ["", "## 2. 양식 버전 (2026)", ""]
    by_v = defaultdict(list)
    for d in new:
        by_v[d.version].append(d)
    vrows = []
    for v, ds in sorted(by_v.items()):
        dates = sorted(d.rcept_dt for d in ds)
        vrows.append((v, len(ds), dates[0], dates[-1], sum(not d.is_correction for d in ds), sum(d.is_correction for d in ds)))
    L += md_table(["버전 (ADATE)", "건수", "첫 공시일", "마지막 공시일", "원본", "정정"], vrows)
    L += ["", "※ 정정본은 옛 사건이라도 정정 시점의 양식으로 제출되는지, 공시일 순으로 버전이 섞이는지 위 날짜 범위로 판단"]

    # CI_MTH 대응표
    L += ["", "## 3. CI_MTH 값 ↔ 화면 글자", ""]
    ci_map = Counter()
    for d in docs:
        c = d.doc.first("CI_MTH")
        ci_map[(c.value if c else None, c.text if c else None)] += 1
    L += md_table(["CI_MTH 값", "화면 글자", "건수(전체)"], [(v, t, n) for (v, t), n in sorted(ci_map.items(), key=lambda x: str(x[0]))])

    # 양식별
    L += ["", "## 4. 양식별 칸 구성 (2026)", ""]
    forms = defaultdict(list)
    for d in new:
        forms[(d.dtype, d.ci, d.version, d.kind)].append(d)
    L += md_table(["문서", "증자방식", "버전", "원본/정정", "건수"] + [f"{KEY_LABELS[k]}<br>`{k}`" for k in KEY_CODES],
                  [(*k, len(ds), *[f"{sum(c in d.codes for d in ds)}/{len(ds)}" for c in KEY_CODES])
                   for k, ds in sorted(forms.items())])
    for (dtype, ci, v, kind), ds in sorted(forms.items()):
        cnt = Counter(c for d in ds for c in d.codes)
        always = {c for c, n in cnt.items() if n == len(ds)}
        some = {c for c, n in cnt.items() if n < len(ds)}
        L += ["", f"### {dtype} · {ci} · {v} · {kind} ({len(ds)}건)", "",
              f"- 항상 나오는 칸 ({len(always)}개): {fmt_codes(always, labels)}",
              f"- 일부에만 나오는 칸 ({len(some)}개): {fmt_codes(some, labels, cnt, len(ds))}"]
        no_dc = [d for d in ds if "DC_RATE" not in d.codes]
        if no_dc:
            ex = []
            for d in no_dc:
                for p in d.doc.body_paragraphs():
                    if "할인" in p.text:
                        ex.append((d, p.text))
                        break
                if len(ex) >= 3:
                    break
            L += [f"- 할인율 칸(`DC_RATE`)이 없는 공시 {len(no_dc)}건 — 자유 서술의 할인율 표현 예시:"]
            L += [f"  - {d.rcept_no} {d.corp_name}: “{snippet(t, '할인', 80)}”" for d, t in ex] or ["  - (자유 서술에 '할인' 없음)"]

    # 정정본 구분
    corr = [d for d in new if d.is_correction]
    orig = [d for d in new if not d.is_correction]
    in_corr_cells = sum(1 for d in corr for c in d.doc.cells if c.in_correction)
    L += ["", "## 5. 정정본: 정정 신고 부분과 본문 구분", "",
          "- 방법: 정정 신고 부분은 `<LIBRARY><CORRECTION>` 요소로 감싸져 있다. **`<CORRECTION>` 안이면서 본문 섹션(`SECTION-*`) 밖 = 정정 신고**, 나머지 = 본문.",
          "- 대부분은 본문 섹션이 `<CORRECTION>` 밖에 있지만, 본문 섹션이 `<CORRECTION>` 안에 들어간 공시도 있다 (아래 8절). 그래서 '`<CORRECTION>` 밖 = 본문'만으로는 본문 값을 못 뽑는 경우가 생긴다.",
          f"- 정정본 {len(corr)}건 중 `<CORRECTION>` 있음 **{sum(d.doc.has_correction for d in corr)}건**",
          f"- 원본 {len(orig)}건 중 `<CORRECTION>` 있음 **{sum(d.doc.has_correction for d in orig)}건**",
          f"- `<CORRECTION>` 안에 코드 칸이 있는 경우: **{in_corr_cells}칸** (0이면 코드 기준 추출만으로 정정 전/후 값이 섞이지 않음)"]
    no_corr = [d for d in corr if not d.doc.has_correction]
    if no_corr:
        L += ["- `<CORRECTION>`이 없는 정정본:"] + md_table(
            ["rcept_no", "회사", "제목"], [(d.rcept_no, d.corp_name, d.title) for d in no_corr[:20]])

    # 현금 외 단어
    L += ["", "## 6. 자유 서술에 현금 외 납입 단어가 나오는 공시 (2026, 본문의 코드 없는 <P>만)", ""]
    hits = []
    for d in new:
        for w in NONCASH_WORDS:
            p = next((p for p in d.doc.body_paragraphs() if w in p.text), None)
            if p:
                hits.append((d, w, p.text))
    wc = Counter(w for _, w, _ in hits)
    L += [f"공시 {len({d.rcept_no for d, _, _ in hits})}건 · 단어별: " + ", ".join(f"{w} {wc[w]}" for w in NONCASH_WORDS), ""]
    L += md_table(["rcept_no", "회사", "원본/정정", "증자방식", "단어", "문맥"],
                  [(d.rcept_no, d.corp_name, d.kind, d.ci, w, snippet(t, w, 40)) for d, w, t in hits])

    # 버전 비교
    L += ["", "## 7. 버전 비교", "", "### 2026년 안", ""] + diff_versions(new, labels)
    L += ["", "### 2023~2025 표본", ""]
    by_yv = defaultdict(list)
    for d in old:
        by_yv[(d.year, d.version)].append(d)
    L += md_table(["연도", "버전", "건수", "증자방식"],
                  [(y, v, len(ds), ", ".join(sorted({d.ci for d in ds}))) for (y, v), ds in sorted(by_yv.items())])
    L += ["", "#### 2026 최신 버전의 핵심 칸(항상 나오는 칸)과 비교 (문서코드 × 증자방식별)", "",
          "- 핵심 칸 = 2026년 최신 버전 원본에서 100% 나오는 칸. 가끔만 나오는 칸은 비교에서 뺐다 (표본이 작아 생기는 착시 방지)", ""]
    core = {}
    for key in {(d.dtype, d.ci) for d in new}:
        ds = [d for d in new if (d.dtype, d.ci) == key and not d.is_correction]
        if not ds:
            continue
        latest = max(d.version for d in ds)
        ds = [d for d in ds if d.version == latest]
        core[key] = (latest, set.intersection(*(d.codes for d in ds)), set().union(*(d.codes for d in ds)))
    for key in sorted({(d.dtype, d.ci) for d in old}):
        for v in sorted({d.version for d in old if (d.dtype, d.ci) == key}):
            ds = [d for d in old if (d.dtype, d.ci) == key and d.version == v]
            codes_all = set().union(*(d.codes for d in ds))
            if key not in core:
                L.append(f"- **{key[0]} · {key[1]}** · {v} ({len(ds)}건): 2026년 원본에 같은 양식 없음")
                continue
            latest, core_codes, any_codes = core[key]
            missing = {c for c in core_codes if not all(c in d.codes for d in ds)}
            only_old = codes_all - any_codes
            renamed = [(o, n) for o in only_old for n in missing if labels.get(o) and labels.get(o) == labels.get(n)]
            L.append(f"- **{key[0]} · {key[1]}** · {v} ({len(ds)}건) vs 2026 {latest}: "
                     f"핵심 칸 중 없음 {fmt_codes(missing, labels)} / 옛 버전에만 {fmt_codes(only_old, labels)}"
                     + (f" / 이름 바뀐 것으로 보임 {', '.join(f'`{o}`→`{n}`' for o, n in renamed)}" if renamed else ""))

    # 추가 확인
    L += ["", "## 8. 추가 확인", ""]
    no_ci = [d for d in docs if d.doc.first("CI_MTH") is None]
    L += [f"### CI_MTH 칸이 없는 공시 {len(no_ci)}건", ""]
    L += md_table(["rcept_no", "회사", "문서", "버전", "원본/정정", "본문 코드 칸 수", "SECTION 제목"],
                  [(d.rcept_no, d.corp_name, d.dtype, d.version, d.kind, len(d.doc.body_cells()),
                    ", ".join(sorted({c.section or "" for c in d.doc.cells}))[:60]) for d in no_ci])
    nested = []
    for d in docs:
        if d.doc.has_correction:
            corr = read_root(RAW / d.rcept_no / d.main_file).find(".//CORRECTION")
            if corr is not None and corr.find(".//SECTION-1") is not None:
                nested.append(d)
    L += ["", f"### 본문 섹션(SECTION-1)이 <CORRECTION> 안에 들어간 정정본 {len(nested)}건", "",
          "옛 규칙('<CORRECTION> 밖 = 본문')이었다면 본문 코드 칸을 하나도 못 뽑았을 공시. 새 규칙으로 정상 추출됨.", ""]
    L += md_table(["rcept_no", "회사", "문서", "버전", "본문 코드 칸 수"],
                  [(d.rcept_no, d.corp_name, d.dtype, d.version, len(d.doc.body_cells())) for d in nested])
    cc_docs = [d for d in new if any(c.in_correction for c in d.doc.cells)]
    L += ["", f"### 정정 신고 부분(<CORRECTION> 안, 본문 섹션 밖)에 코드 칸이 있는 정정본 {len(cc_docs)}건", ""]
    L += md_table(["rcept_no", "회사", "정정 신고 부분 코드 칸", "그중 본문에도 있는 코드"],
                  [(d.rcept_no, d.corp_name, len([c for c in d.doc.cells if c.in_correction]),
                    f"{len({c.code for c in d.doc.cells if c.in_correction} & d.codes)}/{len({c.code for c in d.doc.cells if c.in_correction})}")
                   for d in cc_docs])
    L += ["", "→ 이 칸들은 in_correction으로 표시돼 본문 추출에서 빠진다. 본문에도 같은 코드가 있으면 본문 값을 쓴다."]
    L += ["", f"### 원문 받기 실패 {len(failed_rows)}건 (014 파일이 존재하지 않습니다)", ""]
    L += md_table(["report_tag", "건수"], Counter(t for _, t in failed_rows).most_common())

    # HTML
    VIEW_DIR.mkdir(parents=True, exist_ok=True)
    reps = {}
    for d in sorted(docs, key=lambda d: (d.is_correction, -len(d.doc.body_cells()))):
        reps.setdefault((d.dtype, d.ci, d.version), d)
    index = []
    for (dtype, ci, v), d in sorted(reps.items()):
        name = re.sub(r"[^0-9A-Za-z가-힣.]+", "_", f"{d.doc.doc_code}_{ci}_{v}_{d.rcept_no}") + ".html"
        meta = {"rcept_no": d.rcept_no, "회사": d.corp_name, "공시일": d.rcept_dt, "문서": dtype, "증자방식": ci, "양식 버전": v,
                "원본/정정": d.kind, "문서코드": d.doc.doc_code}
        (VIEW_DIR / name).write_text(render_html(RAW / d.rcept_no / d.main_file, f"{ci} {v}", meta, "index.html"), encoding="utf-8")
        index.append((f"{dtype} · {ci}", v, d, name))
    (VIEW_DIR / "index.html").write_text(
        "<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>유상증자 양식별 대표 공시</title><style>body{font:14px/1.6 sans-serif;margin:16px}td,th{border:1px solid #ccc;padding:4px 8px}"
        "table{border-collapse:collapse}@media(prefers-color-scheme:dark){body{background:#111827;color:#e5e7eb}a{color:#93c5fd}}</style></head><body>"
        "<h2>유상증자 양식(문서 × 증자방식 × 버전)별 대표 공시</h2><table><tr><th>문서 · 증자방식</th><th>버전</th><th>공시</th><th>원본/정정</th></tr>"
        + "".join(f"<tr><td>{html.escape(ci)}</td><td>{html.escape(v)}</td><td><a href='{html.escape(n)}'>{d.rcept_no} {html.escape(d.corp_name)}</a></td><td>{d.kind}</td></tr>"
                  for ci, v, d, n in index) + "</table></body></html>", encoding="utf-8")
    L += ["", "## 9. 양식별 대표 공시 화면", "", f"`data/reports/xml_view/index.html` ({len(index)}개)", ""]
    L += md_table(["문서 · 증자방식", "버전", "대표 공시", "파일"], [(ci, v, f"{d.rcept_no} {d.corp_name} ({d.kind})", n) for ci, v, d, n in index])

    OUT_MD.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"→ {OUT_MD}  ({len(new)} + {len(old)}건, 양식 {len(forms)}개, 화면 {len(index)}개)")


if __name__ == "__main__":
    main()
