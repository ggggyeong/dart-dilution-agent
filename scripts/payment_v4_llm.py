"""⑨-2 납입 방식 2단계 — payment-v4 (prompts/payment_v4.md) 판정 · 반영 · 보고.

    uv run python scripts/payment_v4_llm.py --plan     # 대상 · 우선순위 · 신호별 건수만 (무료)
    uv run python scripts/payment_v4_llm.py --run      # 상한까지 판정 (이미 한 것은 건너뜀) → data/reports/llm_runs/payment-v4/results.jsonl
    uv run python scripts/payment_v4_llm.py --apply    # DB 반영 + 보고 (data/reports/llm_runs/payment-v4/report.md)
    uv run python scripts/payment_v4_llm.py --run --cap 0   # 상한 없이 남은 대상 전부 (⑨-2 뒤 사용자 결정: 남은 106건)

대상 = 1단계 신호가 걸린 공시 전체 (유상증자 · CB). 이번 상한 200건, 우선순위:
1) data/verify_240 240건 중 대상  2) tests/fixtures/manual_cases.csv 납입 방식 사례  3) 새로 걸린 건(예전 값이 규칙)  4) 예전 LLM 건.
실행 못 한 건은 예전 값 그대로 (payment_source의 버전으로 구분). 근거가 입력에 없으면 값 NULL + 기록.
"""

import argparse
import csv
import json
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import anthropic

from filingscope.config import DATA_DIR, PROJECT_ROOT, get_settings
from filingscope.db import connect
from filingscope.llm import payment_v4 as P
from filingscope.parse import cb as CB
from filingscope.parse import rights_issue as RI
from filingscope.parse.acode import read_document

RAW = DATA_DIR / "raw" / "document"
OUT = DATA_DIR / "reports" / "llm_runs" / P.PROMPT_VERSION
RESULTS = OUT / "results.jsonl"
CAP = 200
RETRIES = 3
SAMPLE = DATA_DIR / "verify_240" / "selected.csv"
CASES = PROJECT_ROOT / "tests" / "fixtures" / "manual_cases.csv"
CHECKS = {"20230706000056": "현물", "20230828000483": "현물", "20250530002705": "불명", "20251208000254": "섞임",
          "20241121000382": "빚→주식", "20240722000152": "빚→주식", "20240304000594": "빚→주식"}


def targets(conn) -> list[dict]:
    rows = conn.execute("""SELECT e.event_id, e.event_type, e.corp_name, d.main_file, b.report_nm, e.payment_type, e.payment_source
                           FROM events e JOIN documents d ON d.rcept_no = e.event_id JOIN b001_filings b ON b.rcept_no = e.event_id
                           ORDER BY 1""").fetchall()
    sample = {r["rcept_no"] for r in csv.DictReader(SAMPLE.open(encoding="utf-8"))} if SAMPLE.exists() else set()
    cases = {r["rcept_no"] for r in csv.DictReader(CASES.open(encoding="utf-8")) if r["column"] == "payment_type"}
    out = []
    for no, typ, name, mf, nm, ptype, psrc in rows:
        no = no.strip()
        doc = read_document(RAW / no / mf)
        x = RI.extract(doc, nm) if typ == "유상증자" else CB.extract(doc, nm)
        if x.payment_source != "pending:llm":
            continue
        signals = x.noncash_words if typ == "유상증자" else x.signals
        old_llm = (psrc or "").startswith(("llm:", "manual:"))
        prio = 1 if no in sample else 2 if no in cases else 4 if old_llm else 3
        out.append(dict(rcept_no=no, event_type=typ, corp_name=name, report_nm=nm, path=str(RAW / no / mf), signals=signals,
                        old_type=ptype, old_source=psrc, prio=prio))
    return sorted(out, key=lambda t: (t["prio"], t["rcept_no"]))


def plan(items, cap=CAP):
    sig = Counter()
    for t in items:
        for w in t["signals"]:
            sig[(t["event_type"], w.split("=")[0].split(":")[0])] += 1
    kind = Counter((t["event_type"], "기존 LLM" if (t["old_source"] or "").startswith(("llm:", "manual:")) else "새로 걸림") for t in items)
    prio = Counter(t["prio"] for t in items)
    return {"대상": len(items), "우선순위별": {f"{p}": prio[p] for p in sorted(prio)}, "이번 실행": min(cap, len(items)),
            "남는 건": max(0, len(items) - cap), "기존/새로": {f"{a}:{b}": c for (a, b), c in kind.items()},
            "신호별": {f"{a}:{b}": c for (a, b), c in sig.most_common()}}


def done() -> dict:
    if not RESULTS.exists():
        return {}
    return {r["rcept_no"]: r for r in map(json.loads, RESULTS.read_text(encoding="utf-8").splitlines()) if not r.get("error")}


def one(client, t) -> dict:
    text = P.build_input(read_document(t["path"]), t["event_type"], t["report_nm"])
    last = None
    for i in range(RETRIES + 1):
        try:
            r = P.judge(client, text)
            return {**{k: t[k] for k in ("rcept_no", "event_type", "corp_name", "signals", "old_type", "old_source", "prio")},
                    **r, "input_chars": len(text), "attempts": i + 1}
        except Exception as e:  # noqa: BLE001 — 호출 실패만 다시
            last = e
            time.sleep(3 * (i + 1))
    return {"rcept_no": t["rcept_no"], "error": f"{type(last).__name__}: {last}"[:300]}


