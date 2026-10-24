"""사실 카드 함수 4개 (명세 docs/facts_spec.md 0~6장). DB만 읽는다.

- 숫자는 원 · 주 단위 그대로. 비율(…_pct)은 퍼센트, 소수점 1자리. debt_ratio는 비(0.64), 소수점 2자리.
- 값은 JSON에 바로 넣을 수 있는 꼴로 바꾼다 (Decimal → int/float, date → 'YYYY-MM-DD'). 칸 이름은 그대로.
- conn을 넘기면 그 연결을 쓰고, 안 넘기면 새로 연다 (전체를 돌릴 때 연결 재사용).
"""

import re
from contextlib import contextmanager
from datetime import date, datetime
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

from filingscope.db import connect
from filingscope.facts.constants import (DEBT_HEAVY_RATIO, REPEAT_MIN, REPEAT_YEARS, UNIT_DECREASE, UNIT_INCREASE_MIN,
                                         UNIT_INCREASE_SLACK)

MARKET = {"KOSPI": "코스피", "KOSDAQ": "코스닥"}
def _sb_typo(rcept_no: str) -> bool:
    """⑨-2 4-1: 사람 판정(manual/facts.csv) 덮어쓰기를 없앴다 — 파이프라인이 오기를 스스로 찾는 규칙이 없으므로 항상 False.
    (카드 모양을 유지하려고 flags.shares_before_typo 칸은 남김. 옛 사례는 tests/fixtures/manual_cases.csv)"""
    return False
FUNDS = ["fund_facility", "fund_business_acq", "fund_operating", "fund_debt", "fund_other_corp", "fund_other"]


@contextmanager
def _db(conn):
    if conn is not None:
        yield conn
    else:
        with connect() as c:
            yield c


def _json(v):
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, str):
        return v.strip() if len(v) == 14 and v.strip().isdigit() else v  # char(14)
    return v


def _row(cur) -> dict | None:
    r = cur.fetchone()
    return None if r is None else {d.name: r[i] for i, d in enumerate(cur.description)}


def _pct(num, den) -> float | None:
    if num is None or den is None or den == 0:
        return None
    return float((Decimal(num) / Decimal(den) * 100).quantize(Decimal("0.1"), ROUND_HALF_UP))


def won_display(n) -> str | None:
    """원 → 알림에 그대로 쓰는 글자 (알림 지시문의 옛 환산 규칙을 코드로, 2026-10-22). LLM이 나눗셈을 하지 않게.
    1억 이상: 억 · 쉼표 · 소수 1자리 반올림 (".0"은 뺌). 100억 미만이고 억으로 나눠 소수 2자리에서 딱 떨어지면 2자리 (29.99억).
    1억 미만: 만 단위 정수. 0 · NULL → None."""
    if not n:
        return None
    d = Decimal(n)
    if abs(d) >= 10 ** 8:
        x = d / Decimal(10 ** 8)
        x2 = x.quantize(Decimal("0.01"), ROUND_HALF_UP)
        x1 = x.quantize(Decimal("0.1"), ROUND_HALF_UP)
        if abs(x) < 100 and x2 == x and x2 != x1:
            return f"{x2:,.2f}억"
        s = f"{x1:,.1f}"
        return (s[:-2] if s.endswith(".0") else s) + "억"
    return f"{(d / Decimal(10 ** 4)).quantize(Decimal(1), ROUND_HALF_UP):,}만"


def foreign_currency_display(v: dict) -> str | None:
    """해외 발행 CB(issue_method 해외)의 권면 금액 뒤에 그대로 붙이는 글자 (2026-10-24, LLM은 복사만).
    통화가 원화가 아니면 "({통화코드} 발행, 원화 환산)", 원화(또는 통화 칸 없음)면 "(해외 발행)". 해외가 아니면 None."""
    if v.get("issue_method") != "해외":
        return None
    cur = (v.get("currency") or "").upper()
    return f"({cur} 발행, 원화 환산)" if cur and cur != "KRW" else "(해외 발행)"


