"""평가 질문지 v2 (docs/cc_task_eval_v2_prep.md B). archive/2026-10-23_rag_v1/scripts/make_eval_questions.py를 복사해 고침 (원본 그대로).

- Q1~Q7: 예전 틀 · 개수 · 정답 뽑는 방식(MCP 도구 함수, LLM 없음) 그대로. 옛 RAG 파일을 읽던 Q8 · Q9는 삭제.
  AS_OF 2026-10-19 그대로, SEED 2026 (예전 42와 다른 회사), 정답은 지금 DB로 (3단계 수정 반영).
- QD 조항 144: data/rag_eval/gold_train.jsonl 100 + gold_trap.jsonl 14 → 학습, gold_sealed.jsonl 30 → 봉인. 질문 글 그대로.
- QE flag: as_of 기준 살아 있는 공시 중 flag(preferred_only · price_pending · new_major_holder · rehab · foreign_currency) 켜진 것 각 4 (없으면 있는 만큼).
- 학습 / 봉인 = 회사 단위 70 : 30 (QD 나눔을 따르고, 그 회사들은 Q1~Q7 · QE에서도 같은 쪽).
LLM · agent 없음.

    uv run python scripts/make_eval_questions_v2.py

결과: data/eval_v2/questions_train.json · questions_sealed.json · questions.md · companies.json · user_questions.jsonl(빈 자리)
"""

import hashlib
import json
import random
import re
from collections import Counter

from filingscope.config import DATA_DIR, PROJECT_ROOT
from filingscope.db import connect
from filingscope.mcp import tools

AS_OF = "2026-10-19"
SEED = 2026   # v2: 예전 42와 다른 회사가 뽑히게
OUT = DATA_DIR / "eval_v2"
EVAL_RAG = DATA_DIR / "rag_eval"
B_NAMES = ["엔투텍", "제일엠앤에스", "STX", "츌립앤사이언스", "뷰노", "뉴온", "코이즈", "헝셩그룹", "코다코", "아스트", "카프로", "디모아"]
B_NOTE = {"엔투텍": "같은 날 CB", "제일엠앤에스": "감자", "STX": "감자", "츌립앤사이언스": "shares_before NULL (구 압타머사이언스)",
          "뷰노": "주주배정", "뉴온": "3자 최대주주", "코이즈": "신호 0", "헝셩그룹": "해외 · 감자", "코다코": "대상자 10명",
          "아스트": "빚→주식", "카프로": "섞임", "디모아": "같은 날 CB로 50% 넘김"}
C_AMBIGUOUS = ["삼성", "현대", "한화"]
C_NOT_FOUND = ["없는회사", "애플", "구글코리아"]
YEARS = ["2023", "2024", "2025", "2026"]
REPEAT_WINDOW_ONLY = {"퀀타매트릭스", "HC홈센타", "플레이위드", "샤페론"}  # "최근 2년" 질문 회사만 근거를 2년 안 공시로 (사용자: 3-1 A(2) Q5-001, v3 B Q5-013 · 019 · 025)
Q3_NULL_EXTRA = "피노"  # 풀 A · B에 '가장 최근 진행 중 공시의 희석률 NULL' 회사가 없어 풀 밖에서 1곳 (사용자, Q3 마지막 자리)


def rng(name: str) -> random.Random:
    return random.Random(f"{SEED}:{name}")


# ---------- 회사 풀 ----------

def load_companies(conn) -> dict:
    """진행 중 공시가 있는 회사 전부 (corp_code → {name, d, kind})."""
    out = {}
    for (corp,) in conn.execute("SELECT DISTINCT corp_code FROM events ORDER BY 1"):
        corp = corp.strip()
        d = tools.get_company_dilution(corp, AS_OF, conn=conn)
        types = {e["type"] for e in d["live_events"]}
        kind = "둘 다" if len(types) == 2 else ("유상증자만" if types == {"유상증자"} else ("CB만" if types == {"CB"} else "없음"))
        out[corp] = {"corp_code": corp, "name": d["corp_name"], "d": d, "kind": kind}
    return out


def pool_a(all_c: dict) -> list[dict]:
    live = [c for c in all_c.values() if c["kind"] != "없음"]
    r = rng("poolA")
    pick = []
    for k in ("유상증자만", "CB만", "둘 다"):
        group = sorted((c for c in live if c["kind"] == k), key=lambda c: c["corp_code"])
        pick += r.sample(group, min(10, len(group)))   # v2: 지금 DB에서 묶음이 10곳보다 작을 수 있음
    rest = sorted((c for c in live if c not in pick), key=lambda c: c["corp_code"])
    pick += r.sample(rest, 30)
    return pick


def pool_b(all_c: dict, conn) -> list[dict]:
    out = []
    for name in B_NAMES:
        f = tools.find_company(name, conn)
        assert "corp_code" in f, (name, f)
        out.append({**all_c[f["corp_code"]], "note": B_NOTE[name]})
    return out


# ---------- 도우미 ----------

def item(cat, diff, question, template, company, tools_, numbers, rcept_nos, source, **extra):
    exp = {"tools": tools_, "numbers": numbers, "rcept_nos": rcept_nos, "refuse": extra.pop("refuse", None)}
    exp.update(extra)
    return {"category": cat, "difficulty": diff, "question": question, "template": template, "company": company,
            "expected": exp, "source": source, "paraphrased": False}


def dil_source(c, conn):
    args = {"company": c["name"], "as_of": AS_OF}
    return args, tools.get_company_dilution(c["name"], AS_OF, conn=conn)


