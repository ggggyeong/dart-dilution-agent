"""희석률 계산 검증 2단계 — docs/calc_criteria.md 상황 1~10이 DB에 몇 건 있고, 지금 숫자가 얼마나 다른지 세기. 읽기만.

    uv run python scripts/calc_count.py [--as-of 2026-10-22]   # → data/calc_count/calc_count.md · calc_count_rows.csv

코드(facts · dilution)는 고치지 않는다. 정의 v0 쪽 숫자는 이 스크립트 안에서만 다시 계산한다 (1번 회사 합산은
dilution()과 같은 합산을 '살아 있는 CB' 조건만 바꿔 돌림 — 먼저 지금 조건으로 dilution()과 전 회사가 같은지 확인).
"""

import argparse
import csv
import re
import statistics
import traceback
from collections import Counter, defaultdict
from datetime import date

from filingscope.config import DATA_DIR
from filingscope.db import connect
from filingscope.facts import cards as C
from filingscope.parse.acode import read_document
from filingscope.parse.rights_issue import to_int

RAW = DATA_DIR / "raw" / "document"
OUT = DATA_DIR / "calc_count"
CAPITAL_REDUCTION_RATIO = 0.5   # 2단계 당시 dilution()의 감자 추정 (3단계에서 facts/constants.py에서 없어짐)
SIGNALS = ("non_cash", "major_holder", "debt_heavy", "halted", "refixing", "old_bonds", "public_single")
_DATE = re.compile(r"(\d{4})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})")


def end_date(text: str | None) -> date | None:
    """EXE_DT '2020년 08월 13일 ~ 2024년 08월 13일' → 두 번째 날짜 (하나뿐이면 None — 종료일 칸 없음으로 봄)."""
    ds = _DATE.findall(text or "")
    if len(ds) < 2:
        return None
    y, m, d = map(int, ds[1])
    try:
        return date(y, m, d)
    except ValueError:
        return None