def price_pending_display(v: dict, as_of: date) -> str | None:
    """발행가 미확정(price_status 예정) 유상증자의 조달액 뒤에 그대로 붙이는 글자 (2026-10-24 fix1, LLM은 복사만).
    확정일이 as_of 이후(같은 날 포함) "(예정 발행가 기준, YYYY-MM-DD 확정)", 확정일 없음 "(예정 발행가 기준)",
    확정일이 as_of 이전 "(예정 발행가 기준, 확정 공시 미반영)". 예정이 아니면 None."""
    if v.get("price_status") != "예정":
        return None
    d = v.get("price_confirm_date")
    if d is None:
        return "(예정 발행가 기준)"
    d = d if isinstance(d, date) else date.fromisoformat(str(d))
    return f"(예정 발행가 기준, {d.isoformat()} 확정)" if d >= as_of else "(예정 발행가 기준, 확정 공시 미반영)"


def share_pct(part, base) -> int | None:
    """자금 용도 비중 (그 칸 ÷ 조달액 · 권면, 정수 %). 0 · NULL → None."""
    if not part or not base:
        return None
    return int((Decimal(part) / Decimal(base) * 100).quantize(Decimal(1), ROUND_HALF_UP))


def _money(calc: dict, values: dict, base) -> None:
    """calc에 fund_*_display · fund_*_pct (알림 문단 2 · debt_heavy용)."""
    for k in FUNDS:
        calc[f"{k}_display"] = won_display(values.get(k))
        calc[f"{k}_pct"] = share_pct(values.get(k), base)


def _today() -> date:
    return date.today()


def _years_before(d: date, n: int) -> date:
    try:
        return d.replace(year=d.year - n)
    except ValueError:  # 2월 29일
        return d.replace(year=d.year - n, day=28)


# ---------- 카드 공통 ----------

def _event(c, rcept_no: str) -> dict:
    cur = c.execute(
        """SELECT e.*, f.rcept_dt FROM filings f JOIN events e ON e.event_id = f.event_id WHERE f.rcept_no = %s""", (rcept_no,))
    ev = _row(cur)
    if ev is None:
        raise KeyError(f"공시 없음: {rcept_no}")
    return ev


def _allottees(c, rcept_no: str) -> list[dict]:
    cur = c.execute("SELECT name, relation, shares, amount, lockup FROM allottees WHERE rcept_no = %s ORDER BY seq", (rcept_no,))
    out = [{k: _json(v) for k, v in zip(("name", "relation", "shares", "amount", "lockup"), r)} for r in cur.fetchall()]
    for a in out:
        a["amount_display"] = won_display(a["amount"])   # CB 대상자 금액 (알림 문단 3)
    return out


MAJOR_HOLDER = {"최대주주", "최대주주본인"}


def holder_kind(relation: str | None) -> str | None:
    """calc_criteria v1 D-6. 공백 제거 후 정확히 최대주주(본인) → "major". "변경"이 있고 변경 · 예정 · 후를 지운 나머지가
    정확히 최대주주(본인) → "new" (변경예정최대주주 · 변경후최대주주 · 최대주주변경예정). 그 외(…의 특수관계인 등) → None."""
    r = re.sub(r"\s", "", relation or "")
    if r in MAJOR_HOLDER:
        return "major"
    if "변경" in r and re.sub(r"변경|예정|후", "", r) in MAJOR_HOLDER:
        return "new"
    return None


def _common_signals(ev: dict, allottees: list[dict], debt_ratio, fund_debt) -> list[dict]:
    out = []
    pt = ev["payment_type"]
    if pt is not None and pt != "현금":  # 불명 포함
        out.append({"name": "non_cash", "payment_type": pt, "payment_evidence": ev["payment_evidence"]})   # ⑨-2 추가 A: indirect 없앰
    holders = [a["name"] for a in allottees if holder_kind(a["relation"]) == "major"]
    if holders:
        out.append({"name": "major_holder", "names": holders})
    if debt_ratio is not None and debt_ratio >= DEBT_HEAVY_RATIO:
        out.append({"name": "debt_heavy", "debt_ratio": _ratio_out(debt_ratio), "fund_debt": _json(fund_debt)})
    if ev["halted_at_filing"]:
        out.append({"name": "halted"})
    return out


