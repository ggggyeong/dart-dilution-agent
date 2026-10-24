"""B001 공시 목록 전체 수집 → b001_filings (제목·시장 구분 없이).

    uv run python scripts/collect_b001.py                                  # .env 기간
    uv run python scripts/collect_b001.py --start 2026-01-16 --end 2026-04-15   # 실패 구간만 다시

같은 rcept_no는 다시 저장하지 않으므로 같은 기간을 여러 번 돌려도 안전하다.
"""

import argparse
from datetime import date, datetime

from filingscope.collect.dart_client import DartClient
from filingscope.collect.filing_list import collect
from filingscope.config import DATA_DIR, get_settings
from filingscope.db import connect
from filingscope.db.filings import insert_b001_filings, insert_collect_log


def main() -> None:
    settings = get_settings()
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=date.fromisoformat, default=settings.collect_start_date)
    ap.add_argument("--end", type=date.fromisoformat, default=settings.collect_end_date)
    args = ap.parse_args()

    run_id = datetime.now().strftime("%Y%m%dT%H%M%S")
    client = DartClient(settings.dart_api_key.get_secret_value())
    print(f"B001 수집 {args.start} ~ {args.end} (run_id {run_id})")

    with connect() as conn:
        def save(filings):
            n = insert_b001_filings(conn, filings)
            conn.commit()
            return n

        def log_call(**row):
            insert_collect_log(conn, **row)
            conn.commit()
            if not row["ok"] or row["page_no"] == 1:
                status = "OK " if row["ok"] else "FAIL"
                print(f"  {status} {row['bgn_de']}~{row['end_de']} p{row['page_no']} "
                      f"status={row['status']} attempts={row['attempts']}")

        s = collect(client, args.start, args.end, DATA_DIR / "raw" / "list", save, log_call, run_id)

    print(f"\n페이지 성공 {s.pages_ok}, 실패 {len(s.pages_failed)}")
    for p in s.pages_failed:
        print(f"  실패 {p.bgn}~{p.end} p{p.page_no}: {p.call.status} {p.call.message} ({p.call.attempts}회)")
    print(f"받은 공시 {s.fetched}건 (새로 저장 {s.inserted}, 이미 있음 {s.duplicates})")
    print("corp_cls별: " + ", ".join(f"{k} {v}" for k, v in sorted(s.by_cls.items())))


if __name__ == "__main__":
    main()
