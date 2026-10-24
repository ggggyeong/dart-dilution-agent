"""⑨-2 전수 재파싱 — 값만 제자리 갱신 (rights-v4 · cb-v3).

    uv run python scripts/reparse_values.py            # 갱신 + data/reports/reparse_v4/summary.json
    uv run python scripts/reparse_values.py --dry-run  # 바뀌는 건수만
    uv run python scripts/reparse_values.py --type CB [--dry-run]   # CB만 (calc 3단계 cb-v3) → data/reports/reparse_cb-v3/

build_rights_tables · build_cb_tables는 events를 지우고 다시 넣는다. 값만 다시 뽑을 때는 이 스크립트: events · filings 줄은 그대로 두고
rights_values · cb_values 칸 갱신, allottees 그 공시 줄만 바꿔 넣기, events는 납입 방식 칸만.

- LLM이 채운 칸은 건드리지 않는다: 주주배정 discount_rate · discount_evidence, right_sale_start · end · evidence.
- 납입 방식: 1단계 신호 없음 → 현금 rule:no_signal, 제목 "(출자전환)" → 빚→주식 rule:title,
  신호 있음 → 지금 값이 규칙이면 pending:llm (값 비움), 지금 값이 LLM이면 그대로 둠 (payment_v4_llm.py가 다시 판정).
"""

import argparse
import json
from collections import Counter
from decimal import Decimal

from filingscope.config import DATA_DIR
from filingscope.db import connect
from filingscope.parse import cb as CB
from filingscope.parse import rights_issue as RI
from filingscope.parse.acode import read_document

RAW = DATA_DIR / "raw" / "document"
OUT = DATA_DIR / "reports" / "reparse_v4"
R_COLS = ["new_shares", "shares_before", "price", "price_status", "price_confirm_date", "discount_rate",
          "fund_facility", "fund_business_acq", "fund_operating", "fund_debt", "fund_other_corp", "fund_other",
          "acquire_target", "lead_manager", "participation", "right_sale", "record_date", "shares_per_share",
          "subscription_start", "subscription_end", "listing_date", "stock_type", "other_new_shares", "other_price"]


