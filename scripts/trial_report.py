"""시험 운행 보고서: data/eval_v2/runs/{run_id}/ (qids.json · results.jsonl · scores.jsonl · meta.json) → report.md.

    uv run python scripts/trial_report.py --run-id trial-v7-20261024

grader_suspects.json({id: 한 줄})이 있으면 "채점기가 틀린 것 같은 문제"로, observations.json([[종류, id, 내용]])이 있으면
"이상한 답 · 고장 후보"로 따로 적는다 (답을 읽고 사람 · Claude Code가 붙인 것).
점수는 score_eval.py가 만든 scores.jsonl 그대로 (여기서 다시 채점하지 않음).
"""

import argparse
import json

from filingscope.config import DATA_DIR

EVAL = DATA_DIR / "eval_v2"
METRICS = ("tool", "number", "evidence", "refuse", "topic", "quote", "answer")


def calls_line(r: dict) -> str:
    out = []
    for t in r["tool_calls"]:
        args = ", ".join(f"{k}={v!r}" for k, v in (t.get("args") or {}).items())
        out.append(f"{t.get('name')}({args})" + (f" → 오류 {t['error']}" if t.get("error") else ""))
    return " → ".join(out) or "(호출 없음)"


def block(text: str) -> list[str]:
    return ["```", (text or "").rstrip(), "```"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    a = ap.parse_args()
    run = EVAL / "runs" / a.run_id
    paths = json.loads((run / "qids.json").read_text(encoding="utf-8"))["paths"]
    qs = {q["id"]: q for q in json.loads((EVAL / "questions_train.json").read_text(encoding="utf-8"))["items"]}
    res = {r["id"]: r for r in map(json.loads, (run / "results.jsonl").read_text(encoding="utf-8").splitlines())}
    sc = {s["id"]: s for s in map(json.loads, (run / "scores.jsonl").read_text(encoding="utf-8").splitlines())}
    meta = json.loads((run / "meta.json").read_text(encoding="utf-8"))
    sp = run / "grader_suspects.json"
    suspects = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {}
    op = run / "observations.json"
    observations = json.loads(op.read_text(encoding="utf-8")) if op.exists() else []
    path_of = {i: p for p, ids in paths.items() for i in ids}

    rows = [sc[i] for i in sc]
    n = len(rows)
    L = [f"# 시험 운행 {a.run_id}", "",
         f"- 학습 질문지에서 {meta['n']}문제 (seed 7, qids.json) · 프롬프트 {meta['prompt_version']} · {meta['model']} · as_of {meta['as_of']} · 1회",
         f"- 실행 {meta.get('done')}문제 · {meta.get('seconds')}초 · 멈춤: {meta.get('stopped') or '없음'}",
         f"- 비용 ${sum(r['usage']['cost_usd'] for r in res.values()):.2f} (상한 ${meta.get('cost_cap')}) · "
         f"평균 turns {sum(r['turns'] for r in res.values()) / len(res):.2f} · "
         f"평균 도구 호출 {sum(len(r['tool_calls']) for r in res.values()) / len(res):.2f}회 · "
         f"도구 오류 {sum(1 for r in res.values() for t in r['tool_calls'] if t.get('error'))}건 "
         f"(오류 난 문제 {sum(1 for r in res.values() if any(t.get('error') for t in r['tool_calls']))}) · "
         f"도구 호출 한도 걸림 {sum(1 for r in res.values() if '(도구 호출 한도)' in (r['answer'] or ''))}건",
         "", "## 1. 경로별 지표 통과 수 (통과/해당 문제 수, 해당 없음 -)", "",
         "| 경로 | n | " + " | ".join(METRICS) + " | 종합 |", "|---|---|" + "---|" * (len(METRICS) + 1)]

    def cnt(g, k):
        v = [s.get(k) for s in g if s.get(k) is not None]
        return f"{sum(v)}/{len(v)}" if v else "-"
    for p, ids in list(paths.items()) + [("전체", list(sc))]:
        g = [sc[i] for i in ids if i in sc]
        L.append(f"| {p} | {len(g)} | " + " | ".join(cnt(g, k) for k in METRICS) + f" | **{cnt(g, 'pass')}** |")
    L += ["", "QD 종합 = tool · evidence · topic · quote · answer, 함정 = tool · refuse · quote, 나머지 = tool · number · evidence · refuse (해당하는 것)."]

    bad = [s for s in rows if not s["pass"]]
    L += ["", f"## 2. 틀린 문제 전부 (n={len(bad)})", ""]
    for s in sorted(bad, key=lambda s: (list(paths).index(path_of[s["id"]]), s["id"])):
        q, r = qs[s["id"]], res[s["id"]]
        fails = [k for k in METRICS if s.get(k) == 0]
        L += [f"### {s['id']} — {path_of[s['id']]}" + (" ⚠ 채점기 의심" if s["id"] in suspects else ""), "",
              f"- 질문: {q['question']}",
              f"- 원인 코드: **{s.get('cause')}** — {s.get('cause_note')} · 실패 지표: {', '.join(fails) or '-'}",
              f"- 기대: {json.dumps({k: v for k, v in q['expected'].items() if v not in (None, [], '')}, ensure_ascii=False)}",
              f"- 도구 호출: {calls_line(r)}", "- 답:"] + block(r["answer"]) + [""]

    L += ["## 3. 채점기가 틀린 것 같은 문제", "",
          "agent 답이 맞아 보이는데 실패, 또는 틀려 보이는데 통과. 고치지 않고 보고만.", ""]
    if suspects:
        L += ["| id | 채점 | 이유 |", "|---|---|---|"]
        L += [f"| {i} | {'통과' if sc[i]['pass'] else '실패'} | {why} |" for i, why in suspects.items()]
    else:
        L.append("없음")

    L += ["", "## 4. 이상한 답 · 고장 후보 (고치지 않음)", ""]
    if observations:
        L += ["| 종류 | 문제 | 내용 |", "|---|---|---|"] + [f"| {k} | {i} | {t} |" for k, i, t in observations]
    else:
        L.append("없음")

    L += ["", "## 부록. 50문제 답 전문", ""]
    for p, ids in paths.items():
        L += [f"### {p}", ""]
        for i in ids:
            s, r = sc.get(i), res.get(i)
            if not r:
                L += [f"#### {i} — 실행 안 됨", ""]
                continue
            L += [f"#### {i} — {'통과' if s['pass'] else '실패 ' + str(s.get('cause'))}", "",
                  f"- 질문: {qs[i]['question']}", f"- 도구 호출: {calls_line(r)}",
                  f"- turns {r['turns']} · ${r['usage']['cost_usd']}"] + block(r["answer"]) + [""]
    (run / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"{run / 'report.md'} · 틀림 {len(bad)}/{n}")


if __name__ == "__main__":
    main()