def _ratio_out(r: Decimal) -> float:
    return float(r.quantize(Decimal("0.01"), ROUND_HALF_UP))


def _card(ev: dict, rcept_no: str, values: dict, allottees: list[dict], calc: dict, signals: list[dict],
          date_flag, shares_before_missing: bool, conn) -> dict:
    """flags (calc_criteria v1): preferred_only · new_major_holder · rehab(exempt_reason에 회생) · price_pending · foreign_currency."""
    co = company_signals(ev["corp_code"], conn=conn)
    return {
        "type": ev["event_type"],
        "rcept_no": rcept_no,
        "event_id": _json(ev["event_id"]),
        "corp_code": _json(ev["corp_code"]),
        "corp_name": ev["corp_name"],
        "rcept_dt": _json(ev["rcept_dt"]),
        "market": MARKET.get(ev["market_at_filing"], ev["market_at_filing"]),
        "values": values,
        "allottees": allottees,
        "calc": calc,
        "signals": signals,
        "company": {k: co[k] for k in ("repeat_count", "repeat", "withdrawn")},
        "flags": {"date_flag": date_flag, "shares_before_missing": shares_before_missing,
                  "shares_before_typo": _sb_typo(rcept_no),
                  "preferred_only": values.get("stock_type") == "우선",
                  "new_major_holder": any(holder_kind(a["relation"]) == "new" for a in allottees),
                  "rehab": "회생" in (values.get("exempt_reason") or ""),
                  "price_pending": values.get("price_status") == "예정",
                  "foreign_currency": values.get("issue_method") == "해외"},
    }


# ---------- 1. 유상증자 ----------

def rights_facts(rcept_no: str, conn=None) -> dict:
    with _db(conn) as c:
        ev = _event(c, rcept_no)
        v = _row(c.execute("SELECT * FROM rights_values WHERE rcept_no = %s", (rcept_no,)))
        if v is None:
            raise KeyError(f"rights_values 없음: {rcept_no}")
        al = _allottees(c, rcept_no)
        values = {k: _json(x) for k, x in v.items()}
        values.update(ci_mth=ev["ci_mth"], payment_type=ev["payment_type"], payment_evidence=ev["payment_evidence"],
                      payment_source=ev["payment_source"])

        ns, sb, price = v["new_shares"], v["shares_before"], v["price"]
        if _sb_typo(rcept_no):
            sb = None  # 사람 판정: 원문 오기 → 희석률 계산 안 함 (values에는 원문 값 그대로)
        raised = Decimal(ns) * Decimal(price) if ns is not None and price is not None else None
        if raised is not None and v.get("other_new_shares") and v.get("other_price") is not None:   # fix2: 혼합은 기타주식 몫까지
            raised += Decimal(v["other_new_shares"]) * Decimal(v["other_price"])
        fund_total = sum(Decimal(v[k] or 0) for k in FUNDS)
        debt_ratio = Decimal(v["fund_debt"] or 0) / raised if raised else None
        preferred_only = v["stock_type"] == "우선"   # calc_criteria v1 B-3: 분자 기타주식 · 분모 보통주라 희석률 안 냄
        calc = {
            "dilution_pct": _pct(ns, sb + ns) if ns is not None and sb is not None and not preferred_only else None,
            "raised": _json(raised),
            "fund_total": _json(fund_total),
            "fund_mismatch": (abs(raised - fund_total) > raised * Decimal("0.01")) if raised is not None else None,
            "debt_ratio": _ratio_out(debt_ratio) if debt_ratio is not None else None,
            "raised_display": won_display(raised),
        }
        _money(calc, v, raised)
        calc["price_pending_display"] = price_pending_display(v, _today())
        signals = _common_signals(ev, al, debt_ratio, v["fund_debt"])
        return _card(ev, rcept_no, values, al, calc, signals, None, v["shares_before"] is None, c)


# ---------- 2. CB ----------

