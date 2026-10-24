"""새 RAG 비교 실험 (docs/cc_task_rag_build.md 3~6장): 자르기 2가지 × 찾기 4가지, 학습 100문제.

    uv run python scripts/rag_experiments.py    # → data/rag_eval/runs/{run}.jsonl · {run}_report.md, exp_report.md · exp_report.html

찾기는 모두 search(question, rcept_nos, k) 모양 (scripts/rag_score.py 그대로 채점). 후보 = 그 공시(들)의 해당 method 조각 전부.
질문 임베딩은 조각과 같은 모델로 100문제를 한 번에 받는다.
"""

import html
import importlib.util
import json
import re
import statistics
import sys
import time
from pathlib import Path

from filingscope.config import DATA_DIR, PROJECT_ROOT, get_settings
from filingscope.db import connect

sys.path.insert(0, str(Path(__file__).parent))
import rag_score as R  # noqa: E402

RUNS = DATA_DIR / "rag_eval" / "runs"
EMBED_MODEL = "text-embedding-3-small"
RRF_K = 10
RULES_V1 = {   # rule v1 (실험 ② · ③ 처음 사전, docs/cc_task_rag_build.md 3장) — 남겨 둠
    "T1": r"발행가|확정\s*가|최종\s*가격|1차|2차|초과\s*청약|더\s*(청약|넣)|추가로|실권|미달|남으면|남는|안\s*팔리|잔여|떠안|청약\s*안",
    "T2": r"리픽싱|시가\s*하락|전환\s*가(액|격)?\s*(조정|내려|하향)|조정\s*(주기|일)|얼마나\s*자주\s*내려",
    "T3": r"콜|매도\s*청구|되사|Call",
    "T4": r"풋|조기\s*상환|Put|돌려\s*달|중도\s*상환|돈\s*빼",
}
RULES_V2_CB = {   # rule v2 (공식, 2026-10-23): 유상증자(주주배정)면 단어 검사 없이 T1, CB면 아래 단어로 T2 · T3 · T4
    "T2": r"리픽싱|전환\s*가|시가\s*하락",
    "T3": r"콜|매도\s*청구|Call",
    "T4": r"풋|조기\s*상환|Put",
}
RULE = "v2"   # 공식 rule
CONN = connect()
QV: dict[str, str] = {}   # 질문 → 벡터 글자


QCACHE = RUNS / "question_embeddings.json"   # 질문 임베딩 저장 — OpenAI 임베딩은 같은 글도 호출마다 미세하게 달라 순위가 1문제쯤 흔들림


def embed_questions(gold):
    from openai import OpenAI
    qs = list(dict.fromkeys(q["question"] for q in gold))
    cached = json.loads(QCACHE.read_text(encoding="utf-8")) if QCACHE.exists() else {}
    if all(q in cached for q in qs):
        QV.update({q: cached[q] for q in qs})
        return 0
    client = OpenAI(api_key=get_settings().openai_api_key.get_secret_value(), timeout=60, max_retries=2)
    resp = client.embeddings.create(model=EMBED_MODEL, input=qs)
    for q, d in zip(qs, resp.data):
        QV[q] = d.embedding
    QCACHE.write_text(json.dumps({**cached, **{q: QV[q] for q in qs}}), encoding="utf-8")
    return resp.usage.total_tokens


def _vec(v) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in v) + "]"


def candidates(method: str, question: str, rcept_nos: list[str]) -> list[dict]:
    """그 공시들의 method 조각 전부 + 벡터 유사도 · 키워드 점수 (AND 먼저, 후보 중 하나도 안 맞으면 OR)."""
    q_and = CONN.execute("SELECT plainto_tsquery('simple', %s)::text", (question,)).fetchone()[0]
    tq = None
    if q_and:
        for t in (q_and, q_and.replace(" & ", " | ")):
            if CONN.execute("SELECT 1 FROM rag_chunks WHERE method = %s AND rcept_no = ANY(%s) AND tsv @@ %s::tsquery LIMIT 1",
                            (method, rcept_nos, t)).fetchone():
                tq = t
                break
    rows = CONN.execute(
        """SELECT id, rcept_no, field, topic, topics, seq, text, doc_type, 1 - (embedding <=> %(qv)s::vector),
                  CASE WHEN %(tq)s::text IS NULL THEN NULL WHEN tsv @@ %(tq)s::tsquery THEN ts_rank(tsv, %(tq)s::tsquery) END
           FROM rag_chunks WHERE method = %(m)s AND rcept_no = ANY(%(nos)s)""",
        {"qv": _vec(QV[question]), "tq": tq, "m": method, "nos": rcept_nos}).fetchall()
    order = {n: i for i, n in enumerate(rcept_nos)}
    out = [dict(chunk_id=i, rcept_no=n.strip(), field=f, topic=t, topics=ts or [t], seq=s, text=x, doc_type=d, vec=float(v),
                kw=(float(k) if k is not None else None)) for i, n, f, t, ts, s, x, d, v, k in rows]
    for c in out:
        c["pos"] = (order[c["rcept_no"]], c["seq"])   # 원문 순서
    return out


def _ranked(cs, key) -> list[dict]:
    return sorted(cs, key=lambda c: (-key(c), c["pos"]))


