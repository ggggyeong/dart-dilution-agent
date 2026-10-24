"""CB 코드 칸 검증: 원문 전체를 Opus가 사람처럼 칸별로 읽고 DB(cb_values · allottees)와 비교. DB는 바꾸지 않음.

    uv run python scripts/validate_cb_fields.py --estimate
    uv run python scripts/validate_cb_fields.py --run       # 읽기 (이미 한 것은 건너뜀) + 보고

표본 30건: 사모 24 (2023~2026 연도별 6) · 공모 3 (전부) · 해외 3 (회사 3곳에서 1건씩), 시드 42.
Opus에는 ACODE가 아니라 원문에 적힌 이름표("2. 사채의 권면(전자등록)총액" 등)로 칸을 설명한다.
old_cb_shares는 소계(A)와 개별 줄을 원문대로 읽게 하고, 비교할 때 DB와 같은 규칙(소계, '-'면 개별 줄 합, 다 비면 0)을 적용한다.
date_flag는 Opus가 읽은 날짜로 같은 함수(parse/cb.py date_flag)로 계산해 비교한다.
규칙 현금(rule:pym_det) 공시는 납입자산 표(TBL_PYM) · 자유 서술의 출자전환 · 상계 · 현물 · 대용 단어를 코드로 센다.
결과: data/reports/cb_field_check/ (opus_reads.jsonl, report.md)
"""

import argparse
import json
import random
import re
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from decimal import Decimal
from typing import Literal, Optional

import anthropic
from pydantic import BaseModel, Field

from filingscope.config import DATA_DIR, get_settings
from filingscope.db import connect
from filingscope.llm import answer_key as ak
from filingscope.parse.acode import read_document, read_root
from filingscope.parse.cb import FUNDS, date_flag

OUT = DATA_DIR / "reports" / "cb_field_check"
READS = OUT / "opus_reads.jsonl"
RAW = DATA_DIR / "raw" / "document"
MODEL, EFFORT = "claude-opus-5-5", "high"
SEED = 42
WORDS = re.compile(r"출자전환|상계|현물|대용")

SYSTEM = """너는 한국 DART 전환사채(CB) 발행결정 공시(주요사항보고서) 원문을 읽고, 요청한 칸의 값을 원문에 적힌 그대로 옮긴다.
- 계산하거나 추측하지 않는다. 원문에 적힌 값만.
- 숫자는 쉼표 없이 숫자로 (예: "1,000,000,000" → 1000000000). 소수는 그대로 (예: "4.5" → 4.5).
- 날짜는 YYYY-MM-DD.
- 원문에 "-"이거나 비어 있으면 null. 단, 자금조달의 목적 6칸은 "-"이면 0.
- 글(text) 칸은 원문 글자 그대로 복사한다.

칸 설명 (원문의 이름표 기준):
- amount: "2. 사채의 권면(전자등록)총액 (원)"
- conv_price: 전환에 관한 사항의 "전환가액 (원/주)"
- conv_shares: 전환에 따라 발행할 주식의 "주식수"
- shares_before: 부속 표 【미상환 주권 관련 사채권에 관한 사항】의 "기발행주식 총수(주) (C)"
- min_price: "최저 조정가액 (원)"
- old_cb_subtotal: 【미상환 주권 관련 사채권에 관한 사항】 표의 "소계" 줄 전환(행사)가능주식수 (A) — 원문에 적힌 소계 그대로
- old_cb_rows: 같은 표에서 소계 위의 기발행 미상환 사채 줄마다 전환(행사)가능주식수 (원문 순서대로, "-"는 null). 신규 발행 사채권 줄은 넣지 않는다.
- fund_facility · fund_business_acq · fund_operating · fund_debt · fund_other_corp · fund_other: "4. 자금조달의 목적"의 시설자금 · 영업양수자금 · 운영자금 · 채무상환자금 · 타법인 증권 취득자금 · 기타자금 (원)
- issue_method: "1. 사채의 종류"의 종류에 "해외"가 있거나 해외발행 칸의 "권면(전자등록)총액(통화단위)" 금액이 있으면 "해외", 아니면 "8. 사채발행방법"의 값 (사모 / 공모)
- exempt_reason: "제출을 면제받은 경우 그 사유" 글
- conv_start · conv_end: 전환청구기간의 시작일 · 종료일
- maturity: "5. 사채만기일"
- pay_date: "납입일"
- subscription_date: "청약일"
- coupon: "표면이자율 (%)", ytm: "만기이자율 (%)"
- allottees: 부속 표 【특정인에 대한 대상자별 사채발행내역】의 대상자마다 이름(발행 대상자명) · 회사 또는 최대주주와의 관계 · 발행권면(전자등록)총액(원). 합계 줄은 넣지 않는다."""


