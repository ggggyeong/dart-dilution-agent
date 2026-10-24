"""평가 채점기 v2 (docs/cc_task_eval_v2_prep.md C; v1 명세 archive/2026-10-23_rag_v1/docs/eval_run_spec.md 1장):
data/eval_v2/questions_{split}.json + runs/{run_id}/results.jsonl → scores.jsonl + summary.md.
(archive/2026-10-23_rag_v1/scripts/score_eval.py를 다시 꺼내 보강. v1 지표 · 원인 코드 · 보강(⑧ C1~C4)은 그대로.)

    uv run python scripts/score_eval.py --run-id my-run                  # 학습 (questions_train.json)
    uv run python scripts/score_eval.py --run-id my-run --split sealed   # 봉인
    uv run python scripts/score_eval.py --selftest                       # 손으로 만든 가짜 결과로 채점기 시험

v2 추가 (QD 조항 · 함정만): topic · quote 지표, 원인 코드 FILING_WRONG · TOPIC_WRONG · NOT_IN_CHUNKS · MISREAD · QUOTE_FABRICATED.
- topic: search_filing_text 호출 중 하나의 topic = expected.topic (함정은 물어본 주제)
- quote: 답 안 따옴표 인용문(“…” · "…")이 그 호출들이 돌려받은 items 글에 정규화(공백 제거 · 따옴표 통일) 후 그대로 있음. 인용문이 없으면 실패.
         함정은 인용문이 없어야 통과 (X-03처럼 expected.answer_norm이 있는 함정은 인용문이 받은 글에 있으면 통과).
- answer (수정 1, 2026-10-24): 답 안 인용문들을 정규화해 이어 붙인 글과 expected.answer_norm의 최장 공통 부분 문자열 길이가
         answer_norm 길이의 60% 이상이면 통과. QD는 number 대신 answer (numbers 칸은 질문지에 남기고 채점에서만 안 씀).
- QD 종합 = tool · evidence(rcept_nos 중 하나) · topic · quote · answer. 함정 종합 = tool · refuse · quote.
- QE flag: number = numbers_alt(flag 문장 핵심 구절) 포함, evidence = 그 공시 접수번호.
- fix1 (2026-10-24, 시험 운행 뒤) 형식 검사 3개 — 종합에도 넣음 (원인 코드는 내용 지표가 다 맞을 때만 붙음):
  · leak (전부): 답에 영문 키 이름(밑줄이 들어간 영문 낱말) 또는 "조각"이 있으면 실패 → LEAK
  · evidence_format (전부): "근거"로 시작하는 줄은 "근거: 접수번호 {14자리}…" 형식이어야 함. 근거 줄이 없으면 통과.
    "근거: 없음" · "근거: 접수번호 없음" 류는 실패 → EVIDENCE_FORMAT
  · trap_short (함정만): 정해진 문장(조항 없음 · 종류 없음 · 원문 없음 · "같은 시기 공시가 N건 더 있어요") + 근거 줄 외 다른 문장이 있으면 실패 → TRAP_VERBOSE
  · 금지어(보조, 종합 밖)는 따옴표 인용문 안을 빼고 본다.
- 마지막 단계 (2026-10-24):
  · 인용 비교: 인용문 · 받은 글 모두 따옴표 문자(" “ ” ' ‘ ’ 「 」)와 \\ 를 지우고 공백 제거 후 비교 (qnorm). 인용은 줄마다 첫 여는 따옴표 ~
    마지막 닫는 따옴표를 먼저 한 덩어리로 보고(원문 안쪽 따옴표 때문에 잘리지 않게), 받은 글에 없으면 따옴표 쌍마다 나눠 본다.
  · 줄임표(… · ... · (…) · (...))가 있는 인용은 줄임표로 나눠 각 조각(8자 이상)이 받은 글에 있으면 통과.
  · answer: answer_norm과 answer_alt(같은 공시 안 같은 사실의 다른 문구) 중 가장 많이 겹치는 것으로.
  · 함정: "없" 포함 + 지어낸 인용 없음이면 통과 (정해진 문장 글자 그대로 아니어도, 원문 근거 인용 허용). trap_short는 보조로만.
  · Q4 리픽싱 없음(null_reason "리픽싱 없음"): "리픽싱 없" 포함이면 통과, % 숫자는 지금 희석률(질문지 source의
    회사 합산 max · worst, 살아 있는 CB의 dilution · worst · total · total_worst)과 같은 값만 허용.
  · 마무리 (2026-10-24): expected.list_ok(목록형 모호 질문)이면 되묻기 대신 search_events 결과의 회사 이름을 밝힌 목록도 거절 통과.
  · flag_misuse (전부, 종합에 넣음 → FLAG_MISUSE): ① "증자로 최대주주"인데 도구 결과에 new_major_holder가 켜진 유상증자가 없음(CB 설명)
    ② "리픽싱"(인용문 밖)인데 답에 쓴 접수번호가 전부 유상증자 ③ "현재 거래정지".

지표 4개 (문제별 0/1, 해당 없으면 None):
- tool      (전부): 호출 도구 이름 집합 = expected.tools 집합 (tools_any_of면 그중 하나). out_of_scope는 호출 0회.
- number    (Q1~Q6 · Q8): expected.numbers 전부가 답에 (±0.1, 쉼표 무시). numbers가 []이면 numbers_alt 중 하나 · numbers_any면 그중 하나.
                     ⑧ C3: null_reason 문제가 numbers_alt로 통과해도 답에 % 숫자가 있으면 실패.
                     numbers와 numbers_alt가 둘 다 있으면 (Q6 결과 0건: [0] / "없") 어느 쪽이든 통과. Q2는 dates 전부 (YYYY-MM-DD · YYYY.MM.DD).
                     숫자를 뽑기 전에 날짜 · 14자리 접수번호는 지운다.
- evidence  (Q1~Q6 · Q8): expected.rcept_nos 전부가 답에. Q6는 rcept_nos_min개 이상.
- refuse    (Q7 · Q9 — expected.refuse가 있는 문제): ambiguous = (must_include "어느" 또는 후보 이름 2개 이상) + 숫자(%·억·주) 없음 /
                  not_found = "없" + 숫자 없음 / out_of_scope = "답할 수 없" + 호출 0회 /
                  no_clause (Q9, rag_spec 7-5) = must_include("없어요") 전부 + 숫자(개월 · %) 없음.
보조 (Q8): field_cited = 근거 줄에 정답 field 이름, quoted = 답에 따옴표 인용 — 종합에 안 들어감.
종합 = 해당 지표 전부 1.

원인 코드: 규칙으로 1차 자동, causes_manual.json({id: [코드, 한 줄]})이 있으면 그것으로 덮는다 (사람 · Claude Code가 읽고 붙인 것).
"""

