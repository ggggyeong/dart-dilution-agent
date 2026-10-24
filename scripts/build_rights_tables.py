"""유상증자 원본 → 명세 v1 테이블 4개 (코드 칸만, LLM 칸은 비워 둠) + 확인 자료.

    uv run python scripts/build_rights_tables.py --sample 30     # 시험지별 10건 시범
    uv run python scripts/build_rights_tables.py --same-sample   # 지금 DB에 있는 사건들로 다시 (규칙 수정 확인용)
    uv run python scripts/build_rights_tables.py --all           # 원본 전체 (보완 후)
    uv run python scripts/build_rights_tables.py --all --start 2023-04-17 --end 2025-12-31   # 기간 지정 → rights_all_20230417_20251231/

대상: 유상증자 원본(기본 2026년~) 중 공시 당시 코스피·코스닥(listing_checks) · 문서코드 11306·11308.
다른 기간의 사건은 건드리지 않는다. 표를 다시 만들면 LLM 값이 지워지므로 apply_llm_results.py도 다시 돌린다.
확인 자료: data/reports/rights_sample/ (values.csv, checks.md, index.html + 원문 화면)
"""

import argparse
import csv
import html
import random
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal

from filingscope.config import DATA_DIR
from filingscope.db import connect
from filingscope.parse.acode import read_document
from filingscope.parse.rights_issue import EXTRACTOR_VERSION, PUBLIC, SHAREHOLDER, THIRD_PARTY, extract
from filingscope.parse.xml_view import render_html

RAW = DATA_DIR / "raw" / "document"
OUT = DATA_DIR / "reports" / "rights_sample"
SEED = 42
MUST = {"20261015000184": "케이에스인더스트리 (정답 XML)", "20260706000318": "판타지오 일반공모", "20260828001315": "뷰노"}
VALUE_COLS = ["new_shares", "shares_before", "price", "price_status", "price_confirm_date", "discount_rate",
              "fund_facility", "fund_business_acq", "fund_operating", "fund_debt", "fund_other_corp", "fund_other",
              "acquire_target", "lead_manager", "participation", "right_sale", "record_date", "shares_per_share",
              "subscription_start", "subscription_end", "listing_date", "stock_type"]
FUNDS = ["fund_facility", "fund_business_acq", "fund_operating", "fund_debt", "fund_other_corp", "fund_other"]
# 시험지별로 명세상 채워져야 하는 코드 칸 (LLM 칸 제외)
EXPECTED = {
    SHAREHOLDER: ["new_shares", "shares_before", "price", "price_status", *FUNDS, "lead_manager", "participation",
                  "right_sale", "record_date", "shares_per_share", "subscription_start", "subscription_end", "listing_date"],
    PUBLIC: ["new_shares", "shares_before", "price", "price_status", "discount_rate", *FUNDS, "lead_manager",
             "participation", "subscription_start", "subscription_end", "listing_date"],
    THIRD_PARTY: ["new_shares", "shares_before", "price", "price_status", "discount_rate", *FUNDS, "participation",
                  "listing_date"],
}


# 칸 → 원문 칸 코드 (빈 칸 이유를 가리기 위해)
SOURCE = {
    "new_shares": "CST_CNT", "shares_before": "BFR_CST_CNT", "price_confirm_date": "PRE_CST_VAL_DT",
    "fund_facility": "FND_USE1", "fund_business_acq": "FND_USE_SQ", "fund_operating": "FND_USE2",
    "fund_debt": "FND_USE_RD", "fund_other_corp": "ANC_ACQ_AMT", "fund_other": "FND_USE3",
    "lead_manager": "CHF_AGN", "right_sale": "NST_GV_YN", "record_date": "ALL_BS_DT", "shares_per_share": "NEW_ASN_CNT",
    "listing_date": "LST_PLN_DT", "discount_rate": "DC_RATE",
}


