"""LLM 판정 결과(실제 판정 모델)를 DB에 넣는다. 다시 돌려도 같은 결과 (build_rights_tables.py로 표를 다시 만들면 이것도 다시 돌린다).

    uv run python scripts/apply_llm_results.py           # 반영 + 점검 보고
    uv run python scripts/apply_llm_results.py --check   # 반영 없이 점검만

- 납입 방식: payment_source가 pending:llm(또는 이전 llm:*)인 사건만. 규칙으로 정한 사건은 건드리지 않는다.
- 할인율 · 권리 매도 기간: 주주배정(CI_MTH 1·2·3) 보통주. 권리 매도 기간은 right_sale = '시장'만.
- 사람 판정 덮어쓰기는 없앴다 (⑨-2 4-1: 파이프라인만의 값). 옛 manual/ 사례는 tests/fixtures/manual_cases.csv (회귀 기대값).
- 납입 방식은 payment-v4(scripts/payment_v4_llm.py)가 판정한 공시는 건드리지 않는다. 여기서는 v4로 아직 안 돌린 공시의
  예전(extract-v2/v3) 값만 다시 넣고, 예전 결과가 없는 대기(pending:llm) 공시는 v4 몫이라 건너뛴다.
- 근거 문장이 모델에 넣은 글에 없으면(evidence_ok = False) 그 값은 쓰지 않는다 (명세 6장).
- 결과 파일: data/reports/llm_compare/<버전>/<모델>.csv (2026년 135건) + data/reports/llm_runs/<버전>/<모델>.csv (run_llm.py).
  VERSIONS 순서대로 읽고 나중 버전이 앞 버전을 덮는다 (v3는 납입 방식 입력만 바뀌어 납입 방식만 다시 돌림 →
  할인율 · 권리 매도 기간은 v2 결과가 남는다). payment_source에는 그 납입 방식을 낸 버전을 적는다. 빠진 대상이 있으면 멈춘다.
"""

import argparse
import csv
from decimal import Decimal

from filingscope.config import DATA_DIR
from filingscope.db import connect
from filingscope.llm.extractor import PROMPT_VERSION

MODEL = "claude-opus-5-5"
VERSIONS = ["extract-v2", "extract-v3", PROMPT_VERSION]  # 오래된 것부터 (v4는 지시문만 바꾸고 돌리지 않음 → 지금 DB 값은 v2 · v3)
FUNDS = ["fund_facility", "fund_business_acq", "fund_operating", "fund_debt", "fund_other_corp", "fund_other"]