def _cb_calc(v: dict) -> tuple[dict, int | None, Decimal | None]:
    sb, conv, old = v["shares_before"], v["conv_shares"], v["old_cb_shares"] or 0
    amount, minp, convp = v["amount"], v["min_price"], v["conv_price"]
    worst = int((Decimal(amount) / Decimal(minp)).to_integral_value(ROUND_FLOOR)) if minp else conv
    debt_ratio = Decimal(v["fund_debt"] or 0) / Decimal(amount) if amount else None
    calc = {
        "dilution_pct": _pct(conv, sb + conv) if sb is not None and conv is not None else None,
        "worst_shares": worst,
        "worst_pct": _pct(worst, sb + worst) if sb is not None and worst is not None else None,
        "total_pct": _pct(old + conv, sb + old + conv) if sb is not None and conv is not None else None,
        # fix1 (2026-10-24): 같은 기준끼리 짝 — 이번 공시만 dilution_pct · worst_pct / 예전 잔량 포함 total_pct · total_worst_pct
        "total_worst_pct": _pct(old + worst, sb + old + worst) if sb is not None and worst is not None else None,
        "floor_pct": _pct(minp, convp) if minp is not None else None,
        "debt_ratio": _ratio_out(debt_ratio) if debt_ratio is not None else None,
    }
    return calc, worst, debt_ratio


def cb_facts(rcept_no: str, conn=None) -> dict:
    with _db(conn) as c:
        ev = _event(c, rcept_no)
        v = _row(c.execute("SELECT * FROM cb_values WHERE rcept_no = %s", (rcept_no,)))
        if v is None:
            raise KeyError(f"cb_values 없음: {rcept_no}")
        al = _allottees(c, rcept_no)
        values = {k: _json(x) for k, x in v.items()}
        values.update(payment_type=ev["payment_type"], payment_evidence=ev["payment_evidence"], payment_source=ev["payment_source"])
        calc, worst, debt_ratio = _cb_calc({**v, "shares_before": None} if _sb_typo(rcept_no) else v)
        calc["amount_display"] = won_display(v["amount"])
        _money(calc, v, v["amount"])
        calc["foreign_currency_display"] = foreign_currency_display(v)
        signals = _common_signals(ev, al, debt_ratio, v["fund_debt"])
        if v["min_price"] is not None:
            signals.append({"name": "refixing", "min_price": _json(v["min_price"]), "floor_pct": calc["floor_pct"],
                            "worst_pct": calc["worst_pct"]})
        if (v["old_cb_shares"] or 0) > 0:
            signals.append({"name": "old_bonds", "old_cb_shares": v["old_cb_shares"], "total_pct": calc["total_pct"]})
        if v["issue_method"] == "공모" and "소액공모" in (v["exempt_reason"] or "") and len(al) == 1:
            signals.append({"name": "public_single"})
        return _card(ev, rcept_no, values, al, calc, signals, v["date_flag"], v["shares_before"] is None, c)


# ---------- 3. 회사 ----------

def company_signals(corp_code: str, as_of: date | None = None, conn=None) -> dict:
    """events만 읽는다. repeat_count = first_date가 (as_of − REPEAT_YEARS) 이후 · as_of 이전인 events 수."""
    as_of = as_of or _today()
    since = _years_before(as_of, REPEAT_YEARS)
    with _db(conn) as c:
        rows = c.execute("""SELECT event_id, event_type, first_date, status FROM events WHERE corp_code = %s
                            ORDER BY first_date, event_id""", (corp_code,)).fetchall()
    n = sum(1 for _, _, fd, _ in rows if since <= fd <= as_of)
    return {
        "corp_code": corp_code,
        "as_of": as_of.isoformat(),
        "repeat_count": n,
        "repeat": n >= REPEAT_MIN,
        "withdrawn": any(st == "철회" for *_, st in rows),
        "events": [{"rcept_no": _json(e), "type": t, "first_date": fd.isoformat(), "status": st} for e, t, fd, st in rows],
    }


# ---------- 4. 희석 합산 ----------

