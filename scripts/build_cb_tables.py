"""전환사채(CB) 원본 → events · filings · cb_values · allottees (명세 docs/cb_spec.md 2장) + 보고 (2-9).

    uv run python scripts/build_cb_tables.py

대상: 공시 당시 코스피 · 코스닥(listing_checks) · 문서코드 11324 · 원본. 정정본 · 80522 · 코넥스 · 비상장 제외.
다시 돌리면 CB 사건만 지우고 다시 넣는다 (유상증자 사건은 건드리지 않음).
넣은 뒤 납입 방식 LLM 결과(cb_payment_llm.py)를 다시 적용한다 (사람 판정 덮어쓰기는 ⑨-2에서 없앰).
값만 다시 뽑을 때는 scripts/reparse_values.py.
결과: data/reports/cb_all/ (values.csv, checks.md)
"""

import csv
from collections import Counter

from filingscope.config import DATA_DIR
from filingscope.db import connect
from filingscope.parse.acode import read_document
from filingscope.parse.cb import EXTRACTOR_VERSION, VALUE_COLS, extract, seq_no
from filingscope.parse.rights_issue import to_int

RAW = DATA_DIR / "raw" / "document"
OUT = DATA_DIR / "reports" / "cb_all"
SHOW = {"20260522000636": "코이즈", "20250909000002": "압타머(츌립앤사이언스)", "20260820000443": "엔투텍", "20260213002703": "헝셩그룹"}


def load(conn):
    rows = conn.execute(
        """SELECT b.rcept_no, b.corp_code, b.corp_name, b.rcept_dt, d.main_file, l.market, l.passed
           FROM b001_filings b JOIN documents d USING (rcept_no) JOIN listing_checks l USING (rcept_no)
           WHERE (b.report_nm LIKE '%%전환사채권발행결정%%' OR b.report_nm LIKE '%%전환사채발행결정%%')
             AND b.report_nm !~ '^\\s*\\[' AND d.doc_code = '11324' AND l.market IN ('KOSPI', 'KOSDAQ')
           ORDER BY b.rcept_no""").fetchall()
    out = []
    for no, corp, name, dt, mf, market, passed in rows:
        doc = read_document(RAW / no / mf)
        out.append(dict(rcept_no=no, corp_code=corp, corp_name=name, rcept_dt=dt, market=market, halted=not passed,
                        doc=doc, x=extract(doc, rcept_dt=dt)))   # cb-v3: 전환 끝난 잔량표 줄 빼기
    # cb-v4: 같은 회사 · 같은 날 CB가 2건 이상이면 서로를 형제로 넘겨 다시 뽑음 (잔량표에 든 형제 줄을 old_cb_shares에서 뺌)
    day = {}
    for c in out:
        day.setdefault((c["corp_code"], c["rcept_dt"]), []).append(c)
    for g in day.values():
        for c in g if len(g) > 1 else []:
            sib = [(o["x"].values["amount"], o["x"].values["conv_price"], seq_no(o["doc"])) for o in g if o is not c]
            c["x"] = extract(c["doc"], rcept_dt=c["rcept_dt"], siblings=sib)
    return out


def save(conn, items):
    ids = [c["rcept_no"] for c in items]
    with conn.transaction():
        conn.execute("DELETE FROM events WHERE event_type = 'CB' AND event_id = ANY(%s)", (ids,))
        for c in items:
            x, no = c["x"], c["rcept_no"]
            conn.execute(
                """INSERT INTO events (event_id, event_type, corp_code, corp_name, ci_mth, first_date, market_at_filing,
                                       halted_at_filing, payment_type, payment_source)
                   VALUES (%s, 'CB', %s, %s, NULL, %s, %s, %s, %s, %s)""",
                (no, c["corp_code"], c["corp_name"], c["rcept_dt"], c["market"], c["halted"], x.payment_type, x.payment_source))
            conn.execute("INSERT INTO filings (rcept_no, event_id, rcept_dt, is_correction, version_no) VALUES (%s, %s, %s, false, 1)",
                         (no, no, c["rcept_dt"]))
            conn.execute(f"""INSERT INTO cb_values (rcept_no, event_id, extractor_version, {', '.join(VALUE_COLS)})
                             VALUES (%s, %s, %s, {', '.join(['%s'] * len(VALUE_COLS))})""",
                         (no, no, EXTRACTOR_VERSION, *[x.values[k] for k in VALUE_COLS]))
            for i, a in enumerate(x.allottees, 1):
                conn.execute("""INSERT INTO allottees (rcept_no, seq, event_id, name, relation, shares, lockup, amount)
                                VALUES (%s, %s, %s, %s, %s, NULL, NULL, %s)""", (no, i, no, a.name, a.relation, a.amount))