def same(a, b) -> bool:
    if isinstance(a, (int, float, Decimal)) and isinstance(b, (int, float, Decimal)):
        return Decimal(str(a)) == Decimal(str(b))
    return a == b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--type", choices=["유상증자", "CB"], help="이 종류만 다시 씀 (없으면 전부)")
    a = ap.parse_args()
    out_dir = OUT if a.type is None else DATA_DIR / "reports" / f"reparse_{CB.EXTRACTOR_VERSION if a.type == 'CB' else RI.EXTRACTOR_VERSION}"
    changed, pay, notes = Counter(), Counter(), {}
    with connect() as conn:
        rows = conn.execute("""SELECT f.rcept_no, e.event_type, d.main_file, b.report_nm, e.payment_type, e.payment_source, f.rcept_dt
                               FROM filings f JOIN events e USING (event_id) JOIN documents d ON d.rcept_no = f.rcept_no
                               JOIN b001_filings b ON b.rcept_no = f.rcept_no
                               WHERE %(t)s::text IS NULL OR e.event_type = %(t)s ORDER BY 1""", {"t": a.type}).fetchall()
        old_r = {r[0].strip(): r for r in conn.execute(f"SELECT rcept_no, {', '.join(R_COLS)} FROM rights_values").fetchall()}
        old_c = {r[0].strip(): r for r in conn.execute(f"SELECT rcept_no, {', '.join(CB.VALUE_COLS)} FROM cb_values").fetchall()}
        siblings = CB.same_day_siblings(conn, RAW)
        old_a = {}
        for r in conn.execute("SELECT rcept_no, seq, name, relation, shares, lockup, amount FROM allottees ORDER BY 1, 2").fetchall():
            old_a.setdefault(r[0].strip(), []).append(tuple(r[2:]))
        with conn.transaction():
            for no, typ, mf, nm, ptype, psrc, rdt in rows:
                no = no.strip()
                doc = read_document(RAW / no / mf)
                if typ == "유상증자":
                    x = RI.extract(doc, nm)
                    cols = [c for c in R_COLS if not (c == "discount_rate" and x.group == RI.SHAREHOLDER)]   # 주주배정 할인율 = LLM
                    new = {c: x.values.get(c) for c in cols}
                    for i, c in enumerate(R_COLS, 1):
                        if c in new and not same(old_r[no][i], new[c]):
                            changed[f"rights_values.{c}"] += 1
                            notes.setdefault(f"rights_values.{c}", []).append(no)
                    allot = [(al.name, al.relation, al.shares, al.lockup, None) for al in x.allottees]
                    signals, version = x.noncash_words, RI.EXTRACTOR_VERSION
                    if not a.dry_run:
                        conn.execute(f"UPDATE rights_values SET {', '.join(f'{c} = %s' for c in cols)}, extractor_version = %s, extracted_at = now() "
                                     "WHERE rcept_no = %s", (*new.values(), version, no))
                else:
                    x = CB.extract(doc, nm, rdt, siblings.get(no, ()))
                    if x.sibling_rows:
                        notes.setdefault("형제 CB 줄 뺌", []).append((no, x.sibling_rows))
                    new = {c: x.values[c] for c in CB.VALUE_COLS}
                    for i, c in enumerate(CB.VALUE_COLS, 1):
                        if not same(old_c[no][i], new[c]):
                            changed[f"cb_values.{c}"] += 1
                            notes.setdefault(f"cb_values.{c}", []).append(no)
                            if a.type:   # 종류 하나만 다시 쓸 때는 바뀐 값 전부 남김
                                notes.setdefault(f"cb_values.{c} 전후", []).append((no, old_c[no][i], new[c], x.old_cb_rule))
                    allot = [(al.name, al.relation, None, None, al.amount) for al in x.allottees]
                    signals, version = x.signals, CB.EXTRACTOR_VERSION
                    if not a.dry_run:
                        conn.execute(f"UPDATE cb_values SET {', '.join(f'{c} = %s' for c in CB.VALUE_COLS)}, extractor_version = %s, extracted_at = now() "
                                     "WHERE rcept_no = %s", (*new.values(), version, no))
                # allottees: 그 공시 줄만 바꿔 넣기
                if [tuple(r) for r in old_a.get(no, [])] != allot:
                    oa = old_a.get(no, [])
                    changed["allottees.공시"] += 1
                    changed["allottees.relation 줄"] += sum(1 for o, n in zip(oa, allot) if o[1] != n[1])
                    if not a.dry_run:
                        conn.execute("DELETE FROM allottees WHERE rcept_no = %s", (no,))
                        for i, al in enumerate(allot, 1):
                            conn.execute("""INSERT INTO allottees (rcept_no, seq, event_id, name, relation, shares, lockup, amount)
                                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""", (no, i, no, *al))
                # 납입 방식
                src = x.payment_source
                old_is_llm = (psrc or "").startswith(("llm:", "manual:"))
                if src in ("rule:no_signal", "rule:title"):
                    if (ptype, psrc) != (x.payment_type, src):
                        pay[f"{psrc} → {src}" + ("" if ptype == x.payment_type else f" ({ptype}→{x.payment_type})")] += 1
                    if not a.dry_run:
                        conn.execute("""UPDATE events SET payment_type = %s, payment_source = %s, payment_evidence = NULL,
                                        payment_indirect = NULL WHERE event_id = %s""", (x.payment_type, src, no))
                elif old_is_llm:
                    pay[f"{(psrc or '').split(':')[0]} 유지 (v4 대기)"] += 1
                else:
                    pay[f"{psrc} ({ptype}) → pending:llm"] += 1
                    notes.setdefault("새로 LLM 대기", []).append((no, signals))
                    if not a.dry_run:
                        conn.execute("""UPDATE events SET payment_type = NULL, payment_source = 'pending:llm', payment_evidence = NULL,
                                        payment_indirect = NULL WHERE event_id = %s""", (no,))
    summary = {"rows": len(rows), "changed": dict(changed.most_common()), "payment": dict(pay.most_common()),
               "examples": {k: (v if a.type else v[:10]) for k, v in notes.items()}}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / ("dry_run.json" if a.dry_run else "summary.json")).write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("rows", "changed", "payment")}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
