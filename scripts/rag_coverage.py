"""section 자르기 꼬리표(topics) 전수 커버리지 — 정답셋 없이 원문 내용으로 (2026-10-23, 봉인 이후 수정 검증용).

    uv run python scripts/rag_coverage.py --tag before   # → data/rag_eval/runs/coverage_before.json
    uv run python scripts/rag_coverage.py --tag after

검사 4개 (rag_chunks method='section'의 topics 배열 기준, 없으면 topic):
- T2: EXE_REG에 주기(매 N개월 · 매 N개월 N일 · 매월 · 매 분기)와 "조정일"이 함께 있는 CB 중, 그 문구를 담은 T2 조각이 있는 비율
- T3: 원문(FREE_TEXT · OPT_FCT)에 매도청구권 / Call이 있는 CB 중 T3 조각이 있는 비율
- T4: 원문에 조기상환청구권 / Put이 있는 CB 중 T4 조각이 있는 비율
- T1: 주주배정 공시 중 T1 조각이 있는 비율
T3 · T4는 "조항이 없다고 밝힌 글"(낱말이 나올 때마다 바로 뒤에 없다 · 해당사항 없음)을 분모에서 빼고 따로 센다.
빠진 공시는 건수와 예시 5개 (rcept_no · 원문 앞 100자 · 낱말 근처 글). 꼬리표 통계(주제별 조각 수 · 2개 이상)도 함께.
"""

import argparse
import json
import re
from collections import Counter, defaultdict

from filingscope.config import DATA_DIR
from filingscope.db import connect

SRC = DATA_DIR / "text_dump" / "free_text_2978.jsonl"
OUT = DATA_DIR / "rag_eval" / "runs"
CYCLE = r"매\s*\d+\s*개월(?:\s*\d+\s*일)?|매\s*월|매\s*분기"
T2_PHRASE = re.compile(rf"(?:{CYCLE})[^.。]{{0,60}}?조정\s*일|조정\s*일[^.。]{{0,60}}?(?:{CYCLE})")
CALL = re.compile(r"매도\s*청구권|call", re.I)
PUT = re.compile(r"조기\s*상환\s*청구권|put", re.I)
ABSENT = re.compile(r"[^.\n]{0,60}?(?:조항은?\s*없|해당\s*사항\s*없|해당\s*없|없습니다|없음|부여하지\s*않|미부여)")


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def around(text: str, m) -> str:
    return text[max(0, m.start() - 30): m.end() + 50].replace("\n", " ")


def absent_only(body: str, pat) -> bool:
    """낱말이 나올 때마다 바로 뒤(같은 문장 60자 안)에 '없다'류가 붙음 = 조항이 없다고 밝힌 글."""
    ms = list(pat.finditer(body))
    return bool(ms) and all(ABSENT.match(body, m.end()) for m in ms)


