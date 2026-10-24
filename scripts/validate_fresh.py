"""새 표본 재검증: 2026년 135건으로 고친 extract-v2가 처음 보는 공시(2023~2025)에서도 맞는지.

    uv run python scripts/validate_fresh.py --estimate   # 표본 고르기 + 정답표 토큰 세기 (무료)
    uv run python scripts/validate_fresh.py --run        # Opus 원문 전체 정답표 만들기 (이미 한 것은 건너뜀) + 보고
    uv run python scripts/validate_fresh.py --report     # 보고만

표본: 2026년 이전 사건 중 LLM 대상에서 납입 방식 30 + 주주배정 30 (시드 42).
비교: 정답표(answer-key-v1, 원문 전체) vs 실제 판정(run_llm.py 결과 llm_runs/<버전>/). run_llm.py를 먼저 돌린다.
결과: data/reports/fresh_check/ (opus_labels.csv, report.md)
"""

import argparse
import csv
import random
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import anthropic

from filingscope.config import DATA_DIR, get_settings
from filingscope.db import connect
from filingscope.llm import answer_key as ak
from filingscope.llm.extractor import PROMPT_VERSION, tasks_for

SEED, N_PAY, N_SH = 42, 30, 30
RAW = DATA_DIR / "raw" / "document"
OUT = DATA_DIR / "reports" / "fresh_check"
KEY = OUT / "opus_labels.csv"
GOT = DATA_DIR / "reports" / "llm_runs" / PROMPT_VERSION / "claude-opus-5-5.csv"
COLS = ["rcept_no", "corp_name", "first_date", "tasks",
        "payment_type", "payment_evidence", "payment_evidence_ok", "payment_reason",
        "discount_rate", "discount_evidence", "discount_evidence_ok", "discount_reason",
        "right_sale_start", "right_sale_end", "right_sale_evidence", "right_sale_evidence_ok", "right_sale_reason",
        "input_tokens", "output_tokens", "error"]


def sample(conn) -> list[dict]:
    rows = conn.execute(
        """SELECT e.event_id, e.corp_name, e.first_date, e.ci_mth, v.stock_type, v.right_sale, e.payment_source, d.main_file
           FROM events e JOIN rights_values v ON v.rcept_no = e.event_id JOIN documents d ON d.rcept_no = e.event_id
           WHERE e.first_date < '2026-01-01' ORDER BY e.event_id""").fetchall()
    items = []
    for no, name, fd, ci, st, rs, src, mf in rows:
        t = tasks_for(ci, st, rs, src)
        if t:
            items.append(dict(rcept_no=no, corp_name=name, first_date=fd, tasks=t, path=RAW / no / mf))
    rng = random.Random(SEED)
    pay = [i for i in items if "payment" in i["tasks"]]
    sh = [i for i in items if "discount" in i["tasks"] and "payment" not in i["tasks"]]
    rng.shuffle(pay)
    rng.shuffle(sh)
    return pay[:N_PAY] + sh[:N_SH]


def client():
    return anthropic.Anthropic(api_key=get_settings().anthropic_api_key.get_secret_value(), max_retries=3, timeout=300.0)


def estimate(items):
    c = client()
    tin = 0
    for it in items:
        want = ", ".join(f"{ak.TASK_NAMES[t]}({t})" for t in it["tasks"])
        msg = f"판정할 항목: {want}\n\n<원문>\n{ak.document_text(it['path'])}\n</원문>"
        tin += c.messages.count_tokens(model=ak.MODEL, system=ak.SYSTEM_PROMPT,
                                       messages=[{"role": "user", "content": msg}]).input_tokens
    tout = 300 * len(items)
    cost = (tin * ak.PRICE_PER_MTOK["input"] + tout * ak.PRICE_PER_MTOK["output"]) / 1e6
    print(f"정답표 토큰 세기 ({len(items)}건, 무료): 입력 {tin:,} + 출력 약 {tout:,} → 약 ${cost:.2f}")


def run(items):
    c = client()
    OUT.mkdir(parents=True, exist_ok=True)
    old = []
    if KEY.exists():
        with KEY.open(encoding="utf-8-sig") as f:
            old = [r for r in csv.DictReader(f) if not r["error"]]
    done = {r["rcept_no"] for r in old}
    todo = [it for it in items if it["rcept_no"] not in done]
    print(f"정답표: 표본 {len(items)}, 이미 함 {len(done)}, 이번 {len(todo)} ({ak.MODEL}, {ak.PROMPT_VERSION})")
    f = KEY.open("w", newline="", encoding="utf-8-sig")
    w = csv.DictWriter(f, fieldnames=COLS)
    w.writeheader()
    w.writerows(old)
    lock = threading.Lock()

    def one(it):
        text = ak.document_text(it["path"])
        row = dict(rcept_no=it["rcept_no"], corp_name=it["corp_name"], first_date=it["first_date"],
                   tasks=",".join(it["tasks"]), error="")
        try:
            out, usage, _ = ak.label(c, text, it["tasks"])
        except Exception as e:
            row["error"] = f"{type(e).__name__}: {e}"[:300]
            return row
        row.update(input_tokens=usage.input_tokens, output_tokens=usage.output_tokens)
        for t in it["tasks"]:
            x = getattr(out, t)
            if t == "payment":
                row.update(payment_type=x.payment_type, payment_reason=x.reason)
            elif t == "discount":
                row.update(discount_rate=x.discount_rate, discount_reason=x.reason)
            else:
                row.update(right_sale_start=x.start, right_sale_end=x.end, right_sale_reason=x.reason)
            row.update({f"{t}_evidence": x.evidence, f"{t}_evidence_ok": ak.evidence_in_text(x.evidence, text)})
        return row

    with ThreadPoolExecutor(max_workers=6) as pool:
        for fut in as_completed([pool.submit(one, it) for it in todo]):
            row = fut.result()
            with lock:
                w.writerow(row)
                f.flush()
                if row["error"]:
                    print(f"  FAIL {row['rcept_no']} {row['corp_name']}: {row['error'][:120]}")
    f.close()