def unit_mismatch(live: list[dict], issued: list[tuple[str, int | None]]) -> list[tuple[str, str, str]]:
    """calc_criteria v1 C-4. live = 살아 있는 카드, issued = 그 회사 모든 공시의 (rcept_dt, new_shares 또는 conv_shares).
    (rcept_dt, rcept_no) 순으로 인접한 A→B의 shares_before를 비교해 어긋난 쌍 (A, B, "감소" | "증가")."""
    seq = sorted((e for e in live if e["values"]["shares_before"]), key=lambda e: (e["rcept_dt"], e["rcept_no"]))
    out = []
    for a, b in zip(seq, seq[1:]):
        x, y = a["values"]["shares_before"], b["values"]["shares_before"]
        if y < x / UNIT_DECREASE:
            out.append((a["rcept_no"], b["rcept_no"], "감소"))
            continue
        added = sum(n or 0 for d, n in issued if a["rcept_dt"] <= d < b["rcept_dt"])   # A 자신 포함, B 전날까지
        if y > x + added * UNIT_INCREASE_SLACK and y >= x * UNIT_INCREASE_MIN:
            out.append((a["rcept_no"], b["rcept_no"], "증가"))
    return out


def combine(live: list[dict], cb_no: str | None, mismatch: bool) -> dict:
    """살아 있는 카드 → dilution 칸. 기준 = rcept_dt가 가장 최근인 공시(종류 무관, 같은 날이면 rcept_no 큰 것)의 shares_before.
    old_cb_shares는 기준 CB(cb_no) 것만. preferred_only는 새 주식 합에서 뺀다. mismatch면 기준 공시 1건만 (CB면 conv + old)."""
    basis = max(live, key=lambda e: (e["rcept_dt"], e["rcept_no"]))
    use = [e for e in ([basis] if mismatch else live) if not e["flags"]["preferred_only"]]
    sb = basis["values"]["shares_before"]
    new = worst = 0
    for e in use:
        if e["type"] == "유상증자":
            new += e["values"]["new_shares"] or 0
            worst += e["values"]["new_shares"] or 0
        else:
            new += e["values"]["conv_shares"] or 0
            worst += e["calc"]["worst_shares"] or 0
            if mismatch or e["rcept_no"] == cb_no:   # 같은 날 CB는 conv만 (명세 6장 3-1)
                new += e["values"]["old_cb_shares"] or 0
                worst += e["values"]["old_cb_shares"] or 0
    ok = sb is not None and bool(use)
    return {"shares_before": sb, "new_shares": new,
            "max_pct": _pct(new, sb + new) if ok else None, "worst_pct": _pct(worst, sb + worst) if ok else None,
            "basis_rcept_no": basis["rcept_no"], "basis_date": basis["rcept_dt"]}


def _live_status(out: dict, open_cb: set[str]) -> None:
    """company.events[].status를 live_events 기준으로 (fix1 → fix2, 2026-10-24) — 3가지:
    "진행" = live_events · "잔량 반영" = 전환 기간이 남았지만 live가 아닌 이전 CB (기준 CB의 old_cb_shares로 합산) ·
    "끝남" = 상장 끝난(또는 상장일 없는) 유상증자 · 전환 기간 끝난 CB. 철회는 그대로 "철회" (withdrawn 신호가 읽는 값).
    open_cb = 그 회사 CB 중 coalesce(conv_end, maturity) > as_of인 event_id."""
    live = {e["event_id"] for e in out["live_events"]}
    for e in out["company"]["events"]:
        if e["status"] != "철회":
            e["status"] = "진행" if e["rcept_no"] in live else "잔량 반영" if e["rcept_no"] in open_cb else "끝남"