class Allottee(BaseModel):
    name: str
    relation: Optional[str]
    amount: Optional[int]


class CbRead(BaseModel):
    amount: Optional[int]
    conv_price: Optional[float]
    conv_shares: Optional[int]
    shares_before: Optional[int]
    min_price: Optional[float]
    old_cb_subtotal: Optional[int]
    old_cb_rows: list[Optional[int]]
    fund_facility: int
    fund_business_acq: int
    fund_operating: int
    fund_debt: int
    fund_other_corp: int
    fund_other: int
    issue_method: Literal["사모", "공모", "해외"]
    exempt_reason: Optional[str]
    conv_start: Optional[str]
    conv_end: Optional[str]
    maturity: Optional[str]
    pay_date: Optional[str]
    subscription_date: Optional[str]
    coupon: Optional[float]
    ytm: Optional[float]
    allottees: list[Allottee] = Field(description="합계 줄 제외")


COMPARE = ["amount", "conv_price", "conv_shares", "shares_before", "min_price", "old_cb_shares", *FUNDS,
           "issue_method", "exempt_reason", "conv_start", "conv_end", "maturity", "pay_date", "coupon", "ytm", "date_flag"]


def sample(conn) -> list[dict]:
    rows = conn.execute(
        """SELECT v.rcept_no, e.corp_name, e.first_date, v.issue_method, e.payment_source, d.main_file
           FROM cb_values v JOIN events e ON e.event_id = v.event_id JOIN documents d ON d.rcept_no = v.rcept_no ORDER BY 1""").fetchall()
    items = [dict(rcept_no=r[0], corp_name=r[1], first_date=r[2], issue_method=r[3], payment_source=r[4], path=RAW / r[0] / r[5]) for r in rows]
    rng = random.Random(SEED)
    pick = []
    for y in ("2023", "2024", "2025", "2026"):
        pool = [i for i in items if i["issue_method"] == "사모" and str(i["first_date"])[:4] == y]
        pick += rng.sample(pool, 6)
    pick += [i for i in items if i["issue_method"] == "공모"]
    abroad = [i for i in items if i["issue_method"] == "해외"]
    by_corp = defaultdict(list)
    for i in abroad:
        by_corp[i["corp_name"]].append(i)
    # 회사마다 1건 (처음엔 연도별로 뽑아 셋 다 오가닉티코스메틱이 됨 → 사용자 지시로 회사 3곳)
    chosen = [rng.choice(by_corp[c]) for c in sorted(by_corp)][:3]
    return pick + chosen


def client():
    return anthropic.Anthropic(api_key=get_settings().anthropic_api_key.get_secret_value(), max_retries=3, timeout=300.0)


def read_one(c, it) -> dict:
    text = ak.document_text(it["path"])
    resp = c.beta.messages.parse(model=MODEL, max_tokens=16000, betas=[ak.FALLBACK_BETA], fallbacks="default", system=SYSTEM,
                                 output_config={"effort": EFFORT}, output_format=CbRead,
                                 messages=[{"role": "user", "content": f"<원문>\n{text}\n</원문>"}])
    if resp.stop_reason == "refusal":
        raise RuntimeError("refusal")
    return dict(rcept_no=it["rcept_no"], read=resp.parsed_output.model_dump(),
                input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens)


