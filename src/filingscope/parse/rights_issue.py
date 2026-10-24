"""유상증자 원문 → 명세 v1(docs/rights_issue_spec.md)의 코드 칸. LLM 칸은 비워 둔다.

시험지(CI_MTH): 1·2·3 주주배정 계열 / 5 일반공모 / 6 제3자배정. 번호가 아니라 칸 코드로 읽는다.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from filingscope.parse.acode import CodedCell, DartDocument

EXTRACTOR_VERSION = "rights-v5"  # v5 (2026-10-24 fix2): 혼합이면 기타주식 몫 other_new_shares · other_price (조달액에 더함). v4 v4 (⑨-2): 우선주만이면 기타주식 칸(PST_*)에서 채움 · 3자 미확정 예정 · 납입 1단계 신호 · ci 2 배정비율 · 정규화
# v3: stock_type(보통/우선/혼합), 우선주만이면 주식 종류별 값 비움
# v2: 할인율(기준주가 '-'면 null), CI_MTH 2 권리 매도, payment_type 5가지·PIN_EXT_YN도 LLM
SHAREHOLDER, PUBLIC, THIRD_PARTY = "주주배정", "일반공모", "제3자배정"
GROUP = {1: SHAREHOLDER, 2: SHAREHOLDER, 3: SHAREHOLDER, 5: PUBLIC, 6: THIRD_PARTY}
NONCASH_WORDS = ("출자전환", "상계", "현물", "대용", "회생채권", "대여금", "채무상환")   # ⑨-2: 채무상환 추가 (유상증자 · CB 같은 목록)
# ⑨-2 2번: 3자배정 발행가 미확정 문장 (ACODE 없는 <P>). 지시 원안의 맨 "미확정"은 "미확정 회생채권" · "상세내용은 미확정"
# (타법인 취득 표 주석) · "상위 미확정 사항"까지 잡아 "미확정 (신주) 발행가"로 좁힘 — 나머지 세 갈래는 원안 그대로
UNDECIDED = re.compile(r"미확정\s*(신주\s*)?발행가|확정\s*(시|후)[^.]{0,8}정정|예정\s*발행가액|예정발행가액")
# ⑨-2 5번: 주주우선공모(ci 2) 1주당 배정주식수 (ACODE 없는 <P>, 첫 매치, 소수점 자릿수 유지)
PER_SHARE = re.compile(r"1주당\s*([0-9]+\.[0-9]+)\s*주")
NO_RELATION = {"-", "없음", "해당없음", "해당사항없음", "무"}   # ⑨-2 6번: 공백 제거 후 이 값이면 relation NULL


def norm_relation(text: str | None) -> str | None:
    if _is_dash(text) or text.replace(" ", "") in NO_RELATION:
        return None
    return text.strip()
_DASH = {"", "-", "–", "—"}
_DATE = re.compile(r"(\d{4})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})")
_LOCKUP = re.compile(r"(\d+)\s*(년|개월)")


def _is_dash(text: str | None) -> bool:
    return text is None or text.strip() in _DASH


def to_number(text: str | None, dash_zero: bool = False) -> Decimal | None:
    """'1,199' → 1199, '-9.95' → -9.95. '-'는 dash_zero면 0, 아니면 None."""
    if _is_dash(text):
        return Decimal(0) if dash_zero and text is not None else None
    t = text.replace(",", "").replace(" ", "")
    m = re.search(r"-?\d+(?:\.\d+)?", t)
    if not m:
        return None
    try:
        return Decimal(m.group())
    except InvalidOperation:
        return None


def to_int(text: str | None, dash_zero: bool = False) -> int | None:
    n = to_number(text, dash_zero)
    return int(n) if n is not None else None


def to_date(cell: CodedCell | None) -> date | None:
    """날짜 칸은 AUNITVALUE에 YYYYMMDD가 있으면 그것을, 없으면 글자('2026년 10월 23일')를 읽는다."""
    if cell is None:
        return None
    if cell.value and re.fullmatch(r"\d{8}", cell.value):
        v = cell.value
        return date(int(v[:4]), int(v[4:6]), int(v[6:]))
    m = _DATE.search(cell.text or "")
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def lockup_period(text: str | None) -> str | None:
    """'사모발행 (1년간 보호예수)' → '1년', '6개월 보호예수' → '6개월'."""
    m = _LOCKUP.search(text or "")
    return f"{m.group(1)}{m.group(2)}" if m else None


@dataclass
class Allottee:
    name: str
    relation: str | None
    shares: int | None
    lockup: str | None


@dataclass
class RightsExtract:
    ci_mth: int | None
    group: str | None
    values: dict = field(default_factory=dict)  # rights_values 칸 (LLM 칸 제외)
    allottees: list[Allottee] = field(default_factory=list)
    payment_type: str | None = None
    payment_source: str | None = None
    noncash_words: list[str] = field(default_factory=list)  # 걸린 단어 (LLM 대기 이유)


def _first(doc: DartDocument, code: str) -> CodedCell | None:
    return doc.first(code)


def _text(doc: DartDocument, code: str) -> str | None:
    c = _first(doc, code)
    return None if c is None or _is_dash(c.text) else c.text.strip()


def _allottees(doc: DartDocument) -> list[Allottee]:
    """대상자 표: 같은 (표, 줄)의 PART·RLT·ALL_CNT·ETC를 한 명으로 묶는다. 이름이 비거나 '합계'인 줄은 뺀다."""
    rows: dict[tuple[int, int], dict[str, str]] = {}
    for c in doc.body_cells():
        if c.code in ("PART", "RLT", "ALL_CNT", "ETC") and c.row_idx >= 0:
            rows.setdefault((c.table_idx, c.row_idx), {}).setdefault(c.code, c.text)
    fallback_lockup = lockup_period(_text(doc, "RPT_SBM"))
    out = []
    for key in sorted(rows):
        r = rows[key]
        name = (r.get("PART") or "").strip()
        if _is_dash(name) or re.fullmatch(r"(합\s*계|계|소\s*계)", name):
            continue
        out.append(Allottee(
            name=name,
            relation=norm_relation(r.get("RLT")),
            shares=to_int(r.get("ALL_CNT")),
            lockup=lockup_period(r.get("ETC")) or fallback_lockup,
        ))
    return out


PAYMENT_TYPES = ("현금", "빚→주식", "현물", "섞임", "불명")  # 출자전환·상계는 '빚→주식' 하나로


def noncash_signals(doc: DartDocument, report_nm: str, table_signals: list[str] = ()) -> list[str]:
    """⑨-2 1단계 비현금 신호 — 유상증자 모든 ci_mth와 CB에 같게: 표 신호(호출 쪽이 줌) · 제목 "(출자전환)" ·
    ACODE 없는 <P> 글의 단어(출자전환 · 상계 · 현물 · 대용 · 회생채권 · 대여금 · 채무상환). 자금 용도 표 칸(ACODE 있음)은 보지 않는다."""
    text = " ".join(p.text for p in doc.body_paragraphs())
    out = list(table_signals)
    if "(출자전환)" in (report_nm or "").replace(" ", ""):
        out.append("title:(출자전환)")
    return out + [w for w in NONCASH_WORDS if w in text]


def _payment(doc: DartDocument, ci: int | None, report_nm: str) -> tuple[str | None, str | None, list[str]]:
    """(payment_type, source, 신호). 신호가 없으면 현금(rule:no_signal), 제목 "(출자전환)"은 빚→주식(rule:title),
    그 밖의 신호는 LLM 대기(pending:llm, payment-v4). 증자 방식(ci_mth)으로 미리 단정하지 않는다."""
    pin = _first(doc, "PIN_EXT_YN")
    signals = noncash_signals(doc, report_nm, ["PIN_EXT_YN=Y"] if pin is not None and pin.value == "Y" else [])
    if not signals:
        return "현금", "rule:no_signal", []
    if signals == ["title:(출자전환)"]:
        return "빚→주식", "rule:title", signals
    return None, "pending:llm", signals


_NO_SHARES = {"", "-", "–", "—", "0"}
# ⑨-2 1번: 우선주(기타주식)만 발행하면 비우지 않고 기타주식 칸에서 읽는다 (전환우선주도 희석에 들어감). 혼합은 보통주 칸.
SHARE_CODES = {"보통": {"cnt": "CST_CNT", "iss": "CST_ISS_VAL", "pre": "PRE_CST_VAL", "pre_dt": "PRE_CST_VAL_DT", "std": "CST_STD_VAL"},
               "우선": {"cnt": "PST_CNT", "iss": "PST_ISS_VAL", "pre": "PRE_PST_VAL", "pre_dt": "PRE_PST_VAL_DT", "std": "PST_STD_VAL"}}


def undecided_price(doc: DartDocument) -> bool:
    """⑨-2 2번: 3자배정 발행가 미확정 — 자유 서술 정규식 또는 부속 표 THD_STD_MTH1의 STD_VAL이 "-"."""
    if any(UNDECIDED.search(p.text) for p in doc.body_paragraphs()):
        return True
    std = [c for c in doc.body_cells() if c.group == "THD_STD_MTH1" and c.code == "STD_VAL"]
    return bool(std) and _is_dash(std[0].text)


def stock_type(doc: DartDocument) -> str | None:
    """보통(보통주만) / 우선(기타주식만) / 혼합(둘 다). 1번 CST_CNT(보통주)·PST_CNT(기타주식)로 판단."""
    cst, pst = _first(doc, "CST_CNT"), _first(doc, "PST_CNT")
    has_c = cst is not None and cst.text.strip() not in _NO_SHARES
    has_p = pst is not None and pst.text.strip() not in _NO_SHARES
    return {(True, False): "보통", (False, True): "우선", (True, True): "혼합"}.get((has_c, has_p))


def extract(doc: DartDocument, report_nm: str) -> RightsExtract:
    ci_cell = _first(doc, "CI_MTH")
    ci = int(ci_cell.value) if ci_cell is not None and ci_cell.value and ci_cell.value.isdigit() else None
    group = GROUP.get(ci)
    v: dict = {}

    st = stock_type(doc)
    sc = SHARE_CODES["우선" if st == "우선" else "보통"]

    # 1. 얼마나 큰가
    v["new_shares"] = to_int(_first(doc, sc["cnt"]).text if _first(doc, sc["cnt"]) else None)
    v["shares_before"] = to_int(_first(doc, "BFR_CST_CNT").text if _first(doc, "BFR_CST_CNT") else None)
    iss = to_number(_text(doc, sc["iss"]))
    if group == SHAREHOLDER:
        if iss is not None:
            v["price"], v["price_status"] = iss, "확정"
        else:
            v["price"], v["price_status"] = to_number(_text(doc, sc["pre"])), "예정"
            v["price_confirm_date"] = to_date(_first(doc, sc["pre_dt"]))
    elif group == THIRD_PARTY:
        v["price"], v["price_status"] = iss, "예정" if undecided_price(doc) else "확정"
    elif group == PUBLIC:
        v["price"], v["price_status"] = iss, "예정"

    dc = _first(doc, "DC_RATE")
    std = to_number(_text(doc, sc["std"]))
    if group in (PUBLIC, THIRD_PARTY) and dc is not None:
        if group == THIRD_PARTY and std is None:
            v["discount_rate"] = None  # 기준주가가 '-'(거래정지 등)면 할인율을 알 수 없음
        else:
            rate = to_number(dc.text, dash_zero=True)  # '-'와 숫자가 같이 있으면 숫자
            if rate is not None:
                rate = abs(rate)  # 회사마다 10 / -10으로 적으므로 크기만
                if group == THIRD_PARTY and iss is not None and iss > std:
                    rate = -rate  # 할증
                v["discount_rate"] = rate
    # 주주배정 할인율은 24번 글 → LLM (비워 둠)

    # 2. 왜 조달하나 ("-"는 0)
    for col, code in (("fund_facility", "FND_USE1"), ("fund_business_acq", "FND_USE_SQ"), ("fund_operating", "FND_USE2"),
                      ("fund_debt", "FND_USE_RD"), ("fund_other_corp", "ANC_ACQ_AMT"), ("fund_other", "FND_USE3")):
        c = _first(doc, code)
        v[col] = to_int(c.text, dash_zero=True) if c is not None else None
    t = _text(doc, "FND2_ISS")   # ⑨-2 추가 B: 모든 증자 방식 (표가 없으면 NULL)
    v["acquire_target"] = None if t and "미정" in t else t   # ⑨-2 6번

    # 3. 누가 사나
    if group in (SHAREHOLDER, PUBLIC):
        v["lead_manager"] = _text(doc, "CHF_AGN")

    # 4. 나는 뭘 할 수 있나 · 언제
    v["participation"] = {SHAREHOLDER: "우선", PUBLIC: "일반", THIRD_PARTY: "불가"}.get(group)
    if group == SHAREHOLDER:
        transfer, listed = _first(doc, "NST_GV_YN"), _first(doc, "WRNT_LST_YN")
        if ci == 2:  # 주주우선공모: 사고팔 수 있는 신주인수권증서가 없고, 표에 NEW_ASN_CNT 칸도 없음 → 글에서 (⑨-2 5번)
            m = next((m for m in (PER_SHARE.search(p.text) for p in doc.body_paragraphs()) if m), None)
            v["right_sale"], v["shares_per_share"] = "불가", Decimal(m.group(1)) if m else None
        else:
            if transfer is not None and transfer.value == "N":
                v["right_sale"] = "불가"
            elif transfer is not None and transfer.value == "Y":
                v["right_sale"] = "시장" if listed is not None and listed.value == "Y" else "장외"
            v["shares_per_share"] = to_number(_text(doc, "NEW_ASN_CNT"))
        v["record_date"] = to_date(_first(doc, "ALL_BS_DT"))
        v["subscription_start"] = to_date(_first(doc, "SH_BGN_DT"))
        v["subscription_end"] = to_date(_first(doc, "SH_END_DT"))
    elif group == PUBLIC:
        v["subscription_start"] = to_date(_first(doc, "OTH_BGN_DT"))
        v["subscription_end"] = to_date(_first(doc, "OTH_END_DT"))
    v["listing_date"] = to_date(_first(doc, "LST_PLN_DT"))

    v["stock_type"] = st
    # v5: 혼합(보통 + 기타주식)이면 기타주식 몫 — 조달액에만 더한다 (희석률은 보통주만)
    if st == "혼합":
        o = SHARE_CODES["우선"]
        v["other_new_shares"] = to_int(_first(doc, o["cnt"]).text) if _first(doc, o["cnt"]) else None
        oiss = to_number(_text(doc, o["iss"]))
        v["other_price"] = oiss if oiss is not None or group != SHAREHOLDER else to_number(_text(doc, o["pre"]))

    out = RightsExtract(ci_mth=ci, group=group, values=v)
    if group == THIRD_PARTY:
        out.allottees = _allottees(doc)
    out.payment_type, out.payment_source, out.noncash_words = _payment(doc, ci, report_nm)
    return out