def same_day_nos(d) -> list[str]:
    base = d["dilution"]["basis_rcept_no"] if d["dilution"] else None
    if not base:
        return []
    bdt = next(e["rcept_dt"] for e in d["live_events"] if e["rcept_no"] == base)
    return [e["rcept_no"] for e in d["live_events"] if e["type"] == "CB" and e["rcept_dt"] == bdt and e["rcept_no"] != base]


def null_reason(d) -> tuple[str, str]:
    if not d["live_events"]:
        return "진행 중 공시 없음", "진행 중인 공시가 없"
    if d["flags"]["capital_reduction"]:
        return "감자", "감자"
    return "미기재", "미기재"


def uniq(cos):
    """corp_code로 중복 제거 (풀 A와 B에 같은 회사가 있을 수 있음)."""
    seen, out = set(), []
    for c in cos:
        if c["corp_code"] not in seen:
            seen.add(c["corp_code"])
            out.append(c)
    return out


def cycle(templates, i):
    return templates[i % len(templates)]


# ---------- Q1 dilution (30) ----------

T1 = {"T1-1": "{c} 희석 얼마나 돼?", "T1-2": "{c} 주식 얼마나 늘어날 수 있어?", "T1-3": "{c} 오버행 얼마야?", "T1-4": "{c} 최대 희석률 알려줘"}


def q1(A, B, conn):
    bset = {b["corp_code"] for b in B}
    cos = B + rng("q1").sample([c for c in A if c["corp_code"] not in bset], 18)
    out = []
    for i, c in enumerate(cos):
        t = cycle(list(T1), i)
        args, raw = dil_source(c, conn)
        d = raw
        mp = d["dilution"]["max_pct"] if d["dilution"] else None
        extra = {}
        if mp is None:
            reason, alt = null_reason(d)
            numbers, extra = [], {"numbers_alt": [alt], "null_reason": reason}
        else:
            numbers = [mp]
        nos = ([d["dilution"]["basis_rcept_no"]] if d["dilution"] and d["dilution"]["basis_rcept_no"] else []) + same_day_nos(d)
        if not nos and d["dilution"]:  # CB 없이 유상증자만이면 살아 있는 유상증자
            nos = [e["rcept_no"] for e in d["live_events"]]
        if mp is None or (d["flags"]["capital_reduction"]):
            diff = "hard"
        elif same_day_nos(d) or len(d["live_events"]) >= 2:
            diff = "medium"
        else:
            diff = "easy"
        out.append(item("dilution", diff, T1[t].format(c=c["name"]), t, c["name"], ["get_company_dilution"], numbers, nos,
                        {"tool": "get_company_dilution", "args": args, "raw": raw}, **extra))
    return out


# ---------- Q2 schedule (30) ----------

T2 = {"T2-1": ("{c} CB 언제부터 전환돼?", ["전환 시작"], "CB"), "T2-2": ("{c} 신주 언제 상장돼?", ["상장"], "유상증자"),
      "T2-3": ("{c} 물량 언제 풀려?", ["전환 시작", "상장"], None), "T2-4": ("{c} CB 만기 언제야?", ["만기"], "CB")}
Q2_PLAN = {"T2-1": 8, "T2-2": 7, "T2-3": 8, "T2-4": 7}


def q2(A, B, conn):
    pool = uniq([c for c in B + A if c["kind"] != "없음"])
    rng("q2").shuffle(pool)
    # 같은 날 CB 회사(엔투텍 · 디모아 등)를 T2-1 · T2-4에 먼저 넣어 hard가 생기게
    pool.sort(key=lambda c: not same_day_nos(c["d"]))
    used, out = set(), []
    for t, n in Q2_PLAN.items():
        q, labels, need = T2[t]
        got = 0
        for c in pool:
            if got == n:
                break
            if c["corp_code"] in used or (need and need not in {e["type"] for e in c["d"]["live_events"]}):
                continue
            args, raw = dil_source(c, conn)
            hits = [s for s in raw["schedule"] if s["label"] in labels]
            if not hits:
                continue
            used.add(c["corp_code"])
            got += 1
            dates = sorted({s["date"] for s in hits})
            sd = bool(same_day_nos(raw)) and t in ("T2-1", "T2-4", "T2-3")
            diff = "hard" if sd else ("medium" if len(dates) >= 2 else "easy")
            out.append(item("schedule", diff, q.format(c=c["name"]), t, c["name"], ["get_company_dilution"], [],
                            sorted({s["rcept_no"] for s in hits}), {"tool": "get_company_dilution", "args": args, "raw": raw},
                            dates=dates))
    return out


# ---------- Q3 compare (30) ----------

def latest_live(d):
    return max(d["live_events"], key=lambda e: (e["rcept_dt"], e["rcept_no"]))