def wrong_tag_examples() -> dict:
    """잘못 붙은 것으로 보이는 꼬리표 두 종류 — 건수와 예시 (서로 다른 공시)."""
    out = {}
    with connect() as conn:
        rows = conn.execute("""SELECT rcept_no, field, topic, topics, text FROM rag_chunks WHERE method = 'section'
                               AND 'T4' = ANY(topics) AND text ~* '중도\\s*상환\\s*청구권\\s*\\(\\s*call' ORDER BY rcept_no, seq""").fetchall()
        ex = []
        for no, f, t, ts, x in rows:
            m = re.search(r"중도\s*상환\s*청구권\s*\(\s*call", x, re.I)
            if no not in {e[0] for e in ex} and len(ex) < 2:
                ex.append((no, f, t, ts, x[max(0, m.start() - 40): m.end() + 60]))
        out["① 콜을 '중도상환청구권(Call Option)'으로 쓴 조각에 T4"] = {"조각 수": len(rows), "예": [dict(zip(("rcept_no", "칸", "처음", "topics", "근처"), (e[0].strip(), *e[1:]))) for e in ex]}
        rows = conn.execute("""SELECT rcept_no, field, topic, topics, text FROM rag_chunks WHERE method = 'section'
                               AND topic IN ('T3', 'T4') AND array_length(topics, 1) >= 2 ORDER BY rcept_no, seq""").fetchall()
        ex, n = [], 0
        for no, f, t, ts, x in rows:
            pat = (r"조기\s*상환\s*청구권?[^.]{0,30}(우선|동시)" if t == "T3" else r"(매도\s*청구권?|콜\s*옵션|call\s*option)[^.]{0,30}(우선|동시)")
            m = re.search(pat, x, re.I)
            if m:
                n += 1
                if no not in {e[0] for e in ex} and len(ex) < 3:
                    ex.append((no, f, t, ts, x[max(0, m.start() - 40): m.end() + 60]))
        out["② 한 옵션 조각 안의 다른 옵션 언급(동시 행사 · 우선)으로 꼬리표"] = {"조각 수": n, "예": [dict(zip(("rcept_no", "칸", "처음", "topics", "근처"), (e[0].strip(), *e[1:]))) for e in ex]}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    a = ap.parse_args()
    texts = defaultdict(lambda: defaultdict(str))
    kind = {}
    for line in SRC.open(encoding="utf-8"):
        r = json.loads(line)
        if r["type"] == "CB" or r["ci_mth"] in (1, 2, 3):
            kind[r["rcept_no"]] = r["type"]
            texts[r["rcept_no"]][r["field"]] += r["text"] + "\n"
    chunks = defaultdict(lambda: defaultdict(list))
    tagstat = Counter()
    with connect() as conn:
        for no, topic, topics, text in conn.execute("SELECT rcept_no, topic, topics, text FROM rag_chunks WHERE method = 'section'"):
            tags = topics or [topic]
            for t in tags:
                chunks[no.strip()][t].append(norm(text))
                tagstat[f"topic {t}"] += 1
            tagstat["조각"] += 1
            tagstat["꼬리표 2개 이상"] += len([t for t in tags if t != "기타"]) >= 2
    res = {"꼬리표": dict(tagstat), "잘못 붙은 예": wrong_tag_examples()}

    def check(name, targets, ok, sample_text, excluded=()):
        miss = [n for n in targets if not ok(n)]
        res[name] = {"대상": len(targets), "있음": len(targets) - len(miss), "비율": round((len(targets) - len(miss)) / max(1, len(targets)), 4),
                     "빠짐": len(miss), "빠진 공시": miss, "조항 없음이라 뺌": len(excluded), "조항 없음 공시": list(excluded),
                     "예시": [{"rcept_no": n, "앞 100자": sample_text(n)[0][:100].replace("\n", " "), "근처": sample_text(n)[1]} for n in miss[:5]]}

    cbs = sorted(n for n in kind if kind[n] == "CB")
    t2 = {}
    for n in cbs:
        m = T2_PHRASE.search(texts[n]["EXE_REG"])
        if m:
            t2[n] = m
    check("T2 (EXE_REG 주기 + 조정일 문구가 T2 조각에)", sorted(t2),
          lambda n: any(norm(t2[n].group(0)) in c for c in chunks[n]["T2"]),
          lambda n: (texts[n]["EXE_REG"], around(texts[n]["EXE_REG"], t2[n])))
    for name, pat, topic in (("T3 (매도청구권 / Call)", CALL, "T3"), ("T4 (조기상환청구권 / Put)", PUT, "T4")):
        tg, absent = {}, []
        for n in cbs:
            body = texts[n]["FREE_TEXT"] + texts[n]["OPT_FCT"]
            m = pat.search(body)
            if not m:
                continue
            if absent_only(body, pat):
                absent.append(n)
                continue
            tg[n] = (body, m)
        check(name, sorted(tg), lambda n, t=topic: bool(chunks[n][t]), lambda n, tg=tg: (tg[n][0], around(*tg[n])), absent)
    rights = sorted(n for n in kind if kind[n] == "유상증자")
    check("T1 (주주배정)", rights, lambda n: bool(chunks[n]["T1"]), lambda n: (texts[n]["FREE_TEXT"], ""))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"coverage_{a.tag}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print("꼬리표:", res["꼬리표"])
    for k, v in res.items():
        if k in ("꼬리표", "잘못 붙은 예"):
            continue
        print(f"{k}: {v['있음']}/{v['대상']} ({v['비율'] * 100:.1f}%) 빠짐 {v['빠짐']} · 조항 없음이라 뺌 {v['조항 없음이라 뺌']}")
        for e in v["예시"]:
            print(f"    {e['rcept_no']} | {e['앞 100자'][:70]} | …{e['근처'][:90]}…")


if __name__ == "__main__":
    main()