def load() -> dict[str, dict]:
    """공시별 결과. 과제마다 가장 나중 버전의 값을 쓰고, 그 과제를 낸 버전을 '<과제>_version'에 남긴다."""
    got: dict[str, dict] = {}
    cols = {"payment": ["payment_type", "payment_indirect", "payment_evidence", "payment_evidence_ok"],
            "discount": ["discount_rate", "discount_evidence", "discount_evidence_ok"],
            "right_sale": ["right_sale_start", "right_sale_end", "right_sale_evidence", "right_sale_evidence_ok"]}
    for ver in dict.fromkeys(VERSIONS):
        for src in (DATA_DIR / "reports" / "llm_compare" / ver / f"{MODEL}.csv", DATA_DIR / "reports" / "llm_runs" / ver / f"{MODEL}.csv"):
            if not src.exists():
                continue
            with src.open(encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    if r["error"]:
                        continue
                    row = got.setdefault(r["rcept_no"], {"rcept_no": r["rcept_no"], "tasks": ""})
                    for t in r["tasks"].split(","):
                        row.update({k: r.get(k, "") for k in cols[t]}, **{f"{t}_version": ver})
                        row["tasks"] = ",".join(dict.fromkeys([*filter(None, row["tasks"].split(",")), t]))
    return got


def targets(conn, got: dict):
    pay = [r[0] for r in conn.execute(
        """SELECT event_id FROM events WHERE event_type = '유상증자' AND (payment_source = 'pending:llm' OR payment_source LIKE 'llm:%%')
           AND payment_source NOT LIKE '%%payment-v4' ORDER BY 1""") if r[0] in got and got[r[0]].get("payment_type")]
    sh = conn.execute(
        """SELECT v.rcept_no, v.right_sale FROM rights_values v JOIN events e ON e.event_id = v.event_id
           WHERE e.ci_mth IN (1, 2, 3) AND v.stock_type = '보통' ORDER BY 1""").fetchall()
    return pay, sh


def apply(conn, got):
    pay, sh = targets(conn, got)
    missing = [n for n, _ in sh if n not in got or "discount" not in got[n]["tasks"]] + \
              [n for n, rs in sh if rs == "시장" and "right_sale" not in got[n]["tasks"]]
    if missing:
        raise SystemExit(f"결과 파일에 없는 대상 {len(missing)}건: {missing[:10]} — 멈춤")
    bad_ev = {(n, t) for n, r in got.items() for t in ("payment", "discount", "right_sale") if r.get(f"{t}_evidence_ok") == "False"}
    if bad_ev:
        print(f"근거가 입력 글에 없어 쓰지 않는 값 {len(bad_ev)}건: {sorted(bad_ev)}")
    with conn.transaction():
        for n in pay:
            r = got[n]
            if (n, "payment") in bad_ev:
                continue
            conn.execute("""UPDATE events SET payment_type = %s, payment_evidence = %s, payment_indirect = %s, payment_source = %s
                            WHERE event_id = %s""",
                         (r["payment_type"], r["payment_evidence"] or None, r["payment_indirect"] == "True",
                          f"llm:{MODEL}:{r['payment_version']}", n))
        for n, rs in sh:
            r = got[n]
            if (n, "discount") in bad_ev:
                r = dict(r, discount_rate="", discount_evidence="")
            if (n, "right_sale") in bad_ev:
                r = dict(r, right_sale_start="", right_sale_end="", right_sale_evidence="")
            conn.execute("UPDATE rights_values SET discount_rate = %s, discount_evidence = %s WHERE rcept_no = %s",
                         (Decimal(r["discount_rate"]) if r["discount_rate"] else None, r["discount_evidence"] or None, n))
            if rs == "시장":
                conn.execute("""UPDATE rights_values SET right_sale_start = %s, right_sale_end = %s, right_sale_evidence = %s
                                WHERE rcept_no = %s""",
                             (r["right_sale_start"] or None, r["right_sale_end"] or None, r["right_sale_evidence"] or None, n))
    from collections import Counter
    print(f"반영: 납입 방식 {len(pay)}건 {dict(Counter(got[n]['payment_version'] for n in pay))}, "
          f"할인율 {len(sh)}건 {dict(Counter(got[n]['discount_version'] for n, _ in sh))}, "
          f"권리 매도 기간 {sum(1 for _, rs in sh if rs == '시장')}건")


def check(conn):
    q = lambda s: conn.execute(s).fetchall()  # noqa: E731
    print("\n[납입 방식] 출처별")
    for src, t, ind, n in q("""SELECT payment_source, coalesce(payment_type, 'NULL'), payment_indirect, count(*) FROM events WHERE event_type = '유상증자'
                               GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"""):
        print(f"  {src:<32} {t:<6} 정황판단={ind} : {n}")
    print("  payment_type 비어 있음:", q("SELECT count(*) FROM events WHERE event_type = '유상증자' AND payment_type IS NULL")[0][0])
    print("\n[주주배정 채움]")
    print("  ", q("""SELECT count(*), count(v.discount_rate), count(v.discount_evidence),
                      count(*) FILTER (WHERE v.right_sale = '시장'),
                      count(v.right_sale_start) FILTER (WHERE v.right_sale = '시장'),
                      count(v.right_sale_end) FILTER (WHERE v.right_sale = '시장')
                    FROM rights_values v JOIN events e ON e.event_id = v.event_id WHERE e.ci_mth IN (1, 2, 3)""")[0],
          "← (주주배정, 할인율, 할인율 근거, 시장, 매도 시작일, 매도 종료일)")
    print("  할인율 분포:", q("""SELECT v.discount_rate, count(*) FROM rights_values v JOIN events e ON e.event_id = v.event_id
                              WHERE e.ci_mth IN (1, 2, 3) GROUP BY 1 ORDER BY 2 DESC"""))
    print("\n[점검] 걸린 것만")
    bad = 0
    for n, name, s, e, rd, ss in q("""SELECT v.rcept_no, e.corp_name, v.right_sale_start, v.right_sale_end, v.record_date, v.subscription_start
                                     FROM rights_values v JOIN events e ON e.event_id = v.event_id WHERE v.right_sale = '시장'"""):
        if s and e and (s > e or (rd and s <= rd) or (ss and e >= ss)):
            bad += 1
            print(f"  권리 매도 기간 순서 {name} {n}: 배정기준일 {rd} / 매도 {s}~{e} / 청약 시작 {ss}")
    for n, name, d in q("""SELECT v.rcept_no, e.corp_name, v.discount_rate FROM rights_values v JOIN events e ON e.event_id = v.event_id
                           WHERE e.ci_mth IN (1, 2, 3) AND v.discount_rate IS NOT NULL AND NOT v.discount_rate BETWEEN 0 AND 40"""):
        bad += 1
        print(f"  할인율 범위(0~40) 밖 {name} {n}: {d}")
    # LLM이 현금으로 판정한 사건은 이제 현금 맞물림 검사 대상
    cols = ", ".join(f"v.{c}" for c in FUNDS)
    for row in q(f"""SELECT e.event_id, e.corp_name, v.new_shares, v.price, v.stock_type, {cols}
                     FROM events e JOIN rights_values v ON v.rcept_no = e.event_id
                     WHERE e.payment_source LIKE 'llm:%' AND e.payment_type = '현금'"""):
        n, name, ns, price, st, *funds = row
        if st != "보통" or ns is None or price is None or any(f is None for f in funds):
            continue
        total, raise_ = sum(funds), Decimal(ns) * price
        if total and abs(raise_ / total - 1) > Decimal("0.01"):
            bad += 1
            print(f"  현금(LLM) 신주 수×가격 {raise_:,.0f} ≠ 자금 용도 합계 {total:,} {name} {n}")
        s = q(f"SELECT coalesce(sum(shares), 0), count(*) FROM allottees WHERE rcept_no = '{n}'")[0]
        if s[1] and s[0] != ns:
            bad += 1
            print(f"  현금(LLM) 대상자 주식 수 합 {s[0]:,} ≠ 신주 수 {ns:,} {name} {n}")
    print(f"  → {bad}건")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    with connect() as conn:
        if not a.check:
            apply(conn, load())
        check(conn)


if __name__ == "__main__":
    main()