def q3(A, B, cache, conn, all_c):
    pool = uniq([c for c in B + A if c["kind"] != "없음"])
    rng("q3").shuffle(pool)
    pool.sort(key=lambda c: c["name"] != "츌립앤사이언스")  # 희석률 NULL 사례를 꼭 넣음
    extra_c = all_c[tools.find_company(Q3_NULL_EXTRA, conn)["corp_code"]]
    out = []
    for i, c in enumerate(pool[:29] + [extra_c]):
        args, raw = dil_source(c, conn)
        ev = latest_live(raw)
        cmp_ = tools.compare_event(ev["rcept_no"], cache)
        m = tools.metric(ev)[1]
        t = cycle(["T3-1", "T3-2", "T3-3"], i)
        if t == "T3-1" and m is None:
            t = "T3-3"
        q = {"T3-1": f"{c['name']} 희석 {m}%면 심한 거야?",
             "T3-2": f"{c['name']} {'유상증자' if ev['type'] == '유상증자' else 'CB'} 큰 편이야?",
             "T3-3": f"{c['name']} 이번 공시 다른 데랑 비교하면 어때?"}[t]
        extra = {}
        if "error" in cmp_:
            numbers, extra = [], {"numbers_alt": ["비교할 수 없"], "null_reason": cmp_.get("reason")}
        else:
            numbers = [cmp_["percentile"]]
            extra = {"group": cmp_["group"], "must_include_any": [cmp_["group"].split()[0]]}
            if cmp_.get("small_sample"):
                extra["numbers_alt"] = ["참고만"]
        steps = [{"tool": "get_company_dilution", "args": args, "raw": raw},
                 {"tool": "compare_event", "args": {"rcept_no": ev["rcept_no"]}, "raw": cmp_}]
        out.append(item("compare", "hard", q, t, c["name"], ["get_company_dilution", "compare_event"], numbers, [ev["rcept_no"]],
                        {"tool": "compare_event", "args": {"rcept_no": ev["rcept_no"]}, "raw": cmp_, "steps": steps}, **extra))
    return out


# ---------- Q4 refixing (20) ----------

T4 = {"T4-1": "{c} 주가 떨어지면 희석 얼마까지 가?", "T4-2": "{c} CB 리픽싱 되면 최대 몇 %야?", "T4-3": "{c} 리픽싱 바닥이 얼마야?"}


def basis_cb(d):
    """기준 공시가 CB일 때만 (v2: 3단계부터 기준 공시 = 가장 최근 살아 있는 공시라 유상증자일 수 있음 → 리픽싱 질문은 기준이 CB인 회사만)."""
    b = d["dilution"]["basis_rcept_no"] if d["dilution"] else None
    e = next((e for e in d["live_events"] if e["rcept_no"] == b), None)
    return e if e is not None and e["type"] == "CB" else None


def q4(A, B, conn):
    pool = uniq([c for c in B + A if basis_cb(c["d"]) and not c["d"]["flags"]["capital_reduction"]])
    rng("q4").shuffle(pool)
    no_ref = [c for c in pool if basis_cb(c["d"])["values"]["min_price"] is None][:5]
    ref = [c for c in pool if c not in no_ref and basis_cb(c["d"])["values"]["min_price"] is not None]
    ref.sort(key=lambda c: not same_day_nos(c["d"]))  # 같은 날 CB 먼저
    cos = ref[:15] + no_ref
    out = []
    for i, c in enumerate(cos):
        args, raw = dil_source(c, conn)
        cb = basis_cb(raw)
        t = cycle(list(T4), i)
        nos = [cb["rcept_no"]] + same_day_nos(raw)
        extra = {}
        if cb["values"]["min_price"] is None:
            numbers, extra, diff = [], {"numbers_alt": ["리픽싱 없"], "null_reason": "리픽싱 없음"}, "medium"
        else:
            if t == "T4-3":
                numbers, extra = [], {"numbers_any": [cb["values"]["min_price"], cb["calc"]["floor_pct"]]}
            else:
                numbers = [raw["dilution"]["worst_pct"]]
            diff = "hard" if same_day_nos(raw) else "easy"
        out.append(item("refixing", diff, T4[t].format(c=c["name"]), t, c["name"], ["get_company_dilution"], numbers, nos,
                        {"tool": "get_company_dilution", "args": args, "raw": raw}, **extra))
    return out


# ---------- Q5 repeat (25) ----------

T5 = {"T5-1": "{c} 최근 2년 몇 번 조달했어?", "T5-2": "{c} 유상증자 CB 자주 해?", "T5-3": "{c} 조달 이력 보여줘"}


def q5(A, B, conn):
    pool = uniq(B + A)
    r = rng("q5")
    hi = [c for c in pool if c["d"]["company"]["repeat_count"] >= 4]
    lo = [c for c in pool if c["d"]["company"]["repeat_count"] <= 3]
    r.shuffle(hi)
    r.shuffle(lo)
    cos = hi[:12] + lo[:13]
    out = []
    for i, c in enumerate(cos):
        args, raw = dil_source(c, conn)
        co = raw["company"]
        t = cycle(list(T5), i)
        evs = sorted(co["events"], key=lambda e: (e["first_date"], e["rcept_no"]), reverse=True)
        if c["name"] in REPEAT_WINDOW_ONLY:
            y, m, d = AS_OF.split("-")
            since = f"{int(y) - 2}-{m}-{d}"
            evs = [e for e in evs if since <= e["first_date"] <= AS_OF]
        evs = evs[:5]
        out.append(item("repeat", "medium" if co["repeat_count"] >= 4 else "easy", T5[t].format(c=c["name"]), t, c["name"],
                        ["get_company_dilution"], [len(co["events"]) if t == "T5-3" else co["repeat_count"]], [e["rcept_no"] for e in evs],   # T5-3 = 전체 공시 수 (eval_run_spec 3-3)
                        {"tool": "get_company_dilution", "args": args, "raw": raw},
                        tools_any_of=[["get_company_dilution"], ["search_events"]]))
    return out


# ---------- Q6 search (35) ----------

