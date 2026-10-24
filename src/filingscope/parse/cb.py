"""전환사채(CB) 원본 → cb_values · allottees 한 줄 (명세 docs/cb_spec.md 2장). LLM 없음, 모든 칸이 코드 칸.

번호가 아니라 ACODE로 읽는다 (서식 버전에 따라 번호가 하나씩 당겨짐). 유상증자 추출기의 XML 읽기 ·
태그 목록(DART_TAGS) · "-" 처리(to_number · to_date)를 그대로 쓴다.
"""

import re
from dataclasses import dataclass, field
from datetime import date

from filingscope.parse.acode import CodedCell, DartDocument, read_document
from filingscope.parse.rights_issue import _is_dash, noncash_signals, norm_relation, to_date, to_int, to_number

EXTRACTOR_VERSION = "cb-v5"  # v5 (2026-10-24): currency = 원문 AMT_UNIT (권면 통화, "-"면 NULL)
# v4 (calc_criteria v1 B-2): 같은 날 공시한 형제 CB(금액 · 전환가액 · 회차가 같은 잔량표 줄)는 old_cb_shares에서 뺌
# v3: old_cb_shares = 잔량표 줄 중 전환종료일 ≥ 공시일인 줄 합 (소계 안 씀, 교환사채 줄 뺌)
# v2 (⑨-2): 납입 1단계 신호(PYM_DET · TBL_PYM · 제목 · 글) · exempt_reason "-" → NULL · date_flag pay_date≥maturity · relation 정규화
FUNDS = {"fund_facility": "FND_USE1", "fund_business_acq": "FND_USE_SQ", "fund_operating": "FND_USE2",
         "fund_debt": "FND_USE_RD", "fund_other_corp": "ANC_ACQ_PRC", "fund_other": "FND_USE3"}
VALUE_COLS = ["amount", "conv_price", "conv_shares", "shares_before", "min_price", "old_cb_shares", *FUNDS,
              "issue_method", "exempt_reason", "conv_start", "conv_end", "maturity", "pay_date", "coupon", "ytm", "date_flag", "currency"]


@dataclass
class CbAllottee:
    name: str
    relation: str | None
    amount: int | None


@dataclass
class CbExtract:
    values: dict
    allottees: list[CbAllottee] = field(default_factory=list)
    payment_type: str | None = None
    payment_source: str | None = None
    pym_det: str | None = None          # 13번 납입방법 원문 (보고용)
    old_cb_rule: str = ""               # old_cb_shares를 정한 방식 (보고용)
    sibling_rows: list[str] = field(default_factory=list)   # 형제 CB라서 뺀 잔량표 줄의 사채 종류 (보고용)
    signals: list[str] = field(default_factory=list)   # ⑨-2 1단계 비현금 신호 (LLM 대기 이유)


def _cells(doc: DartDocument) -> list[CodedCell]:
    return [c for c in doc.body_cells() if not c.in_correction]


def _first(cells: list[CodedCell], code: str) -> CodedCell | None:
    return next((c for c in cells if c.code == code), None)


def _text(cells, code) -> str | None:
    c = _first(cells, code)
    return c.text if c is not None else None


def issue_method(pl_knd: str | None, frn_amt: str | None, issu_mth: str | None) -> str | None:
    """PL_KND에 '해외'가 있거나 FRN_AMT가 '-'가 아니면 해외, 아니면 ISSU_MTH (사모 / 공모).
    AMT_UNIT에 'USD'만 있고 FRN_AMT가 '-'인 공시는 해외로 보지 않는다 (AMT_UNIT은 보지 않음)."""
    if "해외" in (pl_knd or "") or not _is_dash(frn_amt):
        return "해외"
    m = (issu_mth or "").strip()
    return m if m in ("사모", "공모") else None


EB = re.compile(r"교환\s*사채|(?<![A-Za-z])EB(?![A-Za-z])")   # 교환사채: 회사가 가진 자기주식을 줌 → 새 주식 아님
_DATE = re.compile(r"(\d{4})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})")


def period_end(text: str | None) -> date | None:
    """잔량표 EXE_DT(전환청구기간) '2020년 08월 13일 ~ 2024년 08월 13일' → 두 번째 날짜. 날짜가 둘이 아니면 None (종료일 없음)."""
    ds = _DATE.findall(text or "")
    if len(ds) != 2:
        return None
    try:
        return date(*map(int, ds[1]))
    except ValueError:
        return None