import argparse
import difflib
import json
import re
from collections import Counter, defaultdict

from filingscope.config import DATA_DIR

EVAL = DATA_DIR / "eval_v2"
_QUOTE = re.compile(r"[“\"]([^”\"\n]{2,}?)[”\"]")


def norm(x: str) -> str:
    return re.sub("[“”＂]", '"', re.sub("[‘’]", "'", re.sub(r"\s+", "", x or "")))


_QCHARS = re.compile(r"[\"“”＂'‘’「」\\]")
_ELLIPSIS = re.compile(r"\((?:…|\.\.\.)\)|…|\.\.\.")
PIECE_MIN = 8


def qnorm(x: str) -> str:
    """인용 비교용: 따옴표 문자 · 역슬래시 · 공백 제거."""
    return re.sub(r"\s+", "", _QCHARS.sub("", x or ""))


def quote_in(q: str, got: str) -> bool:
    """got = qnorm한 받은 글. 줄임표가 있으면 조각(8자 이상)마다, 없으면 통째로."""
    if _ELLIPSIS.search(q):
        pieces = [p for p in (qnorm(x) for x in _ELLIPSIS.split(q)) if len(p) >= PIECE_MIN]
        return bool(pieces) and all(p in got for p in pieces)
    return qnorm(q) in got


def text_calls(r: dict) -> list[dict]:
    return [t for t in r["tool_calls"] if t.get("name") == "search_filing_text"]


def items_text(r: dict) -> str:
    """search_filing_text 호출들이 돌려받은 items 글 전부 (qnorm)."""
    return qnorm("".join(i.get("text", "") for t in text_calls(r) if isinstance(t.get("result"), dict) for i in t["result"].get("items", [])))


ANSWER_MIN = 0.6   # 인용문과 정답 문구의 최장 공통 부분 문자열 / 정답 문구 길이


def answer_overlap(quotes: list[str], answer_norm: str) -> float:
    """인용문들을 qnorm해 이어 붙인 글과 정답 문구(qnorm)의 최장 공통 부분 문자열 길이 ÷ 정답 문구 길이."""
    a, b = qnorm("".join(quotes)), qnorm(answer_norm)
    if not a or not b:
        return 0.0
    m = difflib.SequenceMatcher(None, a, b, autojunk=False).find_longest_match(0, len(a), 0, len(b))
    return m.size / len(b)


def answer_targets(e: dict) -> list[str]:
    return [x for x in [e.get("answer_norm")] + list(e.get("answer_alt") or []) if x]


_DQ = re.compile(r"[\"“”＂「」]")


def quotes_in(ans: str, got: str | None = None) -> list[str]:
    """줄마다 첫 여는 따옴표 ~ 마지막 닫는 따옴표를 한 덩어리로 본다 (원문 안쪽 따옴표 때문에 잘리지 않게).
    got(받은 글)에 있으면 그 덩어리, 없으면 따옴표 쌍마다 나눈 인용들. got이 없으면 따옴표 쌍마다."""
    out = []
    for line in (ans or "").splitlines():
        if got is None:
            out += [q for q in _QUOTE.findall(line) if norm(q)]
            continue
        i = min((line.find(c) for c in '"“「' if c in line), default=-1)
        j = max(line.rfind(c) for c in '"”」')
        if i < 0 or j <= i + 2:
            continue
        whole = line[i + 1:j]
        if quote_in(whole, got):
            out.append(whole)
            continue
        # 통째로 없으면 따옴표 쌍마다 (홀수 번째 조각이 인용, 짝수 번째는 인용 사이 글). 원문 안쪽 따옴표가 있는데 내용을 바꾼 인용은
        # 조각 중 하나가 받은 글에 없어 지어낸 인용으로 잡힌다.
        out += [x for x in _DQ.split(whole)[0::2] if qnorm(x)]
    return out
FORBIDDEN = ["추천", "매수", "매도", "위험하", "안전하", "괜찮"]
# ⑧ C4: 단어 경계 — 앞이 한글이 아닐 때만, 공시 용어 매도청구권 · 매수선택권 · 매수인은 제외
_FORBIDDEN_RE = {w: re.compile(r"(?<![가-힣])" + w + {"매도": r"(?!청구)", "매수": r"(?!선택권|인)"}.get(w, "")) for w in FORBIDDEN}
_PCT = re.compile(r"\d[\d,]*(?:\.\d+)?\s*%")
_DATE = re.compile(r"(\d{4})[-.](\d{1,2})[-.](\d{1,2})")
_RCEPT = re.compile(r"\d{14}")
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
_CLAUSE_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?\s*(?:%|개월)")
_UNIT_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?\s*(?:%|억|주(?!당|요|식|가|주|관|의|기|로|에|는|를|와)|만\s*주)")


_LEAK = re.compile(r"[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]*")
_EVIDENCE_OK = re.compile(r"^근거:\s*접수번호\s*\d{14}")
_TRAP_FIXED = re.compile(r"이 공시(?:\(종류\))?에는 그 조항이 없어요\.?|원문 조항은 주주배정 유상증자와 CB만 찾을 수 있어요\.?|같은 시기 공시가 \d+건 더 있어요\.?")
LIMIT_NOTE = "(도구 호출 한도)"