def q6(A, B, cache):
    specs = []
    for y in YEARS:
        specs.append(("T6-1", f"{y} CB 중 리픽싱 있는 거 뭐 있어?", dict(type="CB", year=y, signal="refixing")))
    for y, n in zip(["2023", "2024", "2025", "2026", "2023", "2024", "2025", "2026"], [10, 20, 30, 10, 30, 10, 20, 30]):
        specs.append(("T6-2", f"{y} 유상증자 중 희석 {n}% 넘는 거", dict(type="유상증자", year=y, min_dilution=n)))
    for y in YEARS:
        specs.append(("T6-3", f"{y} 최대주주한테 배정한 유상증자 찾아줘", dict(type="유상증자", year=y, signal="major_holder")))
    for y in YEARS:
        specs.append(("T6-4", f"{y} 공모인데 한 곳이 다 산 CB", dict(type="CB", year=y, signal="public_single")))
    for y in YEARS:
        specs.append(("T6-5", f"{y} 현금 아닌 걸로 납입한 공시", dict(year=y, signal="non_cash")))
    bset = {b["corp_code"] for b in B}
    for c in rng("q6").sample([c for c in A if c["corp_code"] not in bset], 7):
        specs.append(("T6-6", f"{c['name']} 공시 다 보여줘", dict(company=c["name"], corp_code=c["corp_code"])))
    for y in YEARS:
        specs.append(("T6-7", f"{y} 거래정지 중에 조달한 회사", dict(year=y, signal="halted")))
    seen, out = set(), []
    for t, q, cond in specs:
        key = (t, tuple(sorted(cond.items())))
        if key in seen:  # 같은 조건이 겹치면 연도를 바꾸지 않고 건너뜀 → 아래에서 개수 확인
            continue
        seen.add(key)
        args = {k: v for k, v in cond.items() if k not in ("year", "corp_code")}
        if "year" in cond:
            args.update(date_from=f"{cond['year']}-01-01", date_to=f"{cond['year']}-12-31")
        k = len([x for x in cond if x != "corp_code"])   # corp_code는 정답 뽑기용 (난이도에 안 셈)
        diff = "easy" if k == 1 else ("medium" if k == 2 else "hard")
        if t == "T6-6":  # 3-1 A(1): 회사 공시 전부는 두 도구 모두 정답
            # 마무리 A-1 (2026-10-24): 정답은 회사 코드로 정확히 그 회사만 (이름 부분 일치면 휴맥스 ↔ 휴맥스홀딩스가 섞임)
            raw = tools.search_events(cache, **{**args, "company": cond["corp_code"], "limit": 100})
            top = [x["rcept_no"] for x in raw["items"][:5]]
            extra = {"tools_any_of": [["search_events"], ["get_company_dilution"]]}
        else:  # 3-1 A(3): 전체 결과에서 희석률 큰 순 상위 5건 (NULL 제외)
            args["order_by"] = "dilution"
            raw = tools.search_events(cache, **args)
            top = [x["rcept_no"] for x in raw["items"] if x["dilution_pct"] is not None][:5]
            extra = {}
        extra["rcept_nos_min"] = min(3, len(top))
        if raw["n"] == 0:
            extra.update(numbers_alt=["없"], null_reason="결과 0건")
        out.append(item("search", diff, q, t, cond.get("company"), ["search_events"], [raw["n"]], top,
                        {"tool": "search_events", "args": args, "raw": raw}, **extra))
    return out


# ---------- Q7 refuse (30) ----------

AMB_Q = ["{w} 희석 얼마야?", "{w} CB 언제 풀려?", "{w} 자주 조달해?", "{w} 신주 언제 상장돼?", "{w} 리픽싱 바닥 얼마야?"]
NF_Q = ["{w} 희석?", "{w} 유상증자 언제?", "{w} CB 있어?", "{w} 최근 2년 몇 번 조달했어?"]
OOS = [("엔투텍 주가 오를까?", "엔투텍"), ("뷰노 사도 돼?", "뷰노"), ("코이즈 매출 얼마야?", "코이즈"), ("엔투텍 CB 뉴스 있어?", "엔투텍"),
       ("제일엠앤에스 망해?", "제일엠앤에스"), ("디모아 지금 팔아야 해?", "디모아"), ("헝셩그룹 대표가 누구야?", "헝셩그룹"),
       ("코이즈 배당 얼마 줘?", "코이즈"), ("STX 내년 실적 어때?", "STX"), ("뉴온 목표 주가 얼마야?", "뉴온")]


def q7(conn):
    out = []
    for i in range(10):
        w = C_AMBIGUOUS[i % 3]
        raw = tools.get_company_dilution(w, AS_OF, conn=conn)
        out.append(item("refuse", "medium", cycle(AMB_Q, i).format(w=w), "T7-amb", w, ["get_company_dilution"], [], [],
                        {"tool": "get_company_dilution", "args": {"company": w, "as_of": AS_OF}, "raw": raw}, refuse="ambiguous",
                        must_include=["어느"], or_candidates=[c["corp_name"] for c in raw.get("candidates", [])],
                        must_not_include_numbers=True))
    for i in range(10):
        w = C_NOT_FOUND[i % 3]
        raw = tools.get_company_dilution(w, AS_OF, conn=conn)
        out.append(item("refuse", "medium", cycle(NF_Q, i).format(w=w), "T7-nf", w, ["get_company_dilution"], [], [],
                        {"tool": "get_company_dilution", "args": {"company": w, "as_of": AS_OF}, "raw": raw}, refuse="not_found",
                        must_include=["없"], must_not_include_numbers=True))
    for q, w in OOS:
        out.append(item("refuse", "easy", q, "T7-oos", w, [], [], [], {"tool": None, "args": {}, "raw": None},
                        refuse="out_of_scope", must_include=["답할 수 없"]))
    return out


# ---------- 사람 말투 30개 ----------