def old_cb_shares(rows: list[tuple], rcept_dt: date | None, siblings=(), dropped: list | None = None) -> tuple[int, str]:
    """잔량표(TBL_NSB) 줄 (EXE_STK, EXE_DT, DEB_KND[, BAL_AMT, EXE_MN])의 EXE_STK 합 (calc_criteria v1 B-2). 빼는 줄:
    - 교환사채(EB) — 자기주식 교환이라 희석 아님 (BW · CB는 넣음)
    - 형제 CB — siblings = 같은 회사가 같은 날 공시한 다른 CB의 (발행금액, 전환가액[, 회차 SEQ_NO]). 줄의 잔액 · 전환가액이
      둘 다 같고, 줄의 사채 종류에 회차 숫자가 있으면 그 숫자도 형제 회차와 같을 때 (우연히 금액 · 가액이 같은 옛 CB를 안 빼려고)
      그 CB는 dilution()에서 따로 더해지므로 뺀다 (cb-v4). 뺀 줄의 사채 종류는 dropped에 넣음
    - 전환종료일 < 공시일 (종료일이 없거나 rcept_dt가 없으면 포함)
    소계(SUB_TOT_STK)는 안 씀."""
    vals, gone, eb, sib = [], 0, 0, 0
    for stk, dt, kind, *more in rows:
        bal, price = (list(more) + [None, None])[:2]
        n = None if _is_dash(stk) else to_int(stk)
        if n is None:
            continue
        if EB.search(kind or ""):
            eb += 1
            continue
        if siblings and _is_sibling(to_int(bal), to_number(price), kind, siblings):
            sib += 1
            if dropped is not None:
                dropped.append(kind or "")
            continue
        end = period_end(dt)
        if rcept_dt and end and end < rcept_dt:
            gone += 1
            continue
        vals.append(n)
    rule = ("줄 합" + (f" (전환 끝난 {gone}줄 뺌)" if gone else "") + (f" (교환사채 {eb}줄 뺌)" if eb else "")
            + (f" (형제 CB {sib}줄 뺌)" if sib else ""))
    return (sum(vals), rule) if vals else (0, "없음" if not (gone or eb or sib) else rule)


def _nums(text: str | None) -> set[str]:
    return set(re.findall(r"\d+", text or ""))


def _is_sibling(bal, price, kind, siblings) -> bool:
    for a, p, *seq in siblings:
        if a is None or p is None or bal != a or price != p:
            continue
        row, sib = _nums(kind), _nums(seq[0] if seq else None)
        if not row or not sib or row & sib:   # 회차 숫자가 양쪽에 다 있으면 같아야 함
            return True
    return False


def seq_no(doc: DartDocument) -> str | None:
    """1. 사채의 종류 · 회차 (SEQ_NO). 형제 CB 대조용."""
    c = _first(_cells(doc), "SEQ_NO")
    return None if c is None or _is_dash(c.text) else c.text.strip()


def _nsb_rows(cells: list[CodedCell]) -> list[tuple[str | None, str | None, str | None]]:
    rows: dict[tuple[int, int], dict[str, str]] = {}
    for c in cells:
        if c.group == "TBL_NSB" and c.code in ("EXE_STK", "EXE_DT", "DEB_KND", "BAL_AMT", "EXE_MN"):
            rows.setdefault((c.table_idx, c.row_idx), {}).setdefault(c.code, c.text)
    return [(r.get("EXE_STK"), r.get("EXE_DT"), r.get("DEB_KND"), r.get("BAL_AMT"), r.get("EXE_MN")) for _, r in sorted(rows.items())]


def date_flag(sbsc: date | None, pay: date | None, conv_start: date | None, maturity: date | None) -> str | None:
    """SBSC_DT ≤ PYM_DT < SB_BGN_DT < EXP_DT · PYM_DT < EXP_DT가 어긋난 곳. 날짜가 없는 쌍은 건너뛴다."""
    out = []
    if sbsc and pay and sbsc > pay:
        out.append("sbsc_dt>pay_date")
    for (a, an), (b, bn) in (((pay, "pay_date"), (conv_start, "conv_start")), ((conv_start, "conv_start"), (maturity, "maturity")),
                             ((pay, "pay_date"), (maturity, "maturity"))):   # ⑨-2 6번: pay_date ≥ maturity
        if a and b and a >= b:
            out.append(f"{an}{'=' if a == b else '>'}{bn}")
    return ",".join(out) or None


def _allottees(cells: list[CodedCell]) -> list[CbAllottee]:
    """부속 표 CRP_ISSU: 같은 (표, 줄)의 ISSU_NM · RLT · ISSU_AMT를 한 명으로. 이름이 비거나 '합계'인 줄은 뺀다."""
    rows: dict[tuple[int, int], dict[str, str]] = {}
    for c in cells:
        if c.code in ("ISSU_NM", "RLT", "ISSU_AMT") and c.row_idx >= 0:
            rows.setdefault((c.table_idx, c.row_idx), {}).setdefault(c.code, c.text)
    out = []
    for _, r in sorted(rows.items()):
        name = (r.get("ISSU_NM") or "").strip()
        if _is_dash(name) or re.fullmatch(r"(합\s*계|계|소\s*계)", name):   # ⑨-2 추가 D: 유상증자와 같게 소계도 제외
            continue
        out.append(CbAllottee(name, norm_relation(r.get("RLT")), to_int(r.get("ISSU_AMT"))))
    return out


