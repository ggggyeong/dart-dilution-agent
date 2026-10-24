"""실제 쓸 모델 비교: Haiku · Sonnet · Opus(잘라 넣음, 대조군)를 Opus 정답표(원문 전체)와 비교.
extract-v1에서 Opus로 결정. extract-v2부터는 Opus만 돌려 확인한다 (RUN_MODELS).
근거 확인은 모델에 넣은 글 기준 (원문에 없는 [이름표]를 인용해도 내용이 맞으면 통과하도록).

    uv run python scripts/llm_compare.py --estimate     # 토큰 세기로 비용만 (무료)
    uv run python scripts/llm_compare.py --run          # 실행 (이미 한 것은 건너뜀) + 보고
    uv run python scripts/llm_compare.py --report       # 보고만

결과: data/reports/llm_compare/<프롬프트 버전>/<모델>.csv, report.md
"""

import argparse
import csv
import glob
import random
import re
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import anthropic

from filingscope.config import DATA_DIR, get_settings
from filingscope.llm.extractor import MODELS, PROMPT_VERSION, judge, request_kwargs
from filingscope.parse.sections import sections_for

KEY = DATA_DIR / "reports" / "opus_labels.csv"
OUT = DATA_DIR / "reports" / "llm_compare" / PROMPT_VERSION
RUN_MODELS = ["claude-opus-5-5"]
COLS = ["rcept_no", "corp_name", "tasks", "why", "fallback",
        "payment_type", "payment_indirect", "payment_evidence", "payment_evidence_ok", "payment_reason",
        "discount_rate", "discount_evidence", "discount_evidence_ok", "discount_reason",
        "right_sale_start", "right_sale_end", "right_sale_evidence", "right_sale_evidence_ok", "right_sale_reason",
        "input_tokens", "output_tokens", "error"]
OUT_TOKENS_GUESS = 400


def key_rows() -> list[dict]:
    with KEY.open(encoding="utf-8-sig") as f:
        return [r for r in csv.DictReader(f) if not r["error"]]


def path_of(no: str):
    return glob.glob(str(DATA_DIR / "raw" / "document" / no / "*.xml"))[0]


def client():
    return anthropic.Anthropic(api_key=get_settings().anthropic_api_key.get_secret_value(), max_retries=3, timeout=180.0)  # 응답 없이 걸리는 요청 방지


def estimate(rows):
    c = client()
    rng = random.Random(42)
    sample = rng.sample(rows, 30)
    total = 0.0
    print(f"토큰 세기: 표본 {len(sample)}건 × 모델 {len(RUN_MODELS)}개 (무료)")
    for model in RUN_MODELS:
        pin, pout, _ = MODELS[model]
        toks = []
        for r in sample:
            secs = sections_for(path_of(r["rcept_no"]), r["tasks"].split(","))
            kw = request_kwargs(model, secs)
            n = c.messages.count_tokens(model=model, system=kw["system"], messages=kw["messages"]).input_tokens
            toks.append(n)
        avg = sum(toks) / len(toks)
        tin = avg * len(rows)
        cost = (tin * pin + OUT_TOKENS_GUESS * len(rows) * pout) / 1e6
        total += cost
        print(f"  {model}: 호출당 입력 평균 {avg:,.0f} 토큰 → 입력 {tin:,.0f} + 출력 약 {OUT_TOKENS_GUESS * len(rows):,} → 약 ${cost:.2f}")
    print(f"합계 약 ${total:.2f}")
    return total


def run(rows):
    c = client()
    OUT.mkdir(parents=True, exist_ok=True)
    for model in RUN_MODELS:
        path = OUT / f"{model}.csv"
        old = []
        if path.exists():
            with path.open(encoding="utf-8-sig") as f:
                old = [r for r in csv.DictReader(f) if not r["error"]]
        done = {r["rcept_no"] for r in old}
        todo = [r for r in rows if r["rcept_no"] not in done]
        print(f"[{model}] 대상 {len(rows)}, 이미 함 {len(done)}, 이번 {len(todo)}")
        f = path.open("w", newline="", encoding="utf-8-sig")
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(old)
        lock = threading.Lock()

        def one(r, model=model):
            tasks = r["tasks"].split(",")
            row = dict(rcept_no=r["rcept_no"], corp_name=r["corp_name"], tasks=r["tasks"], why=r["why"], fallback="")
            row.update(judge(c, model, path_of(r["rcept_no"]), tasks))  # 근거는 모델에 넣은 글 기준
            return row

        with ThreadPoolExecutor(max_workers=6) as pool:
            for fut in as_completed([pool.submit(one, r) for r in todo]):
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