PARA = {
    "T1-1": "{c} 지분 희석 어느 정도 될까", "T1-2": "{c} 지금 물량 얼마나 남았지", "T1-3": "{c} 잠재 물량 총 얼마나 돼?",
    "T1-4": "{c} 최악이면 몇 % 희석이야?", "T2-1": "{c} 전환 청구 언제부터 가능해?", "T2-2": "{c} 새로 찍은 주식 상장 날짜가 언제지",
    "T2-3": "{c} 오버행 풀리는 날짜 알려줄래", "T2-4": "{c} 사채 만기일이 언제였지?", "T3-1": "{c} 이 정도 희석이면 많은 편이야?",
    "T3-2": "{c} 이번 건 규모 다른 회사들이랑 비교하면?", "T3-3": "{c} 이번 공시 평균보다 센 편이야?",
    "T4-1": "{c} 주가 빠지면 물량 얼마나 더 늘어?", "T4-2": "{c} 전환가 최대로 내려가면 희석 몇 %야", "T4-3": "{c} 전환가 어디까지 내려갈 수 있어?",
    "T5-1": "{c} 요즘 2년 사이 자금 몇 번 끌어왔어?", "T5-2": "{c} 증자나 CB 자주 하는 회사야?", "T5-3": "{c} 그동안 조달한 거 쭉 보여줘",
    "T6-6": "{c} 관련 공시 전부 볼 수 있어?",
}
AMB_PARA = {"삼성": "삼성 요즘 증자 많이 해?", "현대": "현대 CB 뭐 있어?", "한화": "한화 희석 어떻게 돼"}
OOS_PARA = {"엔투텍 주가 오를까?": "엔투텍 앞으로 주가 어떻게 될 것 같아?", "뷰노 사도 돼?": "뷰노 지금 들어가도 괜찮을까?",
            "코이즈 매출 얼마야?": "코이즈 작년 매출이 얼마였지?", "엔투텍 CB 뉴스 있어?": "엔투텍 CB 관련 기사 나온 거 있어?",
            "제일엠앤에스 망해?": "제일엠앤에스 이러다 망하는 거 아니야?", "디모아 지금 팔아야 해?": "디모아 지금이라도 정리하는 게 맞을까?",
            "헝셩그룹 대표가 누구야?": "헝셩그룹 사장이 누구더라?", "코이즈 배당 얼마 줘?": "코이즈 배당은 얼마나 나와?",
            "STX 내년 실적 어때?": "STX 내년 실적 좋아질까?", "뉴온 목표 주가 얼마야?": "뉴온 목표가 어느 정도로 봐?"}


def paraphrase(items):
    r = rng("para")
    by_cat = {}
    for it in items:
        if it["template"] in PARA or it["category"] in ("search", "refuse"):
            by_cat.setdefault(it["category"], []).append(it)
    quota = {"dilution": 5, "schedule": 5, "compare": 4, "refixing": 4, "repeat": 4, "search": 4, "refuse": 4}
    taken = {it["question"] for it in items}
    for cat, n in quota.items():
        cands = by_cat.get(cat, [])
        done = 0
        for it in r.sample(cands, len(cands)):   # v2: 바꾼 글이 이미 있는 글이면 그 문제는 건너뛰고 다음 후보 (개수는 그대로)
            if done == n:
                break
            before = it["question"]
            it["original_question"] = it["question"]
            if it["template"] in PARA:
                it["question"] = PARA[it["template"]].format(c=it["company"])
                if it["template"] == "T1-4":   # 마무리 A-2: "최악이면 몇 %"는 회사 합산 최악 희석률 (원래 틀은 최대 희석률)
                    worst = ((it["source"].get("raw") or {}).get("dilution") or {}).get("worst_pct")
                    it["expected_before_para"] = dict(it["expected"])
                    if worst is not None:
                        it["expected"] = {**it["expected"], "numbers": [worst]}
                    it["answer_basis"] = "worst_pct"
            elif cat == "search":
                it["question"] = it["question"].replace(" 뭐 있어?", " 좀 찾아봐").replace(" 찾아줘", " 있었어?") \
                    .replace("넘는 거", "넘었던 것들").replace("공시", "공시 뭐 있었지").replace("회사", "회사들 알려줘") \
                    .replace("다 산 CB", "다 가져간 CB 있어?")
            else:  # refuse
                it["question"] = {"T7-amb": AMB_PARA.get(it["company"], it["question"]), "T7-nf": f"{it['company']}도 증자한 적 있어?",
                                  "T7-oos": OOS_PARA.get(it["question"], it["question"])}[it["template"]]
            if it["question"] in taken:
                it["question"] = before
                del it["original_question"]
                if "expected_before_para" in it:
                    it["expected"] = it.pop("expected_before_para")
                    it.pop("answer_basis", None)
                continue
            it.pop("expected_before_para", None)
            # 마무리 A-3: 목록형 모호 질문("{이름} CB 뭐 있어 · 찾아줘")은 되묻기 또는 회사 이름을 밝힌 검색 목록 둘 다 정답
            if it["template"] == "T7-amb" and re.search(r"뭐 있어|찾아줘|찾아봐", it["question"]):
                it["expected"] = {**it["expected"], "list_ok": True,
                                  "tools_any_of": [["get_company_dilution"], ["search_events"], ["get_company_dilution", "search_events"]]}
            taken.add(it["question"])
            it["paraphrased"] = True
            done += 1


# ---------- QD 조항 (144) — data/rag_eval 정답셋 질문 글 그대로 ----------

