"""1단계 A 거르기: 유상증자 공시가 공시 당시 코스피·코스닥에서 거래 중이었는지 → listing_checks + 보고 1.

    uv run python scripts/check_listing.py                                             # 2026년 (원본 + 정정)
    uv run python scripts/check_listing.py --start 2023-04-17 --end 2025-12-31 --originals-only
    uv run python scripts/check_listing.py --start 2023-04-17 --end 2023-04-17 --originals-only   # 하루치 시험
    uv run python scripts/check_listing.py --kind cb --start 2023-04-17 --end 2026-10-15 --originals-only   # 전환사채
KRX: 1초에 1번 (사용자 규칙). 시작 전에 차단 여부를 확인하고, 차단 중이면 멈춘다.
"""

import argparse
from collections import Counter
from datetime import date, timedelta

from filingscope.classify.listing import check_listing, prior_trading_days
from filingscope.collect.krx import MarketSnapshots, is_blocked, trading_calendar
from filingscope.db import connect

START = date(2026, 1, 1)
END = date(2099, 12, 31)


def print_table(headers, rows):
    cols = list(zip(headers, *rows)) if rows else [(h,) for h in headers]
    widths = [max(len(str(x)) for x in col) for col in cols]
    print("  " + "  ".join(str(h).ljust(w) for h, w in zip(headers, widths)))
    for r in rows:
        print("  " + "  ".join(str(x).ljust(w) for x, w in zip(r, widths)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=date.fromisoformat, default=START)
    ap.add_argument("--end", type=date.fromisoformat, default=END)
    ap.add_argument("--originals-only", action="store_true", help="정정 공시는 빼고 원본만")
    ap.add_argument("--kind", choices=["rights", "cb"], default="rights")
    a = ap.parse_args()
    orig = "AND NOT f.is_correction" if a.originals_only else ""
    kind = ("f.event_type = 'rights_issue'" if a.kind == "rights" else  # 전환사채: fetch_documents.py cb-2023-2026과 같은 조건
            "(f.report_nm LIKE '%%전환사채권발행결정%%' OR f.report_nm LIKE '%%전환사채발행결정%%')")
    if is_blocked():  # pykrx는 import할 때 로그인을 시도하므로 그 전에 확인
        print("KRX 차단 중 — 시도하지 않고 멈춤")
        return
    with connect() as conn:
        docs = conn.execute(
            """SELECT f.rcept_no, f.corp_name, f.stock_code, f.corp_cls, f.rcept_dt, d.doc_code
               FROM title_filings f JOIN documents d USING (rcept_no)
               WHERE {kind} AND f.rcept_dt BETWEEN %s AND %s {orig} ORDER BY f.rcept_no""".format(kind=kind, orig=orig),
            (a.start, a.end)).fetchall()
        no_body = conn.execute(
            """SELECT count(*), string_agg(DISTINCT coalesce(report_tag, ''), ',') FROM title_filings
               WHERE {kind} AND rcept_dt BETWEEN %s AND %s {orig} AND rcept_no NOT IN (SELECT rcept_no FROM documents)""".format(
                kind=kind.replace("f.", ""), orig=orig.replace("f.", "")), (a.start, a.end)).fetchone()

        calendar = trading_calendar(a.start - timedelta(days=31), max(r[4] for r in docs))
        snaps = MarketSnapshots()
        results = []
        for i, (no, name, code, cls, filed, dcode) in enumerate(docs, 1):
            r = check_listing(code, prior_trading_days(calendar, filed), snaps.volume_on)
            results.append((no, name, code, cls, filed, dcode, r))
            if i % 300 == 0:
                print(f"  ... {i}/{len(docs)} (시세 새로 받음 {snaps.fetched})")

        with conn.cursor() as cur:
            cur.execute("DELETE FROM listing_checks WHERE rcept_no = ANY(%s)", ([x[0] for x in results],))
            cur.executemany(
                """INSERT INTO listing_checks (rcept_no, stock_code, window_start, window_end, market,
                                               listed_days, traded_days, passed, reason)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                [(no, code, r.window[0] if r.window else None, r.window[1] if r.window else None, r.market,
                  r.listed_days, r.traded_days, r.passed, r.reason) for no, _, code, _, _, _, r in results])
        conn.commit()

    print(f"\n===== 보고 1: A 거르기 (공시 당시 코스피·코스닥 거래 중) =====")
    print(f"대상: 원문 있는 {a.start}~{min(a.end, max(x[4] for x in results))} {'유상증자' if a.kind == 'rights' else '전환사채'} {len(results)}건 / 원문 없는 [{no_body[1]}] {no_body[0]}건은 제외")
    print(f"기준: 공시일 직전 1거래일에 코스피·코스닥에서 거래량 > 0 (pykrx 날짜별 시세 {snaps.fetched}번 새로 받음)")
    passed = [x for x in results if x[6].passed]
    print(f"\n통과 {len(passed)} / 버림 {len(results) - len(passed)}")
    codes = [dc for dc, _ in Counter(x[5] for x in results).most_common()]
    print_table(["이유", "건수", *codes],
                [(reason, n, *[sum(1 for x in results if x[6].reason == reason and x[5] == dc) for dc in codes])
                 for reason, n in Counter(x[6].reason for x in results).most_common()])

    print("\n[지금 corp_cls별 × 판정]  (지금 구분이 아니라 당시 기준으로 판정했는지 확인)")
    print_table(["지금 corp_cls", "통과", "버림"],
                [(c, sum(1 for x in results if x[3] == c and x[6].passed), sum(1 for x in results if x[3] == c and not x[6].passed))
                 for c in ("Y", "K", "N", "E")])
    rescued = [x for x in passed if x[3] == "E"]
    print(f"\n통과했는데 지금 corp_cls가 E인 공시 (= 생존 편향으로 살린 것): {len(rescued)}건, 회사 {len({x[2] for x in rescued})}곳")
    for no, name, code, _, filed, _, r in rescued[:10]:
        print(f"  {no} {name} {code} 공시일 {filed} → 당시 {r.market}, 거래일 {r.traded_days}/{r.listed_days}")

    yk_dropped = [x for x in results if x[3] in ("Y", "K") and not x[6].passed]
    print(f"\n지금 Y·K인데 버린 공시: {len(yk_dropped)}건 (당시 거래정지였거나 상장 전)")
    for no, name, code, cls, filed, _, r in yk_dropped[:15]:
        print(f"  {no} {name} {code} ({cls}) 공시일 {filed} → {r.reason}, 당시 {r.market or '-'}, 범위 {r.window}")


if __name__ == "__main__":
    main()