def run(items, cap=CAP):
    have = done()
    todo = [t for t in items[:cap] if t["rcept_no"] not in have]
    print(f"이번 상한 {cap} 중 이미 {min(cap, len(items)) - len(todo)}, 남은 {len(todo)}")
    OUT.mkdir(parents=True, exist_ok=True)
    client = anthropic.Anthropic(api_key=get_settings().anthropic_api_key.get_secret_value(), max_retries=0, timeout=120)
    lock, n = threading.Lock(), 0
    with ThreadPoolExecutor(6) as ex, RESULTS.open("a", encoding="utf-8") as f:
        for fut in as_completed([ex.submit(one, client, t) for t in todo]):
            r = fut.result()
            with lock:
                f.write(json.dumps(r, ensure_ascii=False) + "\n"); f.flush()
                n += 1
                if r.get("error"):
                    print(f"  실패 {r['rcept_no']}: {r['error'][:120]}")
                if n % 20 == 0:
                    print(f"  {n}/{len(todo)}", flush=True)


def apply(conn, items):
    got = done()
    with conn.transaction():
        for no, r in got.items():
            conn.execute("""UPDATE events SET payment_type = %s, payment_evidence = %s, payment_indirect = NULL, payment_source = %s
                            WHERE event_id = %s""", (r["payment_type"], r["payment_evidence"] if r["payment_type"] else None, P.SOURCE, no))
    rows = list(got.values())
    cost = sum(r["input_tokens"] * P.PRICE[0] + r["output_tokens"] * P.PRICE[1] for r in rows) / 1e6
    diff = [r for r in rows if (r["payment_type"] or None) != (r["old_type"] or None)]
    cases = {r["rcept_no"]: r for r in csv.DictReader(CASES.open(encoding="utf-8"))}
    L = [f"# 납입 방식 {P.PROMPT_VERSION} ({P.MODEL}, effort {P.EFFORT} — temperature는 이 SDK · 모델에 없음)", "",
         f"- 판정 {len(rows)}건 · 입력 {sum(r['input_tokens'] for r in rows):,} · 출력 {sum(r['output_tokens'] for r in rows):,} 토큰 · **${cost:.2f}**",
         f"- 결과: {dict(Counter(r['payment_type'] for r in rows).most_common())} (모델 원래 값: {dict(Counter(r['raw_type'] for r in rows).most_common())})",
         f"- 근거가 입력에 없어 버림: {sum(not r['evidence_ok'] for r in rows)} {[r['rcept_no'] for r in rows if not r['evidence_ok']]}",
         f"- asset/debt 없어 불명으로: {[r['rcept_no'] for r in rows if r['note'].startswith(('현물', '빚', '섞임'))]}",
         f"- 남은 대상(예전 값 유지): {max(0, len(items) - len(rows))}", "", "## 확인 표", "", "| rcept_no | 기대 | v4 | O/X |", "|---|---|---|---|"]
    for no, want in CHECKS.items():
        r = got.get(no)
        L.append(f"| {no} | {want} | {r['payment_type'] if r else '(대상 아님/미실행)'} {('— ' + r['payment_evidence'][:60]) if r else ''} | {'O' if r and r['payment_type'] == want else 'X'} |")
    L += ["", "## manual 사례 (tests/fixtures/manual_cases.csv) — 파이프라인만", "", "| rcept_no | 회사 | 칸 | 기대 | 파이프라인 | O/X |", "|---|---|---|---|---|---|"]
    for no, c in cases.items():
        if c["column"] == "payment_type":
            val = got[no]["payment_type"] if no in got else conn.execute("SELECT payment_type FROM events WHERE event_id = %s", (no,)).fetchone()[0]
        elif c["column"] == "old_cb_shares":
            val = conn.execute("SELECT old_cb_shares FROM cb_values WHERE rcept_no = %s", (no,)).fetchone()[0]
        else:
            val = "False (사람 판정 없앰)"
        L.append(f"| {no} | {c['corp_name']} | {c['column']} | {c['expected']} | {val} | {'O' if str(val) == c['expected'] else 'X'} |")
    L += ["", f"## 예전 값과 달라진 것 ({len(diff)}건)", "", "| rcept_no | 회사 | 종류 | 전 (출처) | 후 | reason |", "|---|---|---|---|---|---|"]
    L += [f"| {r['rcept_no']} | {r['corp_name']} | {r['event_type']} | {r['old_type']} ({(r['old_source'] or '').split(':')[0]}) | {r['payment_type']} | {r['reason']} |"
          for r in sorted(diff, key=lambda r: r["rcept_no"])]
    (OUT / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L[:40]))


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    for a in ("plan", "run", "apply"):
        g.add_argument(f"--{a}", action="store_true")
    ap.add_argument("--cap", type=int, default=CAP, help="판정 상한 (0 = 대상 전부)")
    a = ap.parse_args()
    with connect() as conn:
        items = targets(conn)
        cap = a.cap or len(items)
        print(json.dumps(plan(items, cap), ensure_ascii=False))
        if a.run:
            run(items, cap)
        elif a.apply:
            apply(conn, items)


if __name__ == "__main__":
    main()