QD_TOPIC = {"T1": "청약절차", "T2": "리픽싱", "T3": "콜", "T4": "풋"}
QD_TOOLS = [["search_events", "search_filing_text"], ["get_company_dilution", "search_filing_text"], ["search_filing_text"]]


def key_numbers(key_fact: str | None) -> list[float]:
    """key_fact에서 괄호 밖 숫자 (없으면 [])."""
    t = re.sub(r"\([^)]*\)|（[^）]*）", " ", key_fact or "")
    out = []
    for m in re.findall(r"\d+(?:\.\d+)?", t):
        x = float(m)
        if x not in out:
            out.append(x)
    return out


def trap_topic(aspect: str) -> str:
    return "청약절차" if "초과청약" in aspect else next(v for k, v in (("풋", "풋"), ("콜", "콜"), ("리픽싱", "리픽싱")) if k in aspect)


ANSWER_ALT = OUT / "answer_alt.json"   # scripts/make_answer_alt.py (학습 QD만, 2026-10-24 마지막 단계 B-1)


def qd(conn) -> list[tuple[str, dict]]:
    """(쪽, item). gold_train · gold_trap → train, gold_sealed → sealed. 학습 QD는 answer_alt.json이 있으면 expected.answer_alt."""
    out = []
    alts = json.loads(ANSWER_ALT.read_text(encoding="utf-8")) if ANSWER_ALT.exists() else {}
    for fname, side in (("gold_train.jsonl", "train"), ("gold_trap.jsonl", "train"), ("gold_sealed.jsonl", "sealed")):
        for g in map(json.loads, (EVAL_RAG / fname).read_text(encoding="utf-8").splitlines()):
            trap = g["expect"] == "없음"
            extra = {"tools_any_of": QD_TOOLS, "rcept_nos_any": True,
                     "topic": trap_topic(g["aspect"]) if trap else QD_TOPIC[g["topic"]]}
            if trap:
                extra.update(refuse="no_clause", must_include=["없"])
                if g.get("answer_norm"):   # X-03: "중도상환을 요구할 수 없다" 문구를 근거로 들어도 정답
                    extra["answer_norm"] = g["answer_norm"]
                numbers = []
            else:
                extra["answer_norm"] = g["answer_norm"]
                if side == "train" and alts.get(f"QD-{g['qid']}"):
                    extra["answer_alt"] = alts[f"QD-{g['qid']}"]
                numbers = key_numbers(g["key_fact"])
            it = item("clause_trap" if trap else "clause", "medium", g["question"], f"QD-{g['topic']}", g["corp_name"],
                      ["search_filing_text"], numbers, list(g["accepted_rcept_nos"]),
                      {"gold": g["qid"], "gold_file": fname, "aspect": g["aspect"], "key_fact": g["key_fact"], "style": g["style"],
                       "doc_type": g["doc_type"]}, **extra)
            it["paraphrased"] = g["style"] == "투자자"
            it["id"] = f"QD-{g['qid']}"
            corp = conn.execute("SELECT corp_code FROM events WHERE event_id = %s", (g["rcept_no"],)).fetchone()[0].strip()
            out.append((side, it, corp))
    return out


# ---------- QE flag (각 4) ----------

QE_FLAGS = {"preferred_only": "보통주 희석률은 계산하지 않았", "price_pending": "예정 발행가 기준",
            "new_major_holder": "이번 발행으로 최대주주가 바뀔 예정",   # 마지막 단계 B-4 (유상증자 · CB 공통 문장)
            "rehab": "회생 절차 중인 회사의 발행",   # fix2 (2026-10-24)
            "foreign_currency": None}   # foreign_currency는 카드 calc.foreign_currency_display 글자 그대로 (2026-10-24, 통화별)
QE_N = 4


def qe(all_c, taken_questions: set) -> tuple[list[dict], dict]:
    out, avail = [], {}
    for flag, phrase in QE_FLAGS.items():
        cands = []
        for c in sorted(all_c.values(), key=lambda c: c["corp_code"]):
            for e in c["d"]["live_events"]:
                if e["flags"].get(flag):
                    cands.append((c, e))
        seen, uniq_c = set(), []
        for c, e in cands:   # 회사당 1건 (가장 최근 공시)
            if c["corp_code"] not in seen:
                seen.add(c["corp_code"])
                uniq_c.append(max(((cc, ee) for cc, ee in cands if cc["corp_code"] == c["corp_code"]), key=lambda x: (x[1]["rcept_dt"], x[1]["rcept_no"])))
        avail[flag] = len(uniq_c)
        rng(f"qe:{flag}").shuffle(uniq_c)
        k = 0
        for c, e in uniq_c:
            if k == QE_N:
                break
            # 마지막 단계 B-3: 그 회사에 같은 종류 살아 있는 공시가 2건 이상이면 질문에 그 공시 날짜 (학습 · 봉인 공통 규칙)
            #   날짜 = 공시 날짜(rcept_dt). 같은 종류 다른 살아 있는 공시와 rcept_dt가 겹치면 접수번호 앞 8자리(접수 날짜)로 구별
            #   (셀리드: 20260921000434 · 20260922000533 둘 다 rcept_dt 2026-09-22)
            same = [x for x in c["d"]["live_events"] if x["type"] == e["type"]]
            when = ""
            if len(same) >= 2:
                clash = any(x["rcept_dt"][:10] == e["rcept_dt"][:10] for x in same if x["rcept_no"] != e["rcept_no"])
                ymd = e["rcept_no"][:8] if clash else e["rcept_dt"][:10].replace("-", "")
                when = f"{int(ymd[:4])}년 {int(ymd[4:6])}월 {int(ymd[6:8])}일 {e['type']} "
            if k % 2 == 0:
                q, tools_, extra = f"{c['name']} {when}희석 얼마나 돼?", ["get_company_dilution"], {}
            else:
                q, tools_ = f"{c['name']} {when or '이번 ' + e['type'] + ' '}내용 알려줘", ["get_company_dilution", "get_event"]
                extra = {"tools_any_of": [["get_company_dilution"], ["get_company_dilution", "get_event"], ["search_events", "get_event"]]}
            if when:   # 날짜로 공시를 찾는 길도 허용
                extra = {"tools_any_of": [["get_company_dilution"], ["get_company_dilution", "get_event"], ["search_events", "get_event"],
                                          ["search_events"], ["search_events", "get_company_dilution"]]}
            if q in taken_questions:   # Q1과 같은 글이면 다음 회사
                continue
            phrase = QE_FLAGS[flag] or e["calc"]["foreign_currency_display"].strip("()")
            taken_questions.add(q)
            out.append(item("flag", "medium", q, f"QE-{flag}", c["name"], tools_, [], [e["rcept_no"]],
                            {"flag": flag, "rcept_no": e["rcept_no"], "type": e["type"]},
                            numbers_alt=[phrase], must_include=[phrase], flag=flag, **extra))
            k += 1
    return out, avail


