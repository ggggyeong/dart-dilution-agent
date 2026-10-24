"""Opus 정답표: LLM 대기 124 + 제3자배정 rule:no_keyword 표본 50 + 주주배정·일반공모 rule:ci_mth 표본 10.

    uv run python scripts/opus_answer_key.py          # 실행 (이미 한 공시는 건너뜀)
    uv run python scripts/opus_answer_key.py --report # 결과 보고만

결과: data/reports/opus_labels.csv
"""

import argparse
import csv
import random
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import anthropic

from filingscope.config import DATA_DIR, get_settings
from filingscope.db import connect
from filingscope.llm.answer_key import (MODEL, PRICE_PER_MTOK, PROMPT_VERSION, document_text, evidence_in_text,
                                        label)

RAW = DATA_DIR / "raw" / "document"
OUT = DATA_DIR / "reports" / "opus_labels.csv"
SEED = 42
COLS = ["rcept_no", "corp_name", "group", "tasks", "why", "rule_payment", "rule_source",
        "payment_type", "payment_evidence", "payment_evidence_ok", "payment_reason",
        "discount_rate", "discount_evidence", "discount_evidence_ok", "discount_reason",
        "right_sale_start", "right_sale_end", "right_sale_evidence", "right_sale_evidence_ok", "right_sale_reason",
        "model", "prompt_version", "input_tokens", "output_tokens", "stop_reason", "error"]
GROUP = {1: "주주배정", 2: "주주배정", 3: "주주배정", 5: "일반공모", 6: "제3자배정"}


def targets(conn) -> list[dict]:
    rows = conn.execute(
        """SELECT e.event_id, e.corp_name, e.ci_mth, e.payment_type, e.payment_source, v.right_sale, d.main_file
           FROM events e JOIN rights_values v ON v.rcept_no = e.event_id JOIN documents d ON d.rcept_no = e.event_id
           WHERE e.first_date >= '2026-01-01'  -- 2026년 정답표 대상 고정 (2023~2025 재검증은 validate_fresh.py)
           ORDER BY e.event_id""").fetchall()
    ev = [dict(rcept_no=r[0], corp_name=r[1], group=GROUP[r[2]], rule_payment=r[3], rule_source=r[4],
               right_sale=r[5], main_file=r[6]) for r in rows]
    rng = random.Random(SEED)
    pend = [e for e in ev if e["rule_source"] == "pending:llm"]
    sh = [e for e in ev if e["group"] == "주주배정"]
    nokw = [e for e in ev if e["rule_source"] == "rule:no_keyword"]
    rng.shuffle(nokw)
    pub = [e for e in ev if e["group"] == "일반공모"]
    rng.shuffle(pub)
    shs = sh[:]
    rng.shuffle(shs)
    plan: dict[str, dict] = {}

    def add(e, task, why):
        p = plan.setdefault(e["rcept_no"], dict(e, tasks=[], why=[]))
        if task not in p["tasks"]:
            p["tasks"].append(task)
        if why not in p["why"]:
            p["why"].append(why)

    for e in pend:
        add(e, "payment", "LLM 대기")
    for e in sh:
        add(e, "discount", "주주배정 할인율")
        if e["right_sale"] == "시장":
            add(e, "right_sale", "권리 매도 = 시장")
    for e in nokw[:50]:
        add(e, "payment", "no_keyword 표본")
    for e in shs[:5] + pub[:5]:
        add(e, "payment", "ci_mth 표본")
    order = {"payment": 0, "discount": 1, "right_sale": 2}
    for p in plan.values():
        p["tasks"].sort(key=order.get)
    return list(plan.values())


def done_ids() -> set[str]:
    if not OUT.exists():
        return set()
    with OUT.open(encoding="utf-8-sig") as f:
        return {r["rcept_no"] for r in csv.DictReader(f) if not r["error"]}