def report(conn, items):
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "values.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["rcept_no", "corp_name", "rcept_dt", "halted", "pym_det", *VALUE_COLS, "allottees"])
        for c in items:
            x = c["x"]
            w.writerow([c["rcept_no"], c["corp_name"], c["rcept_dt"], c["halted"], x.pym_det, *[x.values[k] for k in VALUE_COLS],
                        "; ".join(f"{a.name}/{a.relation or '-'}/{a.amount if a.amount is not None else '-'}" for a in x.allottees)])
    n = len(items)
    saved = conn.execute("SELECT count(*) FROM cb_values").fetchone()[0]
    L = [f"# CB 원본 {n}건 — 코드 칸 (추출 {EXTRACTOR_VERSION})", "", f"- 저장: cb_values {saved}건, "
         f"allottees {conn.execute('SELECT count(*) FROM allottees WHERE amount IS NOT NULL OR rcept_no IN (SELECT rcept_no FROM cb_values)').fetchone()[0]}명",
         f"- 대상자 없는 공시: {sum(1 for c in items if not c['x'].allottees)}", "", "## 칸별 채움률", "", "| 칸 | 값 있음 | NULL |", "|---|---|---|"]
    for k in VALUE_COLS:
        filled = sum(1 for c in items if c["x"].values[k] is not None)
        L.append(f"| {k} | {filled}/{n} ({filled / n * 100:.0f}%) | {n - filled} |")
    zero_old = sum(1 for c in items if c["x"].values["old_cb_shares"] == 0)
    L += ["", f"- old_cb_shares = 0 (예전 CB 없음): {zero_old}건, 정한 방식: {dict(Counter(c['x'].old_cb_rule for c in items))}",
          f"- min_price NULL (리픽싱 없음): {sum(1 for c in items if c['x'].values['min_price'] is None)}건",
          f"- shares_before NULL: {sum(1 for c in items if c['x'].values['shares_before'] is None)}건"]
    pym = Counter(c["x"].pym_det for c in items)
    L += ["", "## 13번 납입방법 (PYM_DET)", ""] + [f"- {k}: {v}" for k, v in pym.most_common()]
    non_cash = [c for c in items if c["x"].pym_det != "현금"]
    L += [f"  - 현금 아님 예시: " + ", ".join(f"{c['corp_name']} {c['rcept_no']}" for c in non_cash[:8])]
    L += ["", "## issue_method", ""] + [f"- {k}: {v}" for k, v in Counter(c["x"].values["issue_method"] for c in items).most_common()]
    flag = [c for c in items if c["x"].values["date_flag"]]
    L += ["", f"## date_flag: {len(flag)}건", "", f"- 종류: {dict(Counter(f for c in flag for f in c['x'].values['date_flag'].split(',')))}"]
    L += [f"- {c['corp_name']} {c['rcept_no']}: {c['x'].values['date_flag']} (납입 {c['x'].values['pay_date']}, 전환 시작 {c['x'].values['conv_start']}, 만기 {c['x'].values['maturity']})" for c in flag[:5]]
    # old_cb_shares 확인: 소계 vs 개별 줄 합
    mism, from_rows = [], []
    for c in items:
        cells = [x for x in c["doc"].body_cells() if not x.in_correction]
        sub = next((x.text for x in cells if x.code == "SUB_TOT_STK"), None)
        rows = [to_int(x.text) for x in cells if x.code == "EXE_STK"]
        s = sum(v for v in rows if v)
        if c["x"].old_cb_rule == "개별 줄 합":
            from_rows.append((c, sub, s))
        elif c["x"].old_cb_rule == "소계" and any(rows) and to_int(sub) != s:
            mism.append((c, to_int(sub), s))
    L += ["", "## old_cb_shares 규칙 확인 (소계 A vs 개별 줄 EXE_STK 합)", "",
          f"- 소계 '-'라 개별 줄 합을 쓴 것: {len(from_rows)}건"] + [f"  - {c['corp_name']} {c['rcept_no']}: 개별 줄 합 {s:,}" for c, _, s in from_rows]
    L += [f"- 소계 ≠ 개별 줄 합 (소계를 씀): {len(mism)}건"] + [f"  - {c['corp_name']} {c['rcept_no']}: 소계 {a:,} / 개별 줄 합 {s:,} (차이 {a - s:+,})" for c, a, s in mism]
    L += ["", "## 대표 4건 (cb_values 줄 전체)", ""]
    for no, label in SHOW.items():
        c = next((c for c in items if c["rcept_no"] == no), None)
        if c is None:
            L.append(f"- {label} {no}: 대상에 없음")
            continue
        L += [f"### {label} {no} ({c['corp_name']}, {c['rcept_dt']}, 납입방법 {c['x'].pym_det})", ""]
        L += [f"- {k}: {c['x'].values[k]}" for k in VALUE_COLS]
        L += [f"- 대상자: " + "; ".join(f"{a.name} / {a.relation or '-'} / {a.amount:,}원" if a.amount else f"{a.name} / {a.relation or '-'} / -" for a in c["x"].allottees), ""]
    (OUT / "checks.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


def main():
    with connect() as conn:
        items = load(conn)
        save(conn, items)
        from cb_payment_llm import apply as apply_llm  # 같은 scripts 폴더
        n = apply_llm(conn)
        report(conn, items)
    print(f"\n납입 방식 LLM 결과 다시 적용: {n}건")
    print(f"\n확인 자료 → {OUT}")


if __name__ == "__main__":
    main()