def dilution(corp_code: str, as_of: date | None = None, conn=None) -> dict:
    as_of = as_of or _today()
    with _db(conn) as c:
        company = company_signals(corp_code, as_of, conn=c)
        name = c.execute("SELECT corp_name FROM events WHERE corp_code = %s ORDER BY first_date DESC LIMIT 1", (corp_code,)).fetchone()
        rights_nos = [r[0] for r in c.execute(
            """SELECT v.rcept_no FROM rights_values v JOIN events e ON e.event_id = v.event_id
               WHERE e.corp_code = %s AND e.event_type = '유상증자' AND v.listing_date > %s ORDER BY e.first_date, v.rcept_no""",
            (corp_code, as_of))]
        # 살아 있는 CB (calc_criteria v1 A-1): 전환 기간이 안 끝난 것 — conv_end, 없으면 maturity
        cb_no = c.execute(
            """SELECT v.rcept_no FROM cb_values v JOIN events e ON e.event_id = v.event_id JOIN filings f ON f.rcept_no = v.rcept_no
               WHERE e.corp_code = %s AND e.event_type = 'CB' AND coalesce(v.conv_end, v.maturity) > %s
               ORDER BY f.rcept_dt DESC, v.rcept_no DESC LIMIT 1""",
            (corp_code, as_of)).fetchone()
        open_cb = {_json(r[0]) for r in c.execute(
            """SELECT e.event_id FROM cb_values v JOIN events e ON e.event_id = v.event_id
               WHERE e.corp_code = %s AND e.event_type = 'CB' AND coalesce(v.conv_end, v.maturity) > %s""", (corp_code, as_of))}
        rights = [rights_facts(_json(no), conn=c) for no in rights_nos]
        cb = cb_facts(_json(cb_no[0]), conn=c) if cb_no else None
        # 같은 날 CB (명세 6장 3-1): 기준 CB와 rcept_dt가 같은 다른 살아 있는 CB. 기준 CB 잔량표에 안 잡힐 수 있음 (엔투텍 18회)
        same_day = [cb_facts(_json(r[0]), conn=c) for r in c.execute(
            """SELECT v.rcept_no FROM cb_values v JOIN events e ON e.event_id = v.event_id JOIN filings f ON f.rcept_no = v.rcept_no
               WHERE e.corp_code = %s AND e.event_type = 'CB' AND coalesce(v.conv_end, v.maturity) > %s AND f.rcept_dt = %s
                 AND v.rcept_no <> %s ORDER BY v.rcept_no""", (corp_code, as_of, cb["rcept_dt"], cb["rcept_no"]))] if cb else []
        # 단위 불일치 증가 검사용: 그 회사 모든 공시(살아 있지 않은 것 포함)의 새 주식 수
        issued = [(d.isoformat(), n) for d, n in c.execute(
            """SELECT f.rcept_dt, v.new_shares FROM rights_values v JOIN filings f ON f.rcept_no = v.rcept_no
               JOIN events e ON e.event_id = v.event_id WHERE e.corp_code = %s
               UNION ALL
               SELECT f.rcept_dt, v.conv_shares FROM cb_values v JOIN filings f ON f.rcept_no = v.rcept_no
               JOIN events e ON e.event_id = v.event_id WHERE e.corp_code = %s""", (corp_code, corp_code))]
    out = {"corp_code": corp_code, "corp_name": name[0] if name else None, "as_of": as_of.isoformat(),
           "company": company, "live_events": rights + ([cb] if cb else []) + same_day, "dilution": None, "schedule": [],
           "flags": {"capital_reduction": False, "unit_mismatch": []}}
    if not out["live_events"]:
        _live_status(out, open_cb)
        return out
    pairs = unit_mismatch(out["live_events"], issued)
    out["flags"]["unit_mismatch"] = [[a, b] for a, b, _ in pairs]
    _live_status(out, open_cb)
    # 감소로 어긋나면 capital_reduction도 켠다 (agent 프롬프트 · 알림 명세가 이 flag를 읽음)
    out["flags"]["capital_reduction"] = any(k == "감소" for *_, k in pairs)
    out["dilution"] = combine(out["live_events"], cb["rcept_no"] if cb else None, bool(pairs))
    if pairs:
        out["dilution"]["reason"] = "단위 불일치(감자 · 주식 수 기준 차이 추정) → 가장 최근 공시 1건으로 계산"
    elif all(e["flags"]["preferred_only"] for e in out["live_events"]):
        out["dilution"]["reason"] = "살아 있는 공시가 우선주만 발행"
    sched = [{"type": "유상증자", "rcept_no": r["rcept_no"], "date": r["values"]["listing_date"], "label": "상장"} for r in rights]
    for x in ([cb] if cb else []) + same_day:
        sched += [{"type": "CB", "rcept_no": x["rcept_no"], "date": x["values"]["conv_start"], "label": "전환 시작"},
                  {"type": "CB", "rcept_no": x["rcept_no"], "date": x["values"]["maturity"], "label": "만기"}]
    out["schedule"] = sorted((s for s in sched if s["date"]), key=lambda s: (s["date"], s["rcept_no"]))
    return out