def strip_quotes(ans: str) -> str:
    return _QUOTE.sub(" ", ans or "")


def leak_words(ans: str) -> list[str]:
    return _LEAK.findall(ans or "") + (["조각"] if "조각" in (ans or "") else [])


def bad_evidence_lines(ans: str) -> list[str]:
    return [l.strip() for l in (ans or "").splitlines() if l.strip().startswith("근거") and not _EVIDENCE_OK.match(l.strip())]


def trap_extra(ans: str) -> str:
    """함정 답에서 근거 줄 · 호출 한도 표시 · 정해진 문장을 뺀 나머지 (빈 글이면 통과)."""
    body = " ".join(l for l in (ans or "").splitlines() if not l.strip().startswith("근거")).replace(LIMIT_NOTE, " ")
    return re.sub(r"[\s.。]+", " ", _TRAP_FIXED.sub(" ", body)).strip()


def tool_cards(r: dict) -> tuple[dict, set]:
    """도구 결과에서 {rcept_no: type}과 new_major_holder가 켜진 공시 종류 집합."""
    types, nmh = {}, set()
    for t in r["tool_calls"]:
        res = t.get("result")
        if not isinstance(res, dict):
            continue
        cards = list(res.get("live_events") or []) + ([res] if res.get("rcept_no") and res.get("type") else [])
        for c in cards:
            types[c["rcept_no"]] = c.get("type")
            if (c.get("flags") or {}).get("new_major_holder"):
                nmh.add(c.get("type"))
        for e in (res.get("company") or {}).get("events", []) + list(res.get("items") or []):
            if e.get("rcept_no") and e.get("type"):
                types.setdefault(e["rcept_no"], e["type"])
    return types, nmh


def flag_misuse(ans: str, r: dict) -> list[str]:
    types, nmh = tool_cards(r)
    out = []
    if "증자로 최대주주" in (ans or "") and "유상증자" not in nmh:
        out.append("CB에 '증자로 최대주주'")
    used = set(_RCEPT.findall(ans or ""))
    if "리픽싱" in strip_quotes(ans) and used and all(types.get(n) == "유상증자" for n in used):
        out.append("유상증자만 있는 답에 '리픽싱'")
    if "현재 거래정지" in (ans or ""):
        out.append("'현재 거래정지'")
    return out


def allowed_now_pcts(q: dict) -> list[float]:
    """Q4 리픽싱 없음: 지금 희석률로 허용하는 값 (질문지 source = get_company_dilution 결과)."""
    raw = (q.get("source") or {}).get("raw") or {}
    vals = [(raw.get("dilution") or {}).get(k) for k in ("max_pct", "worst_pct")]
    for e in raw.get("live_events") or []:
        if e.get("type") == "CB":
            vals += [(e.get("calc") or {}).get(k) for k in ("dilution_pct", "worst_pct", "total_pct", "total_worst_pct")]
    return [float(v) for v in vals if v is not None]


def numbers_in(text: str) -> list[float]:
    t = _RCEPT.sub(" ", _DATE.sub(" ", text))
    out = []
    for m in _NUM.finditer(t):
        try:
            out.append(float(m.group().replace(",", "")))
        except ValueError:
            pass
    return out


def dates_in(text: str) -> set[str]:
    return {f"{y}-{int(m):02d}-{int(d):02d}" for y, m, d in _DATE.findall(text)}


def has_num(nums: list[float], x) -> bool:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return False
    return any(abs(n - x) <= 0.1 for n in nums)


