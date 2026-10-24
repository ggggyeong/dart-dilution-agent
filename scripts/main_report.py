"""본 실행 보고서: data/eval_v2/runs/{run_id}/ (results.jsonl · scores.jsonl · meta.json) + data/eval_v2/main_qids.json → report.md.

    uv run python scripts/main_report.py --run-id main1-v8-20261024

점수는 score_eval.py가 만든 scores.jsonl 그대로 (여기서 다시 채점하지 않음). 사람 · Claude Code가 답을 읽고 붙인 것:
- odd_answers.json: {종류: [[id, 한 줄], ...]} — "통과했지만 이상한 답"
- grader_suspects.json: {id: 한 줄} — "채점기가 틀린 것 같은 문제"
- notes.json: {"recheck": 한 줄}
"""

import argparse
import json
import statistics
from collections import Counter

from filingscope.config import DATA_DIR

EVAL = DATA_DIR / "eval_v2"
TRIAL = EVAL / "runs" / "trial-v7-20261024" / "qids.json"
METRICS = ("tool", "number", "evidence", "refuse", "topic", "quote", "answer", "leak", "evidence_format", "trap_short")
GROUPS = ("Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "QD T1", "QD T2", "QD T3", "QD T4", "QD 함정", "QE")


def group_of(qid: str) -> str:
    p = qid.split("-")
    if p[0] == "QD":
        return "QD 함정" if p[1] == "X" else f"QD {p[1]}"
    return p[0]


def calls_line(r: dict) -> str:
    out = []
    for t in r["tool_calls"]:
        args = ", ".join(f"{k}={v!r}" for k, v in (t.get("args") or {}).items())
        out.append(f"{t.get('name')}({args})" + (f" → 오류 {t['error']}" if t.get("error") else ""))
    return " → ".join(out) or "(호출 없음)"


def block(text: str) -> list[str]:
    return ["```", (text or "").rstrip(), "```"]


def rate(rows) -> str:
    return f"{sum(s['pass'] for s in rows)}/{len(rows)} ({sum(s['pass'] for s in rows) / len(rows):.1%})" if rows else "–"


def cnt(g, k) -> str:
    v = [s.get(k) for s in g if s.get(k) is not None]
    return f"{sum(v)}/{len(v)}" if v else "-"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    a = ap.parse_args()
    run = EVAL / "runs" / a.run_id
    qs = {q["id"]: q for q in json.loads((EVAL / "questions_train.json").read_text(encoding="utf-8"))["items"]}
    res = {r["id"]: r for r in map(json.loads, (run / "results.jsonl").read_text(encoding="utf-8").splitlines())}
    sc = {s["id"]: s for s in map(json.loads, (run / "scores.jsonl").read_text(encoding="utf-8").splitlines())}
    meta = json.loads((run / "meta.json").read_text(encoding="utf-8"))
    trial = {i for v in json.loads(TRIAL.read_text(encoding="utf-8"))["paths"].values() for i in v}
    load = lambda name, d: json.loads((run / name).read_text(encoding="utf-8")) if (run / name).exists() else d
    odd, suspects, notes = load("odd_answers.json", {}), load("grader_suspects.json", {}), load("notes.json", {})

    rows = [sc[i] for i in sorted(sc)]
    rest = [s for s in rows if s["id"] not in trial]
    tri = [s for s in rows if s["id"] in trial]
    general = [s for s in rows if not s["id"].startswith("QD")]
    qd = [s for s in rows if s["id"].startswith("QD")]
    clause = [s for s in qd if s["category"] == "clause"]
    traps = [s for s in qd if s["category"] == "clause_trap"]
    fabricated = [s["id"] for s in traps if not s.get("refuse") or not s.get("quote")]
    q7 = [s for s in rows if s["id"].startswith("Q7")]
    bad = [s for s in rows if not s["pass"]]
    causes = Counter(s["cause"] for s in bad)
    top = causes.most_common(1)
    rr = list(res.values())
    cost = sum(r["usage"]["cost_usd"] for r in rr)
    secs = [r["seconds"] for r in rr if r.get("seconds") is not None]
    it = sum(r["usage"]["input_tokens"] for r in rr)
    cw = sum(r["usage"].get("cache_write_tokens", 0) for r in rr)
    cr = sum(r["usage"].get("cache_read_tokens", 0) for r in rr)
    ot = sum(r["usage"]["output_tokens"] for r in rr)
    tool_err = sum(1 for r in rr for t in r["tool_calls"] if t.get("error"))
    capped = sum(1 for r in rr if "(도구 호출 한도)" in (r["answer"] or ""))

    L = [f"# 본 실행 {a.run_id}", "",
         f"학습 질문지 178문제 (data/eval_v2/main_qids.json, seed 11) · 프롬프트 {meta['prompt_version']} · {meta['model']} · "
         f"as_of {meta['as_of']} · 도구 5개 · 캐싱 · 1회. 실행 {meta.get('done')}문제 · 멈춤 {meta.get('stopped') or '없음'}.", "",
         "## 0. 요약", "",
         "| 항목 | 값 |", "|---|---|",
         f"| 문제 수 | {len(rows)} (시험 운행 제외 {len(rest)}) |",
         f"| 종합 정답률 | 전체 {rate(rows)} · 시험 운행 제외 {rate(rest)} |",
         f"| 일반 질문 (Q1~Q7 · QE) | {rate(general)} |",
         f"| 조항 (QD 50 = 조항 40 + 함정 10) | {rate(qd)} (조항 {rate(clause)} · 함정 {rate(traps)}) |",
         f"| 함정 지어낸 답 (거절 실패 또는 인용 있음) | {len(fabricated)}/{len(traps)} {fabricated} |",
         f"| 거절 · 되묻기 (Q7) | {rate(q7)} |",
         f"| 질문당 비용 | ${cost / len(rr):.4f} (총 ${cost:.2f}) |",
         f"| 평균 응답 시간 | {statistics.mean(secs):.1f}초 (중앙 {statistics.median(secs):.1f}초, 최대 {max(secs):.1f}초) |" if secs else "| 평균 응답 시간 | – |",
         f"| 가장 많은 실패 원인 | {top[0][0]} {top[0][1]}건 |" if top else "| 가장 많은 실패 원인 | 없음 |",
         "", "묶음별 문제 수", "", "| " + " | ".join(GROUPS) + " | 합 |", "|" + "---|" * (len(GROUPS) + 1),
         "| " + " | ".join(str(sum(1 for s in rows if group_of(s["id"]) == g)) for g in GROUPS) + f" | {len(rows)} |",
         "", "## 1. 종합 통과율", "", "| 범위 | 통과 |", "|---|---|",
         f"| 전체 178 | {rate(rows)} |", f"| 시험 운행 50 | {rate(tri)} |", f"| 나머지 128 | {rate(rest)} |",
         "", "## 2. 묶음별 × 지표별 통과 수 (통과/해당, 해당 없음 -)", "",
         "| 묶음 | n | " + " | ".join(METRICS) + " | 종합 |", "|---|---|" + "---|" * (len(METRICS) + 1)]
    for g in GROUPS + ("전체",):
        gg = rows if g == "전체" else [s for s in rows if group_of(s["id"]) == g]
        L.append(f"| {g} | {len(gg)} | " + " | ".join(cnt(gg, k) for k in METRICS) + f" | **{cnt(gg, 'pass')}** |")
    L += ["", "QD 조항 종합 = tool · evidence · topic · quote · answer · leak · evidence_format, 함정 = tool · refuse · quote · leak · evidence_format · trap_short, "
              "나머지 = tool · number · evidence · refuse(해당하는 것) · leak · evidence_format.",
          "", "## 3. 원인 코드별 실패 수", "", "| 원인 | 건수 | 문제 |", "|---|---|---|"]
    L += [f"| {c} | {n} | {', '.join(s['id'] for s in bad if s['cause'] == c)} |" for c, n in causes.most_common()] or ["| 없음 | 0 | |"]

    L += ["", f"## 4. 틀린 문제 전부 (n={len(bad)})", ""]
    for s in sorted(bad, key=lambda s: (GROUPS.index(group_of(s["id"])), s["id"])):
        q, r = qs[s["id"]], res[s["id"]]
        fails = [k for k in METRICS if s.get(k) == 0]
        L += [f"### {s['id']} ({group_of(s['id'])}{' · 시험 운행' if s['id'] in trial else ''})" + (" ⚠ 채점기 의심" if s["id"] in suspects else ""), "",
              f"- 질문: {q['question']}",
              f"- 원인 코드: **{s['cause']}** — {s['cause_note']} · 실패 지표: {', '.join(fails) or '-'}",
              f"- 기대: {json.dumps({k: v for k, v in q['expected'].items() if v not in (None, [], '')}, ensure_ascii=False)}",
              f"- 도구 호출: {calls_line(r)}", "- 답:"] + block(r["answer"]) + [""]

    n_odd = sum(len(v) for v in odd.values())
    L += [f"## 5. 통과했지만 이상한 답 ({n_odd}건, 고치지 않음)", ""]
    if odd:
        L += ["| 종류 | 건수 | 문제 |", "|---|---|---|"] + [f"| {k} | {len(v)} | {', '.join(i for i, _ in v)} |" for k, v in odd.items()]
        for k, v in odd.items():
            L += ["", f"### {k}", ""] + [f"- **{i}** {qs[i]['question']} — {why}" for i, why in v]
    else:
        L.append("없음")
    L += ["", "## 6. 채점기가 틀린 것 같은 문제", ""]
    if suspects:
        L += ["| id | 채점 | 이유 |", "|---|---|---|"] + [f"| {i} | {'통과' if sc[i]['pass'] else '실패'} | {why} |" for i, why in suspects.items()]
    else:
        L.append("없음")
    allin = it + cw + cr
    L += ["", "## 7. 비용 · 동작", "", "| 항목 | 값 |", "|---|---|",
          f"| 비용 | ${cost:.2f} (상한 ${meta.get('cost_cap')}) · 질문당 ${cost / len(rr):.4f} |",
          f"| 평균 turns | {sum(r['turns'] for r in rr) / len(rr):.2f} |",
          f"| 평균 도구 호출 | {sum(len(r['tool_calls']) for r in rr) / len(rr):.2f}회 |",
          f"| 도구 오류 | {tool_err}건 · 도구 호출 한도 걸림 {capped}건 |",
          f"| 토큰 | 입력 {it:,} · 캐시 쓰기 {cw:,} · 캐시 읽기 {cr:,} · 출력 {ot:,} |",
          f"| 캐시 읽기 비율 | {cr / allin:.1%} (읽기 ÷ 입력 전체) |" if allin else "| 캐시 읽기 비율 | – |",
          f"| 실행 시간 | {meta.get('seconds')}초 |",
          "", "## 8. recheck_dilution.py", "", notes.get("recheck", "–"), ""]
    (run / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"{run / 'report.md'} · 통과 {sum(s['pass'] for s in rows)}/{len(rows)}")


if __name__ == "__main__":
    main()