def table_signals(cells: list[CodedCell]) -> list[str]:
    """⑨-2 1단계 표 신호: PYM_DET ≠ "현금" · 납입자산 부속표 TBL_PYM의 납입자산 종류(PYM_KND)에 현금 외 값."""
    out = []
    pym = (_text(cells, "PYM_DET") or "").strip()
    if pym != "현금":
        out.append(f"PYM_DET={pym or '-'}")
    kinds = [c.text.strip() for c in cells if c.group == "TBL_PYM" and c.code == "PYM_KND" and not _is_dash(c.text)]
    if any("현금" not in k for k in kinds):
        out.append("TBL_PYM:" + "/".join(k for k in kinds if "현금" not in k)[:40])
    return out


def extract(doc: DartDocument, report_nm: str = "", rcept_dt: date | None = None, siblings=()) -> CbExtract:
    """rcept_dt = 공시일, siblings = 같은 회사 · 같은 rcept_dt의 다른 CB들의 (amount, conv_price, SEQ_NO). 둘 다 old_cb_shares에만 씀."""
    cells = _cells(doc)
    t = lambda code: _text(cells, code)  # noqa: E731
    d = lambda code: to_date(_first(cells, code))  # noqa: E731
    dropped: list[str] = []
    old, rule = old_cb_shares(_nsb_rows(cells), rcept_dt, siblings, dropped)
    v = {
        "amount": to_int(t("DNM_SUM")),
        "conv_price": to_number(t("EXE_PRC")),
        "conv_shares": to_int(t("STK_CNT")),
        "shares_before": to_int(t("ISSU_TOT")),          # '-'면 None
        "min_price": to_number(t("MIN_PRC")),             # '-'면 None = 리픽싱 없음
        "old_cb_shares": old,
        **{k: to_int(t(code), dash_zero=True) for k, code in FUNDS.items()},
        "issue_method": issue_method(t("PL_KND"), t("FRN_AMT"), t("ISSU_MTH")),
        "exempt_reason": None if _is_dash(t("RPT_SBM")) else t("RPT_SBM"),   # ⑨-2 6번: "-" → NULL
        "conv_start": d("SB_BGN_DT"),
        "conv_end": d("SB_END_DT"),
        "maturity": d("EXP_DT"),
        "pay_date": d("PYM_DT"),
        "coupon": to_number(t("PRFT_RATE")),
        "ytm": to_number(t("LST_RTN_RT")),
        "currency": None if _is_dash(t("AMT_UNIT")) else (t("AMT_UNIT") or "").strip().upper() or None,   # v5: 권면 통화 (USD · HKD · KRW …)
    }
    v["date_flag"] = date_flag(d("SBSC_DT"), v["pay_date"], v["conv_start"], v["maturity"])
    pym = (t("PYM_DET") or "").strip()
    signals = noncash_signals(doc, report_nm, table_signals(cells))   # ⑨-2: 유상증자와 같은 1단계
    if not signals:
        ptype, psrc = "현금", "rule:no_signal"
    elif signals == ["title:(출자전환)"]:
        ptype, psrc = "빚→주식", "rule:title"
    else:
        ptype, psrc = None, "pending:llm"
    return CbExtract(values=v, allottees=_allottees(cells), payment_type=ptype, payment_source=psrc, pym_det=pym or None,
                     old_cb_rule=rule, signals=signals, sibling_rows=dropped)


def same_day_siblings(conn, raw_dir, day: date | None = None) -> dict[str, list[tuple]]:
    """cb-v4 형제 CB: rcept_no → 같은 회사 · 같은 rcept_dt의 다른 CB들의 (amount, conv_price, SEQ_NO).
    amount · conv_price는 old_cb_shares와 상관없는 칸이라 DB 값, SEQ_NO는 원문에서. day를 주면 그날 공시만."""
    rows = conn.execute("""SELECT v.rcept_no, e.corp_code, f.rcept_dt, v.amount, v.conv_price, d.main_file FROM cb_values v
                           JOIN filings f ON f.rcept_no = v.rcept_no JOIN events e ON e.event_id = v.event_id
                           JOIN documents d ON d.rcept_no = v.rcept_no WHERE %(d)s::date IS NULL OR f.rcept_dt = %(d)s""",
                        {"d": day}).fetchall()
    groups: dict[tuple, list] = {}
    for no, corp, dt, amt, price, mf in rows:
        groups.setdefault((corp, dt), []).append((no.strip(), amt, price, mf))
    out = {}
    for g in groups.values():
        if len(g) < 2:
            continue
        seq = {no: seq_no(read_document(raw_dir / no / mf)) for no, _, _, mf in g}
        for no, *_ in g:
            out[no] = [(a, p, seq[o]) for o, a, p, _ in g if o != no]
    return out
