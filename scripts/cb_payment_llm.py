"""CB 납입 방식 판정 (13번 납입방법 "기타"): 정답표 20건 → 대조 → 전체 → DB 반영.

    uv run python scripts/cb_payment_llm.py --estimate   # 토큰 세기 (무료)
    uv run python scripts/cb_payment_llm.py --key        # Opus 원문 전체 정답표 20건 (시드 42)
    uv run python scripts/cb_payment_llm.py --run        # 실제 판정 전체 (이미 한 것은 건너뜀)
    uv run python scripts/cb_payment_llm.py --report     # 값별 건수 · 정답표 대조 · 근거 없음
    uv run python scripts/cb_payment_llm.py --apply      # DB 반영 (build_cb_tables.py도 마지막에 부름)

사람 판정 덮어쓰기는 없앴다 (⑨-2 4-1). payment-v4(scripts/payment_v4_llm.py)로 판정한 공시는 건드리지 않는다.
판정 방식은 llm/cb_payment.py. payment_source = 'llm:<프롬프트 버전>' (cb-v1 → cb-v2). 근거가 입력 글에 없으면 값을 쓰지 않는다.
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
from filingscope.llm import answer_key as ak
from filingscope.llm import cb_payment as cp
from filingscope.llm.extractor import MODELS, request_kwargs
from filingscope.parse.sections import Section

RAW = DATA_DIR / "raw" / "document"
KEY = DATA_DIR / "reports" / "cb_payment" / f"opus_labels_{cp.PROMPT_VERSION}.csv"
GOT = DATA_DIR / "reports" / "llm_runs" / cp.PROMPT_VERSION / f"{cp.MODEL}.csv"
REPORT = DATA_DIR / "reports" / "cb_payment" / f"report_{cp.PROMPT_VERSION}.md"
N_KEY, SEED = 20, 42
COLS = ["rcept_no", "corp_name", "input_source", "payment_type", "payment_indirect", "payment_evidence", "payment_evidence_ok",
        "payment_reason", "input_tokens", "output_tokens", "error"]


def targets(conn) -> list[dict]:
    """13번 납입방법이 '현금'이 아닌 CB (규칙으로 정하지 못한 것)."""
    rows = conn.execute(
        """SELECT e.event_id, e.corp_name, d.main_file FROM events e JOIN documents d ON d.rcept_no = e.event_id
           WHERE e.event_type = 'CB' AND (e.payment_source IS NULL OR e.payment_source LIKE 'llm:cb%%') ORDER BY 1""").fetchall()
    return [dict(rcept_no=no, corp_name=name, path=RAW / no / mf) for no, name, mf in rows]


def client():
    return anthropic.Anthropic(api_key=get_settings().anthropic_api_key.get_secret_value(), max_retries=3, timeout=300.0)


def read(path):
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig") as f:
        return {r["rcept_no"]: r for r in csv.DictReader(f) if not r["error"]}


def run_many(items, fn, path):
    old = read(path)
    todo = [it for it in items if it["rcept_no"] not in old]
    print(f"{path.name}: 대상 {len(items)}, 이미 함 {len(old)}, 이번 {len(todo)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    f = path.open("w", newline="", encoding="utf-8-sig")
    w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore")
    w.writeheader()
    w.writerows(old.values())
    c, lock = client(), threading.Lock()
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = {pool.submit(fn, c, it["path"]): it for it in todo}
        for fut in as_completed(futs):
            it = futs[fut]
            row = dict(rcept_no=it["rcept_no"], corp_name=it["corp_name"], **fut.result())
            with lock:
                w.writerow(row)
                f.flush()
                if row["error"]:
                    print(f"  FAIL {row['rcept_no']} {row['corp_name']}: {row['error'][:120]}")
    f.close()


def key_sample(items):
    rng = random.Random(SEED)
    return rng.sample(items, min(N_KEY, len(items)))


def estimate(items):
    c = client()
    pin, pout, _ = MODELS[cp.MODEL]
    tin = sum(c.messages.count_tokens(model=cp.MODEL, **{k: v for k, v in request_kwargs(
        cp.MODEL, {"payment": Section(cp.payment_input(it["path"])[0], False)}, cp.SYSTEM_PROMPT).items() if k in ("system", "messages")}).input_tokens
        for it in items)
    run = (tin * pin + 400 * len(items) * pout) / 1e6
    ks = key_sample(items)
    kin = sum(c.messages.count_tokens(model=ak.MODEL, system=cp.KEY_SYSTEM_PROMPT, messages=[{"role": "user", "content":
              f"판정할 항목: 납입 방식(payment)\n\n<원문>\n{ak.document_text(it['path'])}\n</원문>"}]).input_tokens for it in ks)
    key = (kin * ak.PRICE_PER_MTOK["input"] + 400 * len(ks) * ak.PRICE_PER_MTOK["output"]) / 1e6
    print(f"실제 판정 {len(items)}건: 입력 {tin:,} → 약 ${run:.2f} | 정답표 {len(ks)}건: 입력 {kin:,} → 약 ${key:.2f} | 합계 약 ${run + key:.2f}")


def report(items):
    got, key = read(GOT), read(KEY)
    names = {it["rcept_no"]: it["corp_name"] for it in items}
    pin, pout, _ = MODELS[cp.MODEL]
    cost = sum(int(r["input_tokens"] or 0) * pin + int(r["output_tokens"] or 0) * pout for r in got.values()) / 1e6
    kcost = sum(int(r["input_tokens"] or 0) * ak.PRICE_PER_MTOK["input"] + int(r["output_tokens"] or 0) * ak.PRICE_PER_MTOK["output"] for r in key.values()) / 1e6
    L = [f"# CB 납입 방식 판정 ({cp.SOURCE}) — 대상 {len(items)}건", "",
         f"- 실제 판정 {len(got)}건 (${cost:.2f}), 입력 출처 {dict(Counter(r['input_source'] for r in got.values()))}",
         f"- 값별: {dict(Counter(r['payment_type'] for r in got.values()).most_common())}",
         f"- 정황 판단: {sum(r['payment_indirect'] == 'True' for r in got.values())}",
         f"- 근거가 입력 글에 없음: {sum(r['payment_evidence_ok'] == 'False' for r in got.values())} "
         + str([(n, names.get(n)) for n, r in got.items() if r['payment_evidence_ok'] == 'False']),
         f"- 정답표 {len(key)}건 (${kcost:.2f}), 정답표 근거가 원문에 없음 {sum(r['payment_evidence_ok'] == 'False' for r in key.values())}"]
    pairs = [(k, got.get(n)) for n, k in key.items()]
    same = [p for p in pairs if p[1] and p[0]["payment_type"] == p[1]["payment_type"]]
    L += [f"- **정답표 대조: {len(same)}/{len(key)} 일치**", "", "## 불일치 (정답표 쪽이 틀렸을 수도 있음)", ""]
    for k, g in pairs:
        if g and k["payment_type"] != g["payment_type"]:
            L += [f"- {k['corp_name']} {k['rcept_no']} — 정답표 **{k['payment_type']}** / 실제 판정 **{g['payment_type']}** (입력 {g['input_source']})",
                  f"  - 정답표 근거: “{k['payment_evidence']}” — {k['payment_reason']}",
                  f"  - 실제 판정 근거: “{g['payment_evidence']}” — {g['payment_reason']}"]
    if len(same) == len(key):
        L.append("- 없음")
    L += ["", "## 전체 판정", ""] + [f"- {names.get(n, '')} {n} {r['payment_type']}{' (정황)' if r['payment_indirect'] == 'True' else ''} [{r['input_source']}]: {(r['payment_evidence'] or '')[:90]}"
                                     for n, r in sorted(got.items())]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L[:20]))
    print(f"\n상세 → {REPORT}")


def apply(conn) -> int:
    """실제 판정 결과(cb-v2)를 CB events에 넣는다. 근거가 입력 글에 없으면 쓰지 않는다. payment-v4 공시는 건너뜀."""
    got, n = read(GOT), 0
    with conn.transaction():
        for no, r in got.items():
            if r["payment_evidence_ok"] == "False":
                continue
            n += conn.execute("""UPDATE events SET payment_type = %s, payment_evidence = %s, payment_indirect = %s, payment_source = %s
                                 WHERE event_id = %s AND event_type = 'CB' AND (payment_source IS NULL OR payment_source LIKE 'llm:cb%%')""",
                              (r["payment_type"], r["payment_evidence"] or None, r["payment_indirect"] == "True", cp.SOURCE, no)).rowcount
    return n


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    for a in ("estimate", "key", "run", "report", "apply"):
        g.add_argument(f"--{a}", action="store_true")
    a = ap.parse_args()
    with connect() as conn:
        items = targets(conn)
        print(f"대상 {len(items)}건")
        if a.estimate:
            estimate(items)
        elif a.key:
            run_many(key_sample(items), cp.label, KEY)
        elif a.run:
            run_many(items, cp.judge, GOT)
        elif a.report:
            report(items)
        else:
            print(f"DB 반영 {apply(conn)}건 ({cp.SOURCE})")


if __name__ == "__main__":
    main()
