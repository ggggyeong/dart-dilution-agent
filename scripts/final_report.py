"""마지막 단계 보고서 → data/eval_v2/runs/final_report.md (+ 각 run의 answers.md).

    uv run python scripts/final_report.py

입력 (점수는 score_eval.py가 만든 것 그대로, 여기서 다시 채점하지 않음):
- v8 다시 채점: runs/main1-v8-20261024/scores_rescore.jsonl (예전 판정은 scores.jsonl)
- v9 학습: runs/main2-v9-20261024/scores.jsonl · v9 봉인: runs/sealed-v9-20261024/scores.jsonl
- runs/final_notes.json (사람 · Claude Code가 쓴 것): {"odd_manual": {종류: [[run, id, 한 줄]]}, "notes": [..], "recheck": "..",
  "b3_note": "..", "diff_v8_v9": 파일 경로}
이상한 답은 자동 검사(아래 ODD_*) + odd_manual을 합친다.
"""

import json
import re
import statistics
import sys
from pathlib import Path

from filingscope.config import DATA_DIR

sys.path.insert(0, str(Path(__file__).parent))
from score_eval import quotes_in, items_text, strip_quotes  # noqa: E402

EVAL = DATA_DIR / "eval_v2"
RUNS = EVAL / "runs"
TRIAL = RUNS / "trial-v7-20261024" / "qids.json"
V8, V9, SEALED = "main1-v8-20261024", "main2-v9-20261024", "sealed-v9-20261024"
B3_CHANGED = {"QE-new_major_holder-08"}                     # 질문 글이 바뀐 문제 (B-3)
PHRASE_CHANGED = {"QE-new_major_holder-05", "QE-new_major_holder-06", "QE-new_major_holder-07", "QE-new_major_holder-08"}  # 정답 문구 바뀜 (B-4)


def load(run: str, scores: str = "scores.jsonl"):
    d = RUNS / run
    res = {r["id"]: r for r in map(json.loads, (d / "results.jsonl").read_text(encoding="utf-8").splitlines())}
    sc = {s["id"]: s for s in map(json.loads, (d / scores).read_text(encoding="utf-8").splitlines())}
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    return res, sc, meta


def questions():
    out = {}
    for split in ("train", "sealed"):
        for q in json.loads((EVAL / f"questions_{split}.json").read_text(encoding="utf-8"))["items"]:
            out[(split, q["id"])] = q
    return out


def pct(rows):
    return f"{sum(s['pass'] for s in rows)}/{len(rows)} ({sum(s['pass'] for s in rows) / len(rows):.1%})" if rows else "–"


def body_len(ans: str) -> int:
    return len("".join(l for l in (ans or "").splitlines() if not l.strip().startswith("근거")).strip())


def summary(res, sc):
    rows = list(sc.values())
    general = [s for s in rows if not s["id"].startswith("QD")]
    qd = [s for s in rows if s["id"].startswith("QD")]
    traps = [s for s in qd if s["category"] == "clause_trap"]
    fabricated = [s["id"] for s in traps if not s.get("refuse") or not s.get("quote")]
    q7 = [s for s in rows if s["id"].startswith("Q7")]
    rr = list(res.values())
    secs = [r["seconds"] for r in rr if r.get("seconds") is not None]
    return {"n": len(rows), "all": pct(rows), "general": pct(general), "qd": pct(qd),
            "clause": pct([s for s in qd if s["category"] == "clause"]), "trap": pct(traps),
            "fabricated": f"{len(fabricated)}/{len(traps)}" + (f" {fabricated}" if fabricated else ""),
            "q7": pct(q7), "flag_misuse": sum(1 for s in rows if s.get("flag_misuse") == 0),
            "flag_ids": [s["id"] for s in rows if s.get("flag_misuse") == 0],
            "cost": sum(r["usage"]["cost_usd"] for r in rr), "per_q": sum(r["usage"]["cost_usd"] for r in rr) / len(rr),
            "sec": statistics.mean(secs) if secs else None, "turns": sum(r["turns"] for r in rr) / len(rr),
            "tools": sum(len(r["tool_calls"]) for r in rr) / len(rr),
            "tool_err": sum(1 for r in rr for t in r["tool_calls"] if t.get("error")),
            "cache_read": (lambda it, cw, cr: cr / (it + cw + cr) if it + cw + cr else 0)(
                sum(r["usage"]["input_tokens"] for r in rr), sum(r["usage"].get("cache_write_tokens", 0) for r in rr),
                sum(r["usage"].get("cache_read_tokens", 0) for r in rr))}