def stats(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return "–"
    return f"평균 {statistics.mean(xs):.1f} · 중앙 {statistics.median(xs):.1f} · 최대 {max(xs):.1f} (n={len(xs)})"


def aggregate(corp_code, as_of, cards, cb_alive):
    """dilution()과 같은 합산. cb_alive(card) → 살아 있는 CB인가. 반환 (max_pct, worst_pct, live_cards)."""
    ev = [c for c in cards if c["corp_code"] == corp_code]
    rights = sorted((c for c in ev if c["type"] == "유상증자" and c["values"]["listing_date"] and c["values"]["listing_date"] > as_of),
                    key=lambda c: (c["_first_date"], c["rcept_no"]))
    cbs = sorted((c for c in ev if c["type"] == "CB" and cb_alive(c)), key=lambda c: (c["rcept_dt"], c["rcept_no"]), reverse=True)
    cb = cbs[0] if cbs else None
    same_day = sorted((c for c in cbs[1:] if c["rcept_dt"] == cb["rcept_dt"]), key=lambda c: c["rcept_no"]) if cb else []
    live = rights + ([cb] if cb else []) + same_day
    if not live:
        return None, None, live
    seq = [e["values"]["shares_before"] for e in sorted(live, key=lambda e: (e["rcept_dt"], e["rcept_no"])) if e["values"]["shares_before"]]
    cr = any(b <= a * CAPITAL_REDUCTION_RATIO for a, b in zip(seq, seq[1:]))
    sb = cb["values"]["shares_before"] if cb else max(rights, key=lambda r: (r["rcept_dt"], r["rcept_no"]))["values"]["shares_before"]
    rn = sum(r["values"]["new_shares"] or 0 for r in rights)
    conv = ((cb["values"]["conv_shares"] or 0) + sum(s["values"]["conv_shares"] or 0 for s in same_day)) if cb else 0
    old = (cb["values"]["old_cb_shares"] or 0) if cb else 0
    worst = ((cb["calc"]["worst_shares"] or 0) + sum(s["calc"]["worst_shares"] or 0 for s in same_day)) if cb else 0
    if sb is None or cr:
        return None, None, live
    return C._pct(rn + conv + old, sb + rn + conv + old), C._pct(rn + worst + old, sb + rn + worst + old), live


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", default=date.today().isoformat())
    a = ap.parse_args()
    as_of = date.fromisoformat(a.as_of)
    AS = as_of.isoformat()
    C._today = lambda: as_of
    OUT.mkdir(parents=True, exist_ok=True)
    rows_out = []   # (상황, rcept_no, corp_name, 종류, 내용, 지금, 정의 v0)
    L = [f"# 희석률 계산 검증 2단계 — 상황별 건수 (as_of {AS})", "",
         "docs/calc_criteria.md v0 기준. 읽기만 했고 코드 · DB는 바꾸지 않았다. 건별 목록은 calc_count_rows.csv.", ""]

    with connect() as conn:
        meta = conn.execute("""SELECT f.rcept_no, e.event_type, e.corp_code, e.corp_name, e.first_date, d.main_file
                               FROM filings f JOIN events e USING (event_id) JOIN documents d ON d.rcept_no = f.rcept_no ORDER BY 1""").fetchall()
        # ---- 카드 전부 (불변식 · 예외 포함)
        cards, errors = [], []
        for no, typ, corp, name, fd, mf in meta:
            no = no.strip()
            try:
                card = (C.rights_facts if typ == "유상증자" else C.cb_facts)(no, conn=conn)
                card["_first_date"] = fd.isoformat()
                card["_main_file"] = mf
                cards.append(card)
            except Exception as e:  # noqa: BLE001
                errors.append((no, f"{type(e).__name__}: {e}"))
        by_no = {c["rcept_no"]: c for c in cards}
        docs = {}

        def doc(no):
            if no not in docs:
                docs[no] = read_document(RAW / no / by_no[no]["_main_file"])
            return docs[no]

        rights = [c for c in cards if c["type"] == "유상증자"]
        cbs = [c for c in cards if c["type"] == "CB"]
        corps = sorted({c["corp_code"] for c in cards})

        # ---- 1번 CB 살아 있음
        alive_now = lambda c: c["values"]["maturity"] is not None and c["values"]["maturity"] > AS  # noqa: E731
        alive_v0 = lambda c: (c["values"]["conv_end"] or c["values"]["maturity"] or "") > AS  # noqa: E731
        dead_conv = [c for c in cbs if c["values"]["conv_end"] and c["values"]["conv_end"] < AS < (c["values"]["maturity"] or "")]
        perpetual = [c for c in dead_conv if c["values"]["maturity"] and (date.fromisoformat(c["values"]["maturity"]) - date.fromisoformat(c["rcept_dt"])).days >= 365 * 20]
        # 지금 조건으로 aggregate가 dilution()과 같은지 (전 회사)
        mismatch, dil_err, old_basis = [], [], []
        now, v0 = {}, {}
        for corp in corps:
            try:
                d = C.dilution(corp, as_of, conn=conn)
            except Exception as e:  # noqa: BLE001
                dil_err.append((corp, f"{type(e).__name__}: {e}"))
                continue
            m_now, _, live_now = aggregate(corp, AS, cards, alive_now)
            real = d["dilution"]["max_pct"] if d["dilution"] else None
            if real != m_now:
                mismatch.append((corp, real, m_now))
            if d["dilution"] and d["dilution"]["basis_date"] and (as_of - date.fromisoformat(d["dilution"]["basis_date"])).days >= 730:
                old_basis.append(corp)
            now[corp] = (m_now, live_now)
            v0[corp] = aggregate(corp, AS, cards, alive_v0)
        dead_nos = {c["rcept_no"] for c in dead_conv}
        picked = [corp for corp, (_, live) in now.items() if any(e["rcept_no"] in dead_nos for e in live)]
        diffs = []
        for corp in picked:
            a_, b_ = now[corp][0], v0[corp][0]
            diff = None if a_ is None else (a_ - (b_ or 0.0))
            diffs.append(diff)
            name = next(c["corp_name"] for c in cards if c["corp_code"] == corp)
            dn = [e["rcept_no"] for e in now[corp][1] if e["rcept_no"] in dead_nos]
            rows_out.append((1, ";".join(dn), name, "회사", "전환기간 끝난 CB가 살아 있는 CB로 뽑힘", a_, b_))
        L += ["## 1. CB 살아 있음 (conv_end < 오늘 < maturity)", "",
              "| 항목 | 값 |", "|---|---|",
              f"| 전체 CB | {len(cbs):,} |",
              f"| conv_end < 오늘 < maturity | {len(dead_conv)} ({len(dead_conv) / len(cbs):.1%}) |",
              f"| 그중 만기 − 공시일 ≥ 20년 (영구형) | {len(perpetual)} |",
              f"| 그 CB가 dilution()에서 살아 있는 CB로 뽑힌 회사 | {len(picked)} |",
              f"| max_pct 지금 − conv_end 기준 (%p) | {stats(diffs)} |",
              f"| 차이 ≥ 5%p 회사 | {sum(1 for x in diffs if x is not None and x >= 5)} |",
              f"| (확인) 지금 조건 재계산 = dilution() | {len(corps) - len(mismatch) - len(dil_err)}/{len(corps)} 회사 같음, 다름 {len(mismatch)} {mismatch[:3]} |", ""]
        L.append(f"→ **{'고칠 가치 있음' if picked else '없음'}**: 전환 못 하는 CB가 회사 {len(picked)}곳의 합산에 들어가 있음 "
                 f"(차이 ≥ 5%p {sum(1 for x in diffs if x is not None and x >= 5)}곳).\n")

        # ---- 2번 잔량표 옛 CB
        old_pos = [c for c in cbs if (c["values"]["old_cb_shares"] or 0) > 0]
        has_end, expired, exp_pct, outside = 0, [], [], []
        for c in old_pos:
            cells = doc(c["rcept_no"]).body_cells()
            nsb = [x for x in cells if x.group == "TBL_NSB"]
            rows = defaultdict(dict)
            for x in nsb:
                if x.code in ("EXE_STK", "EXE_DT", "DEB_KND"):
                    rows[(x.table_idx, x.row_idx)][x.code] = x.text
            ends = [(end_date(r.get("EXE_DT")), to_int(r.get("EXE_STK")) or 0, r.get("DEB_KND", "")) for r in rows.values()]
            if any(e for e, *_ in ends):
                has_end += 1
            gone = [(e, s, k) for e, s, k in ends if e and e < date.fromisoformat(c["rcept_dt"])]
            if gone:
                share = sum(s for _, s, _ in gone)
                pct = share / c["values"]["old_cb_shares"] * 100
                expired.append(c)
                exp_pct.append(pct)
                rows_out.append((2, c["rcept_no"], c["corp_name"], "CB", f"전환종료일 < 공시일 줄 {len(gone)}개 {share:,}주",
                                 c["values"]["old_cb_shares"], c["values"]["old_cb_shares"] - share))
            if any(x.code == "EXE_STK" and x.group != "TBL_NSB" for x in cells):
                outside.append(c["rcept_no"])
        L += ["## 2. 잔량표 옛 CB", "", "| 항목 | 값 |", "|---|---|",
              f"| old_cb_shares > 0 | {len(old_pos)} ({len(old_pos) / len(cbs):.1%}) |",
              f"| 그중 잔량표 줄에 전환종료일(EXE_DT) 있음 | {has_end} |",
              f"| 종료일 < 공시일 줄이 하나라도 있음 | {len(expired)} |",
              f"| 그 줄 주식 수 ÷ old_cb_shares (%) | {stats(exp_pct)} |",
              f"| 잔량표 밖 EXE_STK가 있는 건 (⑨-2 C항: 소계 없음 → 줄 합 4건 중 밖 0) | {len(outside)} {outside[:5]} |", ""]
        L.append(f"→ **{'고칠 가치 있음' if expired else '없음'}**: 끝난 옛 CB를 잔량으로 세는 건 {len(expired)}건 "
                 f"(old_cb_shares > 0의 {len(expired) / max(1, len(old_pos)):.0%}).\n")

        # ---- 3번 우선주
        st = Counter(c["values"]["stock_type"] for c in rights)
        bfr_pst, diff3 = [], []
        for c in rights:
            d_ = doc(c["rcept_no"])
            bp = to_int(d_.first("BFR_PST_CNT").text) if d_.first("BFR_PST_CNT") else None
            pst = to_int(d_.first("PST_CNT").text) if d_.first("PST_CNT") else None
            cst = to_int(d_.first("CST_CNT").text) if d_.first("CST_CNT") else None
            sb = c["values"]["shares_before"]
            if bp:
                bfr_pst.append(c)
            if c["values"]["stock_type"] == "혼합" or bp:
                nc, np_, bp_ = cst or 0, pst or 0, bp or 0
                if sb is None or nc + np_ == 0:
                    continue
                incl = C._pct(nc + np_, sb + bp_ + nc + np_)
                excl = C._pct(nc, sb + nc) if nc else None
                if incl is not None and excl is not None and abs(incl - excl) >= 1:
                    diff3.append(c)
                    rows_out.append((3, c["rcept_no"], c["corp_name"], "유상증자", f"stock_type {c['values']['stock_type']} · 증자 전 기타주식 {bp_:,}",
                                     excl, incl))
        pref_calc = [c for c in rights if c["values"]["stock_type"] == "우선" and c["calc"]["dilution_pct"] is not None]
        L += ["## 3. 우선주", "", "| 항목 | 값 |", "|---|---|",
              f"| stock_type 우선 / 혼합 / 보통 / NULL | {st['우선']} / {st['혼합']} / {st['보통']} / {st[None]} |",
              f"| BFR_PST_CNT(증자 전 기타주식) > 0 | {len(bfr_pst)} |",
              f"| 혼합 · 기타주식 보유 건 중 분모에 우선주 넣을 때 vs 뺄 때 차이 ≥ 1%p | {len(diff3)} |",
              f"| (참고) 우선만 발행인데 지금 dilution_pct가 계산됨 (⑨-2: 분자 = 기타주식, 분모 = 보통주 섞임) | {len(pref_calc)} |", ""]
        L.append(f"→ **{'고칠 가치 있음' if pref_calc or diff3 else '없음'}**: 우선만 발행 {len(pref_calc)}건은 분자(우선주) · 분모(보통주) 단위가 섞임, "
                 f"분모 차이 ≥ 1%p {len(diff3)}건.\n")

        # ---- 4번 단위 불일치
        multi = {corp: [e for e in live if e["values"]["shares_before"]] for corp, (_, live) in now.items() if len(live) >= 2}
        ratios = {}
        for corp, live in multi.items():
            if len(live) < 2:
                continue
            hi, lo = max(live, key=lambda e: e["values"]["shares_before"]), min(live, key=lambda e: e["values"]["shares_before"])
            ratios[corp] = (hi["values"]["shares_before"] / lo["values"]["shares_before"], hi, lo)
        L += ["## 4. 단위 불일치 (감자)", "", "| 항목 | 값 |", "|---|---|",
              f"| 살아 있는 공시 2건 이상인 회사 | {len(multi)} |"]
        for k in (2, 3, 5, 10):
            L.append(f"| shares_before 최대/최소 ≥ {k}배 | {sum(1 for r, *_ in ratios.values() if r >= k)} |")
        L += ["", "≥ 3배 회사:", "", "| 회사 | 큰 쪽 rcept_no · shares_before · 날짜 | 작은 쪽 | 배수 | 지금 capital_reduction(50%) |", "|---|---|---|---|---|"]
        for corp, (r, hi, lo) in sorted(ratios.items(), key=lambda kv: -kv[1][0]):
            if r >= 3:
                cr = now[corp][0] is None
                L.append(f"| {hi['corp_name']} | {hi['rcept_no']} · {hi['values']['shares_before']:,} · {hi['rcept_dt']} | "
                         f"{lo['rcept_no']} · {lo['values']['shares_before']:,} · {lo['rcept_dt']} | {r:.1f} | {'걸림(NULL)' if cr else '안 걸림'} |")
                rows_out.append((4, f"{hi['rcept_no']};{lo['rcept_no']}", hi["corp_name"], "회사", f"shares_before {r:.1f}배", now[corp][0], "unit_mismatch"))
        n3 = sum(1 for r, *_ in ratios.values() if r >= 3)
        L.append(f"\n→ **{'고칠 가치 있음' if n3 else '없음'}**: ≥ 3배 {n3}곳. 지금 감자 추정(뒤 값 ≤ 앞 값 50%)이 잡는지 표의 마지막 칸.\n")

        # ---- 5번 회생
        rehab, rehab_ex = defaultdict(list), defaultdict(list)
        for c in cards:
            text = " ".join(p.text for p in doc(c["rcept_no"]).body_paragraphs())
            in_ex = "회생" in (c["values"].get("exempt_reason") or "")
            if in_ex or "회생" in text:
                rehab[c["type"]].append(c)
                if in_ex:
                    rehab_ex[c["type"]].append(c)
                rows_out.append((5, c["rcept_no"], c["corp_name"], c["type"], "회생 · exempt_reason" if in_ex else "회생 · 글에만",
                                 c["calc"]["dilution_pct"], "rehab flag"))
        L += ["## 5. 회생", "", "| 종류 | 건수 (exempt_reason 또는 글) | 중앙 · 최대 | exempt_reason에만 한정 | 중앙 · 최대 |", "|---|---|---|---|---|"]
        md = lambda cs: (lambda ds: f"{statistics.median(ds) if ds else '–'} · {max(ds) if ds else '–'}")(  # noqa: E731
            [c["calc"]["dilution_pct"] for c in cs if c["calc"]["dilution_pct"] is not None])
        for t in ("유상증자", "CB"):
            L.append(f"| {t} | {len(rehab[t])} | {md(rehab[t])} | {len(rehab_ex[t])} | {md(rehab_ex[t])} |")
        L.append(f"\n→ **판단 보류**: 회생 언급 {sum(len(v) for v in rehab.values())}건 중 exempt_reason에 있는 건 {sum(len(v) for v in rehab_ex.values())}건뿐 — "
                 "나머지는 글에만 나옴(위험 문구 · 기한이익상실 조항일 수 있음). 정의 v0의 '글에 회생 포함' 조건은 너무 넓음.\n")

        # ---- 6번 major_holder
        on_now, on_v0, rel_diff = 0, 0, Counter()
        newmh = 0
        for c in cards:
            rels = [a_["relation"] for a_ in c["allottees"] if a_["relation"]]
            n_ = any("최대주주" in r for r in rels)
            v_ = any(r.replace(" ", "") in ("최대주주", "최대주주본인") for r in rels)
            on_now += n_
            on_v0 += v_
            newmh += any(re.sub(r"\s", "", r) in ("변경예정최대주주", "변경후최대주주") for r in rels)
            if n_ != v_:
                for r in rels:
                    if "최대주주" in r and r.replace(" ", "") not in ("최대주주", "최대주주본인"):
                        rel_diff[r] += 1
                rows_out.append((6, c["rcept_no"], c["corp_name"], c["type"], "; ".join(sorted({r for r in rels if '최대주주' in r}))[:120], n_, v_))
        L += ["## 6. major_holder", "", "| 항목 | 값 |", "|---|---|",
              f"| 지금 켜짐 | {on_now} |", f"| 정의 v0로 켜짐 | {on_v0} |", f"| 차이 (지금만 켜짐) | {on_now - on_v0} |",
              f"| (참고) 변경예정 · 변경후 최대주주 (new_major_holder 후보) | {newmh} |", "",
              "차이 나는 relation 상위 10:", "", "| relation | 건수 |", "|---|---|"]
        L += [f"| {r} | {n_} |" for r, n_ in rel_diff.most_common(10)]
        L.append(f"\n→ **{'고칠 가치 있음' if on_now - on_v0 else '없음'}**: 지금 켜진 {on_now}건 중 {on_now - on_v0}건이 정의 v0로는 꺼짐.\n")

        # ---- 7 · 8 · 9 · 10
        no_list = [c for c in rights if c["values"]["listing_date"] is None]
        pending = [c for c in rights if c["values"]["price_status"] == "예정"]
        pend_live = [c for c in pending if c["values"]["listing_date"] and c["values"]["listing_date"] > AS]
        multi_f = conn.execute("SELECT count(*) FROM (SELECT event_id FROM filings GROUP BY 1 HAVING count(*) > 1) t").fetchone()[0]
        foreign = [c for c in cbs if c["values"]["issue_method"] == "해외"]
        for c in no_list:
            rows_out.append((7, c["rcept_no"], c["corp_name"], "유상증자", f"listing_date NULL · stock_type {c['values']['stock_type']}", None, None))
        for c in pend_live:
            rows_out.append((8, c["rcept_no"], c["corp_name"], "유상증자", "price_status 예정 · 살아 있음", c["calc"]["dilution_pct"], "price_pending"))
        for c in foreign:
            rows_out.append((10, c["rcept_no"], c["corp_name"], "CB", "issue_method 해외", c["calc"]["dilution_pct"], "foreign_currency"))
        L += ["## 7. 유상증자 살아 있음 — listing_date NULL", "",
              f"- listing_date NULL 유상증자: **{len(no_list)}건** (stock_type {dict(Counter(c['values']['stock_type'] for c in no_list))})",
              "- 지금 처리: dilution()의 SQL `listing_date > as_of`에서 NULL은 거짓 → **살아 있는 공시에서 항상 빠짐** (판단 불가인데 '끝남'으로 취급).",
              f"\n→ **{'판단 보류' if no_list else '없음'}**: {len(no_list)}건 — 우선주만 발행이 많은지 표 확인.\n",
              "## 8. 발행가 예정", "", f"- price_status 예정: **{len(pending)}건**, 그중 살아 있음(listing_date > 오늘): **{len(pend_live)}건**",
              f"\n→ **{'고칠 가치 있음 (flag만)' if pend_live else '없음'}**: 살아 있는 예정가 {len(pend_live)}건은 희석률이 바뀔 수 있다는 표시가 필요.\n",
              "## 9. 정정본", "", f"- 한 event에 filings 2건 이상: **{multi_f}개** (filings는 원본만 넣음 → dilution()은 event마다 1번).",
              f"\n→ **{'없음' if multi_f == 0 else '고칠 가치 있음'}** (지금 DB 기준).\n",
              "## 10. 해외", "", f"- issue_method 해외 CB: **{len(foreign)}건**",
              f"\n→ **{'flag만' if foreign else '없음'}**: {len(foreign)}건 (원화 환산액이라 계산은 그대로).\n"]

        # ---- 불변식
        bad = defaultdict(list)
        for c in cards:
            k = c["calc"]
            dp = k.get("dilution_pct")
            if dp is not None and not 0 <= dp <= 100:
                bad["0 ≤ dilution_pct ≤ 100"].append(c["rcept_no"])
            if c["type"] == "CB":
                wp, tp, fp = k.get("worst_pct"), k.get("total_pct"), k.get("floor_pct")
                if dp is not None and wp is not None and not dp <= wp <= 100:
                    bad["dilution_pct ≤ worst_pct ≤ 100"].append(c["rcept_no"])
                if dp is not None and tp is not None and dp > tp:
                    bad["dilution_pct ≤ total_pct"].append(c["rcept_no"])
                if fp is not None and fp > 100:
                    bad["floor_pct ≤ 100"].append(c["rcept_no"])
                names = {s["name"] for s in c["signals"]}
                if ("refixing" in names) != (c["values"]["min_price"] is not None):
                    bad["refixing ⇔ min_price NOT NULL"].append(c["rcept_no"])
                if ("old_bonds" in names) != ((c["values"]["old_cb_shares"] or 0) > 0):
                    bad["old_bonds ⇔ old_cb_shares > 0"].append(c["rcept_no"])
        for name, nos in bad.items():
            for no in nos:
                c = by_no[no]
                rows_out.append(("불변식", no, c["corp_name"], c["type"], name, c["calc"].get("dilution_pct"), None))
        fm = [c for c in rights if c["calc"]["fund_mismatch"]]
        fm_known = [c for c in rights if c["calc"]["fund_mismatch"] is not None]
        sig = {t: Counter() for t in ("유상증자", "CB")}
        for c in cards:
            for s in {x["name"] for x in c["signals"]}:
                sig[c["type"]][s] += 1
        L += ["## 불변식 (전수, 정답 없이)", "", "| 검사 | 깨진 건수 | 예시 |", "|---|---|---|"]
        for name in ("0 ≤ dilution_pct ≤ 100", "dilution_pct ≤ worst_pct ≤ 100", "dilution_pct ≤ total_pct", "floor_pct ≤ 100",
                     "refixing ⇔ min_price NOT NULL", "old_bonds ⇔ old_cb_shares > 0"):
            L.append(f"| {name} | {len(bad[name])} | {', '.join(bad[name][:5])} |")
        L += ["", f"- fund_mismatch true: **{len(fm)}/{len(fm_known)} ({len(fm) / max(1, len(fm_known)):.1%})** (유상증자)"]
        if len(fm) / max(1, len(fm_known)) > 0.05:
            L += ["", "| rcept_no | 회사 | raised | fund_total | payment_type |", "|---|---|---|---|---|"]
            for c in fm[:10]:
                L.append(f"| {c['rcept_no']} | {c['corp_name']} | {c['calc']['raised']:,} | {c['calc']['fund_total']:,} | {c['values']['payment_type']} |")
        L += ["", "신호별 켜진 비율:", "", "| 신호 | 유상증자 (n=%d) | CB (n=%d) |" % (len(rights), len(cbs)), "|---|---|---|"]
        for s in SIGNALS:
            L.append(f"| {s} | {sig['유상증자'][s]} ({sig['유상증자'][s] / len(rights):.1%}) | {sig['CB'][s]} ({sig['CB'][s] / len(cbs):.1%}) |")
        L += ["", f"- 카드 예외: **{len(errors)}건** {errors[:5]}",
              f"- dilution() 전 회사 {len(corps)}곳: 예외 **{len(dil_err)}건** {dil_err[:5]}",
              f"- basis_date(기준 CB 공시일)가 as_of보다 2년 이상 오래된 회사: **{len(old_basis)}곳**"]

    (OUT / "calc_count.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    with (OUT / "calc_count_rows.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["상황", "rcept_no", "corp_name", "종류", "내용", "지금", "정의_v0"])
        w.writerows(rows_out)
    print("\n".join(L))


if __name__ == "__main__":
    main()
