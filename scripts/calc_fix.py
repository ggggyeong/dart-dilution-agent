"""희석률 계산 검증 3단계 — 수정 전/후 저장과 비교 (docs/calc_criteria.md v1). 읽기만.

    uv run python scripts/calc_fix.py --stage before   # 코드 고치기 전: dilution_all_before.json · cards_before.json
    uv run python scripts/calc_fix.py --stage after    # 고친 뒤: dilution_all_after.json · cards_after.json
                                                       #   + report.md · signals_before_after.csv
    uv run python scripts/calc_fix.py --stage after2   # cb-v4(같은 날 형제 CB) 뒤: dilution_all_after2.json
                                                       #   + report_after2.md · signals_before_after2.csv (수정 전과 비교)

as_of는 전/후 같게 2026-10-22로 고정. 출력은 data/calc_fix/.
"""

import argparse
import csv
import json
import statistics
from collections import Counter
from datetime import date

from filingscope.config import DATA_DIR
from filingscope.db import connect
from filingscope.facts import cards as C
from filingscope.parse import cb as CB
from filingscope.parse.acode import read_document

OUT = DATA_DIR / "calc_fix"
AS_OF = date(2026, 10, 22)
SIGNALS = ("non_cash", "major_holder", "debt_heavy", "halted", "refixing", "old_bonds", "public_single")


