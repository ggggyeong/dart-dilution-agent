"""QD answer_alt (학습만): 같은 공시 안에 같은 사실을 다른 말로 적은 문구(원문 그대로) → data/eval_v2/answer_alt.json.

    uv run python scripts/make_answer_alt.py

2026-10-24 마지막 단계 B-1. 본 실행에서 맞는 답이 정답 문구와 다른 구절을 인용해 answer 실패한 두 종류를 규칙으로:
- 비율: answer_norm이 "초과청약비율:배정신주1주당X주"면 같은 공시의 "초과청약 비율(X×100%)" 문구 (계산식이 있으면 계산식 줄째)
  — QD-T1-08-I 딥노이드 "초과청약 한도주식수 = 신주인수권증서청약 한도주식수 X 초과청약 비율(20%)"
- 날짜: answer_norm에 날짜(YYYY년MM월DD일)가 2개 이상이고, 같은 공시의 다른 곳에 그 날짜들이 '발행일로부터 … 매 N개월'과 함께
  다른 문장으로 다시 나오면 그 구간
  — QD-T3-14-I 프리시젼바이오 "본 사채의 발행일로부터 3개월이 되는 2024년 05월 08일부터 24개월이 되는 2026년 02월 08일까지 매3개월에 해당되는 날까지"
원문(rag_chunks section 조각)에서 그대로 잘라 오고, 정답 문구와 같은 구절(겹침 60% 이상)이면 뺀다. 질문지 생성기가 이 파일을 읽어 학습 QD expected.answer_alt에 넣는다.
"""

import difflib
import json
import re

from filingscope.config import DATA_DIR
from filingscope.db import connect

EVAL = DATA_DIR / "eval_v2"
GOLD = DATA_DIR / "rag_eval" / "gold_train.jsonl"
OUT = EVAL / "answer_alt.json"
SAME = 0.6


def qn(x: str) -> str:
    return re.sub(r"\s+", "", re.sub(r"[\"“”＂'‘’「」\\]", "", x or ""))


def overlap(a: str, b: str) -> float:
    a, b = qn(a), qn(b)
    m = difflib.SequenceMatcher(None, a, b, autojunk=False).find_longest_match(0, len(a), 0, len(b))
    return m.size / min(len(a), len(b)) if a and b else 0.0


def date_rx(y, m, d):
    return rf"{y}\s*[년.\-]\s*0?{int(m)}\s*[월.\-]\s*0?{int(d)}\s*일?"


def ratio_alts(norm: str, text: str) -> list[str]:
    m = re.search(r"1주당([\d.]+)주", norm)
    if not m or "초과청약" not in norm:
        return []
    pct = float(m.group(1)) * 100
    p = f"{pct:g}"
    formula = re.search(rf"초과청약\s*한도\s*주식\s*수\s*=\s*[^=()]{{0,60}}?초과청약\s*비율\s*\(\s*{p}\s*%\s*\)", text)
    if formula:
        return [formula.group()]
    short = re.search(rf"초과청약\s*비율\s*\(\s*{p}\s*%\s*\)", text)
    return [short.group()] if short else []


def date_alts(norm: str, text: str) -> list[str]:
    dates = re.findall(r"(\d{4})년(\d{1,2})월(\d{1,2})일", norm)
    if len(dates) < 2:   # 기간(시작 · 끝 날짜)이 있는 정답만 — 날짜 하나는 다른 조항(표 · 다른 권리)과 섞이기 쉬움
        return []
    out = []
    first = re.compile(date_rx(*dates[0]))
    for m in first.finditer(text):
        end = m.end()
        ok = True
        for d in dates[1:]:   # 나머지 날짜가 200자 안에 이어서 나와야 같은 사실
            n = re.compile(date_rx(*d)).search(text, end, end + 200)
            if not n:
                ok = False
                break
            end = n.end()
        if not ok:
            continue
        start = max(0, m.start() - 40)
        while start > 0 and not text[start - 1].isspace():
            start += 1
        tail = re.match(r"[^.\n]{0,30}?(?:해당되는 날|해당하는 날|되는 날)", text[end:end + 40])
        stop = end + (tail.end() if tail else 0)
        seg = text[start:stop].strip()
        if not (re.search(r"발행일\s*로부터", seg) and re.search(r"매\s*\d+\s*개월", seg)):   # 같은 사실 = 기간 정의 + 주기가 함께
            continue
        if overlap(seg, norm) < SAME and all(overlap(seg, x) < SAME for x in out):
            out.append(seg)
    return out


def main():
    gold = [g for g in map(json.loads, GOLD.read_text(encoding="utf-8").splitlines()) if g.get("answer_norm")]
    alts = {}
    with connect() as conn:
        for g in gold:
            texts = [r[0] for no in g["accepted_rcept_nos"] for r in conn.execute(
                "SELECT text FROM rag_chunks WHERE rcept_no = %s AND method = 'section' ORDER BY seq", (no,))]
            found = []
            for t in texts:
                for a in ratio_alts(g["answer_norm"], t) + date_alts(g["answer_norm"], t):
                    if overlap(a, g["answer_norm"]) < SAME and all(overlap(a, x) < SAME for x in found):
                        found.append(a)
            if found:
                alts[f"QD-{g['qid']}"] = found
    OUT.write_text(json.dumps(alts, ensure_ascii=False, indent=1), encoding="utf-8")
    for k, v in alts.items():
        print(k, v)
    print(f"answer_alt {len(alts)}문제 → {OUT}")


if __name__ == "__main__":
    main()
