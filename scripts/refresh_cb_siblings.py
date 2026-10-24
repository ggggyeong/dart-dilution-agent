"""같은 날 CB 형제 다시 계산 (cb-v4, docs/calc_criteria.md v1 B-2) — 수집 마지막 단계.

    uv run python scripts/refresh_cb_siblings.py                    # 오늘 rcept_dt
    uv run python scripts/refresh_cb_siblings.py --date 2026-10-22 [--dry-run]

같은 회사가 같은 날 CB를 2건 이상 공시하면, 먼저 넣은 CB는 그때 형제가 DB에 없어 잔량표의 형제 줄을 old_cb_shares에서
못 뺀다. 그날 CB가 다 들어온 뒤 이 스크립트로 그 회사의 그날 CB들 old_cb_shares만 다시 계산해 덮어쓴다.
(전체를 다시 뽑는 reparse_values.py · build_cb_tables.py는 형제를 이미 넘기므로 따로 필요 없음.)
"""

import argparse
from datetime import date

from filingscope.config import DATA_DIR
from filingscope.db import connect
from filingscope.parse import cb as CB
from filingscope.parse.acode import read_document

RAW = DATA_DIR / "raw" / "document"


def refresh(conn, day: date, dry_run: bool = False) -> list[tuple]:
    """→ [(rcept_no, 전 old_cb_shares, 후, 규칙)] 바뀐 것만."""
    sib = CB.same_day_siblings(conn, RAW, day)
    out = []
    for no, siblings in sorted(sib.items()):
        mf, nm, old = conn.execute("""SELECT d.main_file, b.report_nm, v.old_cb_shares FROM documents d
                                      JOIN b001_filings b ON b.rcept_no = d.rcept_no JOIN cb_values v ON v.rcept_no = d.rcept_no
                                      WHERE d.rcept_no = %s""", (no,)).fetchone()
        x = CB.extract(read_document(RAW / no / mf), nm, day, siblings)
        new = x.values["old_cb_shares"]
        if new != old:
            out.append((no, old, new, x.old_cb_rule))
            if not dry_run:
                conn.execute("UPDATE cb_values SET old_cb_shares = %s, extractor_version = %s, extracted_at = now() WHERE rcept_no = %s",
                             (new, CB.EXTRACTOR_VERSION, no))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", type=date.fromisoformat, default=date.today())
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    with connect() as conn, conn.transaction():
        changed = refresh(conn, a.date, a.dry_run)
    print(f"{a.date}: 같은 날 CB 형제로 old_cb_shares {'바뀔' if a.dry_run else '바뀐'} 것 {len(changed)}건")
    for r in changed:
        print("  ", *r)


if __name__ == "__main__":
    main()