def estimate(items):
    c = client()
    tin = sum(c.messages.count_tokens(model=MODEL, system=SYSTEM, messages=[{"role": "user", "content": f"<원문>\n{ak.document_text(i['path'])}\n</원문>"}]).input_tokens
              for i in items)
    print(f"{len(items)}건: 입력 {tin:,} → 약 ${(tin * 4 + 1500 * len(items) * 20) / 1e6:.2f} (출력 건당 약 1,500 토큰 가정)")


def run(items):
    OUT.mkdir(parents=True, exist_ok=True)
    done = {}
    if READS.exists():
        done = {j["rcept_no"]: j for j in map(json.loads, READS.read_text(encoding="utf-8").splitlines()) if "read" in j}
    todo = [i for i in items if i["rcept_no"] not in done]
    print(f"읽기: 표본 {len(items)}, 이미 함 {len(done)}, 이번 {len(todo)}")
    c, lock = client(), threading.Lock()
    with READS.open("a", encoding="utf-8") as f, ThreadPoolExecutor(max_workers=6) as pool:
        futs = {pool.submit(read_one, c, i): i for i in todo}
        for fut in as_completed(futs):
            i = futs[fut]
            try:
                row = fut.result()
            except Exception as e:
                row = dict(rcept_no=i["rcept_no"], error=f"{type(e).__name__}: {e}"[:300])
                print(f"  FAIL {i['rcept_no']} {i['corp_name']}: {row['error'][:120]}")
            with lock:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
                f.flush()


def _num(v):
    if v is None or v == "":
        return None
    return Decimal(str(v)).normalize()


def _txt(v):
    return re.sub(r"\s+", "", str(v)) if v not in (None, "") else None


def _d(v):
    try:
        return date.fromisoformat(v) if v else None
    except ValueError:
        return None


def opus_values(r: dict) -> dict:
    sub, rows = r["old_cb_subtotal"], [x for x in r["old_cb_rows"] if x is not None]
    old = sub if sub is not None else (sum(rows) if rows else 0)  # DB와 같은 규칙
    v = {k: r[k] for k in COMPARE if k in r}
    v["old_cb_shares"] = old
    v["date_flag"] = date_flag(_d(r["subscription_date"]), _d(r["pay_date"]), _d(r["conv_start"]), _d(r["maturity"]))
    return v


def same(k, a, b) -> bool:
    if k in ("issue_method", "date_flag"):
        return (a or None) == (b or None)
    if k == "exempt_reason":
        return _txt(a) == _txt(b)
    if k in ("conv_start", "conv_end", "maturity", "pay_date"):
        return (str(a) if a else None) == (b or None)
    return _num(a) == _num(b)


