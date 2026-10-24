"""DART 전용 API(유상증자·유무상증자·CB 결정)로 제목 판정 크로스체크.

    uv run python scripts/crosscheck_dart_api.py targets   # 대상 회사 A(전부)·B(무작위 300, 시드 42) 저장
    uv run python scripts/crosscheck_dart_api.py run       # 회사 × API 3개 호출 (이어받기, 020이면 멈춤)
    uv run python scripts/crosscheck_dart_api.py report    # 비교 결과표

run은 여러 번 돌려도 된다: 이미 성공한 (API, 회사)는 건너뛴다.
"""

import argparse
import csv
import random
from collections import Counter
from datetime import datetime

from psycopg.types.json import Jsonb

from filingscope.collect.dart_client import DartClient
from filingscope.collect.ds005 import DS005_APIS, fetch_corp_codes, run_ds005
from filingscope.config import DATA_DIR, get_settings
from filingscope.db import connect
from filingscope.db.filings import insert_collect_log
from filingscope.validate.crosscheck import compare

B_SIZE, B_SEED = 300, 42
B_LISTING_DATES = ("20230417", "20261015")  # 수집 시작일(일요일) 다음 영업일, 종료일
REPORT_DIR = DATA_DIR / "reports"


def cmd_targets(conn, settings) -> None:
    if conn.execute("SELECT count(*) FROM crosscheck_targets").fetchone()[0]:
        print("이미 대상이 있음 — 그대로 사용 (다시 뽑으려면 crosscheck_targets를 비울 것)")
        return
    a = [r[0] for r in conn.execute("SELECT DISTINCT corp_code FROM title_filings ORDER BY 1")]

    from pykrx import stock  # get_settings() 뒤에 import해야 KRX 로그인 정보가 넘어간다
    tickers = set()
    for d in B_LISTING_DATES:
        for market in ("KOSPI", "KOSDAQ"):
            got = stock.get_market_ticker_list(d, market=market)
            print(f"  pykrx {d} {market}: {len(got)}종목")
            tickers |= set(got)
    stock_to_corp = fetch_corp_codes(settings.dart_api_key.get_secret_value())
    listed = {stock_to_corp[t] for t in tickers if t in stock_to_corp}
    unmapped = sorted(t for t in tickers if t not in stock_to_corp)
    pool = sorted(listed - set(a))
    b = random.Random(B_SEED).sample(pool, B_SIZE)
    print(f"  상장사 종목 {len(tickers)} → corp_code 연결 {len(listed)} (연결 안 됨 {len(unmapped)}: {unmapped[:10]})")
    print(f"  B 후보(A 제외) {len(pool)}곳 → 무작위 {B_SIZE}곳 (시드 {B_SEED})")

    with conn.cursor() as cur:
        cur.executemany("INSERT INTO crosscheck_targets (corp_code, grp, note) VALUES (%s, 'A', %s)",
                        [(c, "유상증자·CB 판정 있음") for c in a])
        cur.executemany("INSERT INTO crosscheck_targets (corp_code, grp, note) VALUES (%s, 'B', %s)",
                        [(c, f"상장사 중 판정 없음, seed={B_SEED}, pool={len(pool)}") for c in b])
    conn.commit()
    print(f"대상 저장: A {len(a)}곳, B {len(b)}곳")