def _num(x):
    try:
        return round(float(x), 4)
    except (TypeError, ValueError):
        return None


def report():
    with KEY.open(encoding="utf-8-sig") as f:
        key = {r["rcept_no"]: r for r in csv.DictReader(f) if not r["error"]}
    with GOT.open(encoding="utf-8-sig") as f:
        got = {r["rcept_no"]: r for r in csv.DictReader(f) if not r["error"]}
    tin = sum(int(r["input_tokens"] or 0) for r in key.values())
    tout = sum(int(r["output_tokens"] or 0) for r in key.values())
    cost = (tin * ak.PRICE_PER_MTOK["input"] + tout * ak.PRICE_PER_MTOK["output"]) / 1e6
    res = {"payment": [0, 0], "discount": [0, 0], "right_sale": [0, 0]}
    bad, missing = [], []
    for no, k in key.items():
        r = got.get(no)
        if r is None:
            missing.append(no)
            continue
        for t in k["tasks"].split(","):
            if t not in r["tasks"].split(","):  # 실제 판정에서 이 과제를 다시 돌리지 않은 경우
                continue
            if t == "payment":
                kv, rv = k["payment_type"], r["payment_type"]
            elif t == "discount":
                kv, rv = _num(k["discount_rate"]), _num(r["discount_rate"])
            else:
                kv, rv = (k["right_sale_start"] or "", k["right_sale_end"] or ""), (r["right_sale_start"] or "", r["right_sale_end"] or "")
            res[t][1] += 1
            if kv == rv:
                res[t][0] += 1
            else:
                bad.append((t, k, r, kv, rv))
    key_ev_bad = [(no, t) for no, k in key.items() for t in k["tasks"].split(",") if k.get(f"{t}_evidence_ok") == "False"]
    names = {"payment": "납입 방식", "discount": "할인율", "right_sale": "권리 매도 기간"}
    L = [f"# 새 표본 재검증 (2023~2025) — 정답 {ak.PROMPT_VERSION}(원문 전체) vs 실제 판정 {PROMPT_VERSION}", "",
         f"- 정답표 {len(key)}건, 비용 ${cost:.2f}. 실제 판정 결과가 없는 표본 {len(missing)}건 {missing[:5]}",
         f"- 정답표 근거가 원문에 없음: {len(key_ev_bad)} {key_ev_bad[:5]}", "",
         "| 항목 | 일치 |", "|---|---|"]
    L += [f"| {names[t]} | {a}/{n} |" for t, (a, n) in res.items()]
    L += ["", "## 불일치 (정답표 쪽이 틀렸을 수도 있음 — 양쪽 근거)", ""]
    for t, k, r, kv, rv in bad:
        L += [f"- **{names[t]}** {k['corp_name']} {k['rcept_no']} ({k['first_date']}) — 정답표 **{kv}** / 실제 판정 **{rv}**"
              + (" (정황 판단)" if t == "payment" and r.get("payment_indirect") == "True" else ""),
              f"  - 정답표 근거: “{k[f'{t}_evidence']}” — {k[f'{t}_reason']}",
              f"  - 실제 판정 근거: “{r[f'{t}_evidence']}” — {r[f'{t}_reason']}"]
    if not bad:
        L.append("- 없음")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L[:12 + len(res)]))
    print(f"\n상세 → {OUT / 'report.md'}")


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--estimate", action="store_true")
    g.add_argument("--run", action="store_true")
    g.add_argument("--report", action="store_true")
    a = ap.parse_args()
    if a.report:
        return report()
    with connect() as conn:
        items = sample(conn)
    print(f"표본 {len(items)}건: 납입 방식 {sum('payment' in i['tasks'] for i in items)}, "
          f"주주배정 {sum('discount' in i['tasks'] for i in items)} (권리 매도 기간 {sum('right_sale' in i['tasks'] for i in items)})")
    if a.estimate:
        estimate(items)
    else:
        run(items)
        report()


if __name__ == "__main__":
    main()
