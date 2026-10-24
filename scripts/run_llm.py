"""실제 판정: DB에서 LLM이 할 일이 남은 사건을 찾아 Opus(extract-v2)로 판정한다. DB 반영은 apply_llm_results.py.

    uv run python scripts/run_llm.py --estimate   # 토큰 세기로 비용만 (무료)
    uv run python scripts/run_llm.py --run        # 실행 (이미 판정한 공시는 건너뜀)
    uv run python scripts/run_llm.py --run --tasks payment --ids 2024...,2025...   # 과제 · 공시 골라서

대상 (명세 6장): 납입 방식 LLM 대기 + 주주배정 보통주의 할인율 · 권리 매도 기간(시장).
이미 판정한 것 = 같은 프롬프트 버전의 결과 파일(llm_compare/<버전>/, llm_runs/<버전>/)에 있는 공시.
결과: data/reports/llm_runs/<프롬프트 버전>/<모델>.csv (이어 씀)
"""

import argparse
import csv
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import anthropic

from filingscope.config import DATA_DIR, get_settings
from filingscope.db import connect
from filingscope.llm.extractor import MODELS, PROMPT_VERSION, RESULT_COLS, judge, request_kwargs, tasks_for
from filingscope.parse.sections import sections_for

MODEL = "claude-opus-5-5"
OUT = DATA_DIR / "reports" / "llm_runs" / PROMPT_VERSION / f"{MODEL}.csv"
DONE_FILES = [DATA_DIR / "reports" / "llm_compare" / PROMPT_VERSION / f"{MODEL}.csv", OUT]
RAW = DATA_DIR / "raw" / "document"
COLS = ["rcept_no", "corp_name", "first_date", "tasks", *RESULT_COLS]
OUT_TOKENS_GUESS = 400


def done_ids() -> set[str]:
    ids = set()
    for p in DONE_FILES:
        if p.exists():
            with p.open(encoding="utf-8-sig") as f:
                ids |= {r["rcept_no"] for r in csv.DictReader(f) if not r["error"]}
    return ids


def targets(conn, only_tasks: set[str] | None = None, only_ids: set[str] | None = None) -> list[dict]:
    rows = conn.execute(
        """SELECT e.event_id, e.corp_name, e.first_date, e.ci_mth, v.stock_type, v.right_sale, e.payment_source, d.main_file
           FROM events e JOIN rights_values v ON v.rcept_no = e.event_id JOIN documents d ON d.rcept_no = e.event_id
           ORDER BY e.event_id""").fetchall()
    done = done_ids()
    out = []
    for no, name, fd, ci, st, rs, src, mf in rows:
        if only_ids is not None and (src or "").startswith("manual:"):
            src = "llm:"  # 확인용으로 공시를 직접 고르면 사람 판정 공시도 판정한다 (DB 반영 때는 사람 판정이 우선)
        t = [x for x in tasks_for(ci, st, rs, src) if not only_tasks or x in only_tasks]
        if only_ids is not None and no not in only_ids:
            continue
        if t and no not in done:
            out.append(dict(rcept_no=no, corp_name=name, first_date=fd, tasks=t, path=RAW / no / mf))
    return out


def client():
    return anthropic.Anthropic(api_key=get_settings().anthropic_api_key.get_secret_value(), max_retries=3, timeout=180.0)


def estimate(items):
    c = client()
    pin, pout, _ = MODELS[MODEL]
    toks = [c.messages.count_tokens(model=MODEL, system=kw["system"], messages=kw["messages"]).input_tokens
            for kw in (request_kwargs(MODEL, sections_for(it["path"], it["tasks"])) for it in items)]
    tin = sum(toks)
    cost = (tin * pin + OUT_TOKENS_GUESS * len(items) * pout) / 1e6
    print(f"토큰 세기 (전부 {len(items)}건, 무료): 입력 {tin:,} (호출당 {tin / max(len(items), 1):,.0f}) + 출력 약 {OUT_TOKENS_GUESS * len(items):,} → 약 ${cost:.2f}")
    return cost


def run(items):
    c = client()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    old = []
    if OUT.exists():
        with OUT.open(encoding="utf-8-sig") as f:
            old = [r for r in csv.DictReader(f) if not r["error"]]
    f = OUT.open("w", newline="", encoding="utf-8-sig")
    w = csv.DictWriter(f, fieldnames=COLS)
    w.writeheader()
    w.writerows(old)
    lock, n = threading.Lock(), [0]

    def one(it):
        row = dict(rcept_no=it["rcept_no"], corp_name=it["corp_name"], first_date=it["first_date"], tasks=",".join(it["tasks"]))
        row.update(judge(c, MODEL, it["path"], it["tasks"]))
        return row

    with ThreadPoolExecutor(max_workers=6) as pool:
        for fut in as_completed([pool.submit(one, it) for it in items]):
            row = fut.result()
            with lock:
                w.writerow(row)
                f.flush()
                n[0] += 1
                if row["error"]:
                    print(f"  FAIL {row['rcept_no']} {row['corp_name']}: {row['error'][:120]}")
                if n[0] % 25 == 0:
                    print(f"  ... {n[0]}/{len(items)}")
    f.close()
    with OUT.open(encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    ok = [r for r in rows if not r["error"]]
    pin, pout, _ = MODELS[MODEL]
    cost = sum(int(r["input_tokens"] or 0) * pin + int(r["output_tokens"] or 0) * pout for r in ok) / 1e6
    bad_ev = sum(1 for r in ok for t in ("payment", "discount", "right_sale") if r.get(f"{t}_evidence_ok") == "False")
    print(f"결과 파일 {len(rows)}줄 (실패 {len(rows) - len(ok)}), 근거가 입력에 없음 {bad_ev}, 누적 비용 ${cost:.2f} → {OUT}")


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--estimate", action="store_true")
    g.add_argument("--run", action="store_true")
    ap.add_argument("--tasks", help="쉼표로: payment,discount,right_sale (기본: 전부)")
    ap.add_argument("--ids", help="쉼표로 접수번호 (기본: 남은 대상 전부)")
    a = ap.parse_args()
    with connect() as conn:
        items = targets(conn, set(a.tasks.split(",")) if a.tasks else None, set(a.ids.split(",")) if a.ids else None)
    by = {}
    for it in items:
        by[",".join(it["tasks"])] = by.get(",".join(it["tasks"]), 0) + 1
    print(f"남은 대상 {len(items)}건: {by}")
    if not items:
        return
    if a.estimate:
        estimate(items)
    else:
        run(items)


if __name__ == "__main__":
    main()