# ---------- 이상한 답 자동 검사 (지난번 종류) ----------

def cards(r):
    out = {}
    for t in r["tool_calls"]:
        res = t.get("result")
        if isinstance(res, dict):
            for c in list(res.get("live_events") or []) + ([res] if res.get("rcept_no") and res.get("calc") else []):
                out[c["rcept_no"]] = c
    return out


def odd_auto(run, res, sc, qs, split):
    found = {}

    def add(kind, qid, why):
        found.setdefault(kind, []).append([run, qid, why])
    for i, r in sorted(res.items()):
        a, q, cs = r["answer"] or "", qs[(split, i)], cards(r)
        used = set(re.findall(r"\d{14}", a))
        # ① flag 문장 어색 · 잘못 붙음
        for no in used:
            c = cs.get(no)
            if not c:
                continue
            disp = (c.get("calc") or {}).get("price_pending_display")
            if c["flags"].get("price_pending") and disp and disp not in a and re.search(r"조달액|예정 발행가", a):
                add("flag 문장: price_pending_display 글자 그대로 아님", i, f"{no} 카드 {disp!r}")
            if c["flags"].get("rehab") and "회생 절차 중인 회사의 발행이에요" not in a:
                add("flag 문장: rehab 문장 빠짐", i, no)
        if "최대주주가 바뀔 예정" in a and cs and not any(cs[n]["flags"].get("new_major_holder") for n in used if n in cs):
            add("flag 문장: 답에 쓴 공시엔 new_major_holder가 없는데 문장 있음 (다른 공시 flag 옮김)", i, sorted(used))
        if "증자로 최대주주" in a:
            add("flag 문장: 옛 문장 '증자로 최대주주'", i, "")
        # ② 시점
        if re.search(r"현재\s*거래\s*정지|지금\s*거래\s*정지", a):
            add("시점: '현재 거래정지'", i, "")
        # ③ 오래된 숫자: unit_mismatch 회사에서 CB 잔량 포함 숫자를 말함
        for t in r["tool_calls"]:
            res_ = t.get("result")
            if isinstance(res_, dict) and (res_.get("flags") or {}).get("unit_mismatch"):
                for c in res_.get("live_events") or []:
                    tp = (c.get("calc") or {}).get("total_pct")
                    if c["type"] == "CB" and tp is not None and (c["calc"].get("dilution_pct") != tp) and f"{tp}%" in a:
                        add("오래된 숫자: 단위 불일치 회사의 CB 잔량 포함 희석률", i, f"{c['rcept_no']} {tp}%")
        # ④ 인용 규칙 (QD): 인용 3개 이상
        if q["category"] in ("clause", "clause_trap"):
            n = len(quotes_in(a, items_text(r)))
            if n > 2:
                add("인용 3개 이상 (v9: 1~2개, 자동 셈 — 원문 안쪽 따옴표로 많게 셀 수 있음)", i, f"{n}개")
        # ⑤ 내부 말 · 메타 말 · 정해진 문장 따옴표
        if re.search(r"도구를\s*부르지|부르지\s*않겠|조각", a) or re.search(r"[A-Za-z]+_[A-Za-z_]+", a):
            add("내부 말 · 메타 말", i, ", ".join(sorted(set(re.findall(r"도구를\s*부르지\s*않[^.]*|조각|[A-Za-z]+_[A-Za-z_]+", a))))[:80])
        if re.search(r"[\"“](?:리픽싱 없어요|진행 중인 공시가 없어요|이 공시에는 그 조항이 없어요)[\"”]", a):
            add("정해진 문장을 따옴표로 감쌈", i, "")
        # ⑥ 비교 말 (v9 6-8)
        if q["category"] == "compare" and re.search(r"큰 편|작은 편", a) and not re.search(r"중앙값[^\n]{0,15}?보다 (큰|작은) 편", a):
            add("비교 말: '중앙값보다 큰/작은 편' 아님", i, "")
        if q["category"] == "repeat" and re.search(r"자주 하는 편|자주 한다고|편입니다|편이에요", a):
            add("비교 말: '자주 하는 편' 평가", i, "")
        # ⑦ 묻지 않은 항목 (키워드 추정)
        extra = {"dilution": ["배정 대상", "자금 용도", "운영자금", "채무상환", "상장 예정", "만기"],
                 "schedule": ["배정 대상", "자금", "희석률"], "compare": ["배정 대상", "자금", "상장", "만기", "전환 시작"],
                 "refixing": ["배정 대상", "자금", "상장", "만기"]}.get(q["category"])
        if extra:
            hit = [w for w in extra if w in strip_quotes(a)]
            if hit:
                add(f"묻지 않은 항목 (키워드 추정, {q['category']})", i, ", ".join(hit))
    return found