# ---------- 학습 / 봉인 (회사 단위 70 : 30) ----------

def company_key(it, conn, cache_keys: dict) -> str:
    if it["category"] == "search" and not it["company"]:
        return f"q:{it['id']}"   # 회사 없는 검색 질문은 질문마다 한 단위
    name = it["company"]
    if name not in cache_keys:
        f = tools.find_company(name, conn)
        cache_keys[name] = f["corp_code"] if "corp_code" in f else f"word:{name}"
    return cache_keys[name]


def split(items_q17, qd_items, qe_items, conn):
    keys = {}
    fixed = {}
    conflicts = set()
    for side, it, corp in qd_items:
        if fixed.get(corp, side) != side:
            conflicts.add(corp)
        fixed.setdefault(corp, side)
    other = sorted({company_key(it, conn, keys) for it in items_q17 + qe_items} - set(fixed))
    real = [k for k in other if not k.startswith(("q:", "word:"))]
    loose = [k for k in other if k.startswith(("q:", "word:"))]
    side_of = dict(fixed)
    r = rng("split")
    r.shuffle(real)
    total_real = len(real) + len(fixed)
    need = max(0, round(total_real * 0.3) - sum(1 for v in fixed.values() if v == "sealed"))
    for i, k in enumerate(real):
        side_of[k] = "sealed" if i < need else "train"
    r2 = rng("split_loose")
    r2.shuffle(loose)
    for i, k in enumerate(loose):
        side_of[k] = "sealed" if i < round(len(loose) * 0.3) else "train"
    train, sealed = [], []
    for it in items_q17 + qe_items:
        k = company_key(it, conn, keys)
        it["split_key"] = k
        (sealed if side_of[k] == "sealed" else train).append(it)
    for side, it, corp in qd_items:
        it["split_key"] = corp
        (sealed if side == "sealed" else train).append(it)
    return train, sealed, side_of, conflicts, total_real


# ---------- 예전 질문지와 비교 (같은 틀 · 같은 회사인데 정답 숫자가 바뀐 것) ----------

OLD = PROJECT_ROOT / "archive" / "2026-10-23_rag_v1" / "eval" / "questions.json"


def compare_old(items) -> list[dict]:
    if not OLD.exists():
        return []
    olds = [q for q in json.loads(OLD.read_text(encoding="utf-8"))["items"] if q.get("company")]
    old = {(q["template"], q["company"]): q for q in olds}
    # 틀이 달라도 정답이 같은 값인 틀끼리 묶어 같은 회사도 비교: Q1 전부(최대 희석률) · Q3 전부(백분위) · Q4 T4-1 · T4-2(최악 희석률) · Q5 T5-1 · T5-2(2년 횟수)
    def same_answer(q):
        t = q["template"]
        return q.get("answer_basis") or {"dilution": "max_pct", "compare": "percentile"}.get(q["category"]) or \
            ("worst_pct" if t in ("T4-1", "T4-2") else "repeat_count" if t in ("T5-1", "T5-2") else None)
    old_same = {(same_answer(q), q["company"]): q for q in olds if same_answer(q)}
    out = []
    for it in items:
        o = old.get((it["template"], it["company"])) or (old_same.get((same_answer(it), it["company"])) if same_answer(it) else None)
        if not o:
            continue
        a, b = o["expected"], it["expected"]
        keys = ("numbers", "numbers_any", "dates", "numbers_alt")
        if any(a.get(k) != b.get(k) for k in keys):
            out.append({"id": it["id"], "template": it["template"], "old_id": o["id"], "old_template": o["template"],
                        "company": it["company"], "question": it["question"],
                        "old": {k: a.get(k) for k in keys if a.get(k)}, "new": {k: b.get(k) for k in keys if b.get(k)},
                        "old_null_reason": a.get("null_reason"), "new_null_reason": b.get("null_reason")})
    return out


# ---------- 기계 검사 ----------

PLAN = {"Q1": 30, "Q2": 30, "Q3": 30, "Q4": 20, "Q5": 25, "Q6": 35, "Q7": 30, "QD": 144}


