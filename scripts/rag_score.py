"""RAG 채점기 (docs/cc_task_rag_baseline.md 1장 · data/rag_eval/README.md 채점 규칙). 검색 방법과 무관 — 순위표만 보고 채점한다.

검색 방법 하나 = 함수 두 개:
    search(question: str, rcept_nos: list[str], k: int) -> list[dict]   # [{chunk_id, rcept_no, text, score}] 점수 큰 순, rcept_nos 공시의 조각만
    all_chunks(rcept_nos: list[str]) -> list[dict]                      # 그 공시의 조각 전부 [{chunk_id, rcept_no, text}] 원문 순서 ("답 조각 없음" 판정용)

    from rag_score import run
    run("baseline_v1", search, all_chunks, extra={...})   # → data/rag_eval/runs/{run_name}.jsonl · {run_name}_report.md

- 정규화: 공백 전부 제거, “”"＂ → ", ‘’' → '
- 적중@k: 상위 k 조각 중 하나의 정규화 글에 answer_norm. 적중@k(이어붙임): 상위 k 조각을 순위대로 이어 붙인 글에 answer_norm (별도 지표)
- 답 조각 없음: 그 공시 조각 전부를 이어 붙여도 answer_norm이 없음 (청킹이 답 문장을 깨뜨리거나 뺌)
- 주 지표 Recall@3, 보조 Recall@1 · @5 · @10 · MRR(k=10). 고르는 순서 Recall@3 → MRR → Recall@1. 전체 / T1~T4 / 말투별
- 상위 1 · 3 · 5 조각 글자 수 합 (중앙값) — 조각이 커서 맞는 효과
- TRAP(expect=없음)은 Recall에서 빼고 따로 (정답셋 v2의 gold_train은 함정이 없어 100문제 전부 채점): 1위 조각 앞 100자, 물어본 조항 단어가 1위 조각에 있으면 "잘못된 근거". X-03은 answer_norm 포함이면 적중
"""

import json
import re
import statistics
import time
from collections import defaultdict
from pathlib import Path

from filingscope.config import DATA_DIR

EVAL = DATA_DIR / "rag_eval"
GOLD = EVAL / "gold_train.jsonl"
RUNS = EVAL / "runs"
K = 10
CLAUSE_WORDS = {"풋": ["조기상환청구", "put"], "콜": ["매도청구", "call"], "리픽싱": ["시가하락", "조정일"], "초과청약": ["초과청약"]}
TRAP_HIT_OK = {"X-03"}   # 원문에 "중도상환을 요구할 수 없다"가 있어 그 문구를 찾아오면 정답


def norm(s: str | None) -> str:
    s = re.sub(r"\s+", "", s or "")
    return re.sub("[“”＂]", '"', re.sub("[‘’]", "'", s))


def load_gold(path: Path = GOLD) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _clause(aspect: str) -> str | None:
    return next((c for c in CLAUSE_WORDS if c in aspect), None)


def score_one(q: dict, hits: list[dict], chunks: list[dict]) -> dict:
    ans = norm(q.get("answer_norm"))
    has = [bool(ans) and ans in norm(h["text"]) for h in hits]
    rank = next((i + 1 for i, x in enumerate(has) if x), None)
    out = {"qid": q["qid"], "topic": q["topic"], "style": q["style"], "expect": q["expect"],
           "top": [{"chunk_id": h["chunk_id"], "rcept_no": h["rcept_no"], "score": round(float(h["score"]), 6), "has_answer": a}
                   for h, a in zip(hits, has)],
           "rank": rank, "rr": 1 / rank if rank else 0.0}
    out["hit@10"] = rank is not None and rank <= 10
    for k in (1, 3, 5):
        out[f"chars@{k}"] = sum(len(h["text"]) for h in hits[:k])   # 상위 k 조각 글자 수 합 (조각이 커서 맞는 효과 보기)
        out[f"hit@{k}"] = rank is not None and rank <= k
        out[f"hit@{k}_concat"] = bool(ans) and ans in norm("".join(h["text"] for h in hits[:k]))
    out["no_answer_chunk"] = bool(ans) and ans not in norm("".join(c["text"] for c in chunks))
    out["n_chunks"] = len(chunks)
    if q["expect"] == "없음":
        top1 = hits[0]["text"] if hits else ""
        words = CLAUSE_WORDS.get(_clause(q["aspect"]) or "", [])
        found = [w for w in words if w.lower() in top1.lower()]
        ok = q["target"] in TRAP_HIT_OK and rank == 1
        out["trap"] = {"clause": _clause(q["aspect"]), "top1_head": top1[:100], "clause_words_in_top1": found,
                       "wrong_evidence": bool(found) and not ok, "answer_hit_top1": ok}
    return out


