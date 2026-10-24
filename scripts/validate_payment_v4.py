"""유상증자 납입 방식 v4 새 표본 검증 (DB는 바꾸지 않음).

    uv run python scripts/validate_payment_v4.py --estimate
    uv run python scripts/validate_payment_v4.py --run      # 정답표 + v4 판정 (이미 한 것은 건너뜀) + 보고

표본: 2023~2025 유상증자 중 납입 방식 LLM 대상(llm · manual), 이전 정답표(fresh_check)에 쓴 공시 제외, 30건
      (대상자 2명 이상 15건 이상, 시드 42).
정답표: Opus 원문 전체 high, 정답표 지시문에 v4와 같은 규칙 줄 추가 (answer-key-v2).
판정: llm/extractor.py 현재 버전(extract-v4), 납입 방식 입력 = 원문 전체.
결과: data/reports/v4_check/ (정답표 · 판정 csv, report.md). llm_runs에 쓰지 않으므로 DB 반영 대상이 아님.
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
from filingscope.llm import extractor as ex
from filingscope.parse.sections import sections_for

OUT = DATA_DIR / "reports" / "v4_check"
KEY, GOT = OUT / "opus_labels.csv", OUT / f"{ex.PROMPT_VERSION}.csv"
USED = DATA_DIR / "reports" / "fresh_check" / "opus_labels.csv"
RAW = DATA_DIR / "raw" / "document"
N, N_MULTI, SEED = 30, 15, 42
RULE = ex.SYSTEM_PROMPT.split("- 불명: 판단할 수 없음\n")[1].split("\n")[0]
KEY_PROMPT = ak.SYSTEM_PROMPT.replace("- 불명: 원문으로 판단할 수 없음\n", f"- 불명: 원문으로 판단할 수 없음\n{RULE}\n")
assert RULE.startswith("상계·대용납입이라도") and RULE in KEY_PROMPT
KEY_VERSION = "answer-key-v2"
COLS = ["rcept_no", "corp_name", "first_date", "n_allottees", "payment_type", "payment_indirect", "payment_evidence",
        "payment_evidence_ok", "payment_reason", "input_tokens", "output_tokens", "error"]


def sample(conn) -> list[dict]:
    with USED.open(encoding="utf-8-sig") as f:
        used = {r["rcept_no"] for r in csv.DictReader(f)}
    rows = conn.execute(
        """SELECT e.event_id, e.corp_name, e.first_date, d.main_file, (SELECT count(*) FROM allottees a WHERE a.rcept_no = e.event_id)
           FROM events e JOIN documents d ON d.rcept_no = e.event_id
           WHERE e.event_type = '유상증자' AND e.first_date < '2026-01-01'
             AND (e.payment_source LIKE 'llm:%%' OR e.payment_source LIKE 'manual:%%') ORDER BY 1""").fetchall()
    pool = [dict(rcept_no=no, corp_name=n, first_date=fd, path=RAW / no / mf, n_allottees=k) for no, n, fd, mf, k in rows if no not in used]
    rng = random.Random(SEED)
    multi = [p for p in pool if p["n_allottees"] >= 2]
    rest = [p for p in pool if p["n_allottees"] < 2]
    rng.shuffle(multi)
    rng.shuffle(rest)
    pick = multi[:N_MULTI]
    pick += (rest + multi[N_MULTI:])[: N - len(pick)]
    print(f"후보 {len(pool)}건 (대상자 2명 이상 {len(multi)}) → 표본 {len(pick)}건 (2명 이상 {sum(p['n_allottees'] >= 2 for p in pick)})")
    return pick


def client():
    return anthropic.Anthropic(api_key=get_settings().anthropic_api_key.get_secret_value(), max_retries=3, timeout=300.0)


def key_one(c, it):
    text = ak.document_text(it["path"])
    out, usage, _ = ak.label(c, text, ["payment"], KEY_PROMPT)
    x = out.payment
    return dict(payment_type=x.payment_type, payment_evidence=x.evidence, payment_reason=x.reason,
                payment_evidence_ok=ak.evidence_in_text(x.evidence, text), input_tokens=usage.input_tokens, output_tokens=usage.output_tokens)


def judge_one(c, it):
    r = ex.judge(c, "claude-opus-5-5", it["path"], ["payment"])
    if r.get("error"):
        raise RuntimeError(r["error"])
    return {k: r.get(k) for k in COLS if k in r}


def read(path):
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig") as f:
        return {r["rcept_no"]: r for r in csv.DictReader(f) if not r["error"]}


def run_many(items, fn, path):
    old = read(path)
    todo = [it for it in items if it["rcept_no"] not in old]
    OUT.mkdir(parents=True, exist_ok=True)
    f = path.open("w", newline="", encoding="utf-8-sig")
    w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore")
    w.writeheader()
    w.writerows(old.values())
    c, lock = client(), threading.Lock()
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = {pool.submit(fn, c, it): it for it in todo}
        for fut in as_completed(futs):
            it = futs[fut]
            row = {k: it[k] for k in ("rcept_no", "corp_name", "first_date", "n_allottees")}
            try:
                row.update(fut.result(), error="")
            except Exception as e:
                row["error"] = f"{type(e).__name__}: {e}"[:300]
                print(f"  FAIL {it['rcept_no']} {it['corp_name']}: {row['error'][:120]}")
            with lock:
                w.writerow(row)
                f.flush()
    f.close()
    print(f"{path.name}: 이번 {len(todo)}건")


def estimate(items):
    c = client()
    kin = sum(c.messages.count_tokens(model=ak.MODEL, system=KEY_PROMPT, messages=[{"role": "user", "content":
              f"판정할 항목: 납입 방식(payment)\n\n<원문>\n{ak.document_text(it['path'])}\n</원문>"}]).input_tokens for it in items)
    jin = sum(c.messages.count_tokens(model="claude-opus-5-5", **{k: v for k, v in ex.request_kwargs(
              "claude-opus-5-5", sections_for(it["path"], ["payment"])).items() if k in ("system", "messages")}).input_tokens for it in items)
    print(f"정답표 입력 {kin:,} → 약 ${(kin * 4 + 400 * len(items) * 20) / 1e6:.2f} | v4 판정 입력 {jin:,} → 약 ${(jin * 4 + 400 * len(items) * 20) / 1e6:.2f}")


def report(items):
    key, got = read(KEY), read(GOT)
    db = {}
    with connect() as conn:
        for no, pt, src in conn.execute("SELECT event_id, payment_type, payment_source FROM events WHERE event_id = ANY(%s)", ([i["rcept_no"] for i in items],)):
            db[no] = (pt, src)
    cost = sum(int(r["input_tokens"] or 0) * 4 + int(r["output_tokens"] or 0) * 20 for r in list(key.values()) + list(got.values())) / 1e6
    pairs = [(it, key.get(it["rcept_no"]), got.get(it["rcept_no"])) for it in items]
    same = [p for p in pairs if p[1] and p[2] and p[1]["payment_type"] == p[2]["payment_type"]]
    L = [f"# 유상증자 납입 방식 {ex.PROMPT_VERSION} 새 표본 검증 — 정답 {KEY_VERSION} (Opus 원문 전체, 같은 규칙 줄)", "",
         f"- 표본 {len(items)}건 (대상자 2명 이상 {sum(i['n_allottees'] >= 2 for i in items)}), 비용 ${cost:.2f}",
         f"- **일치 {len(same)}/{len(items)}**",
         f"- 근거가 입력에 없음: v4 판정 {sum(r['payment_evidence_ok'] == 'False' for r in got.values())}, 정답표 {sum(r['payment_evidence_ok'] == 'False' for r in key.values())}",
         f"- 정황 판단 (v4): {sum(r['payment_indirect'] == 'True' for r in got.values())}",
         f"- 참고: 지금 DB 값(v3 · 사람 판정)과 v4 판정이 다른 것 {sum(1 for it, k, g in pairs if g and db.get(it['rcept_no'], (None,))[0] != g['payment_type'])}건", "",
         "## 불일치 (정답표 쪽이 틀렸을 수도 있음)", ""]
    for it, k, g in pairs:
        if k and g and k["payment_type"] != g["payment_type"]:
            L += [f"- **{it['corp_name']} {it['rcept_no']}** ({it['first_date']}, 대상자 {it['n_allottees']}명) — 정답 **{k['payment_type']}** / v4 **{g['payment_type']}**"
                  + (" (정황)" if g["payment_indirect"] == "True" else "") + f" / 지금 DB {db.get(it['rcept_no'])}",
                  f"  - 정답 근거: “{k['payment_evidence']}” — {k['payment_reason']}",
                  f"  - v4 근거: “{g['payment_evidence']}” — {g['payment_reason']}"]
    if len(same) == len(items):
        L.append("- 없음")
    L += ["", "## 전체", ""] + [f"- {it['corp_name']} {it['rcept_no']} 대상자 {it['n_allottees']}명: 정답 {k['payment_type'] if k else '-'} / v4 {g['payment_type'] if g else '-'} / DB {db.get(it['rcept_no'], ('-',))[0]}"
                                for it, k, g in pairs]
    (OUT / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--estimate", action="store_true")
    g.add_argument("--run", action="store_true")
    g.add_argument("--report", action="store_true")
    a = ap.parse_args()
    with connect() as conn:
        items = sample(conn)
    if a.estimate:
        estimate(items)
    elif a.run:
        run_many(items, key_one, KEY)
        run_many(items, judge_one, GOT)
        report(items)
    else:
        report(items)


if __name__ == "__main__":
    main()