def why_empty(c, k: str) -> str:
    """빈 칸의 이유: 원문 '-' / 칸 없음 / 못 읽음 (+ 규칙상 null)."""
    g, doc = c["x"].group, c["doc"]
    if c["x"].values.get("stock_type") == "우선" and k in ("new_shares", "price", "price_status", "discount_rate", "listing_date"):
        return "우선주만 발행 (보류)"
    if k == "discount_rate" and g == THIRD_PARTY and c["x"].values.get("price") is not None:
        std = doc.first("CST_STD_VAL")
        if std is None or std.text.strip() in ("", "-"):
            return "기준주가 '-' (규칙상 null)"
    if k == "price":
        codes = ["CST_ISS_VAL", "PRE_CST_VAL"] if g == SHAREHOLDER else ["CST_ISS_VAL"]
    elif k in ("subscription_start", "subscription_end"):
        codes = [{SHAREHOLDER: "SH_", PUBLIC: "OTH_"}[g] + ("BGN_DT" if k.endswith("start") else "END_DT")]
    else:
        codes = [SOURCE.get(k, "")]
    cells = [doc.first(code) for code in codes if code]
    cells = [x for x in cells if x is not None]
    if not cells:
        return "칸 없음"
    if all(x.text.strip() in ("", "-", "–", "—") for x in cells):
        return "원문 '-'"
    return "못 읽음"


def load_candidates(conn, start: date = date(2026, 1, 1), end: date = date(2099, 12, 31)):
    rows = conn.execute(
        """SELECT f.rcept_no, f.corp_code, f.corp_name, f.rcept_dt, f.report_nm, d.main_file, l.market, l.passed
           FROM title_filings f
           JOIN documents d USING (rcept_no)
           JOIN listing_checks l USING (rcept_no)
           WHERE f.event_type = 'rights_issue' AND NOT f.is_correction AND f.rcept_dt BETWEEN %s AND %s
             AND d.doc_code IN ('11306', '11308') AND l.market IN ('KOSPI', 'KOSDAQ')
           ORDER BY f.rcept_no""", (start, end)).fetchall()
    out = []
    for no, corp, name, dt, nm, mf, market, passed in rows:
        doc = read_document(RAW / no / mf)
        out.append(dict(rcept_no=no, corp_code=corp, corp_name=name, rcept_dt=dt, report_nm=nm, main_file=mf,
                        market=market, halted=not passed, x=extract(doc, nm), doc=doc))
    return out


def pick_sample(cands, per_group: int):
    rng = random.Random(SEED)
    by_group = defaultdict(list)
    for c in cands:
        by_group[c["x"].group].append(c)
    chosen, why = [], {}

    def take(c, reason):
        if c not in chosen:
            chosen.append(c)
            why[c["rcept_no"]] = reason

    for c in cands:
        if c["rcept_no"] in MUST:
            take(c, MUST[c["rcept_no"]])
    third = by_group[THIRD_PARTY][:]
    rng.shuffle(third)
    for c in [c for c in third if len(c["x"].allottees) >= 2][:2]:
        take(c, "대상자 여러 명")
    for c in [c for c in third if "PIN_EXT_YN=Y" in c["x"].noncash_words][:1]:
        take(c, "현물출자 칸 = Y")
    for c in [c for c in third if c["x"].payment_source == "pending:llm"][:2]:
        take(c, f"현금 외 단어: {','.join(c['x'].noncash_words)}")
    halted = [c for c in cands if c["halted"]]
    rng.shuffle(halted)
    for c in halted[:3]:
        take(c, "공시 당시 거래정지")
    for g in (SHAREHOLDER, PUBLIC, THIRD_PARTY):
        pool = by_group[g][:]
        rng.shuffle(pool)
        if g == SHAREHOLDER:  # 1·2·3이 섞이게
            pool.sort(key=lambda c: sum(1 for x in chosen if x["x"].ci_mth == c["x"].ci_mth))
        for c in pool:
            if sum(1 for x in chosen if x["x"].group == g) >= per_group:
                break
            take(c, "무작위")
    return chosen, why


