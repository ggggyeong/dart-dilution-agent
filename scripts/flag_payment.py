"""납입 방식 검사 → review/payment_flags.csv (사람 확인용).

    uv run python scripts/flag_payment.py

대상: LLM이 "현금"으로 판정한 사건. 걸리는 조건은 validate/payment_flags.py.
참고로 규칙(rule:*)으로 현금이 된 사건에 같은 검사를 했을 때 걸리는 수도 출력한다 (목록에는 안 올림).
"""

import csv
from collections import Counter

from filingscope.config import DATA_DIR, PROJECT_ROOT
from filingscope.db import connect
from filingscope.parse.acode import read_document
from filingscope.validate.payment_flags import payment_flags

OUT = PROJECT_ROOT / "review" / "payment_flags.csv"
RAW = DATA_DIR / "raw" / "document"


def main():
    with connect() as conn:
        rows = conn.execute(
            """SELECT e.event_id, e.corp_name, e.first_date, e.payment_type, e.payment_source, e.payment_indirect,
                      e.payment_evidence, d.main_file
               FROM events e JOIN documents d ON d.rcept_no = e.event_id
               WHERE e.event_type = '유상증자' AND e.payment_type = '현금' ORDER BY e.event_id""").fetchall()
        names = {}
        for no, name in conn.execute("SELECT rcept_no, name FROM allottees ORDER BY rcept_no, seq"):
            names.setdefault(no, []).append(name)
    flagged, rule_hits = [], Counter()
    for no, corp, fd, pt, src, ind, ev, mf in rows:
        why = payment_flags(read_document(RAW / no / mf), names.get(no, []))
        if not why:
            continue
        if src.startswith("llm:"):
            flagged.append(dict(rcept_no=no, corp_name=corp, first_date=fd, payment_type=pt, payment_source=src,
                                payment_indirect=ind, reasons=" | ".join(why), payment_evidence=ev))
        else:
            rule_hits[src] += 1
            print(f"  (참고, 규칙 현금) {corp} {no} {src}: {' | '.join(why)[:150]}")
    OUT.parent.mkdir(exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["rcept_no", "corp_name", "first_date", "payment_type", "payment_source",
                                          "payment_indirect", "reasons", "payment_evidence"])
        w.writeheader()
        w.writerows(flagged)
    llm_cash = sum(1 for r in rows if r[4].startswith("llm:"))
    print(f"\n현금 사건 {len(rows)}건 (LLM 판정 {llm_cash}) 중 LLM 현금이 걸린 것 {len(flagged)}건 → {OUT}")
    for r in flagged:
        print(f"  {r['corp_name']} {r['rcept_no']}: {r['reasons'][:200]}")
    print(f"참고: 규칙으로 현금이 된 사건에서 걸린 것 {sum(rule_hits.values())}건 {dict(rule_hits)}")


if __name__ == "__main__":
    main()