def score_one(q: dict, r: dict) -> dict:
    e, ans = q["expected"], r["answer"] or ""
    called = [t["name"] for t in r["tool_calls"] if t.get("name")]
    cset = set(called)
    s = {"id": q["id"], "category": q["category"], "difficulty": q["difficulty"], "paraphrased": q["paraphrased"]}
    # tool
    if e.get("refuse") == "out_of_scope":
        s["tool"] = int(len(called) == 0)
    elif e.get("tools_any_of"):
        s["tool"] = int(any(cset == set(x) for x in e["tools_any_of"]))
    else:
        s["tool"] = int(cset == set(e["tools"]))
    nums = numbers_in(ans)
    if not e.get("refuse"):
        # number
        if q["category"] == "schedule":
            s["number"] = int(set(e.get("dates", [])) <= dates_in(ans))
        elif e.get("numbers_any"):
            s["number"] = int(any(has_num(nums, x) for x in e["numbers_any"]))
        elif e["numbers"]:
            ok = all(has_num(nums, x) for x in e["numbers"])
            if not ok and e.get("numbers_alt"):
                ok = any(a in ans for a in e["numbers_alt"])
            s["number"] = int(ok)
        elif e.get("null_reason") == "리픽싱 없음":
            # 마지막 단계: "리픽싱 없" + % 숫자는 지금 희석률과 같은 값만
            pcts = [float(x.rstrip("%").replace(",", "").strip()) for x in _PCT.findall(_RCEPT.sub(" ", _DATE.sub(" ", ans)))]
            allowed = allowed_now_pcts(q)
            s["pct_on_null"] = int(any(all(abs(p - a) > 0.1 for a in allowed) for p in pcts))
            s["number"] = int("리픽싱 없" in ans and not s["pct_on_null"])
        else:
            s["number"] = int(any(a in ans for a in e.get("numbers_alt", [])))
            # ⑧ C3: NULL 답(감자 · 진행 중 없음 · 비교 불가)이 numbers_alt로 통과했는데 % 숫자를 말하면 실패
            s["pct_on_null"] = int(bool(s["number"] and e.get("null_reason") and _PCT.search(_RCEPT.sub(" ", _DATE.sub(" ", ans)))))
            if s["pct_on_null"]:
                s["number"] = 0
        # evidence
        want = e["rcept_nos"]
        hit = [n for n in want if n in ans]
        need = e.get("rcept_nos_min", len(want)) if q["category"] == "search" else len(want)
        if e.get("rcept_nos_any"):   # v2 QD: 같은 날 형제 공시 중 하나면 통과
            need = min(1, len(want))
        s["evidence"] = int(len(hit) >= need)
        s["refuse"] = None
    else:
        s["number"] = s["evidence"] = None
        unit_nums = _UNIT_NUM.findall(_RCEPT.sub(" ", ans))
        kind = e["refuse"]
        if kind == "ambiguous":
            cands = sum(1 for c in e.get("or_candidates", []) if c in ans)
            s["refuse"] = int((any(m in ans for m in e.get("must_include", [])) or cands >= 2) and not unit_nums)
            if not s["refuse"] and e.get("list_ok"):   # 마무리 A-3: 목록형 모호 질문은 회사 이름을 밝힌 검색 목록도 정답
                names = {x.get("corp_name") for t in r["tool_calls"] if t.get("name") == "search_events" and isinstance(t.get("result"), dict)
                         for x in t["result"].get("items", []) if x.get("corp_name")}
                s["refuse"] = int(bool(names) and any(n in ans for n in names))
        elif kind == "not_found":
            s["refuse"] = int("없" in ans and not unit_nums)
        elif kind == "no_clause":
            s["refuse"] = int("없" in ans)   # 마지막 단계: "없" + 지어낸 인용 없음(quote)이면 통과
        else:
            s["refuse"] = int("답할 수 없" in ans and len(called) == 0)
    # v2: QD topic · quote
    s["topic"] = s["quote"] = None
    if q["category"] in ("clause", "clause_trap") and e.get("topic"):
        s["topic"] = int(any(t["args"].get("topic") == e["topic"] for t in text_calls(r)))
        got = items_text(r)
        qs_ = quotes_in(ans, got)
        s["quotes"] = qs_
        s["quotes_ok"] = int(bool(qs_) and all(quote_in(x, got) for x in qs_))
        if q["category"] == "clause":
            s["quote"] = s["quotes_ok"]
            s["answer_overlap"] = round(max((answer_overlap(qs_, t) for t in answer_targets(e)), default=0.0), 3)
            s["answer"] = int(s["answer_overlap"] >= ANSWER_MIN)
        else:
            s["quote"] = int(all(quote_in(x, got) for x in qs_))   # 인용 없거나 전부 받은 글에 있음
    s.setdefault("answer", None)
    keys = ("tool", "evidence", "topic", "quote", "answer") if q["category"] == "clause" else \
           ("tool", "refuse", "quote") if q["category"] == "clause_trap" else ("tool", "number", "evidence", "refuse")
    s["core_pass"] = int(all(s[k] for k in keys if s.get(k) is not None))
    # fix1 형식 검사
    s["leak_words"] = leak_words(ans)
    s["leak"] = int(not s["leak_words"])
    s["bad_evidence_lines"] = bad_evidence_lines(ans)
    s["evidence_format"] = int(not s["bad_evidence_lines"])
    s["trap_short"] = None
    if q["category"] == "clause_trap":   # 마지막 단계: 보조 기록만 (종합 밖)
        s["trap_extra"] = trap_extra(ans)
        s["trap_short"] = int(not s["trap_extra"])
    s["flag_misuse_why"] = flag_misuse(ans, r)
    s["flag_misuse"] = int(not s["flag_misuse_why"])
    fmt = [s[k] for k in ("leak", "evidence_format", "flag_misuse") if s[k] is not None]
    s["pass"] = int(s["core_pass"] and all(fmt))
    # 보조 기록
    s["q6_full"] = int(all(n in ans for n in q["expected"]["rcept_nos"])) if q["category"] == "search" else None
    if q["category"] == "clause":
        basis = " ".join(l for l in ans.splitlines() if l.strip().startswith("근거"))
        s["field_cited"] = int(any(f in basis for f in e.get("must_include", [])))
        s["quoted"] = int(bool(re.search(r'["“”「」]', ans)))
    s["turns"], s["n_tools"], s["cost"] = r["turns"], len(called), r["usage"]["cost_usd"]
    s["sentences"] = len([x for x in re.split(r"(?<=[.?!요다])\s+|\n+", ans.strip()) if x.strip() and not x.strip().startswith("근거")])
    s["forbidden"] = [w for w, rx in _FORBIDDEN_RE.items() if rx.search(strip_quotes(ans))]   # fix1: 인용문 안은 뺌
    s["auto_cause"], s["auto_note"] = auto_cause(q, r, s, called)
    return s