def save(conn, items):
    ids = [c["rcept_no"] for c in items]
    with conn.transaction():
        conn.execute("DELETE FROM events WHERE event_id = ANY(%s) AND event_type = '유상증자'", (ids,))
        for c in items:
            x = c["x"]
            conn.execute(
                """INSERT INTO events (event_id, corp_code, corp_name, ci_mth, first_date, market_at_filing,
                                       halted_at_filing, payment_type, payment_source)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (c["rcept_no"], c["corp_code"], c["corp_name"], x.ci_mth, c["rcept_dt"], c["market"], c["halted"],
                 x.payment_type, x.payment_source))
            conn.execute("INSERT INTO filings (rcept_no, event_id, rcept_dt, is_correction, version_no) VALUES (%s, %s, %s, false, 1)",
                         (c["rcept_no"], c["rcept_no"], c["rcept_dt"]))
            cols = [k for k in VALUE_COLS if k in x.values]
            conn.execute(
                f"""INSERT INTO rights_values (rcept_no, event_id, extractor_version, {', '.join(cols)})
                    VALUES (%s, %s, %s, {', '.join(['%s'] * len(cols))})""",
                (c["rcept_no"], c["rcept_no"], EXTRACTOR_VERSION, *[x.values[k] for k in cols]))
            for i, a in enumerate(x.allottees, 1):
                conn.execute("""INSERT INTO allottees (rcept_no, seq, event_id, name, relation, shares, lockup)
                                VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                             (c["rcept_no"], i, c["rcept_no"], a.name, a.relation, a.shares, a.lockup))


def checks(c) -> list[str]:
    """맞물림 검사: 값끼리 서로 맞는가.
    현금으로 정해지지 않은 사건(빚→주식·현물·LLM 대기)은 '신주 수 × 가격 ≈ 자금 용도 합계'와
    '대상자 합 = 신주 수'를 건너뛴다 (새 돈이 없거나 대상자 표가 일부만 적혀 있을 수 있음)."""
    x, v, out = c["x"], c["x"].values, []
    ns, sb, price = v.get("new_shares"), v.get("shares_before"), v.get("price")
    funds = [v.get(k) for k in FUNDS]
    cash = x.payment_type == "현금" and v.get("stock_type") == "보통"  # 혼합·우선은 우선주 몫이 빠져 있어 건너뜀
    if cash and ns is not None and price is not None and all(f is not None for f in funds):
        total, raise_ = sum(funds), Decimal(ns) * price
        if total and abs(raise_ / total - 1) > Decimal("0.01"):
            out.append(f"신주 수×가격 {raise_:,.0f} ≠ 자금 용도 합계 {total:,} ({raise_ / total * 100:.1f}%)")
        if not total:
            out.append("자금 용도 합계 0")
    if ns is not None and sb:
        d = ns / (sb + ns)
        if not 0 < d < 1:
            out.append(f"희석률 이상 {d:.2%}")
    if x.group == THIRD_PARTY:
        s = sum(a.shares or 0 for a in x.allottees)
        if not x.allottees:
            out.append("대상자 없음")
        elif cash and ns is not None and s != ns:
            out.append(f"대상자 주식 수 합 {s:,} ≠ 신주 수 {ns:,}")
    seq = [("record_date", v.get("record_date")), ("subscription_start", v.get("subscription_start")),
           ("subscription_end", v.get("subscription_end")), ("listing_date", v.get("listing_date"))]
    seq = [(k, d) for k, d in seq if d is not None]
    for (k1, d1), (k2, d2) in zip(seq, seq[1:]):
        if d1 > d2:
            out.append(f"날짜 순서 {k1} {d1} > {k2} {d2}")
    if v.get("listing_date") and v["listing_date"] <= c["rcept_dt"]:
        out.append(f"상장 예정일 {v['listing_date']}이 공시일보다 빠름")
    return out


def notes(c) -> list[str]:
    """오류는 아니지만 확인 자료에 표시할 것."""
    x, v, out = c["x"], c["x"].values, []
    ns = v.get("new_shares")
    s = sum(a.shares or 0 for a in x.allottees)
    if x.group == THIRD_PARTY and x.payment_type != "현금" and x.allottees and ns and s < ns:
        out.append(f"대상자 일부만 기재 (표의 합 {s:,} / 신주 수 {ns:,})")
    if x.payment_source == "pending:llm":
        out.append(f"LLM 대기 ({','.join(x.noncash_words)})")
    return out


