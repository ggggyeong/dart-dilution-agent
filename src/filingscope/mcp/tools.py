"""MCP 도구 4개(get_event · get_company_dilution · compare_event · search_events)의 본체 (명세 docs/mcp_spec.md v2; 5번째 search_filing_text는 text_search.py). 사실 카드 함수 껍데기 — 새 계산은 compare_event 백분위 · search_events 조회뿐.

서버 시작 시 사실 카드 전부(유상증자 + CB)를 한 번 계산해 메모리에 둔다 (Cache). DB가 바뀌면 재시작.
"""

import re
import statistics
import time
from dataclasses import dataclass, field
from datetime import date

from filingscope.db import connect
from filingscope.facts import cb_facts, company_signals, dilution, rights_facts
from filingscope.facts.constants import SMALL_SAMPLE_N

SIGNALS = ("non_cash", "major_holder", "debt_heavy", "halted", "refixing", "old_bonds", "public_single")
CI_GROUP = {1: "주주배정", 2: "주주배정", 3: "주주배정", 5: "공모", 6: "3자배정"}


@dataclass
class Cache:
    cards: dict[str, dict] = field(default_factory=dict)
    seconds: float = 0.0

    @classmethod
    def build(cls) -> "Cache":
        t0 = time.time()
        c = cls()
        with connect() as conn:
            for no, t in conn.execute("SELECT event_id, event_type FROM events ORDER BY event_id"):
                no = no.strip()
                c.cards[no] = (rights_facts if t == "유상증자" else cb_facts)(no, conn=conn)
        c.seconds = round(time.time() - t0, 1)
        return c


def metric(card: dict) -> tuple[str | None, float | None]:
    """compare · search에 쓰는 희석률: 유상증자 dilution_pct, CB total_pct (NULL이면 dilution_pct)."""
    if card["type"] == "유상증자":
        return "dilution_pct", card["calc"]["dilution_pct"]
    if card["calc"]["total_pct"] is not None:
        return "total_pct", card["calc"]["total_pct"]
    return "dilution_pct", card["calc"]["dilution_pct"]


def group_name(card: dict) -> str:
    if card["type"] == "CB":
        return f"{card['market']} CB"
    return f"{card['market']} {CI_GROUP.get(card['values']['ci_mth'], '기타')}"


# ---------- 도구 ----------

def get_event(rcept_no: str, conn=None) -> dict:
    def go(c):
        t = c.execute("SELECT e.event_type FROM filings f JOIN events e ON e.event_id = f.event_id WHERE f.rcept_no = %s",
                      (rcept_no,)).fetchone()
        if t is None:
            return {"error": "not_found", "rcept_no": rcept_no}
        card = (rights_facts if t[0] == "유상증자" else cb_facts)(rcept_no, conn=c)
        d = dilution(card["corp_code"], conn=c)
        # 알림과 같은 조합: 카드 + company_signals + 회사 합산의 flags (v2: unit_mismatch · capital_reduction은 dilution()에만 있어서 그대로 붙임)
        return {**card, "company_signals": company_signals(card["corp_code"], conn=c),
                "dilution_flags": {**d["flags"], "reason": (d["dilution"] or {}).get("reason")}}
    if conn is not None:
        return go(conn)
    with connect() as c:
        return go(c)


def find_company(company: str, conn) -> dict:
    """corp_code(8자리) → 그대로. 회사명이면 정확히 같은 회사가 1곳이면 그것, 없으면 부분 일치 (여러 곳이면 ambiguous)."""
    q = company.strip()
    if re.fullmatch(r"\d{8}", q):
        row = conn.execute("SELECT corp_code FROM events WHERE corp_code = %s LIMIT 1", (q,)).fetchone()
        return {"corp_code": q} if row else {"error": "not_found"}
    exact = conn.execute("SELECT DISTINCT corp_code FROM events WHERE corp_name = %s", (q,)).fetchall()
    if len(exact) == 1:
        return {"corp_code": exact[0][0].strip()}
    rows = conn.execute("""SELECT corp_code, max(corp_name) FROM events WHERE corp_name ILIKE %s
                           GROUP BY corp_code ORDER BY max(corp_name)""", (f"%{q}%",)).fetchall()
    if not rows:
        return {"error": "not_found"}
    if len(rows) > 1:
        return {"error": "ambiguous", "candidates": [{"corp_code": r[0].strip(), "corp_name": r[1]} for r in rows[:5]]}
    return {"corp_code": rows[0][0].strip()}