def auto_cause(q, r, s, called) -> tuple[str | None, str]:
    """규칙으로 1차 원인. 통과면 None."""
    if s["pass"]:
        return None, ""
    if s.get("core_pass"):   # fix1: 내용은 맞고 형식만 틀림
        if not s["leak"]:
            return "LEAK", f"내부 이름 {s['leak_words'][:5]}"
        if not s["evidence_format"]:
            return "EVIDENCE_FORMAT", f"근거 줄 형식 {s['bad_evidence_lines'][:2]}"
        if not s["flag_misuse"]:
            return "FLAG_MISUSE", f"{s['flag_misuse_why']}"
    e = q["expected"]
    exp = set(e["tools"]) if not e.get("tools_any_of") else set(e["tools_any_of"][0])
    if e.get("refuse") == "out_of_scope" and called:
        return "TOOL_EXTRA", f"out_of_scope인데 {called} 호출"
    if any(t.get("error") for t in r["tool_calls"]) or "도구 호출 한도" in (r["answer"] or ""):
        return "TOOL_ERROR", "도구 오류 또는 호출 한도"
    if q["category"] in ("clause", "clause_trap"):
        return qd_cause(q, r, s, called, exp)
    cmp_results = [t for t in r["tool_calls"] if t["name"] == "compare_event"]
    if len(cmp_results) >= 2 and isinstance(cmp_results[0]["result"], dict) and cmp_results[0]["result"].get("error") == "no_value":
        return "RETRY_AFTER_NULL", f"compare no_value 뒤 다른 공시로 재시도 ({[t['args'].get('rcept_no') for t in cmp_results]})"
    if not s["tool"] and not e.get("refuse"):
        if set(called) < exp or (not called):
            return "TOOL_MISSING", f"기대 {sorted(exp)} · 호출 {called}"
        return "TOOL_WRONG", f"기대 {sorted(exp)} · 호출 {called}"
    if q["category"] == "search":
        args = next((t["args"] for t in r["tool_calls"] if t["name"] == "search_events"), {})
        want = q["source"]["args"]
        diff = {k: (args.get(k), v) for k, v in want.items() if args.get(k) != v}
        if diff and (not s["number"] or not s["evidence"]):
            return "ARG_WRONG", f"search 인자 다름 (호출, 정답): {diff}"
    if q["category"] == "compare" and cmp_results:
        want = q["source"]["args"]["rcept_no"]
        got = cmp_results[-1]["args"].get("rcept_no")
        if got != want and not s["number"]:
            return "ARG_WRONG", f"compare 공시 다름: 호출 {got} · 정답 {want}"
    if e.get("refuse"):
        if not s["tool"]:
            return "TOOL_WRONG", f"기대 {sorted(exp)} · 호출 {called}"
        return "REFUSE_FAIL", f"{e['refuse']} 거절 문구 · 숫자 조건 불충족"
    if not s["number"]:
        if q["category"] == "flag":   # 마지막 단계: QE는 flag 문장이 없을 때 (예전엔 NUM_EXTRA_NULL로 잘못 붙음, 판정은 그대로)
            return "FLAG_PHRASE_MISSING", f"flag 문장 {e.get('must_include')} 없음"
        if e.get("null_reason") == "리픽싱 없음" and not s.get("pct_on_null"):
            return "NUM_MISSING", "'리픽싱 없' 문구 없음 (숫자는 지금 희석률과 같음)"
        if s.get("pct_on_null"):
            return "NUM_EXTRA_NULL", f"NULL({e.get('null_reason')}) 문구는 맞지만 % 숫자를 말함 (⑧ C3)"
        if not e["numbers"] and e.get("numbers_alt") and _UNIT_NUM.findall(r["answer"] or ""):
            return "NUM_EXTRA_NULL", f"NULL({e.get('null_reason')})인데 숫자 말함"
        return "NUM_MISSING", f"정답 {e['numbers'] or e.get('numbers_any') or e.get('dates') or e.get('numbers_alt')} 없음"
    if not s["evidence"]:
        miss = [n for n in e["rcept_nos"] if n not in (r["answer"] or "")]
        return "EVIDENCE_MISSING", f"접수번호 누락 {miss[:5]}"
    if not s["tool"]:
        return "TOOL_WRONG", f"기대 {sorted(exp)} · 호출 {called}"
    return "OTHER", ""


def qd_cause(q, r, s, called, exp) -> tuple[str, str]:
    """v2 QD 조항 · 함정 원인 (순서: 도구 → 공시 → 주제 → 인용 지어내기 → 조각에 답 없음 → 잘못 읽음)."""
    e, calls = q["expected"], text_calls(r)
    if not calls:
        return "TOOL_MISSING", f"search_filing_text 안 부름 · 호출 {called}"
    if not s["tool"]:
        return "TOOL_WRONG", f"기대 {e.get('tools_any_of')} · 호출 {called}"
    if not any(t["args"].get("rcept_no") in e["rcept_nos"] for t in calls):
        return "FILING_WRONG", f"다른 공시를 찾음: {[t['args'].get('rcept_no') for t in calls]} · 정답 {e['rcept_nos']}"
    if not s["topic"]:
        return "TOPIC_WRONG", f"topic {[t['args'].get('topic') for t in calls]} · 정답 {e['topic']}"
    got = items_text(r)
    bad = [x for x in s.get("quotes", []) if not quote_in(x, got)]
    if bad:
        return "QUOTE_FABRICATED", f"받은 조각에 없는 인용문 {bad[:2]}"
    if q["category"] == "clause_trap":
        return ("REFUSE_FAIL", "'없' 문구 없음") if not s["refuse"] else ("OTHER", "")
    if not any(qnorm(t) in got for t in answer_targets(e)):
        return "NOT_IN_CHUNKS", "받은 조각에 정답 문구가 없음"
    return "MISREAD", f"조각에 답이 있는데 틀리게 답함 (answer 겹침 {s.get('answer_overlap')} · evidence {s['evidence']} · quote {s['quote']})"


def pct(rows, k):
    v = [r.get(k) for r in rows if r.get(k) is not None]
    return f"{sum(v) / len(v) * 100:.0f}%" if v else "–"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id")
    ap.add_argument("--split", default="train", choices=["train", "sealed"])
    ap.add_argument("--suffix", default="", help="출력 이름 뒤에 붙임: scores_{suffix}.jsonl · summary_{suffix}.md · causes_manual_{suffix}.json")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    sfx = f"_{a.suffix}" if a.suffix else ""
    run = EVAL / "runs" / a.run_id
    qs = {q["id"]: q for q in json.loads((EVAL / f"questions_{a.split}.json").read_text(encoding="utf-8"))["items"]}
    summarize(a.run_id, a.suffix, sfx, run, qs)