def make_search(method: str, how: str, rule: str = RULE):
    def search(question: str, rcept_nos: list[str], k: int) -> list[dict]:
        cs = candidates(method, question, rcept_nos)
        doc_type = cs[0]["doc_type"] if cs else None
        if how == "vector":
            ranked = _ranked(cs, lambda c: c["vec"])
            score = {c["chunk_id"]: c["vec"] for c in cs}
        elif how == "keyword":
            ranked = _ranked(cs, lambda c: c["kw"] or 0.0)
            score = {c["chunk_id"]: c["kw"] or 0.0 for c in cs}
        elif how == "hybrid":   # RRF k=10, 키워드에 안 걸린 조각은 키워드 등수 = 후보 수 + 1
            n = len(cs)
            kr = {c["chunk_id"]: i + 1 for i, c in enumerate(_ranked([c for c in cs if c["kw"] is not None], lambda c: c["kw"]))}
            vr = {c["chunk_id"]: i + 1 for i, c in enumerate(_ranked(cs, lambda c: c["vec"]))}
            score = {c["chunk_id"]: 1 / (RRF_K + kr.get(c["chunk_id"], n + 1)) + 1 / (RRF_K + vr[c["chunk_id"]]) for c in cs}
            ranked = _ranked(cs, lambda c: score[c["chunk_id"]])
        elif how == "rule_vector":   # 실험 ③: rule로 고른 topic 조각만 후보 → 그 안에서 벡터 순. 주제 못 잡으면 전체를 벡터 순
            pick = rule_pick(cs, rule_topics(question, doc_type, rule))
            ranked = _ranked(pick, lambda c: c["vec"])
            score = {c["chunk_id"]: c["vec"] for c in pick}
        elif how == "rule":     # 질문의 주제 → 그 topic 조각을 원문 순서대로. 여러 주제면 전부, 없으면 전체
            pick = rule_pick(cs, rule_topics(question, doc_type, rule))
            ranked = sorted(pick, key=lambda c: c["pos"])
            score = {c["chunk_id"]: 1 / (i + 1) for i, c in enumerate(ranked)}
        else:
            raise ValueError(how)
        return [{"chunk_id": c["chunk_id"], "rcept_no": c["rcept_no"], "text": c["text"], "score": score[c["chunk_id"]],
                 "topic": c["topic"], "field": c["field"]} for c in ranked[:k]]
    return search


def rule_pick(cs: list[dict], topics: set[str]) -> list[dict]:
    """고른 주제가 꼬리표(topics) 안에 있는 조각. 주제를 못 고르거나(빈 집합) 그 공시에 그런 조각이 0개면 공시 조각 전부 (안전망)."""
    pick = [c for c in cs if topics & set(c["topics"])] if topics else []
    return pick or cs


def rule_topics(question: str, doc_type: str | None = None, version: str = RULE) -> set[str]:
    """v1: 질문 글을 T1~T4 사전에. v2: 유상증자(주주배정)면 {T1}, CB면 T2 · T3 · T4 사전 (여러 개면 전부, 없으면 빈 집합 = 전체)."""
    if version == "v1":
        return {t for t, p in RULES_V1.items() if re.search(p, question, re.I)}
    if doc_type == "유상증자":
        return {"T1"}
    return {t for t, p in RULES_V2_CB.items() if re.search(p, question, re.I)}


def make_all_chunks(method: str):
    def all_chunks(rcept_nos: list[str]) -> list[dict]:
        rows = CONN.execute("""SELECT id, rcept_no, field, topic, topics, section_title, seq, text FROM rag_chunks
                               WHERE method = %s AND rcept_no = ANY(%s)""", (method, rcept_nos)).fetchall()
        order = {n: i for i, n in enumerate(rcept_nos)}
        rows = sorted(rows, key=lambda r: (order[r[1].strip()], r[6]))
        return [dict(chunk_id=i, rcept_no=n.strip(), field=f, topic=t, topics=ts or [t], section_title=st, seq=s, text=x)
                for i, n, f, t, ts, st, s, x in rows]
    return all_chunks