def report(items):
    reads = {j["rcept_no"]: j for j in map(json.loads, READS.read_text(encoding="utf-8").splitlines()) if "read" in j}
    cost = sum(j["input_tokens"] * 4 + j["output_tokens"] * 20 for j in reads.values()) / 1e6  # 누적 (표본에서 빠진 읽기 포함)
    with connect() as conn:
        cols = ", ".join(COMPARE)
        db = {r[0]: dict(zip(COMPARE, r[1:])) for r in conn.execute(f"SELECT rcept_no, {cols} FROM cb_values WHERE rcept_no = ANY(%s)", ([i["rcept_no"] for i in items],))}
        al = defaultdict(list)
        for no, name, rel, amt in conn.execute("SELECT rcept_no, name, relation, amount FROM allottees WHERE rcept_no = ANY(%s) ORDER BY rcept_no, seq", ([i["rcept_no"] for i in items],)):
            al[no].append((name, rel, amt))
    ok, total, bad = Counter(), Counter(), []
    al_ok, al_bad = 0, []
    for it in items:
        j = reads.get(it["rcept_no"])
        if not j:
            continue
        o = opus_values(j["read"])
        for k in COMPARE:
            total[k] += 1
            if same(k, db[it["rcept_no"]][k], o[k]):
                ok[k] += 1
            else:
                bad.append((it, k, db[it["rcept_no"]][k], o[k], j["read"] if k == "old_cb_shares" else None))
        oa = [(_txt(a["name"]), _txt(a["relation"]) if a["relation"] not in (None, "-") else None, a["amount"]) for a in j["read"]["allottees"]]
        da = [(_txt(n), _txt(r) if r else None, int(a) if a is not None else None) for n, r, a in al[it["rcept_no"]]]
        if oa == da:
            al_ok += 1
        else:
            al_bad.append((it, da, oa))
    # 규칙 현금: 납입자산 표 · 단어
    cash = [it for it in items if it["payment_source"] == "rule:pym_det"]
    tbl, word = [], []
    for it in cash:
        if any((g.get("ACLASS") or "").strip() == "TBL_PYM" for g in read_root(it["path"]).iter("TABLE-GROUP")):
            tbl.append(it)
        free = "\n".join(p.text for p in read_document(it["path"]).body_paragraphs())
        hits = [free[max(0, m.start() - 40): m.end() + 40].replace("\n", " ") for m in WORDS.finditer(free)]
        if hits:
            word.append((it, hits))
    n_read = sum(1 for it in items if it["rcept_no"] in reads)  # 표본 안에서 읽은 공시 (표본에서 빠진 예전 읽기는 세지 않음)
    allc = sum(ok.values()), sum(total.values())
    L = [f"# CB 코드 칸 검증 — 표본 {len(items)}건 (Opus {MODEL} {EFFORT}, 원문 전체를 이름표로 읽기)", "",
         f"- 표본: 사모 {sum(i['issue_method'] == '사모' for i in items)} · 공모 {sum(i['issue_method'] == '공모' for i in items)} · 해외 {sum(i['issue_method'] == '해외' for i in items)}, "
         f"연도 {dict(sorted(Counter(str(i['first_date'])[:4] for i in items).items()))}. 읽은 공시 {n_read}, 비용 ${cost:.2f}",
         f"- **전체 칸 일치 {allc[0]}/{allc[1]} ({allc[0] / allc[1] * 100:.1f}%)**, 대상자 표 일치 {al_ok}/{n_read}", "",
         "## 칸별 일치율", "", "| 칸 | 일치 |", "|---|---|"]
    L += [f"| {k} | {ok[k]}/{total[k]} |" for k in COMPARE]
    L += ["", "## 불일치 (공시 · 칸 · DB 값 · 원문 값(Opus 읽기))", ""]
    for it, k, a, b, r in bad:
        extra = f" (원문 소계 {r['old_cb_subtotal']}, 개별 줄 {r['old_cb_rows']})" if r else ""
        L.append(f"- {it['corp_name']} {it['rcept_no']} · {k} · DB `{a}` · 원문 `{b}`{extra}")
    if not bad:
        L.append("- 없음")
    L += ["", "## 대상자 표 불일치", ""]
    for it, da, oa in al_bad:
        L += [f"- {it['corp_name']} {it['rcept_no']}: DB {len(da)}명 / 원문 {len(oa)}명",
              f"  - DB: {da[:6]}", f"  - 원문: {oa[:6]}"]
    if not al_bad:
        L.append("- 없음")
    L += ["", f"## 규칙 현금 {len(cash)}건: 납입자산 표 · 단어", "",
          f"- 납입자산 표(TBL_PYM) 있음: {len(tbl)} {[i['corp_name'] for i in tbl]}",
          f"- 자유 서술에 출자전환 · 상계 · 현물 · 대용: {len(word)}"]
    L += [f"  - {it['corp_name']} {it['rcept_no']}: " + " / ".join(f"…{h}…" for h in hits[:2]) for it, hits in word]
    L += ["", "## 표본", ""] + [f"- {i['rcept_no']} {i['corp_name']} {i['first_date']} {i['issue_method']} {i['payment_source']}" for i in items]
    (OUT / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    for a in ("estimate", "run", "report"):
        g.add_argument(f"--{a}", action="store_true")
    a = ap.parse_args()
    with connect() as conn:
        items = sample(conn)
    print(f"표본 {len(items)}건")
    if a.estimate:
        estimate(items)
    elif a.run:
        run(items)
        report(items)
    else:
        report(items)


if __name__ == "__main__":
    main()
