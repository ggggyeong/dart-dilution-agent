"""알림 문장 점검 (4번 축소판 50건) — 3번 수정(cb-v4 · 새 flag) 뒤 알림 문장이 카드와 어긋나지 않는지.

    uv run python scripts/alert_check.py --sample   # 층화 50건 → data/alert_check/sample50.csv (seed 42)
    uv run python scripts/alert_check.py --run      # 서비스 alert() 그대로 (Sonnet) → alerts50.jsonl (이미 만든 것은 건너뜀)
    uv run python scripts/alert_check.py --check    # 기계 검사 → report.md

검사 함수는 src/filingscope/alert/checks.py를 그대로 쓰고(숫자 · 신호 수 · repeat), 지시서에만 있는 검사는 여기에.
"""

import argparse
import csv
import json
import random
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

import anthropic

from filingscope.alert import PRICE, alert
from filingscope.alert.checks import COUNTED, body_lines, counted_signals, number_check, repeat_check, signal_count_check
from filingscope.config import DATA_DIR, get_settings
from filingscope.db import connect
from filingscope.facts import cb_facts, rights_facts

OUT = DATA_DIR / "alert_check"
SAMPLE = OUT / "sample50.csv"
ALERTS = OUT / "alerts50.jsonl"
SEED = 42
COST_CAP = 1.5   # 달러. 넘으면 멈춤 (처음 1.0 → 39건에서 멈춤, 사용자 승인으로 1.5)
KRW = 1400       # 보고용 환율 가정
FORBIDDEN = ["위험", "주의", "추천", "매수", "매도", "사라", "팔아라", "좋다", "나쁘다", "안전", "우려"]
PHRASES = {   # 0번 표 문구 (켜진 flag마다 문장에 있어야 함)
    "preferred_only": "우선주 발행이라 보통주 희석률은 계산하지 않았어요",
    "price_pending": "(예정 발행가 기준",
    "new_major_holder": "이번 발행으로 최대주주가 바뀔 예정이에요",   # 마지막 단계 B-4 (유상증자 · CB 공통)
    "rehab": "회생 절차 중인 회사의 발행이에요",   # 2026-10-24 fix2: 켜진 4건이 전부 현금 CB라 출자전환 문장에서 바꿈
    "foreign_currency": "원화 환산)",   # 2026-10-24: 실제 문구는 카드 calc.foreign_currency_display ("(USD 발행, 원화 환산)" · "(해외 발행)") — checks()에서 그 글자로 봄
    "unit_mismatch": "중간에 주식 수가 크게 바뀌어 합산 희석률은 가장 최근 공시 기준이에요",
}
TITLES = {"유상증자": ["📊 얼마나 큰가", "💰 왜 조달하나", "👤 누가 사나", "🙋 나는 뭘 할 수 있나 · 언제", "🔍 이 회사 괜찮나"],
          "CB": ["📊 얼마나 큰가", "💰 왜 조달하나", "👤 누가 사나", "📅 언제 풀리나", "🔍 이 회사 괜찮나"]}
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿⬀-⯿]")


# ---------- 1. 표본 ----------

def sample():
    with connect() as conn:
        rows = conn.execute("SELECT f.rcept_no, e.event_type FROM filings f JOIN events e USING (event_id) ORDER BY 1").fetchall()
        cards = {no.strip(): (rights_facts if t == "유상증자" else cb_facts)(no.strip(), conn=conn) for no, t in rows}
    dil = json.loads((DATA_DIR / "calc_fix" / "dilution_all_after2.json").read_text(encoding="utf-8"))
    rng, used, out = random.Random(SEED), set(), []

    def pick(group, pool, n):
        pool = sorted(no for no in pool if no not in used)
        got = rng.sample(pool, min(n, len(pool)))
        used.update(got)
        out.extend((no, group, len(pool)) for no in got)

    fl = lambda k: [no for no, c in cards.items() if c["flags"][k]]  # noqa: E731
    for k, n in (("rehab", 2), ("foreign_currency", 3), ("preferred_only", 5), ("new_major_holder", 5), ("price_pending", 5)):
        pick(f"flag:{k}", fl(k), n)
    pick("신호 많은 CB (문단 5 세는 신호 ≥ 3)", [no for no, c in cards.items() if c["type"] == "CB" and counted_signals(c) >= 3], 10)
    um = sorted(c for c, d in dil.items() if d["flags"]["unit_mismatch"])
    latest = {c: max((x for x in cards.values() if x["corp_code"] == c), key=lambda x: (x["rcept_dt"], x["rcept_no"]))["rcept_no"] for c in um}
    pick("unit_mismatch 회사 최신 공시", [latest[c] for c in rng.sample(um, 5)], 5)
    pick("신호 0개", [no for no, c in cards.items() if not c["signals"]], 5)
    pick("무작위", list(cards), 10)
    OUT.mkdir(parents=True, exist_ok=True)
    with SAMPLE.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rcept_no", "묶음", "후보 수", "corp_name", "type"])
        for no, g, n in out:
            w.writerow([no, g, n, cards[no]["corp_name"], cards[no]["type"]])
    print(f"표본 {len(out)}건 → {SAMPLE}")
    for no, g, n in out:
        print(f"  {g:28s} {no} {cards[no]['corp_name']} (후보 {n})")