def get_company_dilution(company: str, as_of: str | None = None, conn=None) -> dict:
    d = date.fromisoformat(as_of) if as_of else None
    def go(c):
        f = find_company(company, c)
        return f if "error" in f else dilution(f["corp_code"], d, conn=c)
    if conn is not None:
        return go(conn)
    with connect() as c:
        return go(c)


def compare_event(rcept_no: str, cache: Cache) -> dict:
    card = cache.cards.get(rcept_no)
    if card is None:
        return {"error": "not_found", "rcept_no": rcept_no}
    m, v = metric(card)
    if v is None:
        reason = "기존 주식 수가 없거나 원문 오기라 희석률을 계산할 수 없음" if (card["flags"]["shares_before_missing"] or card["flags"].get("shares_before_typo")) \
            else "희석률 값 없음"
        return {"error": "no_value", "reason": reason}
    g = group_name(card)
    vals = [x for x in (metric(o)[1] for o in cache.cards.values() if group_name(o) == g) if x is not None]
    out = {"rcept_no": rcept_no, "type": card["type"], "group": g, "metric": m, "value": v,
           "percentile": round(sum(1 for x in vals if x <= v) / len(vals) * 100), "n": len(vals),
           "median": round(statistics.median(vals), 1)}
    if len(vals) < SMALL_SAMPLE_N:
        out["small_sample"] = True  # 그룹이 작아 백분위가 크게 흔들림 (그룹은 합치지 않음)
    return out


def search_events(cache: Cache, company: str | None = None, type: str | None = None, date_from: str | None = None,
                  date_to: str | None = None, min_dilution: float | None = None, max_dilution: float | None = None,
                  signal: str | None = None, limit: int = 20, order_by: str = "rcept_dt") -> dict:
    """order_by: "rcept_dt"(기본, 접수일 최신순) | "dilution"(희석률 큰 순, NULL은 뒤). 정렬한 뒤 limit."""
    if signal is not None and signal not in SIGNALS:
        return {"error": "unknown_signal", "signal": signal, "allowed": list(SIGNALS)}
    q = (company or "").strip()
    out = []
    for card in cache.cards.values():
        if q and not (card["corp_code"] == q or q.lower() in card["corp_name"].lower()):
            continue
        if type and card["type"] != type:
            continue
        if date_from and card["rcept_dt"] < date_from:
            continue
        if date_to and card["rcept_dt"] > date_to:
            continue
        v = metric(card)[1]
        if (min_dilution is not None or max_dilution is not None) and v is None:
            continue
        if min_dilution is not None and v < min_dilution:
            continue
        if max_dilution is not None and v > max_dilution:
            continue
        names = [s["name"] for s in card["signals"]]
        if signal and signal not in names:
            continue
        out.append({"rcept_no": card["rcept_no"], "corp_name": card["corp_name"], "type": card["type"],
                    "rcept_dt": card["rcept_dt"], "dilution_pct": v, "signals": names})
    if order_by not in ("rcept_dt", "dilution"):
        return {"error": "unknown_order_by", "order_by": order_by, "allowed": ["rcept_dt", "dilution"]}
    out.sort(key=lambda x: (x["rcept_dt"], x["rcept_no"]), reverse=True)
    if order_by == "dilution":  # 안정 정렬: 같은 희석률이면 접수일 최신순
        out.sort(key=lambda x: (x["dilution_pct"] is None, -(x["dilution_pct"] or 0)))
    return {"n": len(out), "items": out[:limit], "truncated": len(out) > limit}