def run(items):
    client = anthropic.Anthropic(api_key=get_settings().anthropic_api_key.get_secret_value(), max_retries=4)
    done = done_ids()
    todo = [p for p in items if p["rcept_no"] not in done]
    print(f"대상 {len(items)}건, 이미 함 {len(items) - len(todo)}건 → 이번 {len(todo)}건 (모델 {MODEL}, {PROMPT_VERSION})")
    old = []
    if OUT.exists():
        with OUT.open(encoding="utf-8-sig") as f:
            old = [r for r in csv.DictReader(f) if not r["error"]]
    lock = threading.Lock()
    f = OUT.open("w", newline="", encoding="utf-8-sig")
    w = csv.DictWriter(f, fieldnames=COLS)
    w.writeheader()
    w.writerows(old)
    f.flush()

    def one(p):
        text = document_text(RAW / p["rcept_no"] / p["main_file"])
        row = {k: p.get(k, "") for k in ("rcept_no", "corp_name", "group", "rule_payment", "rule_source")}
        row.update(tasks=",".join(p["tasks"]), why=",".join(p["why"]), model=MODEL, prompt_version=PROMPT_VERSION, error="")
        try:
            out, usage, stop = label(client, text, p["tasks"])
        except Exception as e:  # 한 건 실패가 전체를 멈추지 않게
            row["error"] = f"{type(e).__name__}: {e}"[:300]
            return row
        row.update(input_tokens=usage.input_tokens, output_tokens=usage.output_tokens, stop_reason=stop)
        if "payment" in p["tasks"]:
            x = out.payment
            row.update(payment_type=x.payment_type, payment_evidence=x.evidence, payment_reason=x.reason,
                       payment_evidence_ok=evidence_in_text(x.evidence, text))
        if "discount" in p["tasks"]:
            x = out.discount
            row.update(discount_rate=x.discount_rate, discount_evidence=x.evidence, discount_reason=x.reason,
                       discount_evidence_ok=evidence_in_text(x.evidence, text))
        if "right_sale" in p["tasks"]:
            x = out.right_sale
            row.update(right_sale_start=x.start, right_sale_end=x.end, right_sale_evidence=x.evidence,
                       right_sale_reason=x.reason, right_sale_evidence_ok=evidence_in_text(x.evidence, text))
        return row

    n = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        for fut in as_completed([pool.submit(one, p) for p in todo]):
            row = fut.result()
            with lock:
                w.writerow(row)
                f.flush()
                n += 1
                if row["error"]:
                    print(f"  FAIL {row['rcept_no']} {row['corp_name']}: {row['error'][:120]}")
                if n % 20 == 0:
                    print(f"  ... {n}/{len(todo)}")
    f.close()


def report():
    with OUT.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    ok = [r for r in rows if not r["error"]]
    tin = sum(int(r["input_tokens"] or 0) for r in ok)
    tout = sum(int(r["output_tokens"] or 0) for r in ok)
    cost = (tin * PRICE_PER_MTOK["input"] + tout * PRICE_PER_MTOK["output"]) / 1e6
    print(f"===== Opus 정답표 ({MODEL}, {PROMPT_VERSION}) =====")
    print(f"성공 {len(ok)} / 실패 {len(rows) - len(ok)} | 토큰 입력 {tin:,} · 출력 {tout:,} → 약 ${cost:.2f}")
    pay = [r for r in ok if r["payment_type"]]
    print("\n[납입 방식] 묶음별 Opus 판정")
    for why in ("LLM 대기", "no_keyword 표본", "ci_mth 표본"):
        g = [r for r in pay if why in r["why"]]
        print(f"  {why} {len(g)}건: {dict(Counter(r['payment_type'] for r in g))}")
    diff = [r for r in pay if r["rule_payment"] and r["rule_payment"] != r["payment_type"]]
    print(f"\n[규칙 판정과 다른 것] {len(diff)}건")
    for r in diff:
        print(f"  {r['corp_name']} {r['rcept_no']} 규칙 {r['rule_payment']}({r['rule_source']}) → Opus {r['payment_type']}: {r['payment_reason']}")
    disc = [r for r in ok if "discount" in r["tasks"]]
    print(f"\n[할인율] {len(disc)}건: 값 있음 {sum(1 for r in disc if r['discount_rate'])}, null {sum(1 for r in disc if not r['discount_rate'])}")
    print("  값 분포:", dict(Counter(r["discount_rate"] for r in disc).most_common(10)))
    rs = [r for r in ok if "right_sale" in r["tasks"]]
    print(f"\n[권리 매도 기간] {len(rs)}건: 날짜 있음 {sum(1 for r in rs if r['right_sale_start'] and r['right_sale_end'])}")
    print("\n[근거 문장이 원문에 없음]")
    for t in ("payment", "discount", "right_sale"):
        bad = [r for r in ok if r.get(f"{t}_evidence_ok") == "False"]
        print(f"  {t}: {len(bad)}건 " + ", ".join(f"{r['corp_name']}" for r in bad[:10]))
    unk = [r for r in pay if r["payment_type"] == "불명"]
    print(f"\n[불명] {len(unk)}건: " + "; ".join(f"{r['corp_name']} {r['rcept_no']}: {r['payment_reason']}" for r in unk))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    if not args.report:
        with connect() as conn:
            items = targets(conn)
        print("과제 구성:", dict(Counter(",".join(p["tasks"]) for p in items)))
        run(items)
    report()


if __name__ == "__main__":
    main()