def summarize(run_id, suffix, sfx, run, qs):
    res = {r["id"]: r for r in map(json.loads, (run / "results.jsonl").read_text(encoding="utf-8").splitlines())}
    meta = json.loads((run / "meta.json").read_text(encoding="utf-8")) if (run / "meta.json").exists() else {}
    mpath = run / f"causes_manual{sfx}.json"
    manual = json.loads(mpath.read_text(encoding="utf-8")) if mpath.exists() else {}
    rows = []
    for qid in sorted(qs):
        if qid not in res:
            continue
        s = score_one(qs[qid], res[qid])
        if not s["pass"]:
            code, note = manual.get(qid, (s["auto_cause"], s["auto_note"]))
            s["cause"], s["cause_note"], s["cause_by"] = code, note, "manual" if qid in manual else "auto"
        rows.append(s)
    with (run / f"scores{sfx}.jsonl").open("w", encoding="utf-8") as f:
        for s in rows:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    n = len(rows)
    L = [f"## {run_id}{' (' + suffix + ')' if suffix else ''} · {n}문제 · {meta.get('as_of', '')}", "",
         f"- 모델 {meta.get('model', '')} · 프롬프트 {meta.get('prompt_version', '')} · 실행 {meta.get('seconds', '?')}초", "",
         "| 구분 | n | 도구 | 숫자 | 근거 | 거절 | topic | 인용 | answer | leak | 근거형식 | flag오용없음 | 종합 |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]

    def line(name, g):
        return (f"| {name} | {len(g)} | {pct(g, 'tool')} | {pct(g, 'number')} | {pct(g, 'evidence')} | {pct(g, 'refuse')} | "
                f"{pct(g, 'topic')} | {pct(g, 'quote')} | {pct(g, 'answer')} | {pct(g, 'leak')} | {pct(g, 'evidence_format')} | "
                f"{pct(g, 'flag_misuse')} | {pct(g, 'pass')} |")
    L.append(line("전체", rows))
    for cat, qn in (("dilution", "Q1"), ("schedule", "Q2"), ("compare", "Q3"), ("refixing", "Q4"), ("repeat", "Q5"), ("search", "Q6"), ("refuse", "Q7"),
                    ("clause", "QD 조항"), ("clause_trap", "QD 함정"), ("flag", "QE")):
        L.append(line(f"{qn} {cat}", [r for r in rows if r["category"] == cat]))
    for d in ("easy", "medium", "hard"):
        L.append(line(d, [r for r in rows if r["difficulty"] == d]))
    L.append(line("틀", [r for r in rows if not r["paraphrased"]]))
    L.append(line("말투 변형", [r for r in rows if r["paraphrased"]]))
    q6 = [r for r in rows if r["category"] == "search"]
    L += ["", f"Q6 5/5: {sum(r['q6_full'] for r in q6)}/{len(q6)} · 평균 turns {sum(r['turns'] for r in rows) / n:.1f} · "
              f"평균 도구 {sum(r['n_tools'] for r in rows) / n:.1f}회 · 총 ${sum(r['cost'] for r in rows):.2f} · "
              f"평균 문장 {sum(r['sentences'] for r in rows) / n:.1f} · 금지어 포함 {sum(1 for r in rows if r['forbidden'])}건 "
              f"{[(r['id'], r['forbidden']) for r in rows if r['forbidden']]}"]
    q8 = [r for r in rows if r["category"] == "clause"]
    if q8:
        L.append(f"QD 보조: 따옴표 인용 {sum(r['quoted'] for r in q8)}/{len(q8)}")
    bad = [r for r in rows if not r["pass"]]
    L += ["", f"## 틀린 문제 (n={len(bad)})", "", "| id | 종류 | 원인 코드 | 한 줄 |", "|---|---|---|---|"]
    L += [f"| {r['id']} | {r['category']} | {r['cause']} | {r['cause_note']} |" for r in bad]
    by = defaultdict(list)
    for r in bad:
        by[r["cause"]].append(r["id"])
    L += ["", "## 원인별 집계", "", "| 원인 | 건수 | 예시 id |", "|---|---|---|"]
    L += [f"| {c} | {len(ids)} | {', '.join(ids[:6])}{' …' if len(ids) > 6 else ''} |" for c, ids in sorted(by.items(), key=lambda kv: -len(kv[1]))]
    (run / f"summary{sfx}.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    return rows


def selftest():
    """손으로 만든 질문(QD 조항 · 함정 · QE · Q4) + 가짜 results.jsonl → 채점 → 기대 코드와 같은지. 금지어는 따로 2개."""
    q = {"id": "QD-TEST", "category": "clause", "difficulty": "medium", "paraphrased": False, "question": "가상회사 CB 리픽싱 몇 달마다야?",
         "expected": {"tools": ["search_filing_text"], "tools_any_of": [["search_events", "search_filing_text"], ["get_company_dilution", "search_filing_text"], ["search_filing_text"]],
                      "numbers": [7.0], "rcept_nos": ["99990101000001"], "rcept_nos_any": True, "topic": "리픽싱",
                      "answer_norm": "본사채발행후매7개월이경과한날을전환가액조정일로", "refuse": None}}
    ok_items = [{"rank": 1, "text": "라. 위 가목 내지 다목과는 별도로 본 사채 발행 후 매 7개월이 경과한 날을 전환가액 조정일로 하고"}]
    other_items = [{"rank": 1, "text": "다. 감자 및 주식 병합 등 주식가치 상승사유가 발생하는 경우 상향 조정한다"}]

    trap = {**q, "category": "clause_trap", "question": "가상회사 CB 풋옵션 있어?",
            "expected": {**q["expected"], "topic": "풋", "refuse": "no_clause", "must_include": ["없"], "numbers": [], "answer_norm": None}}

    nested_items = [{"rank": 1, "text": "라. 위 가목 내지 다목과는 별도로 “본 사채” 발행 후 매 7개월이 경과한 날을 전환가액 조정일로 하고"}]

    def res(i, args, items, answer):
        call = {"name": "search_filing_text", "args": args, "result": {"items": items}}
        return {"id": f"QD-TEST-{i}", "answer": answer, "tool_calls": [call], "turns": 2, "usage": {"cost_usd": 0.0}}

    # QE · Q4용: get_company_dilution 결과 1개짜리 가짜
    def card(no, typ, nmh, calc):
        return {"rcept_no": no, "type": typ, "flags": {"new_major_holder": nmh}, "calc": calc}
    cb_card = card("99990101000011", "CB", True, {"dilution_pct": 10.0})
    rights_card = card("99990101000012", "유상증자", True, {"dilution_pct": 10.0})
    q4_card = card("99990101000013", "CB", False, {"dilution_pct": 7.8, "worst_pct": 7.8, "total_pct": 7.8, "total_worst_pct": 7.8})

    def dil(i, c, answer):
        result = {"live_events": [c], "dilution": {"max_pct": c["calc"]["dilution_pct"], "worst_pct": c["calc"]["dilution_pct"]},
                  "company": {"events": [{"rcept_no": c["rcept_no"], "type": c["type"]}]}}
        call = {"name": "get_company_dilution", "args": {"company": "가상회사"}, "result": result}
        return {"id": f"QD-TEST-{i}", "answer": answer, "tool_calls": [call], "turns": 2, "usage": {"cost_usd": 0.0}}

    def qe(c):
        return {"id": "QE-TEST", "category": "flag", "difficulty": "medium", "paraphrased": False, "question": "가상회사 희석 얼마나 돼?",
                "expected": {"tools": ["get_company_dilution"], "numbers": [], "numbers_alt": ["최대주주가 바뀔 예정"],
                             "rcept_nos": [c["rcept_no"]], "refuse": None}}
    qe_cb, qe_rights = qe(cb_card), qe(rights_card)
    q4 = {"id": "Q4-TEST", "category": "refixing", "difficulty": "medium", "paraphrased": False, "question": "가상회사 주가 떨어지면 희석 얼마까지 가?",
          "expected": {"tools": ["get_company_dilution"], "numbers": [], "numbers_alt": ["리픽싱 없"], "null_reason": "리픽싱 없음",
                       "rcept_nos": [q4_card["rcept_no"]], "refuse": None},
          "source": {"raw": {"dilution": {"max_pct": 7.8, "worst_pct": 7.8}, "live_events": [q4_card]}}}
    good = {"rcept_no": "99990101000001", "topic": "리픽싱", "question": q["question"]}
    cases = [
        ("통과", None, res(1, good, ok_items, '"본 사채 발행 후 매 7개월이 경과한 날을 전환가액 조정일로" 해요.\n근거: 접수번호 99990101000001')),
        ("다른 공시", "FILING_WRONG", res(2, {**good, "rcept_no": "99990101000002"}, ok_items, '"매 7개월" 근거: 접수번호 99990101000002')),
        ("다른 주제", "TOPIC_WRONG", res(3, {**good, "topic": "콜"}, ok_items, '"매 7개월이 경과한 날" 근거: 접수번호 99990101000001')),
        ("조각에 답 없음", "NOT_IN_CHUNKS", res(4, good, other_items, '"상향 조정한다" 근거: 접수번호 99990101000001')),
        ("잘못 읽음", "MISREAD", res(5, good, ok_items, '"본 사채 발행 후" 매 6개월이에요. 근거: 접수번호 99990101000001')),
        ("인용 지어내기", "QUOTE_FABRICATED", res(6, good, ok_items, '"매 7개월마다 전환가를 낮춘다" 근거: 접수번호 99990101000001')),
        # 수정 1: answer 지표 (number 대신)
        ("정답 문구 그대로 인용", None, res(7, good, ok_items, '“본 사채 발행 후 매 7개월이 경과한 날을 전환가액 조정일로”라고 적혀 있어요.\n근거: 접수번호 99990101000001')),
        ("절반만 인용", "MISREAD", res(8, good, ok_items, '"본 사채 발행 후 매 7개월이" 근거: 접수번호 99990101000001')),
        ("다른 조항 인용", "MISREAD", res(9, good, ok_items + other_items, '"감자 및 주식 병합 등 주식가치 상승사유가 발생하는 경우 상향 조정한다" 근거: 접수번호 99990101000001')),
        # fix1 형식 검사 (통과 1 · 실패 1씩). 통과 쪽은 1번 · 7번도 leak · evidence_format 통과
        ("leak 통과 (사람 말)", None, res(10, good, ok_items, '리픽싱 바닥가 기준으로 "본 사채 발행 후 매 7개월이 경과한 날을 전환가액 조정일로" 해요.\n근거: 접수번호 99990101000001')),
        ("leak 실패 (키 이름 · 조각)", "LEAK", res(11, good, ok_items, 'min_price 조각에 "본 사채 발행 후 매 7개월이 경과한 날을 전환가액 조정일로"라고 있어요.\n근거: 접수번호 99990101000001')),
        ("근거 형식 통과 (근거 줄 없음)", None, res(12, good, ok_items, '"본 사채 발행 후 매 7개월이 경과한 날을 전환가액 조정일로" 해요 (접수번호 99990101000001).')),
        ("근거 형식 실패 (근거: 없음)", "EVIDENCE_FORMAT", res(13, good, ok_items, '"본 사채 발행 후 매 7개월이 경과한 날을 전환가액 조정일로" 해요 (접수번호 99990101000001).\n근거: 없음')),
        ("함정 짧게 통과", None, res(14, {**good, "topic": "풋"}, other_items, '이 공시에는 그 조항이 없어요.\n근거: 접수번호 99990101000001')),
        # 마지막 단계: 함정은 "없" + 지어낸 인용 없음이면 통과 → 15번 기대값 TRAP_VERBOSE → 통과 (trap_short는 보조로만)
        ("함정 설명 덧붙임 (이제 통과)", None, res(15, {**good, "topic": "풋"}, other_items, '이 공시에는 그 조항이 없어요. 조회된 글에는 상향 조정 조항만 있습니다.\n근거: 접수번호 99990101000001')),
        # A-1 인용 비교: 따옴표 · 역슬래시 지우고 비교 (원문 안쪽 따옴표)
        ("인용 안쪽 따옴표 통과", None, res(16, good, nested_items, '"“본 사채” 발행 후 매 7개월이 경과한 날을 전환가액 조정일로 하고"라고 적혀 있어요.\n근거: 접수번호 99990101000001')),
        ("인용 안쪽 따옴표 · 내용 다름 실패", "QUOTE_FABRICATED", res(17, good, nested_items, '"“본 사채” 발행 후 매 6개월이 경과한 날을 전환가액 조정일로 하고"라고 적혀 있어요.\n근거: 접수번호 99990101000001')),
        # A-2 줄임표: 조각(8자 이상)마다 받은 글에 있으면 통과
        ("줄임표 인용 통과", None, res(18, good, ok_items, '"라. 위 가목 내지 다목과는 별도로 … 본 사채 발행 후 매 7개월이 경과한 날을 전환가액 조정일로" 해요.\n근거: 접수번호 99990101000001')),
        ("줄임표 인용 · 조각 지어냄 실패", "QUOTE_FABRICATED", res(19, good, ok_items, '"본 사채 발행 후 매 7개월이 경과한 날을 … 최초 전환가의 50%까지 낮춘다"\n근거: 접수번호 99990101000001')),
        # A-3 함정: 원문 근거 인용은 허용, 지어낸 인용은 실패
        ("함정 원문 인용 통과", None, res(20, {**good, "topic": "풋"}, other_items, '이 공시에는 풋 조항이 없어요. 원문에는 "감자 및 주식 병합 등 주식가치 상승사유가 발생하는 경우 상향 조정한다"만 있어요.\n근거: 접수번호 99990101000001')),
        ("함정 지어낸 인용 실패", "QUOTE_FABRICATED", res(21, {**good, "topic": "풋"}, other_items, '이 공시에는 풋 조항이 없어요. 원문에는 "사채권자는 조기상환을 청구할 수 없다"라고 있어요.\n근거: 접수번호 99990101000001')),
        # A-4 flag_misuse ① CB에 '증자로 최대주주'
        ("flag: CB '발행으로' 통과", None, dil(22, cb_card, "CB 희석률은 10.0%예요. 이번 발행으로 최대주주가 바뀔 예정이에요.\n근거: 접수번호 99990101000011")),
        ("flag: CB '증자로' 실패", "FLAG_MISUSE", dil(23, cb_card, "CB 희석률은 10.0%예요. 이번 증자로 최대주주가 바뀔 예정이에요.\n근거: 접수번호 99990101000011")),
        # ② 유상증자만 있는 답에 '리픽싱'
        ("flag: 유상증자 리픽싱 없음 통과", None, dil(24, rights_card, "유상증자 희석률은 10.0%예요. 이번 발행으로 최대주주가 바뀔 예정이에요.\n근거: 접수번호 99990101000012")),
        ("flag: 유상증자에 리픽싱 실패", "FLAG_MISUSE", dil(25, rights_card, "유상증자 희석률은 10.0%예요. 리픽싱 없어요. 이번 발행으로 최대주주가 바뀔 예정이에요.\n근거: 접수번호 99990101000012")),
        # ③ '현재 거래정지'
        ("flag: 공시 당시 거래정지 통과", None, dil(26, cb_card, "CB 희석률은 10.0%예요. 이번 발행으로 최대주주가 바뀔 예정이에요. 공시 당시 거래정지 상태였어요.\n근거: 접수번호 99990101000011")),
        ("flag: 현재 거래정지 실패", "FLAG_MISUSE", dil(27, cb_card, "CB 희석률은 10.0%예요. 이번 발행으로 최대주주가 바뀔 예정이에요. 현재 거래정지 중이에요.\n근거: 접수번호 99990101000011")),
        # B-2 Q4 리픽싱 없음: "리픽싱 없" + 지금 희석률과 같은 숫자만
        ("Q4 리픽싱 없음 · 지금 희석률 통과", None, dil(28, q4_card, "리픽싱 없어요. 이 CB 희석률은 7.8%이고 최악일 때도 7.8%예요.\n근거: 접수번호 99990101000013")),
        ("Q4 리픽싱 없음 · 다른 숫자 실패", "NUM_EXTRA_NULL", dil(29, q4_card, "리픽싱 없어요. 주가가 떨어지면 12.0%까지 갈 수 있어요.\n근거: 접수번호 99990101000013")),
    ]
    trap_cases = {14, 15, 20, 21}
    qmap = {22: qe_cb, 23: qe_cb, 24: qe_rights, 25: qe_rights, 26: qe_cb, 27: qe_cb, 28: q4, 29: q4}
    # 금지어 (종합 밖 보조): 인용문 안은 빼고 본다
    forb = {"금지어 통과 (인용문 안)": ('"사채권자는 주식을 매도할 수 있다"고 적혀 있어요.', []),
            "금지어 실패 (인용문 밖)": ("지금 매도하는 게 좋아요.", ["매도"])}
    run = EVAL / "runs" / "selftest"
    run.mkdir(parents=True, exist_ok=True)
    qs = {f"QD-TEST-{i}": {**qmap.get(i, trap if i in trap_cases else q), "id": f"QD-TEST-{i}"} for i in range(1, len(cases) + 1)}
    (run / "results.jsonl").write_text("\n".join(json.dumps(c[2], ensure_ascii=False) for c in cases) + "\n", encoding="utf-8")
    rows = {r["id"]: r for r in summarize("selftest", "", "", run, qs)}
    print(f"\n채점기 시험 (가짜 결과 {len(cases)}줄):")
    bad = 0
    for i, (name, want, _) in enumerate(cases, 1):
        r = rows[f"QD-TEST-{i}"]
        got = None if r["pass"] else r["cause"]
        mark = "OK" if got == want else "어긋남"
        bad += got != want
        print(f"  {i}. {name}: 기대 {want or '통과'} · 채점 {got or '통과'} (tool {r['tool']} evidence {r['evidence']} "
              f"topic {r['topic']} quote {r['quote']} answer {r['answer']} 겹침 {r.get('answer_overlap')} · number {r['number']} 기록만 · "
              f"leak {r['leak']} 근거형식 {r['evidence_format']} flag오용없음 {r['flag_misuse']} 함정짧게(보조) {r['trap_short']}) {mark}")
    for name, (ans, want) in forb.items():
        got = [w for w, rx in _FORBIDDEN_RE.items() if rx.search(strip_quotes(ans))]
        mark = "OK" if got == want else "어긋남"
        bad += got != want
        print(f"  - {name}: 기대 {want} · 채점 {got} {mark}")
    assert bad == 0, f"채점기 시험 {bad}건 어긋남"
    print(f"채점기 시험 통과 {len(cases) + len(forb)}/{len(cases) + len(forb)}")


if __name__ == "__main__":
    main()