def answers_md(run, res, sc, qs, split, title):
    order = ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "QD-T1", "QD-T2", "QD-T3", "QD-T4", "QD-X", "QE"]
    key = lambda i: (next(n for n, p in enumerate(order) if i.startswith(p + "-")), i)  # noqa: E731
    L = [f"# {title} — 질문과 agent 답 전부 ({len(res)})", "", "답은 results.jsonl 원문 그대로.", ""]
    for n, i in enumerate(sorted(res, key=key), 1):
        s = sc[i]
        L += [f"## {n}. {i} — {'통과' if s['pass'] else '실패 (' + str(s['cause']) + ')'}", "", f"**질문:** {qs[(split, i)]['question']}", "",
              "**답:**", "", "```", (res[i]["answer"] or "").rstrip(), "```", ""]
    (RUNS / run / "answers.md").write_text("\n".join(L), encoding="utf-8")


def block(t):
    return ["```", (t or "").rstrip(), "```"]


def main():
    qs = questions()
    notes = json.loads((RUNS / "final_notes.json").read_text(encoding="utf-8")) if (RUNS / "final_notes.json").exists() else {}
    trial = {i for v in json.loads(TRIAL.read_text(encoding="utf-8"))["paths"].values() for i in v}
    r8, s8old, _ = load(V8)
    _, s8, m8 = load(V8, "scores_rescore.jsonl")
    r9, s9, m9 = load(V9)
    rs, ss, ms = load(SEALED)
    S8, S9, SS = summary(r8, s8), summary(r9, s9), summary(rs, ss)

    L = ["# 마지막 단계 보고 — v8 다시 채점 · v9 학습 · v9 봉인", "",
         f"학습 178문제(data/eval_v2/main_qids.json) · 봉인 115문제 · Sonnet · as_of 2026-10-19 · 도구 5개 · 캐싱 · 각 1회. "
         f"v8은 다시 채점만(재실행 아님), v9는 새로 실행.", "",
         "## 0. 요약", "", "| | v8 (다시 채점) | v9 학습 | v9 봉인 |", "|---|---|---|---|",
         f"| 문제 수 | {S8['n']} | {S9['n']} | {SS['n']} |",
         f"| 종합 정답률 | {S8['all']} | {S9['all']} | {SS['all']} |",
         f"| 일반 질문 (Q1~Q7 · QE) | {S8['general']} | {S9['general']} | {SS['general']} |",
         f"| 조항 (QD, 함정 포함) | {S8['qd']} | {S9['qd']} | {SS['qd']} |",
         f"| 함정 지어낸 답 | {S8['fabricated']} | {S9['fabricated']} | {SS['fabricated']} |",
         f"| 거절 · 되묻기 (Q7) | {S8['q7']} | {S9['q7']} | {SS['q7']} |",
         f"| flag_misuse 건수 | {S8['flag_misuse']} | {S9['flag_misuse']} | {SS['flag_misuse']} |",
         f"| 질문당 비용 | ${S8['per_q']:.4f} | ${S9['per_q']:.4f} | ${SS['per_q']:.4f} |",
         f"| 평균 응답 시간 | {S8['sec']:.1f}초 | {S9['sec']:.1f}초 | {SS['sec']:.1f}초 |", ""]
    L += [f"- 조항 안 나눔: v8 조항 {S8['clause']} · 함정 {S8['trap']} / v9 학습 조항 {S9['clause']} · 함정 {S9['trap']} / "
          f"v9 봉인 조항 {SS['clause']} · 함정 {SS['trap']}",
          "- v8 · v9 학습은 같은 178문제 · 같은 (새) 질문지 · 같은 (새) 채점기. v8 열은 v8 답을 새 채점기로 다시 채점한 값."]
    L += [f"- {x}" for x in notes.get("notes", [])]

    # C. v8 다시 채점
    rows8 = list(s8.values())
    L += ["", "## C. main1-v8 다시 채점 (재실행 아님)", "", "| 범위 | 예전 채점 | 새 채점 |", "|---|---|---|"]
    for name, f in (("전체 178", lambda i: True), ("시험 운행 50", lambda i: i in trial), ("나머지 128", lambda i: i not in trial),
                    ("일반 (Q1~Q7 · QE)", lambda i: not i.startswith("QD")), ("조항 (QD 50)", lambda i: i.startswith("QD"))):
        L.append(f"| {name} | {pct([s for i, s in s8old.items() if f(i)])} | {pct([s for i, s in s8.items() if f(i)])} |")
    L += [f"| 함정 지어낸 답 | – | {S8['fabricated']} |", f"| flag_misuse 건수 | – | {S8['flag_misuse']} {S8['flag_ids']} |", "",
          "바뀐 문제 (B-3에서 질문이 바뀐 문제 · B-4에서 정답 문구가 바뀐 문제는 따로):", "",
          "| id | 예전 → 새 | 예전 원인 | 새 원인 | 비고 |", "|---|---|---|---|---|"]
    for i in sorted(s8):
        a, b = s8old[i], s8[i]
        if a["pass"] != b["pass"] and i not in B3_CHANGED | PHRASE_CHANGED:
            L.append(f"| {i} | {'통과' if a['pass'] else '실패'} → {'통과' if b['pass'] else '실패'} | {a.get('cause') or '-'} | "
                     f"{b.get('cause') or '-'} | {(b.get('cause_note') or '')[:70]} |")
    L += ["", "따로: 질문 · 정답 문구가 바뀐 문제 (v8 답은 옛 질문 · 옛 문장 기준으로 쓴 것)", "", "| id | 바뀐 것 | 예전 → 새 |", "|---|---|---|"]
    for i in sorted(B3_CHANGED | PHRASE_CHANGED):
        what = "질문 날짜 추가 + 정답 문구" if i in B3_CHANGED else "정답 문구 '이번 발행으로 최대주주가 바뀔 예정'"
        L.append(f"| {i} | {what} | {'통과' if s8old[i]['pass'] else '실패'} → {'통과' if s8[i]['pass'] else '실패'} ({s8[i].get('cause') or '-'}) |")
    if notes.get("b3_note"):
        L += ["", notes["b3_note"]]

    # D. 프롬프트 diff
    diff = Path(notes["diff_v8_v9"]).read_text(encoding="utf-8") if notes.get("diff_v8_v9") else ""
    L += ["", "## D. 프롬프트 v9 (`diff docs/agent_prompt_v8.md docs/agent_prompt_v9.md`)", "", "```diff", diff.rstrip(), "```"]

    # 1. v8 → v9
    up = [i for i in sorted(s9) if not s8[i]["pass"] and s9[i]["pass"]]
    down = [i for i in sorted(s9) if s8[i]["pass"] and not s9[i]["pass"]]
    L += ["", "## 1. v8 (다시 채점) → v9 학습에서 바뀐 문제", "", f"실패 → 통과 {len(up)} · 통과 → 실패 {len(down)}", "",
          "| id | v8 원인 | v9 | 비고 |", "|---|---|---|---|"] + [
        f"| {i} | {s8[i].get('cause')} | 통과 | "
        f"{'질문 날짜 추가(B-3) · 정답 문구 바뀜(B-4) — v8 답은 옛 질문 · 옛 문장' if i in B3_CHANGED else '정답 문구 바뀜(B-4) — v8은 옛 문장 \'이번 증자로\'' if i in PHRASE_CHANGED else ''} |"
        for i in up]
    L += ["", "### 통과 → 실패 (답 전문)", ""]
    for i in down:
        L += [f"#### {i} — {s9[i]['cause']}: {s9[i].get('cause_note')}", "", f"- 질문: {qs[('train', i)]['question']}", "- v9 답:"] + block(r9[i]["answer"]) + \
             ["- v8 답:"] + block(r8[i]["answer"]) + [""]
    if not down:
        L.append("없음")

    # 2. v9 실패 전부
    for title, run, sc_, rr, split in (("v9 학습", V9, s9, r9, "train"), ("v9 봉인", SEALED, ss, rs, "sealed")):
        bad = [s for s in sorted(sc_.values(), key=lambda s: s["id"]) if not s["pass"]]
        L += ["", f"## 2. {title} 실패 전부 (n={len(bad)})", ""]
        for s in bad:
            r = rr[s["id"]]
            calls = " → ".join(f"{t.get('name')}({', '.join(f'{k}={v!r}' for k, v in (t.get('args') or {}).items())})" for t in r["tool_calls"]) or "(호출 없음)"
            L += [f"### {s['id']}", "", f"- 질문: {qs[(split, s['id'])]['question']}", f"- 원인 코드: **{s['cause']}** — {s.get('cause_note')}",
                  f"- 도구 호출: {calls}", "- 답:"] + block(r["answer"]) + [""]
        if not bad:
            L.append("없음")

    # 3. 이상한 답
    odd8 = odd_auto(V8, r8, s8, qs, "train")
    odd9 = odd_auto(V9, r9, s9, qs, "train")
    odds = odd_auto(SEALED, rs, ss, qs, "sealed")
    kinds = sorted(set(odd8) | set(odd9) | set(odds) | set(notes.get("odd_manual", {})))
    L += ["", "## 3. 통과했지만 이상한 답 (자동 검사 + 직접 읽음)", "",
          "자동 검사는 지난번(main1-v8 보고서 5절) 종류를 그대로 찾는 규칙. 통과 · 실패 모두 셈 (실패는 2절에도 있음).", "",
          "| 종류 | v8 | v9 학습 | v9 봉인 |", "|---|---|---|---|"]
    man = notes.get("odd_manual", {})
    for k in kinds:
        c = lambda d, run: len(d.get(k, [])) + sum(1 for x in man.get(k, []) if x[0] == run)  # noqa: E731
        L.append(f"| {k} | {c(odd8, V8)} | {c(odd9, V9)} | {c(odds, SEALED)} |")
    for k in kinds:
        ex = [x for x in odd9.get(k, []) + odds.get(k, []) + [m for m in man.get(k, []) if m[0] != V8]]
        if ex:
            L += ["", f"### {k} (v9)", ""] + [f"- {run.split('-')[0]} **{i}** {why}" for run, i, why in ex[:12]] + (["- …"] if len(ex) > 12 else [])

    # 4. 길이
    L += ["", "## 4. 일반 질문 답 길이 (근거 줄 뺀 글자 수 평균)", "", "| 묶음 | v8 | v9 | 변화 |", "|---|---|---|---|"]
    for g in ("Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "QE"):
        a = [body_len(r8[i]["answer"]) for i in r8 if i.startswith(g + "-")]
        b = [body_len(r9[i]["answer"]) for i in r9 if i.startswith(g + "-")]
        L.append(f"| {g} | {statistics.mean(a):.0f} | {statistics.mean(b):.0f} | {statistics.mean(b) / statistics.mean(a) - 1:+.0%} |")
    short = [i for i in r9 if i.startswith(("Q1-", "Q2-")) and body_len(r9[i]["answer"]) < 60]
    L += ["", f"Q1 · Q2에서 v9 답이 60자 미만: {len(short)}건 {short}"]
    L += [f"- {x}" for x in notes.get("length_notes", [])]

    # 5. 학습 vs 봉인
    L += ["", "## 5. 학습 vs 봉인", "", "| | v9 학습 | v9 봉인 | 차이 (봉인 − 학습) |", "|---|---|---|---|"]

    def rate(sc_, f):
        rows = [s for s in sc_.values() if f(s["id"])]
        return (sum(s["pass"] for s in rows) / len(rows), len(rows)) if rows else (None, 0)
    for name, f in (("종합", lambda i: True), ("일반", lambda i: not i.startswith("QD")), ("조항 (QD)", lambda i: i.startswith("QD")),
                    ("Q7", lambda i: i.startswith("Q7")), ("QE", lambda i: i.startswith("QE"))):
        (a, na), (b, nb) = rate(s9, f), rate(ss, f)
        L.append(f"| {name} | {a:.1%} (n={na}) | {b:.1%} (n={nb}) | {(b - a) * 100:+.1f}%p |" if a is not None and b is not None else f"| {name} | – | – | – |")
    L += [f"- {x}" for x in notes.get("split_notes", [])]

    # 6. 비용
    L += ["", "## 6. 비용 · 동작", "", "| | v9 학습 | v9 봉인 | 합 |", "|---|---|---|---|",
          f"| 비용 | ${S9['cost']:.2f} | ${SS['cost']:.2f} | ${S9['cost'] + SS['cost']:.2f} (상한 $8) |",
          f"| 질문당 | ${S9['per_q']:.4f} | ${SS['per_q']:.4f} | |",
          f"| 평균 turns | {S9['turns']:.2f} | {SS['turns']:.2f} | |",
          f"| 평균 도구 호출 | {S9['tools']:.2f} | {SS['tools']:.2f} | |",
          f"| 도구 오류 | {S9['tool_err']} | {SS['tool_err']} | |",
          f"| 캐시 읽기 비율 | {S9['cache_read']:.1%} | {SS['cache_read']:.1%} | |",
          f"| 실행 시간 | {m9.get('seconds')}초 | {ms.get('seconds')}초 | |"]

    # 7. 답 전부
    answers_md(V9, r9, s9, qs, "train", "main2-v9-20261024 (v9 학습)")
    answers_md(SEALED, rs, ss, qs, "sealed", "sealed-v9-20261024 (v9 봉인)")
    L += ["", "## 7. 질문과 답 전부", "", f"- v9 학습: runs/{V9}/answers.md", f"- v9 봉인: runs/{SEALED}/answers.md",
          "", "## H. recheck_dilution.py", "", notes.get("recheck", "없음"), ""]
    (RUNS / "final_report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print(RUNS / "final_report.md")


if __name__ == "__main__":
    main()