def cmd_run(conn, settings) -> None:
    targets = [r[0] for r in conn.execute("SELECT corp_code FROM crosscheck_targets ORDER BY grp, corp_code")]
    done = {(ep.removesuffix(".json"), c) for ep, c in conn.execute(
        "SELECT DISTINCT endpoint, corp_code FROM collect_log WHERE ok AND corp_code IS NOT NULL")}
    run_id = datetime.now().strftime("%Y%m%dT%H%M%S")
    print(f"대상 {len(targets)}곳 × API {len(DS005_APIS)}개, 이미 성공 {len(done)} (run_id {run_id})")

    def save_rows(api, items):
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO crosscheck_api_rows (api, rcept_no, corp_code, corp_name, corp_cls, raw)
                   VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (api, rcept_no) DO NOTHING""",
                [(api, i["rcept_no"], i["corp_code"], i.get("corp_name"), i.get("corp_cls"), Jsonb(i)) for i in items])
        conn.commit()

    n = [0]

    def log_call(**row):
        insert_collect_log(conn, **row)
        conn.commit()
        n[0] += 1
        if not row["ok"]:
            print(f"  FAIL {row['endpoint']} {row['corp_code']}: {row['status']} {row['message']}")
        if n[0] % 500 == 0:
            print(f"  ... {n[0]}번 호출")

    client = DartClient(settings.dart_api_key.get_secret_value())
    s = run_ds005(client, targets, settings.collect_start_date, settings.collect_end_date,
                  DATA_DIR / "raw" / "ds005", done, save_rows, log_call, run_id)
    print(f"호출 {s.calls}, 건너뜀 {s.skipped}, 실패 {len(s.failed)}, 받은 공시 {s.rows}")
    if s.stopped_on_limit:
        print("하루 한도 초과(020)로 멈춤 — 내일 같은 명령을 다시 실행하면 이어서 받는다")


def write_csv(name: str, headers: list[str], rows: list[tuple]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    with (REPORT_DIR / name).open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(headers)
        w.writerows(rows)


def print_table(headers, rows):
    cols = list(zip(headers, *rows)) if rows else [(h,) for h in headers]
    widths = [max(len(str(x)) for x in col) for col in cols]
    print("  " + "  ".join(str(h).ljust(w) for h, w in zip(headers, widths)))
    for r in rows:
        print("  " + "  ".join(str(x).ljust(w) for x, w in zip(r, widths)))


def cmd_report(conn, settings) -> None:
    progress = conn.execute(
        """SELECT count(DISTINCT (endpoint, corp_code)) FROM collect_log
           WHERE ok AND corp_code IN (SELECT corp_code FROM crosscheck_targets)""").fetchone()[0]
    n_targets = conn.execute("SELECT count(*) FROM crosscheck_targets").fetchone()[0]
    print(f"===== 크로스체크 결과 (호출 완료 {progress}/{n_targets * len(DS005_APIS)}) =====")

    b001 = {r[0]: r[1] for r in conn.execute("SELECT rcept_no, report_nm FROM b001_filings")}
    titles_none = dict(conn.execute("SELECT report_nm, event_type FROM title_classifications").fetchall())

    for grp in ("A", "B"):
        corps = [r[0] for r in conn.execute("SELECT corp_code FROM crosscheck_targets WHERE grp = %s", (grp,))]
        ours = [dict(zip(("rcept_no", "corp_code", "corp_name", "corp_cls", "event_type", "is_mixed", "is_correction", "report_nm"), r))
                for r in conn.execute(
                    """SELECT rcept_no, corp_code, corp_name, corp_cls, event_type, is_mixed, is_correction, report_nm
                       FROM title_filings WHERE corp_code = ANY(%s)""", (corps,))]
        api_rows = [dict(zip(("api", "rcept_no", "corp_code", "corp_name", "corp_cls"), r))
                    for r in conn.execute(
                        "SELECT api, rcept_no, corp_code, corp_name, corp_cls FROM crosscheck_api_rows WHERE corp_code = ANY(%s)",
                        (corps,))]
        r = compare(ours, api_rows, set(b001))

        print(f"\n### {grp}그룹: 회사 {len(corps)}곳, 우리 판정 공시 {len(ours):,}건, API 공시 {len(api_rows):,}건")
        print("\n[API → 우리]  (API 공시가 우리 판정에 있는가)")
        rows = []
        for api in DS005_APIS:
            c = Counter(st for (ap, _), st in r.api_status.items() if ap == api)
            rows.append((api, sum(c.values()), c["match"], c["type_diff"], c["missed_title_none"], c["missed_not_in_b001"]))
        rows.append(("합계", *[sum(x[i] for x in rows) for i in range(1, 6)]))
        print_table(["API", "API 공시", "일치", "종류 다름", "놓침: 제목 none", "놓침: B001에 없음"], rows)

        print("\n[우리 → API]  (우리 공시의 최종본이 API에 있는가)")
        c = Counter(r.our_status.values())
        corr = Counter((o["is_correction"], r.our_status[o["rcept_no"]]) for o in ours)
        print_table(["상태", "건수", "원본", "정정본"],
                    [(st, c[st], corr[(False, st)], corr[(True, st)]) for st in ("exact", "superseded", "uncovered")])
        print("  exact: 접수번호 그대로 있음 / superseded: 같은 계열의 더 나중 최종본이 있음 / uncovered: 최종본 없음")

        print(f"\n[사건 수: 회사·계열별 우리 원본 수 vs API 건수] 같음 {r.count_same}, 다름 {len(r.count_diff)}")
        more_ours = sum(1 for *_, x, y in r.count_diff if x > y)
        print(f"  우리가 많음 {more_ours}, API가 많음 {len(r.count_diff) - more_ours}")

        # 목록
        by_no_api = {x["rcept_no"]: x for x in api_rows}
        missed = sorted((api, no, by_no_api[no]["corp_name"], by_no_api[no]["corp_cls"], st,
                         b001.get(no, ""), titles_none.get(b001.get(no, ""), ""))
                        for (api, no), st in r.api_status.items() if st.startswith("missed") or st == "type_diff")
        write_csv(f"crosscheck_{grp}_api_side.csv",
                  ["api", "rcept_no", "corp_name", "corp_cls", "status", "b001_report_nm", "our_event_type"], missed)
        ours_by_no = {x["rcept_no"]: x for x in ours}
        uncovered = sorted((no, ours_by_no[no]["corp_name"], ours_by_no[no]["corp_cls"], ours_by_no[no]["event_type"],
                            ours_by_no[no]["is_correction"], ours_by_no[no]["report_nm"])
                           for no, st in r.our_status.items() if st == "uncovered")
        write_csv(f"crosscheck_{grp}_uncovered.csv",
                  ["rcept_no", "corp_name", "corp_cls", "event_type", "is_correction", "report_nm"], uncovered)
        write_csv(f"crosscheck_{grp}_count_diff.csv", ["corp_code", "family", "our_originals", "api_rows"], r.count_diff)

        if missed:
            print(f"\n  놓침·종류 다름 목록 ({len(missed)}건, 앞 20건) → data/reports/crosscheck_{grp}_api_side.csv")
            print_table(["API", "rcept_no", "회사", "cls", "상태", "B001 제목"], [m[:6] for m in missed[:20]])
        if uncovered:
            print(f"\n  uncovered 목록 ({len(uncovered)}건, 앞 20건) → data/reports/crosscheck_{grp}_uncovered.csv")
            print_table(["rcept_no", "회사", "cls", "종류", "정정", "제목"], uncovered[:20])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["targets", "run", "report"])
    args = ap.parse_args()
    settings = get_settings()
    with connect() as conn:
        {"targets": cmd_targets, "run": cmd_run, "report": cmd_report}[args.cmd](conn, settings)


if __name__ == "__main__":
    main()