def rule_all(gold, version: str = RULE) -> list[dict]:
    """실험 ③-2: rule이 고른 topic 조각 '전부'를 넘긴다고 볼 때 정답 포함 비율 · 넘기는 글자 수. 순위 없음."""
    allc = make_all_chunks("section")
    rows = []
    for q in gold:
        cs = allc(q["accepted_rcept_nos"])
        topics = rule_topics(q["question"], q["doc_type"], version)
        pick = rule_pick(cs, topics)
        rows.append({"qid": q["qid"], "topic": q["topic"], "style": q["style"], "rule_topics": sorted(topics), "n_chunks": len(pick),
                     "chars": sum(len(c["text"]) for c in pick), "has_answer": any(q["answer_norm"] in R.norm(c["text"]) for c in pick)})
    (RUNS / f"exp3_section_rule_all{'' if version == 'v1' else '_' + version}.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    return rows


def rule_all_summary(rows) -> dict:
    ch = sorted(r["chars"] for r in rows)
    return {"n": len(rows), "포함": sum(r["has_answer"] for r in rows) / len(rows), "중앙": statistics.median(ch),
            "90%": ch[min(len(ch) - 1, int(len(ch) * 0.9))], "최대": ch[-1], "빈 후보": sum(r["n_chunks"] == 0 for r in rows),
            "조각 수 중앙": statistics.median(r["n_chunks"] for r in rows)}


def load_rows(run):
    return [json.loads(line) for line in (RUNS / f"{run}.jsonl").read_text(encoding="utf-8").splitlines()]


def mtx(rows, topic=None, style=None) -> dict:
    rs = [r for r in rows if (topic is None or r["topic"] == topic) and (style is None or r["style"] == style)]
    return R.metrics(rs)


def better(a: dict, b: dict) -> bool:
    """a가 b보다 나은가: Recall@3 → MRR → Recall@1."""
    return (a["R@3"], a["MRR"], a["R@1"]) > (b["R@3"], b["MRR"], b["R@1"])


def main():
    t0 = time.time()
    gold = R.load_gold()
    n_all = len(gold)
    q_tokens = embed_questions(gold)
    runs, timing, versions = {}, {}, {}

    def go(name, method, how, rule="v1"):
        t = time.time()
        tag = f" · rule {rule}" if how.startswith("rule") else ""
        R.run(name, make_search(method, how, rule), make_all_chunks(method), gold,
              extra={"자르기": method, "찾기": how + tag}, notes=[f"자르기 `{method}` · 찾기 `{how}`{tag} (docs/cc_task_rag_build.md)"])
        timing[name] = round(time.time() - t, 1)
        runs[name] = (method, how, load_rows(name))
        versions[name] = rule

    go("exp1_size_hybrid", "size", "hybrid")
    go("exp1_section_hybrid", "section", "hybrid")
    ms = {k: R.metrics(v[2]) for k, v in runs.items()}
    a, b = ms["exp1_size_hybrid"], ms["exp1_section_hybrid"]
    if abs(a["R@3"] - b["R@3"]) <= 0.02:
        best1 = "size" if a["chars@3"] < b["chars@3"] else "section"
        why1 = f"Recall@3 차이 {abs(a['R@3'] - b['R@3']) * 100:.0f}%p (2%p 이내) → 상위 3조각 글자 수가 적은 쪽"
    else:
        best1 = "size" if a["R@3"] > b["R@3"] else "section"
        why1 = "Recall@3 높은 쪽"
    exp2 = [f"exp2_{best1}_{h}" for h in (["rule"] if best1 == "section" else []) + ["keyword", "vector"]]
    for name in exp2:
        go(name, best1, name.rsplit("_", 1)[1])
    go("exp3_section_rule_vector", "section", "rule_vector")   # 실험 ③ (자르기 section 고정), rule v1
    go("exp2_section_rule_v2", "section", "rule", "v2")         # rule v2 (공식)
    go("exp3_section_rule_vector_v2", "section", "rule_vector", "v2")
    rall = {"v1": rule_all(gold, "v1"), "v2": rule_all(gold, "v2")}
    # 동점이면 먼저 온 쪽이 남으므로 공식 rule(v2) run을 v1보다 앞에
    v2 = ["exp2_section_rule_v2", "exp3_section_rule_vector_v2"] if best1 == "section" else []
    cand = [f"exp1_{best1}_hybrid"] + v2 + exp2 + (["exp3_section_rule_vector"] if best1 == "section" else [])
    ms = {k: R.metrics(v[2]) for k, v in runs.items()}
    best = cand[0]
    for c in cand[1:]:
        if better(ms[c], ms[best]):
            best = c

    # 참고: 현재 RAG (보관한 옛 조각 + 옛 하이브리드 C) — 비교에 넣지 않음
    ref = None
    try:
        spec = importlib.util.spec_from_file_location("rag_baseline_v1", PROJECT_ROOT / "scripts" / "rag_baseline_v1.py")
        B = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(B)
        B.S.QUERY_CACHE.update(QV)   # 같은 임베딩 모델 → 질문 임베딩 재사용
        R.run("ref_current_rag", B.search, B.all_chunks, gold, notes=["참고용: archive_filing_chunks + 옛 하이브리드 C (비교에 넣지 않음)"])
        ref = R.metrics(load_rows("ref_current_rag"))
    except Exception as e:  # noqa: BLE001
        ref = {"error": str(e)[:200]}

    stats = json.loads((RUNS / "chunk_stats.json").read_text(encoding="utf-8"))
    report(gold, n_all, runs, ms, best1, why1, exp2, best, ref, stats, q_tokens, timing, round(time.time() - t0), rall, versions)


# ---------- 보고 ----------

def fmt(x, pct=True):
    return f"{x:.3f}" if pct else f"{x:,.0f}"


def section_examples(gold) -> list[tuple[str, list]]:
    picks = []
    t1 = next(q for q in gold if q["topic"] == "T1")
    cb = [q for q in gold if q["topic"] in ("T2", "T3", "T4")]
    seen = set()
    for q in [t1] + cb:
        if len(picks) == 3:
            break
        if q["rcept_no"] in seen or (q["topic"] != "T1" and len(picks) == 0):
            continue
        if q["topic"] == "T1" and picks:
            continue
        seen.add(q["rcept_no"])
        rows = CONN.execute("""SELECT seq, field, topic, section_title, length(text), left(text, 80) FROM rag_chunks
                               WHERE method = 'section' AND rcept_no = %s ORDER BY seq""", (q["rcept_no"],)).fetchall()
        picks.append((f"{q['corp_name']} {q['rcept_no']} ({q['doc_type']})", rows))
        if len(picks) == 2:   # CB 두 번째는 다른 주제 공시
            cb = [x for x in cb if x["topic"] != q["topic"]]
    return picks


def report(gold, n_all, runs, ms, best1, why1, exp2, best, ref, stats, q_tokens, timing, secs, rall, versions):
    by_q = {q["qid"]: q for q in gold}
    bm = ms[best]
    bmethod, bhow, brows = runs[best]
    L = ["# 새 RAG 비교 실험 — 자르기 2 × 찾기 4 (학습 100문제)", "",
         "## 결론", "",
         f"1. **자르기 `{bmethod}` · 찾기 `{bhow}`** (run `{best}`)",
         f"2. **Recall@3 {bm['R@3']:.3f}** (MRR {bm['MRR']:.3f} · Recall@1 {bm['R@1']:.3f} · Recall@5 {bm['R@5']:.3f})",
         f"3. 상위 3조각 글자 수 중앙 **{bm['chars@3']:,.0f}자**", "",
         f"- 채점 문제 수 확인: 정답셋 {n_all}문제 (함정 0) → 모든 run에서 {ms[best]['n']}문제 전부 채점 (빼는 문제 없음).",
         f"- 실험 ① 1등 자르기: `{best1}` — {why1}. 실험 ②③ 1등: Recall@3 → MRR → Recall@1 순 (①의 hybrid · ③ rule_vector 포함, rule_all은 순위가 없어 제외).",
         f"- 공식 rule = {RULE} (v1 사전은 RULES_V1로 남김). 동점이면 공식 rule run을 1등으로.",
         "- 봉인 30문제는 쓰지 않았다 (사람이 따로 돌림).", ""]
    head = "| run | Recall@3 | Recall@1 | Recall@5 | Recall@10 | MRR | 상위3 글자 수(중앙) |"
    sep = "|---|---|---|---|---|---|---|"

    def line(r, star=""):
        m = ms[r]
        return f"| {r}{star} | **{fmt(m['R@3'])}** | {fmt(m['R@1'])} | {fmt(m['R@5'])} | {fmt(m['R@10'])} | {fmt(m['MRR'])} | {m['chars@3']:,.0f} |"
    L += ["## 표 A — 실험 ① 자르기 (찾기 = hybrid RRF k=10)", "", head, sep] + [line(r) for r in ("exp1_size_hybrid", "exp1_section_hybrid")]
    L += ["", f"## 표 B — 실험 ② · ③ 찾기 (자르기 = `{best1}`) — rule v1 · v2 나란히 (공식 rule = {RULE})", "", head, sep]
    order = [f"exp1_{best1}_hybrid"] + [r for r in exp2 if not r.endswith("_rule")] + \
            ["exp2_section_rule", "exp2_section_rule_v2", "exp3_section_rule_vector", "exp3_section_rule_vector_v2"]
    L += [line(r, (" ★" if r == best else "")) for r in order if r in ms]
    for v in ("v1", "v2"):
        ra = rule_all_summary(rall[v])
        name = "exp3_section_rule_all" + ("" if v == "v1" else "_v2")
        L.append(f"| {name} (topic 조각 전부 넘김, 순위 없음) | 정답 포함 **{ra['포함']:.3f}** | – | – | – | – | "
                 f"넘기는 글자 수 중앙 {ra['중앙']:,.0f} · 90% {ra['90%']:,.0f} · 최대 {ra['최대']:,.0f} · 조각 수 중앙 {ra['조각 수 중앙']:g} |")
    L += ["", "- rule v1 = 질문 글을 T1~T4 사전에 (docs/cc_task_rag_build.md 3장). rule v2 = 유상증자(주주배정)면 단어 검사 없이 T1, "
          "CB면 T2 `리픽싱|전환\\s*가|시가\\s*하락` · T3 `콜|매도\\s*청구|Call` · T4 `풋|조기\\s*상환|Put` (여럿이면 전부, 없으면 전체 조각).",
          "- rule_vector = 같은 후보를 벡터 순으로. rule_all = 그 후보 전부를 넘긴다고 볼 때."]
    # 주제 판별 결과 v1 · v2
    def judge(version):
        out = {"정확": [], "여럿": [], "없음": [], "틀림": []}
        for q in gold:
            t = rule_topics(q["question"], q["doc_type"], version)
            key = "없음" if not t else "틀림" if q["topic"] not in t else "정확" if len(t) == 1 else "여럿"
            out[key].append((q, sorted(t)))
        return out
    jd = {v: judge(v) for v in ("v1", "v2")}
    L += ["", "## rule 주제 판별 (100문제)", "", "| | 정확 (정답 주제 하나) | 여럿 (정답 주제 + 다른 주제) | 없음 (전체 조각으로) | 틀림 (정답 주제 빠짐) |", "|---|---|---|---|---|"]
    for v in ("v1", "v2"):
        L.append(f"| rule {v} | {len(jd[v]['정확'])} | {len(jd[v]['여럿'])} | {len(jd[v]['없음'])} | {len(jd[v]['틀림'])} |")
    for v in ("v1", "v2"):
        rest = [(k, q, t) for k in ("여럿", "없음", "틀림") for q, t in jd[v][k]]
        if rest:
            L += ["", f"rule {v} — 정확이 아닌 질문:", "", "| 판별 | qid | 정답 주제 | 질문 | 걸린 주제 |", "|---|---|---|---|---|"]
            L += [f"| {k} | {q['qid']} | {q['topic']} | {q['question']} | {'·'.join(t) or '없음'} |" for k, q, t in rest]
    # v1 → v2 바뀐 문제
    L += ["", "## rule v1 → v2 바뀐 문제 (Recall@3 기준, rule_all은 정답 포함)", "", "| run | v1 맞음 → v2 틀림 | v1 틀림 → v2 맞음 |", "|---|---|---|"]
    for a1, a2 in (("exp2_section_rule", "exp2_section_rule_v2"), ("exp3_section_rule_vector", "exp3_section_rule_vector_v2")):
        if a1 in runs and a2 in runs:
            h1 = {x["qid"]: x["hit@3"] for x in runs[a1][2]}
            h2 = {x["qid"]: x["hit@3"] for x in runs[a2][2]}
            down = [q for q in h1 if h1[q] and not h2[q]]
            up = [q for q in h1 if not h1[q] and h2[q]]
            L.append(f"| {a1} → _v2 | {len(down)} {', '.join(down) or ''} | {len(up)} {', '.join(up) or ''} |")
    h1 = {x["qid"]: x["has_answer"] for x in rall["v1"]}
    h2 = {x["qid"]: x["has_answer"] for x in rall["v2"]}
    down = [q for q in h1 if h1[q] and not h2[q]]
    up = [q for q in h1 if not h1[q] and h2[q]]
    L.append(f"| exp3_section_rule_all → _v2 | {len(down)} {', '.join(down)} | {len(up)} {', '.join(up)} |")
    L += ["", f"## 표 C — 1등 `{best}` 주제별 · 말투별", "", "| 묶음 | n | Recall@3 | Recall@1 | MRR |", "|---|---|---|---|---|"]
    groups = [("전체", None, None)] + [(t, t, None) for t in ("T1", "T2", "T3", "T4")] + [(f"{s} 말투", None, s) for s in ("공시", "투자자")]
    for name, t, s in groups:
        m = mtx(brows, t, s)
        L.append(f"| {name} | {m['n']} | **{fmt(m['R@3'])}** | {fmt(m['R@1'])} | {fmt(m['MRR'])} |")
    L += ["", "## 표 D — 모든 run의 말투별 Recall@3", "", "| run | 공시 말투 | 투자자 말투 | 차이 |", "|---|---|---|---|"]
    for r, (_, _, rows) in runs.items():
        a, b = mtx(rows, style="공시")["R@3"], mtx(rows, style="투자자")["R@3"]
        L.append(f"| {r} | {fmt(a)} | {fmt(b)} | {(b - a) * 100:+.0f}%p |")
    for v in ("v1", "v2"):
        rr = rall[v]
        a = sum(x["has_answer"] for x in rr if x["style"] == "공시") / max(1, sum(x["style"] == "공시" for x in rr))
        b = sum(x["has_answer"] for x in rr if x["style"] == "투자자") / max(1, sum(x["style"] == "투자자" for x in rr))
        L.append(f"| exp3_section_rule_all{'' if v == 'v1' else '_v2'} (정답 포함 비율) | {fmt(a)} | {fmt(b)} | {(b - a) * 100:+.0f}%p |")
    # 봉인 (수정 전 1회) · 봉인 이후 수정과 전수 커버리지
    sealed_md = RUNS / "sealed_report.md"
    if sealed_md.exists():
        sr = R.metrics(load_rows("sealed_rule_vector_v2"))
        ra_line = next((ln for ln in sealed_md.read_text(encoding="utf-8").splitlines() if ln.startswith("| 봉인 |")), "")
        L += ["", "## 봉인 시험 (수정 전, 1회 — data/rag_eval/runs/sealed_report.md)", "",
              f"- section + rule v2 · rule_vector_v2: 봉인 {sr['n']}문제 (expect=없음 6 뺌) Recall@3 **{sr['R@3']:.3f}** · Recall@1 {sr['R@1']:.3f} · MRR {sr['MRR']:.3f}. "
              f"rule_all_v2 정답 포함 {ra_line.split('|')[2].strip() if ra_line else '?'}.",
              "- **이 점수는 아래 수정 전 점수로 그대로 둔다. 봉인은 다시 돌리지 않았다.**",
              "- 봉인에서 놓친 T2-04(지엔코)는 정답 조각이 EXE_REG \"ㄹ) 위 ㄱ) 내지 ㄷ)과 **별개로** 매 7개월…\"인데 자르기 규칙이 \"별도\" 낱말을 찾아 topic=기타 → rule_all 22/24의 빠진 2건."]
    def cov_table(b, a_, title_b, title_a):
        out = [f"| 검사 | {title_b} | {title_a} | 빠짐 | 조항 없음이라 분모에서 뺌 |", "|---|---|---|---|---|"]
        for k in a_:
            if k in ("꼬리표", "잘못 붙은 예"):
                continue
            out.append(f"| {k} | {b[k]['있음']}/{b[k]['대상']} ({b[k]['비율'] * 100:.1f}%) | {a_[k]['있음']}/{a_[k]['대상']} ({a_[k]['비율'] * 100:.1f}%) | "
                       f"{a_[k]['빠짐']} | {a_[k].get('조항 없음이라 뺌', 0)} |")
        return out
    c1b, c1a = RUNS / "coverage_before_t2fix.json", RUNS / "coverage_after_t2fix.json"
    c2b, c2a = RUNS / "coverage_before.json", RUNS / "coverage_after.json"
    if c1b.exists() and c1a.exists():
        L += ["", "## 봉인 이후 수정 ① — EXE_REG T2 규칙을 내용 기준으로 (정답셋 없이 전수 커버리지로 검증)", "",
              "- T2 = 항목 안에 주기(매 N개월 · 매 N개월 N일 · 매월 · 매 분기)와 \"조정일\" (\"별도\" 표현 조건 삭제). 글 그대로 · 다시 임베딩 0, topic 586 바뀜 "
              "(기타→T2 321 · T2→기타 265 = 주기 없는 상향 조정 항목).", ""]
        L += cov_table(json.loads(c1b.read_text(encoding="utf-8")), json.loads(c1a.read_text(encoding="utf-8")), "수정 전", "수정 후")
    if c2b.exists() and c2a.exists():
        b2, a2 = json.loads(c2b.read_text(encoding="utf-8")), json.loads(c2a.read_text(encoding="utf-8"))
        tb, ta = b2["꼬리표"], a2["꼬리표"]
        L += ["", "## 봉인 이후 수정 ② — 꼬리표 여러 개 (\"빠뜨리는 것보다 더 넣는 게 낫다\")", "",
              "- 조각 글 그대로 · 다시 임베딩 0. 처음 주제(topic, 머리줄 · 절 규칙)는 그대로 두고, 조각 글 안 내용으로 topics에 더함:",
              "  CB T3 `(?<!동반)매도청구 · Call Option · 콜옵션 · 조기매입청구 · 매수청구권(Call` · T4 `조기상환청구 · Put Option · Putl Option(원문 오타) · 풋옵션 · 중도상환청구 · 중도상환권 · 조기상환권` · "
              "T2 주기 + \"조정일\" 같은 조각 / 주주배정 T1 `발행가액 · 초과청약 · 실권 · 잔여주식 · 미달` (Option · 옵션은 대소문자 무시).",
              "- 사용자 목록에 더한 것 (지난 전수 검사에서 빠진 공시 원문에 쓰인 표현, 정답셋 질문은 안 봄): 중도상환권 · 조기상환권 · Putl Option · 매수청구권(Call …). "
              "뺀 것: 동반매도청구권(Tag-along, 콜 아님). \"매수청구권\" 전체는 \"주식매수청구권\"이 걸려 넣지 않음.",
              "- rule 검색: 고른 주제가 topics 안에 있는 조각이 후보. **안전망**: 그 공시에 그런 조각이 0개면 공시 조각 전부를 벡터 순으로.",
              "- 커버리지 분모: T3 · T4는 '조항이 없다고 밝힌 글'(낱말 바로 뒤에 없다 · 해당사항 없음)을 빼고 따로 셈.", ""]
        L += cov_table(b2, a2, "꼬리표 전", "꼬리표 후")
        L += ["", "- T3 남은 빠짐 3 = 원문에 콜 조항 없이 \"동반매도청구권(Tag-along)\"만 있는 공시 (검사 낱말 '매도청구권'에 걸린 것, 꼬리표가 안 붙는 게 맞음).", "",
              "| 꼬리표 | 전 | 후 |", "|---|---|---|"]
        for t in ("T1", "T2", "T3", "T4", "기타"):
            L.append(f"| {t} 조각 | {tb.get(f'topic {t}', 0):,} | {ta.get(f'topic {t}', 0):,} |")
        L.append(f"| 꼬리표 2개 이상 (T 기준) | {tb.get('꼬리표 2개 이상', 0):,} | {ta.get('꼬리표 2개 이상', 0):,} |")
        L += ["", "잘못 붙은 것으로 보이는 예 (둘 다 후보를 넓히기만 함 — 정답을 빼지는 않음):", ""]
        for kind, v in a2.get("잘못 붙은 예", {}).items():
            L.append(f"- {kind}: {v['조각 수']}조각")
            for e in v["예"]:
                L.append(f"  - {e['rcept_no']} {e['칸']} 처음 {e['처음']} → {'·'.join(e['topics'])} | …{re.sub(r'\\s+', ' ', e['근처'])}…")
    # 꼬리표 전후 학습 점수 · 넘기는 글자 수 (E = 공시 조각 전부)
    bt = RUNS / "exp3_section_rule_vector_v2_before_tags.jsonl"
    if bt.exists() and "exp3_section_rule_vector_v2" in ms:
        mb, ma = R.metrics(load_rows("exp3_section_rule_vector_v2_before_tags")), ms["exp3_section_rule_vector_v2"]
        rb = load_rows("exp3_section_rule_all_v2_before_tags")
        allc = make_all_chunks("section")
        e_chars = sorted(sum(len(c["text"]) for c in allc(q["accepted_rcept_nos"])) for q in gold)
        def dist(xs):
            xs = sorted(xs)
            return statistics.median(xs), xs[min(len(xs) - 1, int(len(xs) * 0.9))], xs[-1]
        b_med, b90, bmax = dist(x["chars"] for x in rb)
        a_med, a90, amax = dist(x["chars"] for x in rall["v2"])
        e_med, e90, emax = dist(e_chars)
        L += ["", "## 꼬리표 전후 — 학습 100 (봉인은 다시 안 돌림)", "", "| rule_vector_v2 | Recall@1 | Recall@3 | Recall@5 | Recall@10 | MRR | 상위3 글자 수 |", "|---|---|---|---|---|---|---|"]
        for name, m_ in (("꼬리표 전", mb), ("꼬리표 후", ma)):
            L.append(f"| {name} | {fmt(m_['R@1'])} | **{fmt(m_['R@3'])}** | {fmt(m_['R@5'])} | {fmt(m_['R@10'])} | {fmt(m_['MRR'])} | {m_['chars@3']:,.0f} |")
        L += ["", "| 넘기는 글 | 정답 포함 | 글자 수 중앙 | 90% | 최대 |", "|---|---|---|---|---|",
              f"| rule_all_v2 꼬리표 전 | {sum(x['has_answer'] for x in rb)}/{len(rb)} | {b_med:,.0f} | {b90:,.0f} | {bmax:,.0f} |",
              f"| rule_all_v2 꼬리표 후 | {sum(x['has_answer'] for x in rall['v2'])}/{len(rall['v2'])} | {a_med:,.0f} ({(a_med - b_med) / b_med * 100:+.0f}%) | "
              f"{a90:,.0f} ({(a90 - b90) / b90 * 100:+.0f}%) | {amax:,.0f} ({(amax - bmax) / bmax * 100:+.0f}%) |",
              f"| 참고: 공시 RAG 조각 전부 (E) | {len(e_chars)}/{len(e_chars)} | {e_med:,.0f} | {e90:,.0f} | {emax:,.0f} |"]
    # 조각 통계
    c = stats["counts"]
    L += ["", "## 조각 통계", "", "| | size | section |", "|---|---|---|"]
    for k in ("조각", "공시", "공시당 중앙", "공시당 최대", "길이 중앙", "길이 90%", "길이 최대"):
        L.append(f"| {k} | {stats['size'][k]:,} | {stats['section'][k]:,} |")
    L += ["", f"- section topic별 조각: {' · '.join(f'{k} {v:,}' for k, v in sorted(stats['section']['topic별'].items()))}",
          f"- 칸별 조각 (size / section): " + " · ".join(f"{f} {stats['size']['칸별'].get(f, 0):,} / {stats['section']['칸별'].get(f, 0):,}"
                                                    for f in ("FREE_TEXT", "EXE_REG", "MIN_RSN", "OPT_FCT", "ISS_FUNC", "PRC_PLN")),
          f"- 넣지 않음(참조 · 참고만, 60자 미만): ISS_FUNC {c.get('참조만이라 뺌:ISS_FUNC', 0)} · PRC_PLN {c.get('참조만이라 뺌:PRC_PLN', 0)} · OPT_FCT {c.get('참조만이라 뺌:OPT_FCT', 0)}",
          f"- 경계 못 찾음 (칸 전체 1조각 · 기타): " + " · ".join(f"{k.split(':', 1)[1]} {v}" for k, v in c.items() if k.startswith("경계 못 찾음")),
          f"- 풋 · 콜 낱말은 있는데 머리줄을 못 찾은 CB FREE_TEXT: {c.get('풋·콜 머리줄 못 찾음:CB FREE_TEXT', 0)} (해당 글은 맨 위 표시로 나눠 기타)",
          f"- OPT_FCT 중복(같은 공시 FREE_TEXT 풋 · 콜과 5글자 조각 겹침 0.8 이상)으로 뺀 부분: {c.get('OPT_FCT 중복으로 뺌', 0)}",
          "- **자르기 규칙 손질 기록 (학습셋 진단으로 찾은 일반 규칙, 공시별 예외 없음)**: 항목 표시 앞 마침표 허용(\"…한다.나. 합병\") · \"가 .\" · (가) · 가) · a. 추가 · \"다.목\"(앞 항목 가리킴) 제외 · "
          "첫 항목에 표시 없는 글은 2번부터 순서 인정 · 풋 · 콜 머리줄은 줄 맨 앞 / 여는 괄호 / 항목 표시 / '…에 관한 사항'으로 시작 · 끝날 때만, 키워드 앞에 '.' · 괄호 없음 · OPT_FCT는 풋 ↔ 콜이 바뀌는 곳에서만 나눔. "
          "이 뒤로 학습 100문제의 답이 모두 한 조각 안 · section 정답 조각 topic 100/100 일치. 학습셋에 맞춰졌을 수 있어 봉인 30문제로 확인 필요."]
    L += ["", "## 답 조각 없음 (그 공시 조각 전부를 이어도 답 문구 없음)", "", "| run | 건수 | 문제 |", "|---|---|---|"]
    for r, (_, _, rows) in runs.items():
        na = [x["qid"] for x in rows if x["no_answer_chunk"]]
        L.append(f"| {r} | {len(na)} | {', '.join(na) or '–'} |")
    # 틀린 문제
    wrong = [r for r in brows if not r["hit@3"]]
    chunks = {}
    for r in wrong:
        ids = [t["chunk_id"] for t in r["top"]]
        if ids:
            for i, f, t, x in CONN.execute("SELECT id, field, topic, text FROM rag_chunks WHERE id = ANY(%s)", (ids,)).fetchall():
                chunks[i] = (f, t, x)
    L += ["", f"## 1등 `{best}`에서 틀린 문제 전부 (Recall@3 실패 {len(wrong)}건)", "",
          "| qid | 질문 | 정답 문구 앞 60자 | 1위 조각 (topic · 앞 100자) | 정답 순위 | 이유 |", "|---|---|---|---|---|---|"]
    for r in wrong:
        q = by_q[r["qid"]]
        top = r["top"][0] if r["top"] else None
        f, t, x = chunks.get(top["chunk_id"], ("", "", "")) if top else ("", "", "")
        rt = rule_topics(q["question"], q["doc_type"], versions.get(best, RULE))
        if bhow in ("rule", "rule_vector"):
            why = ("질문에서 주제를 못 찾아 전체 원문 순서" if not rt else
                   f"질문 주제 {'·'.join(sorted(rt))} — 정답 주제 {q['topic']}" if q["topic"] not in rt else
                   (f"같은 주제 조각이 원문에서 정답보다 앞에 {(r['rank'] or 11) - 1}개 이상" if bhow == "rule"
                    else "같은 주제 안에서 벡터로 다른 조각이 위"))
        else:
            why = (f"1위가 다른 주제({t})" if t and t != q["topic"] else "같은 주제 안 다른 조각이 위") + ("" if r["rank"] else " · 정답 10위 밖")
        L.append(f"| {r['qid']} | {q['question']} | {q['answer_text'][:60]} | {t} · {x[:100].replace(chr(10), ' ').replace('|', '/')} | "
                 f"{r['rank'] or '10위 밖'} | {why} |")
    # section 예시
    L += ["", "## section 조각 예시 (주주배정 1 · CB 2)"]
    for title, rows in section_examples(gold):
        L += ["", f"### {title}", "", "| seq | 칸 | topic | section_title | 길이 | 앞 80자 |", "|---|---|---|---|---|---|"]
        for s, f, t, st, ln, head in rows:
            L.append(f"| {s} | {f} | {t} | {(st or '')[:50].replace('|', '/')} | {ln} | {head.replace(chr(10), ' ').replace('|', '/')} |")
    emb = stats.get("embedding", {})
    L += ["", "## 비용 · 시간 · 모델", "",
          f"- 임베딩 모델 `{emb.get('model', EMBED_MODEL)}` (1536차원). 조각 임베딩: size {emb.get('tokens', {}).get('size', 0):,} · section {emb.get('tokens', {}).get('section', 0):,}토큰 · "
          f"**${emb.get('cost_usd', 0):.3f}** · {emb.get('seconds', 0)}초",
          (f"- 질문 임베딩 1회 (100문제) {q_tokens:,}토큰 · ${q_tokens * 0.02 / 1e6:.5f} → data/rag_eval/runs/question_embeddings.json에 저장" if q_tokens
           else "- 질문 임베딩: 저장해 둔 것(question_embeddings.json) 다시 씀 — 호출마다 값이 미세하게 달라 순위가 흔들리는 것을 막으려고"),
          f"- 실험 실행 {secs}초 (run별: " + ", ".join(f"{k} {v}초" for k, v in timing.items()) + ")"]
    if ref and "R@3" in ref:
        L += ["", f"참고: 현재 RAG(보관한 옛 조각 39,673 + 옛 하이브리드 C)를 새 100문제로 — Recall@3 {ref['R@3']:.3f} · MRR {ref['MRR']:.3f} (비교에 넣지 않음)"]
    elif ref:
        L += ["", f"참고: 현재 RAG 실행 실패 — {ref.get('error')}"]
    (RUNS / "exp_report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    (RUNS / "exp_report.html").write_text(to_html(L, runs, ms, best, best1, exp2, brows), encoding="utf-8")
    print("\n".join(L[:40]))


def to_html(lines, runs, ms, best, best1, exp2, brows) -> str:
    """md 표를 그대로 HTML로 + 맨 위에 Recall@3 가로 막대. 외부 라이브러리 없음."""
    def bar(label, v, hi=False):
        return (f'<div class="row"><span class="lab">{html.escape(label)}</span><span class="track"><span class="fill{" hi" if hi else ""}" '
                f'style="width:{v * 100:.1f}%"></span></span><span class="val">{v:.3f}</span></div>')
    bars = ['<h2>Recall@3 — 모든 run</h2>'] + [bar(r, ms[r]["R@3"], r == best) for r in runs]
    bars += [f'<h2>1등 {html.escape(best)} — 주제별 · 말투별 Recall@3</h2>']
    for name, t, s in [("전체", None, None)] + [(t, t, None) for t in ("T1", "T2", "T3", "T4")] + [(f"{s} 말투", None, s) for s in ("공시", "투자자")]:
        bars.append(bar(name, mtx(brows, t, s)["R@3"]))
    body, i = [], 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("|"):
            tbl = []
            while i < len(lines) and lines[i].startswith("|"):
                tbl.append(lines[i])
                i += 1
            rows = [[c.strip() for c in t.strip("|").split("|")] for t in tbl if not re.fullmatch(r"\|[-| ]+\|", t)]
            h = "".join(f"<th>{inline(c)}</th>" for c in rows[0])
            b = "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in rows[1:])
            body.append(f"<table><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table>")
            continue
        if ln.startswith("### "):
            body.append(f"<h3>{inline(ln[4:])}</h3>")
        elif ln.startswith("## "):
            body.append(f"<h2>{inline(ln[3:])}</h2>")
        elif ln.startswith("# "):
            body.append(f"<h1>{inline(ln[2:])}</h1>")
        elif re.match(r"^(\d+\.|-) ", ln):
            body.append(f"<p class='li'>{inline(ln)}</p>")
        elif ln.strip():
            body.append(f"<p>{inline(ln)}</p>")
        i += 1
    css = """body{font-family:-apple-system,'Apple SD Gothic Neo','Noto Sans KR',sans-serif;max-width:1100px;margin:24px auto;padding:0 16px;color:#1d1d1f;background:#fff;line-height:1.5}
table{border-collapse:collapse;margin:8px 0 16px;font-size:13px;width:100%}th,td{border:1px solid #ddd;padding:4px 8px;text-align:left;vertical-align:top}
th{background:#f4f5f7}h1{font-size:22px}h2{font-size:18px;margin-top:28px;border-bottom:2px solid #eee;padding-bottom:4px}h3{font-size:15px}
.row{display:flex;align-items:center;gap:8px;margin:4px 0;font-size:13px}.lab{width:220px}.track{flex:1;background:#eef0f3;height:16px;border-radius:3px;overflow:hidden}
.fill{display:block;height:100%;background:#8aa4c8}.fill.hi{background:#2f6fd0}.val{width:52px;text-align:right;font-variant-numeric:tabular-nums}
p.li{margin:2px 0}code{background:#f4f5f7;padding:0 3px;border-radius:3px}"""
    first_h2 = next((k for k, x in enumerate(body) if x.startswith("<h2>")), 1)
    content = body[:first_h2 + 6] + bars + body[first_h2 + 6:]
    return f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>RAG 비교 실험</title><style>{css}</style></head><body>{''.join(content)}</body></html>"


def inline(s: str) -> str:
    s = html.escape(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    return re.sub(r"`(.+?)`", r"<code>\1</code>", s)


if __name__ == "__main__":
    main()