def snapshot(stage: str):
    C._today = lambda: AS_OF   # 카드 안 company_signals도 같은 날짜로
    OUT.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        rows = conn.execute("SELECT f.rcept_no, e.event_type FROM filings f JOIN events e USING (event_id) ORDER BY 1").fetchall()
        cards = {}
        for no, typ in rows:
            no = no.strip()
            c = (C.rights_facts if typ == "유상증자" else C.cb_facts)(no, conn=conn)
            cards[no] = {"type": c["type"], "corp_code": c["corp_code"], "corp_name": c["corp_name"], "rcept_dt": c["rcept_dt"],
                         "signals": {s["name"]: s for s in c["signals"]}, "calc": c["calc"], "flags": c["flags"],
                         "values": {k: c["values"].get(k) for k in ("shares_before", "new_shares", "conv_shares", "old_cb_shares",
                                                                    "stock_type", "listing_date", "conv_end", "maturity", "min_price")}}
        corps = sorted({c["corp_code"] for c in cards.values()})
        dil = {corp: C.dilution(corp, AS_OF, conn=conn) for corp in corps}
    (OUT / f"cards_{stage}.json").write_text(json.dumps(cards, ensure_ascii=False, default=str), encoding="utf-8")
    (OUT / f"dilution_all_{stage}.json").write_text(json.dumps(dil, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"{stage}: 카드 {len(cards)} · 회사 {len(dil)}")


def load(stage):
    return (json.loads((OUT / f"cards_{stage}.json").read_text(encoding="utf-8")),
            json.loads((OUT / f"dilution_all_{stage}.json").read_text(encoding="utf-8")))


def med(xs):
    xs = [x for x in xs if x is not None]
    return f"{statistics.median(xs):.1f} (n={len(xs)})" if xs else "–"


def report(stage: str = "after"):
    cb0, d0 = load("before")
    cb1, d1 = load(stage)
    L = [f"# 희석률 계산 검증 3단계 — 수정 전후 (as_of {AS_OF})", "",
         f"docs/calc_criteria.md v1. 수정 전 = 커밋 78a70a7 코드 + cb-v2 DB, 수정 후({stage}) = 이번 코드 + {CB.EXTRACTOR_VERSION} DB.", ""]
    nos = sorted(cb1)
    rights = [n for n in nos if cb1[n]["type"] == "유상증자"]
    cbs = [n for n in nos if cb1[n]["type"] == "CB"]

    # ---- 1-1
    pref = [n for n in rights if cb1[n]["flags"]["preferred_only"]]
    pref_null = [n for n in pref if cb0[n]["calc"]["dilution_pct"] is not None and cb1[n]["calc"]["dilution_pct"] is None]
    pref_live = {corp: [e["rcept_no"] for e in d["live_events"] if e["flags"]["preferred_only"]] for corp, d in d1.items()}
    pref_live = {k: v for k, v in pref_live.items() if v}
    other_dp = [n for n in nos if n not in pref and cb0[n]["calc"].get("dilution_pct") != cb1[n]["calc"].get("dilution_pct")]
    L += ["## 1. 고친 것", "", "### 1-1 우선주", "",
          f"- preferred_only 공시 {len(pref)}건 (기대 88) → dilution_pct NULL로 바뀐 것 {len(pref_null)}건",
          f"- 회사 합산에서 살아 있는 목록에 preferred_only가 있는 회사 {len(pref_live)}곳: "
          + ", ".join(f"{d1[k]['corp_name']} {v}" for k, v in pref_live.items()),
          f"- 혼합 · 기타주식 보유: 코드 변경 없음 (rights-v4가 이미 보통주만 읽음). 우선주 외 공시 중 dilution_pct가 바뀐 것 {len(other_dp)}건"
          + (f" {other_dp[:10]}" if other_dp else ""), ""]

    # ---- 1-2
    mh0 = [n for n in nos if "major_holder" in cb0[n]["signals"]]
    mh1 = [n for n in nos if "major_holder" in cb1[n]["signals"]]
    nmh = [n for n in nos if cb1[n]["flags"]["new_major_holder"]]
    L += ["### 1-2 major_holder", "", f"- 켜짐 {len(mh0)} → **{len(mh1)}** (기대 303 근처), 새로 켜진 것 {len(set(mh1) - set(mh0))}",
          f"- new_major_holder flag {len(nmh)}건 (그중 예전 major_holder였던 것 {len(set(nmh) & set(mh0))})", ""]

    # ---- 1-3 · 1-4a · 1-4b (회사)
    def key(d):
        x = d["dilution"] or {}
        return (x.get("shares_before"), x.get("new_shares"), x.get("max_pct"), x.get("worst_pct"), x.get("basis_rcept_no"))
    changed = [c for c in d1 if key(d0[c]) != key(d1[c])]
    basis_ch = [c for c in d1 if (d0[c]["dilution"] or {}).get("basis_rcept_no") != (d1[c]["dilution"] or {}).get("basis_rcept_no")]
    sb_ch = [c for c in basis_ch if (d0[c]["dilution"] or {}).get("shares_before") != (d1[c]["dilution"] or {}).get("shares_before")]
    live = lambda d: {e["rcept_no"] for e in d["live_events"] if e["type"] == "CB"}  # noqa: E731
    cb_live_ch = [c for c in d1 if live(d0[c]) != live(d1[c])]
    L += ["### 1-3 기준 주식 수 = 가장 최근 살아 있는 공시", "",
          f"- basis_rcept_no가 바뀐 회사 {len(basis_ch)}곳 (그중 shares_before 값도 바뀐 곳 {len(sb_ch)}) — "
          f"예전 기준이 없던(유상증자만) 회사 {sum(1 for c in basis_ch if not (d0[c]['dilution'] or {}).get('basis_rcept_no'))}곳 포함", "",
          "### 1-4 전환 기간 끝난 CB", "",
          f"- 살아 있는 CB 목록이 바뀐 회사 {len(cb_live_ch)}곳 (2단계 기대 1곳): "
          + ", ".join(f"{d1[c]['corp_name']} {sorted(live(d0[c]) - live(d1[c]))}→{sorted(live(d1[c]) - live(d0[c]))}" for c in cb_live_ch)]
    old_ch = [n for n in cbs if cb0[n]["values"]["old_cb_shares"] != cb1[n]["values"]["old_cb_shares"]]
    L += [f"- {CB.EXTRACTOR_VERSION} old_cb_shares 바뀐 공시 **{len(old_ch)}건** (아래 2번 표)", ""]

    # ---- 2 flag
    fl = Counter(k for n in nos for k in ("rehab", "price_pending", "foreign_currency") if cb1[n]["flags"][k])
    L += ["## 2. flag만 추가", "", f"- rehab {fl['rehab']} (기대 4) · price_pending {fl['price_pending']} (2단계 356) · "
          f"foreign_currency {fl['foreign_currency']} (2단계 6)", ""]

    # ---- 2번 보고: old_cb_shares 표
    L += [f"## 3. {CB.EXTRACTOR_VERSION}로 old_cb_shares 바뀐 공시 (수정 전 cb-v2 대비)", "", "| rcept_no | 회사 | 전 | 후 | 차이 | 이유 |", "|---|---|---|---|---|---|"]
    with connect() as conn:   # 이유는 지금 파서로 다시 읽어서 (old_cb_rule)
        for n in old_ch:
            mf, dt = conn.execute("""SELECT d.main_file, f.rcept_dt FROM documents d JOIN filings f ON f.rcept_no = d.rcept_no
                                     WHERE d.rcept_no = %s""", (n,)).fetchone()
            rule = CB.extract(read_document(DATA_DIR / "raw" / "document" / n / mf), rcept_dt=dt).old_cb_rule
            a, b = cb0[n]["values"]["old_cb_shares"], cb1[n]["values"]["old_cb_shares"]
            why = rule.replace("줄 합 (", "").replace(")", "").replace(" (", " · ") if "뺌" in rule else "소계 → 줄 합"
            L.append(f"| {n} | {cb1[n]['corp_name']} | {a:,} | {b:,} | {b - a:+,} | {why} |")
    L.append("")

    # ---- 단위 불일치
    mm = {c: d for c, d in d1.items() if d["flags"]["unit_mismatch"]}
    old_cr = [c for c, d in d0.items() if d["flags"]["capital_reduction"]]
    L += ["## 4. 단위 불일치 회사", "", f"- 걸린 회사 **{len(mm)}곳**. 예전 감자 추정(50%)에 걸린 {len(old_cr)}곳 중 지금도 걸림 "
          f"{sum(1 for c in old_cr if c in mm)}곳, 빠짐 {[d0[c]['corp_name'] for c in old_cr if c not in mm]}", "",
          "| 회사 | A rcept_no · shares_before · 날짜 | B | 방향 | max_pct 전 → 후 | 기준 공시 |", "|---|---|---|---|---|---|"]
    for c, d in sorted(mm.items(), key=lambda kv: kv[1]["corp_name"]):
        ev = {e["rcept_no"]: e for e in d["live_events"]}
        for a, b in d["flags"]["unit_mismatch"]:
            x, y = ev[a]["values"]["shares_before"], ev[b]["values"]["shares_before"]
            L.append(f"| {d['corp_name']} | {a} · {x:,} · {ev[a]['rcept_dt']} | {b} · {y:,} · {ev[b]['rcept_dt']} | "
                     f"{'감소' if y < x else '증가'} | {(d0[c]['dilution'] or {}).get('max_pct')} → {d['dilution']['max_pct']} | {d['dilution']['basis_rcept_no']} |")
    L.append("")

    # ---- 4번: 상장예정일 NULL 보통주
    L += ["## 5. 상장예정일 NULL 보통주 유상증자 (확인만)", "", "| rcept_no | 회사 | ci_mth | 원문 LST_PLN_DT 칸 |", "|---|---|---|---|"]
    with connect() as conn:
        for n in rights:
            v = cb1[n]["values"]
            if v["listing_date"] is None and v["stock_type"] == "보통":
                ci, mf = conn.execute("""SELECT e.ci_mth, d.main_file FROM events e JOIN documents d ON d.rcept_no = e.event_id
                                         WHERE e.event_id = %s""", (n,)).fetchone()
                doc = read_document(DATA_DIR / "raw" / "document" / n / mf)
                cell = doc.first("LST_PLN_DT")
                L.append(f"| {n} | {cb1[n]['corp_name']} | {ci} | {('칸 없음' if cell is None else repr(cell.text))} |")
    L.append("")

    # ---- 불변식
    bad = Counter()
    ex = {}
    for n in nos:
        k, v, sg = cb1[n]["calc"], cb1[n]["values"], cb1[n]["signals"]
        dp = k.get("dilution_pct")
        checks = {"0 ≤ dilution_pct ≤ 100": dp is None or 0 <= dp <= 100}
        if cb1[n]["type"] == "CB":
            wp, tp, fp = k.get("worst_pct"), k.get("total_pct"), k.get("floor_pct")
            checks |= {"dilution_pct ≤ worst_pct ≤ 100": dp is None or wp is None or dp <= wp <= 100,
                       "dilution_pct ≤ total_pct": dp is None or tp is None or dp <= tp,
                       "floor_pct ≤ 100": fp is None or fp <= 100,
                       "refixing ⇔ min_price NOT NULL": ("refixing" in sg) == (v["min_price"] is not None),
                       "old_bonds ⇔ old_cb_shares > 0": ("old_bonds" in sg) == ((v["old_cb_shares"] or 0) > 0)}
        for name, ok in checks.items():
            if not ok:
                bad[name] += 1
                ex.setdefault(name, []).append(n)
    L += ["## 6. 불변식 (수정 후 전수)", "", "| 검사 | 깨진 건수 |", "|---|---|"]
    for name in ("0 ≤ dilution_pct ≤ 100", "dilution_pct ≤ worst_pct ≤ 100", "dilution_pct ≤ total_pct", "floor_pct ≤ 100",
                 "refixing ⇔ min_price NOT NULL", "old_bonds ⇔ old_cb_shares > 0"):
        L.append(f"| {name} | {bad[name]} {ex.get(name, [])[:5]} |")
    L.append("")

    # ---- 분포
    L += ["## 7. 전후 분포", "", "| 신호 | 유상증자 전 → 후 | CB 전 → 후 |", "|---|---|---|"]
    for s in SIGNALS:
        r0, r1 = (sum(1 for n in rights if s in cb[n]["signals"]) for cb in (cb0, cb1))
        c0, c1 = (sum(1 for n in cbs if s in cb[n]["signals"]) for cb in (cb0, cb1))
        L.append(f"| {s} | {r0} ({r0 / len(rights):.1%}) → {r1} ({r1 / len(rights):.1%}) | {c0} ({c0 / len(cbs):.1%}) → {c1} ({c1 / len(cbs):.1%}) |")
    L += ["", f"- dilution max_pct 중앙값: {med((d['dilution'] or {}).get('max_pct') for d in d0.values())} → "
          f"{med((d['dilution'] or {}).get('max_pct') for d in d1.values())}",
          f"- worst_pct 중앙값: {med((d['dilution'] or {}).get('worst_pct') for d in d0.values())} → "
          f"{med((d['dilution'] or {}).get('worst_pct') for d in d1.values())}",
          f"- 회사 dilution이 바뀐 곳 (shares_before · new_shares · max_pct · worst_pct · basis 중 하나): **{len(changed)}/{len(d1)}**",
          f"- max_pct 값이 바뀐 곳 {sum(1 for c in d1 if (d0[c]['dilution'] or {}).get('max_pct') != (d1[c]['dilution'] or {}).get('max_pct'))}곳 "
          f"(NULL → 값 {sum(1 for c in d1 if (d0[c]['dilution'] or {}).get('max_pct') is None and (d1[c]['dilution'] or {}).get('max_pct') is not None)}, "
          f"값 → NULL {sum(1 for c in d1 if (d0[c]['dilution'] or {}).get('max_pct') is not None and (d1[c]['dilution'] or {}).get('max_pct') is None)})"]
    (OUT / ("report.md" if stage == "after" else f"report_{stage}.md")).write_text("\n".join(L) + "\n", encoding="utf-8")

    with (OUT / f"signals_before_{stage}.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rcept_no", "corp_name", "type"] + [f"{s}_{t}" for s in SIGNALS for t in ("before", "after")])
        for n in nos:
            w.writerow([n, cb1[n]["corp_name"], cb1[n]["type"]]
                       + [int(s in cb[n]["signals"]) for s in SIGNALS for cb in (cb0, cb1)])
    print("\n".join(L))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["before", "after", "after2"], required=True)
    a = ap.parse_args()
    snapshot(a.stage)
    if a.stage != "before":
        report(a.stage)


if __name__ == "__main__":
    main()