def checks(train, sealed, qe_plan, conflicts=()) -> dict:
    allq = train + sealed
    bad_null = [it["id"] for it in allq for k in ("numbers", "numbers_any", "dates") if any(x is None for x in it["expected"].get(k) or [])]
    texts = Counter(it["question"] for it in allq)
    dup = [t for t, n in texts.items() if n > 1]
    qd_conflict = set(conflicts)   # 정답셋(QD)이 처음부터 학습 · 봉인에 나눠 둔 회사 — 나눔을 바꾸지 않고 따로 보고
    overlap = sorted(({it["split_key"] for it in train} & {it["split_key"] for it in sealed}) - qd_conflict)
    got = Counter(it["id"].split("-")[0] for it in allq)
    plan = {**PLAN, "QE": qe_plan}
    counts_ok = {k: (got.get(k, 0), v) for k, v in plan.items()}
    res = {"numbers에 NULL": bad_null, "같은 질문 글": dup, "학습/봉인 회사 겹침 (QD 정답셋 충돌 제외)": overlap,
           "QD 정답셋 충돌 회사 (따로 보고)": sorted(qd_conflict),
           "묶음별 개수 (실제, 계획)": counts_ok, "QD 144": got.get("QD", 0) == 144}
    res["pass"] = not bad_null and not dup and not overlap and all(a == b for a, b in counts_ok.values()) and res["QD 144"]
    return res


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    cache = tools.Cache.build()
    with connect() as conn:
        all_c = load_companies(conn)
        A = pool_a(all_c)
        B = pool_b(all_c, conn)
        cats = [("Q1", q1(A, B, conn)), ("Q2", q2(A, B, conn)), ("Q3", q3(A, B, cache, conn, all_c)), ("Q4", q4(A, B, conn)),
                ("Q5", q5(A, B, conn)), ("Q6", q6(A, B, cache)), ("Q7", q7(conn))]
        for c in uniq(B + A):  # 질문 회사명이 도구에서 그 회사 1곳으로 찾아지는지
            assert tools.find_company(c["name"], conn) == {"corp_code": c["corp_code"]}, c["name"]
        items = []
        for prefix, lst in cats:
            for i, it in enumerate(lst, 1):
                items.append({"id": f"{prefix}-{i:03d}", **it})
        paraphrase(items)
        qe_items, avail = qe(all_c, {it["question"] for it in items})
        for i, it in enumerate(qe_items, 1):
            it["id"] = f"QE-{it['source']['flag']}-{i:02d}"
        qd_items = qd(conn)
        train, sealed, side_of, conflicts, total_real = split(items, qd_items, qe_items, conn)
    qe_plan = sum(min(QE_N, n) for n in avail.values())
    chk = checks(train, sealed, qe_plan, conflicts)
    changed = compare_old(items)
    for name, lst in (("train", train), ("sealed", sealed)):
        doc = {"as_of": AS_OF, "seed": SEED, "split": name, "n": len(lst), "items": lst}
        (OUT / f"questions_{name}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    up = OUT / "user_questions.jsonl"
    if not up.exists():
        up.write_text("", encoding="utf-8")   # 직접 쓴 질문(G) 자리 — 지금은 빈 파일
    meta = {"as_of": AS_OF, "seed": SEED, "qe_available": avail, "qe_plan": qe_plan, "checks": chk, "conflicts": sorted(conflicts),
            "companies": {"real_total": total_real, "train": sum(1 for k, v in side_of.items() if v == "train" and not k.startswith(("q:", "word:"))),
                          "sealed": sum(1 for k, v in side_of.items() if v == "sealed" and not k.startswith(("q:", "word:")))},
            "changed_vs_old": changed,
            "sha256": {n: hashlib.sha256(json.dumps(l, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:16]
                       for n, l in (("train", train), ("sealed", sealed))}}
    (OUT / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    groups = ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "QD", "QE"]
    gname = {"Q1": "희석", "Q2": "일정", "Q3": "비교", "Q4": "리픽싱 최악", "Q5": "이력", "Q6": "검색", "Q7": "거절", "QD": "조항", "QE": "flag"}
    cnt = lambda lst, g: sum(1 for it in lst if it["id"].split("-")[0] == g)  # noqa: E731
    L = [f"# 평가 질문지 v2 (as_of {AS_OF}, seed {SEED})", "", "| 묶음 | 학습 | 봉인 | 합 |", "|---|---|---|---|"]
    L += [f"| {g} {gname[g]} | {cnt(train, g)} | {cnt(sealed, g)} | {cnt(train, g) + cnt(sealed, g)} |" for g in groups]
    L += [f"| 합 | {len(train)} | {len(sealed)} | {len(train) + len(sealed)} |", "",
          f"- 회사(실제 회사) {meta['companies']['real_total']}곳 → 학습 {meta['companies']['train']} · 봉인 {meta['companies']['sealed']}. QD 나눔과 충돌 {len(conflicts)}",
          f"- QE 후보(살아 있는 공시에 flag 켜진 회사 수): {avail} → 계획 {qe_plan}",
          f"- 기계 검사 통과: {chk['pass']}", ""]
    for g in groups:
        for side, lst in (("학습", train), ("봉인", sealed)):
            ex = [it for it in lst if it["id"].split("-")[0] == g][:2]
            for it in ex:
                e = it["expected"]
                ans = e.get("numbers") or e.get("numbers_any") or e.get("dates") or e.get("numbers_alt") or e.get("refuse") or ""
                L.append(f"- [{g} {side}] {it['id']} · {it['question']} → {ans}" + (f" · topic {e['topic']}" if e.get("topic") else ""))
    (OUT / "questions.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print("기계 검사:", json.dumps({k: v for k, v in chk.items()}, ensure_ascii=False))
    assert chk["pass"], chk


if __name__ == "__main__":
    main()