def report(items, why):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "xml").mkdir(exist_ok=True)
    head = ["rcept_no", "회사", "시험지", "CI_MTH", "고른 이유", "거래정지", "payment_type", "payment_source", "걸린 단어",
            *VALUE_COLS, "대상자", "맞물림 검사", "참고"]
    rows = []
    for c in items:
        x = c["x"]
        alls = "; ".join(f"{a.name}/{a.relation or '-'}/{a.shares or '-'}/{a.lockup or '-'}" for a in x.allottees)
        rows.append([c["rcept_no"], c["corp_name"], x.group, x.ci_mth, why[c["rcept_no"]], c["halted"],
                     x.payment_type or ("LLM 대기" if x.payment_source == "pending:llm" else ""),
                     x.payment_source, ",".join(x.noncash_words), *[x.values.get(k, "") for k in VALUE_COLS], alls,
                     " / ".join(checks(c)), " / ".join(notes(c))])
    with (OUT / "values.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(head)
        w.writerows(rows)

    lines = [f"# 유상증자 {len(items)}건 — 코드 칸 확인 (추출 {EXTRACTOR_VERSION})", ""]
    lines += ["## 채움률 (시험지별, 명세상 채워져야 하는 코드 칸)", ""]
    for g in (SHAREHOLDER, PUBLIC, THIRD_PARTY):
        gi = [c for c in items if c["x"].group == g]
        lines += [f"### {g} ({len(gi)}건, CI_MTH {dict(Counter(c['x'].ci_mth for c in gi))})", "",
                  "| 칸 | 채움 | 빈 칸 이유 | 빈 공시 (최대 8) |", "|---|---|---|---|"]
        for k in EXPECTED[g]:
            empty = [c for c in gi if c["x"].values.get(k) is None]
            reasons = Counter(why_empty(c, k) for c in empty)
            names = ", ".join(f"{c['corp_name']}" for c in empty[:8]) + (" …" if len(empty) > 8 else "")
            lines.append(f"| {k} | {len(gi) - len(empty)}/{len(gi)} ({(len(gi) - len(empty)) / len(gi) * 100:.0f}%) | "
                         f"{', '.join(f'{r} {n}' for r, n in reasons.most_common())} | {names} |")
        if g == SHAREHOLDER:
            n = sum(1 for c in gi if c["x"].values.get("price_status") == "예정")
            lines.append(f"| price_confirm_date (예정가일 때) | {sum(1 for c in gi if c['x'].values.get('price_confirm_date'))}/{n} | |")
        if g == THIRD_PARTY:
            lines.append(f"| 대상자 (allottees) | {sum(1 for c in gi if c['x'].allottees)}/{len(gi)} | "
                         f"{', '.join(c['corp_name'] for c in gi if not c['x'].allottees)} |")
        lines.append("")
    lines += ["## 주식 종류 (stock_type)", ""] + [f"- {k}: {n}" for k, n in Counter(c["x"].values.get("stock_type") for c in items).most_common()]
    lines += [f"  - 혼합: {c['corp_name']} {c['rcept_no']}" for c in items if c["x"].values.get("stock_type") == "혼합"]
    lines += ["", "## payment_type", ""] + [f"- {k}: {n}" for k, n in Counter(f"{c['x'].payment_type or 'LLM 대기'} ({c['x'].payment_source})" for c in items).items()]
    pend = [c for c in items if c["x"].payment_source == "pending:llm"]
    words = Counter(w for c in pend for w in c["x"].noncash_words)
    sh = [c for c in items if c["x"].group == SHAREHOLDER]
    sh_market = [c for c in sh if c["x"].values.get("right_sale") == "시장"]
    no_kw = [c for c in items if c["x"].payment_source == "rule:no_keyword"]
    ci_rule = [c for c in items if c["x"].payment_source == "rule:ci_mth"]
    lines += ["", "## LLM 대기 (종류별)", "",
              "| LLM 칸 | 대상 | 건수 |", "|---|---|---|",
              f"| payment_type | 제3자배정 중 규칙으로 못 정한 것 | {len(pend)} |",
              f"| discount_rate | 주주배정 계열 (24번 글) | {len(sh)} |",
              f"| right_sale_period | 주주배정 중 권리 매도 = 시장 | {len(sh_market)} |", "",
              "payment_type LLM 대기의 걸린 단어 (한 공시에 여러 개 가능): " + ", ".join(f"{w} {n}" for w, n in words.most_common()), "",
              "## Opus 정답표 대상 규모 (명세 6장)", "",
              f"- LLM 대기 전부: payment_type {len(pend)} + 할인율 {len(sh)} + 권리 매도 기간 {len(sh_market)}",
              f"- 제3자배정 rule:no_keyword 전부: {len(no_kw)}",
              f"- 주주배정 · 일반공모 rule:ci_mth 표본: 전체 {len(ci_rule)}건 중 일부 (표본 크기는 LLM 단계 계획 때 정함)"]
    lines += ["", "## 맞물림 검사에서 걸린 것", ""]
    bad = [(c, checks(c)) for c in items if checks(c)]
    lines += [f"- {c['corp_name']} {c['rcept_no']}: " + " / ".join(m) for c, m in bad] or ["- 없음"]
    lines += ["", "## 참고 (오류 아님)", ""]
    lines += [f"- {c['corp_name']} {c['rcept_no']}: " + " / ".join(notes(c)) for c in items if notes(c)] or ["- 없음"]
    (OUT / "checks.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    trs = []
    for c, r in zip(items, rows):
        meta = {"rcept_no": c["rcept_no"], "회사": c["corp_name"], "공시일": c["rcept_dt"], "시험지": c["x"].group,
                "CI_MTH": c["x"].ci_mth, "거래정지": c["halted"]}
        (OUT / "xml" / f"{c['rcept_no']}.html").write_text(
            render_html(RAW / c["rcept_no"] / c["main_file"], c["corp_name"], meta, "../index.html"), encoding="utf-8")
        filled = "".join(f"<tr><th>{html.escape(k)}</th><td>{html.escape(str(val))}</td></tr>"
                         for k, val in zip(head[5:], r[5:]) if val not in ("", None))
        trs.append(f"<details><summary><b>{html.escape(c['corp_name'])}</b> {c['rcept_no']} · {c['x'].group} · "
                   f"{html.escape(why[c['rcept_no']])} · <a href='xml/{c['rcept_no']}.html'>원문</a>"
                   f"{' · ⚠ ' + html.escape(r[-2]) if r[-2] else ''}</summary><table>{filled}</table></details>")
    (OUT / "index.html").write_text(
        "<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>유상증자 30건 시범</title><style>body{font:14px/1.6 sans-serif;margin:16px;max-width:1000px}"
        "td,th{border:1px solid #ccc;padding:2px 8px;text-align:left}table{border-collapse:collapse;margin:6px 0 12px}"
        "details{margin:6px 0}@media(prefers-color-scheme:dark){body{background:#111827;color:#e5e7eb}a{color:#93c5fd}}</style></head>"
        "<body><h2>유상증자 30건 시범 — 추출값과 원문</h2>" + "".join(trs) + "</body></html>", encoding="utf-8")


def main():
    global OUT
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--sample", type=int)
    g.add_argument("--same-sample", action="store_true")
    g.add_argument("--all", action="store_true")
    ap.add_argument("--start", type=date.fromisoformat, default=date(2026, 1, 1))
    ap.add_argument("--end", type=date.fromisoformat, default=date(2099, 12, 31))
    args = ap.parse_args()
    with connect() as conn:
        cands = load_candidates(conn, args.start, args.end)
        print(f"대상 원본 {len(cands)}건: " + ", ".join(f"{k} {n}" for k, n in Counter(c['x'].group for c in cands).items()))
        if args.all:
            OUT = DATA_DIR / "reports" / ("rights_all" if args.start == date(2026, 1, 1) else f"rights_all_{args.start:%Y%m%d}_{args.end:%Y%m%d}")
            items, why = cands, {c["rcept_no"]: "전체" for c in cands}
        elif args.same_sample:
            ids = {r[0] for r in conn.execute("SELECT event_id FROM events WHERE event_type = '유상증자' AND first_date BETWEEN %s AND %s", (args.start, args.end))}
            items, why = [c for c in cands if c["rcept_no"] in ids], {c["rcept_no"]: "지난번과 같은 표본" for c in cands}
        else:
            items, why = pick_sample(cands, args.sample // 3)
        save(conn, items)
        print(f"저장: events·filings·rights_values {len(items)}건, allottees {sum(len(c['x'].allottees) for c in items)}명")
    report(items, why)
    print(f"확인 자료 → {OUT.relative_to(DATA_DIR.parent)}/ (values.csv, checks.md, index.html)")


if __name__ == "__main__":
    main()