def metrics(rows: list[dict]) -> dict:
    rows = [r for r in rows if r["expect"] == "있음"]
    if not rows:
        return {"n": 0}
    n = len(rows)
    m = {"n": n, "MRR": sum(r["rr"] for r in rows) / n, "R@10": sum(r["rank"] is not None and r["rank"] <= 10 for r in rows) / n}
    for k in (1, 3, 5):
        m[f"R@{k}"] = sum(r[f"hit@{k}"] for r in rows) / n
        m[f"R@{k}_concat"] = sum(r[f"hit@{k}_concat"] for r in rows) / n
        m[f"chars@{k}"] = statistics.median(r[f"chars@{k}"] for r in rows)
    return m


def run(run_name: str, search, all_chunks, gold: list[dict] | None = None, extra: dict | None = None, notes: list[str] | None = None) -> dict:
    """문제마다 search(question, accepted_rcept_nos, k=10) → runs/{run_name}.jsonl + {run_name}_report.md. extra = 보고 7번 등 방법별 숫자."""
    gold = gold or load_gold()
    RUNS.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    rows, chunk_cache = [], {}
    for q in gold:
        key = tuple(q["accepted_rcept_nos"])
        if key not in chunk_cache:
            chunk_cache[key] = all_chunks(list(key))
        hits = search(q["question"], list(key), K)
        rows.append(score_one(q, hits, chunk_cache[key]))
    secs = time.time() - t0
    with (RUNS / f"{run_name}.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    report(run_name, gold, rows, chunk_cache, secs, extra or {}, notes or [])
    return metrics(rows)


def _pct(x: float) -> str:
    return f"{x:.3f}"


def report(run_name, gold, rows, chunk_cache, secs, extra, notes):
    by_q = {q["qid"]: q for q in gold}
    groups = [("전체", rows)] + [(t, [r for r in rows if r["topic"] == t]) for t in ("T1", "T2", "T3", "T4")] + \
             [(f"{s} 말투", [r for r in rows if r["style"] == s]) for s in ("공시", "투자자")]
    L = [f"# RAG 채점 — {run_name}", "", *notes, "",
         "## 1. 지표 (TRAP 제외, k=10 순위표)", "",
         "| 묶음 | n | **Recall@3** | Recall@1 | Recall@5 | MRR |", "|---|---|---|---|---|---|"]
    for name, rs in groups:
        m = metrics(rs)
        if m["n"]:
            L.append(f"| {name} | {m['n']} | **{_pct(m['R@3'])}** | {_pct(m['R@1'])} | {_pct(m['R@5'])} | {_pct(m['MRR'])} |")
    # 2. 조각 수 · 길이
    filings = defaultdict(list)
    for key, ch in chunk_cache.items():
        for c in ch:
            filings[c["rcept_no"]].append(c)
    counts = [len(v) for v in filings.values()]
    lens = [len(c["text"]) for v in filings.values() for c in v]
    L += ["", "## 2. 정답 공시의 조각", "",
          f"- 정답 공시 {len(filings)}건 · 조각 {sum(counts):,}개 — 공시당 조각 수 중앙 {statistics.median(counts):g} · 최대 {max(counts)} · 최소 {min(counts)}",
          f"- 조각 길이(글자): 중앙 {statistics.median(lens):g} · 평균 {statistics.mean(lens):.0f} · 최대 {max(lens)} · 최소 {min(lens)}"]
    # 3. 답 조각 없음
    na = [r for r in rows if r["expect"] == "있음" and r["no_answer_chunk"]]
    L += ["", "## 3. 답 조각 없음 (공시 조각 전부를 이어 붙여도 answer_norm 없음)", "",
          f"- {len(na)}건 / {sum(r['expect'] == '있음' for r in rows)}" + (f": {', '.join(r['qid'] for r in na)}" if na else "")]
    # 4. 이어붙임 차이
    m = metrics(rows)
    L += ["", "## 4. 적중@k vs 적중@k(이어붙임)", "", "| k | 적중 | 이어붙임 | 차이 (경계에 걸린 답) |", "|---|---|---|---|"]
    for k in (1, 3, 5):
        diff = [r["qid"] for r in rows if r["expect"] == "있음" and r[f"hit@{k}_concat"] and not r[f"hit@{k}"]]
        L.append(f"| {k} | {_pct(m[f'R@{k}'])} | {_pct(m[f'R@{k}_concat'])} | {len(diff)} {diff[:8]} |")
    # 5. TRAP
    traps = [r for r in rows if r["expect"] == "없음"]
    L += ["", f"## 5. TRAP {len(traps)}문제 — 잘못된 근거 {sum(r['trap']['wrong_evidence'] for r in traps)}건", "",
          "| qid | 조항 | 1위 조각에 조항 단어 | 판정 | 1위 조각 앞 100자 |", "|---|---|---|---|---|"]
    for r in traps:
        t = r["trap"]
        verdict = "적중 (명시 문구)" if t["answer_hit_top1"] else ("잘못된 근거" if t["wrong_evidence"] else "–")
        L.append(f"| {r['qid']} | {t['clause']} | {', '.join(t['clause_words_in_top1']) or '없음'} | {verdict} | {t['top1_head'].replace('|', '/')} |")
    # 6. 틀린 문제 10개
    wrong = [r for r in rows if r["expect"] == "있음" and not r["hit@3"]]
    far = sorted((r for r in wrong if r["rank"] is None), key=lambda r: r["qid"])      # 10위 밖 (주제가 고르게)
    near = sorted((r for r in wrong if r["rank"] is not None), key=lambda r: (r["rank"], r["qid"]))   # 아슬아슬
    far_pick = sorted(far, key=lambda r: (sum(x["topic"] == r["topic"] for x in far[:far.index(r)]), r["qid"]))[:5]
    pick = far_pick + near[:10 - len(far_pick)]
    L += ["", f"## 6. 틀린 문제 (Recall@3 실패 {len(wrong)}건 — 10위 밖 {len(far)} · 4~10위 {len(near)}) 중 10개: 10위 밖 {len(far_pick)} + 4위부터", ""]
    for r in pick:
        q = by_q[r["qid"]]
        top1 = chunk_text(chunk_cache, r["top"][0]["chunk_id"]) if r["top"] else ""
        ans_c = next((c for ch in chunk_cache.values() for c in ch if norm(q["answer_norm"]) in norm(c["text"])), None)
        where = f" — 답이 든 조각: {ans_c.get('field', '')} chunk {ans_c['chunk_id']}" if ans_c else ""
        top1s = f"1위 점수 {r['top'][0]['score']:.3f}" if r["top"] else ""
        ans_s = next((t["score"] for t in r["top"] if t["has_answer"]), None)
        why = ("답 조각 없음 — 공시 조각 어디에도 답 문구가 통째로 없음 (청킹)" if r["no_answer_chunk"]
               else f"정답 조각이 {r['rank']}위 (점수 {ans_s:.3f} vs {top1s}){where}" if r["rank"]
               else f"정답 조각이 10위 밖 ({top1s}){where}")
        L += [f"- **{r['qid']}** {q['question']}", f"  - 정답: {q['answer_text']}",
              f"  - 1위 조각: {top1[:150].replace(chr(10), ' ')}", f"  - 이유: {why}"]
    # 7. 시간 · 비용
    L += ["", "## 7. 시간 · 임베딩 · 비용", "", f"- 채점 실행 {secs:.1f}초 (검색 {len(rows)}회)"] + [f"- {k}: {v}" for k, v in extra.items()]
    (RUNS / f"{run_name}_report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


def chunk_text(chunk_cache: dict, chunk_id) -> str:
    for ch in chunk_cache.values():
        for c in ch:
            if c["chunk_id"] == chunk_id:
                return c["text"]
    return ""