def _snippet(text: str, ev: str | None, width: int = 0) -> str:
    return (ev or "")[:200]


def report(rows):
    key = {r["rcept_no"]: r for r in rows}
    L = [f"# 실제 쓸 모델 비교 ({PROMPT_VERSION}) — 정답: Opus 원문 전체 (answer-key-v1)", ""]
    L += ["| 모델 | 성공 | 납입 방식 일치 | 할인율 일치 | 권리 매도 기간 일치 | 근거가 입력 글에 없음 | 정황 판단 | 실제 비용 |",
          "|---|---|---|---|---|---|---|---|"]
    detail = []
    for model in RUN_MODELS:
        pin, pout, _ = MODELS[model]
        path = OUT / f"{model}.csv"
        if not path.exists():
            continue
        with path.open(encoding="utf-8-sig") as f:
            got = [r for r in csv.DictReader(f)]
        ok = [r for r in got if not r["error"]]
        cost = sum(int(r["input_tokens"] or 0) * pin + int(r["output_tokens"] or 0) * pout for r in ok) / 1e6
        m_pay = [(key[r["rcept_no"]], r) for r in ok if "payment" in r["tasks"]]
        m_dis = [(key[r["rcept_no"]], r) for r in ok if "discount" in r["tasks"]]
        m_rs = [(key[r["rcept_no"]], r) for r in ok if "right_sale" in r["tasks"]]
        bad_pay = [(k, r) for k, r in m_pay if k["payment_type"] != r["payment_type"]]
        bad_dis = [(k, r) for k, r in m_dis if _num(k["discount_rate"]) != _num(r["discount_rate"])]
        bad_rs = [(k, r) for k, r in m_rs if (k["right_sale_start"] or "", k["right_sale_end"] or "") != (r["right_sale_start"] or "", r["right_sale_end"] or "")]
        ev_bad = sum(1 for r in ok for t in ("payment", "discount", "right_sale") if r.get(f"{t}_evidence_ok") == "False")
        indirect = sum(1 for r in ok if r["payment_indirect"] == "True")
        L.append(f"| {model} | {len(ok)}/{len(got)} | {len(m_pay) - len(bad_pay)}/{len(m_pay)} | {len(m_dis) - len(bad_dis)}/{len(m_dis)} | "
                 f"{len(m_rs) - len(bad_rs)}/{len(m_rs)} | {ev_bad} | {indirect} | ${cost:.2f} |")
        detail += ["", f"## {model} 불일치", ""]
        for name, bad, f_key, f_got in (("납입 방식", bad_pay, "payment_type", "payment_type"),
                                        ("할인율", bad_dis, "discount_rate", "discount_rate"),
                                        ("권리 매도 기간", bad_rs, "right_sale_start", "right_sale_start")):
            for k, r in bad:
                pre = name.split()[0]
                t = {"납입 방식": "payment", "할인율": "discount", "권리 매도 기간": "right_sale"}[name]
                kv = f"{k['right_sale_start']}~{k['right_sale_end']}" if t == "right_sale" else k[f_key]
                rv = f"{r['right_sale_start']}~{r['right_sale_end']}" if t == "right_sale" else r[f_got]
                detail += [f"- **{name}** {k['corp_name']} {k['rcept_no']} — 정답표(Opus 원문 전체) **{kv}** / {model} **{rv}**"
                           + (f" (정황 판단)" if t == "payment" and r["payment_indirect"] == "True" else "")
                           + (f" (입력 fallback: {r['fallback']})" if r["fallback"] else ""),
                           f"  - 정답표 근거: “{k[f'{t}_evidence']}” — {k[f'{t}_reason']}",
                           f"  - {model} 근거: “{r[f'{t}_evidence']}” — {r[f'{t}_reason']}"]
        if not any(b for b in (bad_pay, bad_dis, bad_rs)):
            detail.append("- 없음")
        ind = [r for r in ok if r["payment_indirect"] == "True"]
        if ind:
            detail += ["", f"### {model} 정황 판단으로 표시한 납입 방식 {len(ind)}건", ""]
            detail += [f"- {r['corp_name']} {r['rcept_no']} {r['payment_type']}: {r['payment_reason']}" for r in ind]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.md").write_text("\n".join(L + detail) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(f"\n상세 → {OUT / 'report.md'}")


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--estimate", action="store_true")
    g.add_argument("--run", action="store_true")
    g.add_argument("--report", action="store_true")
    a = ap.parse_args()
    rows = key_rows()
    if a.estimate:
        estimate(rows)
    elif a.run:
        run(rows)
        report(rows)
    else:
        report(rows)


if __name__ == "__main__":
    main()