# ---------- 2. 생성 ----------

def done() -> dict:
    if not ALERTS.exists():
        return {}
    return {r["rcept_no"]: r for r in map(json.loads, ALERTS.read_text(encoding="utf-8").splitlines()) if "error" not in r}


def cost(rows) -> float:
    return sum((r["usage"]["input_tokens"] * PRICE[r["model"]][0] + r["usage"]["output_tokens"] * PRICE[r["model"]][1]) / 1e6 for r in rows)


def run():
    todo = [r["rcept_no"] for r in csv.DictReader(SAMPLE.open(encoding="utf-8"))]
    have = done()
    todo = [no for no in todo if no not in have]
    print(f"남은 {len(todo)}건 (이미 {len(have)})")
    client = anthropic.Anthropic(api_key=get_settings().anthropic_api_key.get_secret_value(), max_retries=3, timeout=180.0)
    spent = cost(have.values())

    def one(no):
        with connect() as conn:
            try:
                return alert(no, conn=conn, client=client)
            except Exception as e:  # noqa: BLE001 — 실패는 기록만, 다시 돌리면 그것만 다시
                return {"rcept_no": no, "error": f"{type(e).__name__}: {e}"[:300]}

    with ThreadPoolExecutor(5) as ex, ALERTS.open("a", encoding="utf-8") as f:
        futs = [ex.submit(one, no) for no in todo]
        for fut in as_completed(futs):
            r = fut.result()
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            f.flush()
            if "error" in r:
                print("  실패", r["rcept_no"], r["error"][:120])
                continue
            spent += cost([r])
            if spent > COST_CAP:
                print(f"  비용 ${spent:.2f} > ${COST_CAP} — 멈춤")
                for x in futs:
                    x.cancel()
                break
    print(f"비용 누적 ${spent:.3f}")


# ---------- 3. 검사 ----------

def _adjusted(bad: list[str], card: dict) -> list[str]:
    """checks.number_check 결과에서 검사기 한계 2가지를 뺀다: 할증(카드 할인율이 음수 → 문장은 지시대로 크기만, 반올림 0~2자리)
    · '제3자배정'의 3. 빼고 남은 것이 진짜 실패."""
    from decimal import Decimal
    dr = card["values"].get("discount_rate")
    ok = set()
    if dr is not None and dr < 0:
        a = abs(Decimal(str(dr)))
        ok |= {str(a.quantize(Decimal(1).scaleb(-p)).normalize()) for p in (0, 1, 2)} | {str(a.normalize())}
    return [b for b in bad if b.replace(",", "") not in ok]


def _ctx(text: str, i: int, n: int) -> str:
    return text[max(0, i - 12): i + n + 12].replace("\n", " ")


def checks(r: dict) -> dict:
    card, paras, text = r["card"], r["paragraphs"], r["text"]
    data = {"card": card, "company_signals": r["company_signals"], "dilution_flags": r["dilution_flags"]}
    flat = " ".join(text.split())
    out = {}
    raw = number_check(text.replace("제3자", "제_자"), data)
    out["(보정 전) 숫자"] = number_check(text, data)
    out["숫자"] = _adjusted(raw, card)
    body = "\n".join(body_lines(text)).replace("주의할 점", "")
    out["금지 판단"] = [f"{w}: …{_ctx(body, m.start(), len(w))}…" for w in FORBIDDEN for m in re.finditer(w, body)
                     if not (w == "주의" and m.start() > 0 and body[m.start() - 1] == "주")]   # '최대주주의'의 '주의'는 아님
    on = {k for k in PHRASES if (r["dilution_flags"].get(k) if k == "unit_mismatch" else card["flags"].get(k))}
    phrase = {k: (card["calc"].get("foreign_currency_display") or PHRASES[k]) if k == "foreign_currency" else PHRASES[k] for k in PHRASES}
    out["flag 반영"] = [f"{k} 켜짐인데 '{phrase[k]}' 없음" for k in sorted(on) if " ".join(phrase[k].split()) not in flat]
    out["(참고) 꺼진 flag 문구"] = [f"{k} 꺼짐인데 문구 있음" for k in PHRASES if k not in on and PHRASES[k] in flat]
    s = signal_count_check(paras, card) if len(paras) == 5 else "문단 5개 아님"
    out["신호 누락"] = [s] if s else []
    names = {x["name"] for x in card["signals"]}
    p5 = paras[4] if len(paras) == 5 else ""
    out["꺼진 신호 언급"] = ([f"major_holder 꺼짐인데 문단 5에 '최대주주': …{_ctx(p5, p5.find('최대주주'), 4)}…"]
                         if "major_holder" not in names and "최대주주" in p5 else [])
    rp = repeat_check(paras, data) if len(paras) == 5 else None
    out["repeat 합"] = [rp] if rp else []
    fmt = []
    if len(paras) != 5:
        fmt.append(f"문단 {len(paras)}개")
    for i, (p, t) in enumerate(zip(paras, TITLES[card["type"]]), 1):
        lines = p.strip().splitlines()
        if lines[0].strip() != t:
            fmt.append(f"문단 {i} 제목 '{lines[0].strip()}' ≠ '{t}'")
        for ln in lines[1:]:
            ln = ln.strip()
            rest = ln[2:] if ln.startswith(("⚠️", "✅")) else ln
            for m in EMOJI.finditer(rest):
                if m.group() != "️":
                    fmt.append(f"문단 {i} 본문 이모지 {m.group()}: {ln[:40]}")
    out["형식"] = fmt
    return out


def check():
    sample = {r["rcept_no"]: r for r in csv.DictReader(SAMPLE.open(encoding="utf-8"))}
    rows = done()
    missing = [no for no in sample if no not in rows]
    res = {no: checks(rows[no]) for no in sample if no in rows}
    names = ["숫자", "금지 판단", "flag 반영", "신호 누락", "꺼진 신호 언급", "repeat 합", "형식"]
    usd = cost(rows.values())
    tin = sum(r["usage"]["input_tokens"] for r in rows.values())
    tout = sum(r["usage"]["output_tokens"] for r in rows.values())
    model = sorted({r["model"] for r in rows.values()})
    L = ["# 알림 문장 점검 50건 (4번 축소판)", "",
         f"- 모델 {', '.join(model)} (서비스 alert() 그대로, effort medium) · 지시문 data/alert_check/prompt_after.md (= docs/alert_prompt.md)",
         f"- 생성 {len(res)}/{len(sample)}건" + (f" · 못 만든 것 {missing}" if missing else ""),
         f"- 토큰 입력 {tin:,} · 출력 {tout:,} · **${usd:.3f} (약 {usd * KRW:,.0f}원, 1달러 {KRW}원으로 셈)**", "",
         "## 검사별", "", "| 검사 | 통과 | 실패 |", "|---|---|---|"]
    for n in names:
        bad = sum(1 for x in res.values() if x[n])
        L.append(f"| {n} | {len(res) - bad} | {bad} |")
    ref = sum(1 for x in res.values() if x["(참고) 꺼진 flag 문구"])
    raw = sum(1 for x in res.values() if x["(보정 전) 숫자"])
    L += ["", f"(참고) 꺼진 flag의 문구가 나온 건: {ref}",
          f"(참고) 숫자 검사 보정 전 실패 {raw}건 — 검사기 한계 2가지(할증 = 음수 할인율의 크기, '제3자배정'의 3)를 빼면 위 숫자. "
          "금지 판단도 '최대주주의'의 '주의'는 뺌.", "", "## 묶음별 flag 반영", "", "| 묶음 | 건수 | flag 반영 실패 |", "|---|---|---|"]
    groups = {}
    for no, s in sample.items():
        if no in res:
            groups.setdefault(s["묶음"], []).append(no)
    for g, nos in groups.items():
        L.append(f"| {g} | {len(nos)} | {sum(1 for no in nos if res[no]['flag 반영'])} |")
    L += ["", "## 실패 건 전부", "", "| rcept_no | 회사 | 검사 | 내용 |", "|---|---|---|---|"]
    for no, x in res.items():
        for n in names + ["(참고) 꺼진 flag 문구"]:
            for item in x[n]:
                L.append(f"| {no} | {rows[no]['corp_name']} | {n} | {str(item).replace('|', '/')} |")
    L += ["", "## 50건 문장 전체"]
    for no, s in sample.items():
        if no not in rows:
            continue
        r = rows[no]
        c = r["card"]
        on = [k for k in PHRASES if (r["dilution_flags"].get(k) if k == "unit_mismatch" else c["flags"].get(k))]
        sig = [x["name"] for x in c["signals"]]
        L += ["", f"### {r['corp_name']} {no} ({c['type']}) — {s['묶음']}", "",
              f"카드: signals {sig} · 세는 신호 {counted_signals(c)} · repeat {c['company']['repeat_count']} · 켜진 flag {on}", "", r["text"]]
    (OUT / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L[:30]))
    print("…")
    print("\n".join(l for l in L if l.startswith("| 2") or l.startswith("| 1")) [:6000])


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    for a in ("sample", "run", "check"):
        g.add_argument(f"--{a}", action="store_true")
    a = ap.parse_args()
    if a.sample:
        sample()
    elif a.run:
        run()
    else:
        check()


if __name__ == "__main__":
    main()
