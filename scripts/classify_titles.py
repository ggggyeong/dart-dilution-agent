"""b001_filings 제목을 현재 분류 코드로 다시 판정 → title_classifications(→ title_filings 뷰) → 결과 보고.

    uv run python scripts/classify_titles.py

API를 부르지 않는다. 분류 코드를 고친 뒤 이것만 다시 돌리면 된다.
"""

from collections import Counter

from filingscope.classify.answer_sheet import load_answers
from filingscope.classify.apply import reclassify_all
from filingscope.classify.report_title import CLASSIFIER_VERSION
from filingscope.config import get_settings
from filingscope.db import connect

KS_CORP_CODE = "00618410"
KS_EXPECTED = {  # tests/fixtures/의 정답 XML 3건
    "20261015000184": ("rights_issue", False),
    "20260911000607": ("cb", False),
    "20260930000448": ("cb", True),
}


def pct(n: int, total: int) -> str:
    return f"{n / total * 100:.1f}%" if total else "-"


def print_table(headers: list[str], rows: list[tuple]) -> None:
    cols = list(zip(headers, *rows)) if rows else [(h,) for h in headers]
    widths = [max(len(str(x)) for x in col) for col in cols]
    print("  " + "  ".join(str(h).ljust(w) for h, w in zip(headers, widths)))
    for r in rows:
        print("  " + "  ".join(str(x).ljust(w) for x, w in zip(r, widths)))


def fmt(t: tuple) -> str:
    event_type, is_corr, tag, mixed = t
    s = event_type or "none"
    if mixed:
        s += "+유무상"
    if is_corr:
        s += f" [{tag}]"
    return s


def main() -> None:
    settings = get_settings()
    a0, start, end = settings.analysis_start_date, settings.collect_start_date, settings.collect_end_date

    with connect() as conn:
        results = reclassify_all(conn)
        conn.commit()
        q = lambda sql, *args: conn.execute(sql, args).fetchall()  # noqa: E731

        print(f"===== 1단계 결과 보고 (분류 코드 {CLASSIFIER_VERSION}) =====")
        print(f"수집 기간 {start} ~ {end}, 분석 구간 {a0} ~ {end}")

        total, total_a = q("SELECT count(*), count(*) FILTER (WHERE rcept_dt >= %s) FROM b001_filings", a0)[0]
        print(f"\n[B001] 전체 {total:,}건 (분석 구간 {total_a:,}건), 제목 종류 {len(results)}개")

        print("\n[판정 결과 — 공시 건수]")
        rows = q("""SELECT CASE WHEN t.event_type = 'none' THEN 'none'
                                WHEN t.is_mixed THEN 'rights_issue (유무상)'
                                ELSE t.event_type END,
                           count(*), count(*) FILTER (WHERE NOT t.is_correction),
                           count(*) FILTER (WHERE b.rcept_dt >= %s)
                    FROM b001_filings b JOIN title_classifications t USING (report_nm)
                    GROUP BY 1 ORDER BY 1""", a0)
        print_table(["event_type", "전체", "원본(정정 제외)", "분석 구간"], rows)

        n_fil = q("SELECT count(*) FROM title_filings")[0][0]
        print(f"\n[정정본 비율 — rights_issue·cb {n_fil:,}건 중, 태그별]")
        rows = q("""SELECT coalesce(report_tag, '(원본)'), count(*),
                           count(*) FILTER (WHERE event_type = 'rights_issue'),
                           count(*) FILTER (WHERE event_type = 'cb')
                    FROM title_filings GROUP BY 1 ORDER BY 2 DESC""")
        print_table(["report_tag", "건수", "비율", "유상", "CB"], [(t, n, pct(n, n_fil), r, c) for t, n, r, c in rows])

        print("\n[정정명령부과 목록]")
        rows = q("""SELECT rcept_no, rcept_dt, corp_name, corp_cls, report_nm FROM title_filings
                    WHERE report_tag LIKE '%%정정명령부과%%' ORDER BY rcept_dt""")
        print_table(["rcept_no", "rcept_dt", "회사", "cls", "report_nm"], rows) if rows else print("  없음")

        print("\n[시장별 — rights_issue·cb] (Y 코스피, K 코스닥, N 코넥스, E 기타)")
        rows = q("""SELECT corp_cls,
                           count(*) FILTER (WHERE event_type = 'rights_issue'),
                           count(*) FILTER (WHERE event_type = 'cb'), count(*),
                           count(*) FILTER (WHERE rcept_dt >= %s)
                    FROM title_filings GROUP BY 1
                    ORDER BY array_position(ARRAY['Y','K','N','E']::text[], corp_cls::text)""", a0)
        print_table(["cls", "유상", "CB", "합계", "비율", "분석 구간"],
                    [(c, r, cb, n, pct(n, n_fil), na) for c, r, cb, n, na in rows])

        print("\n[생존 편향 — E(기타)인데 종목코드가 있는 rights_issue·cb = 상장폐지 후보]")
        rows = q("""SELECT event_type, count(*), count(*) FILTER (WHERE rcept_dt >= %s),
                           count(DISTINCT corp_code), count(DISTINCT corp_code) FILTER (WHERE rcept_dt >= %s)
                    FROM title_filings WHERE corp_cls = 'E' AND stock_code IS NOT NULL
                    GROUP BY 1 ORDER BY 1""", a0, a0)
        print_table(["event_type", "공시 전체", "공시 분석 구간", "회사 전체", "회사 분석 구간"], rows)
        yk = q("""SELECT count(*) FILTER (WHERE corp_cls IN ('Y','K')),
                         count(*) FILTER (WHERE corp_cls IN ('Y','K') AND rcept_dt >= %s) FROM title_filings""", a0)[0]
        print(f"  (참고: 코스피·코스닥 rights_issue·cb는 전체 {yk[0]:,}건, 분석 구간 {yk[1]:,}건)")

        print(f"\n[케이에스인더스트리 ({KS_CORP_CODE})]")
        rows = q("""SELECT rcept_no, rcept_dt, event_type, is_correction, coalesce(report_tag, ''), report_nm
                    FROM title_filings WHERE corp_code = %s ORDER BY rcept_dt, rcept_no""", KS_CORP_CODE)
        print_table(["rcept_no", "rcept_dt", "event_type", "정정", "tag", "report_nm"], rows)
        found = {r[0]: (r[2], r[3]) for r in rows}
        for rcept_no, exp in KS_EXPECTED.items():
            got = found.get(rcept_no)
            print(f"  정답 {rcept_no} {exp}: {'OK' if got == exp else f'FAIL (실제 {got})'}")

        counts = Counter(dict(q("SELECT report_nm, count(*) FROM b001_filings GROUP BY 1")))

    answers = {a.report_nm: a for a in load_answers()}
    print(f"\n[코드 vs 정답표(LLM)] 정답표 {len(answers)}개 / DB 제목 {len(results)}개")
    missing = [t for t in results if t not in answers]
    unsure = [a for a in answers.values() if a.is_unsure]
    compared = [a for a in answers.values() if not a.is_unsure and a.report_nm in results]
    mismatch = [a for a in compared
                if (results[a.report_nm].event_type, results[a.report_nm].is_correction,
                    results[a.report_nm].report_tag, results[a.report_nm].is_mixed) != a.as_tuple()]
    n_ok = len(compared) - len(mismatch)
    print(f"  비교 {len(compared)}개: 일치 {n_ok} / 불일치 {len(mismatch)}  "
          f"(공시 건수 기준 일치 {pct(sum(counts[a.report_nm] for a in compared if a not in mismatch), sum(counts[a.report_nm] for a in compared))})")
    print(f"  unsure(사용자 결정 대기) {len(unsure)}개, 정답표에 없는 제목 {len(missing)}개")
    if mismatch:
        print("  불일치 목록:")
        rows = []
        for a in sorted(mismatch, key=lambda a: -counts[a.report_nm]):
            r = results[a.report_nm]
            rows.append((counts[a.report_nm], a.report_nm,
                         fmt((r.event_type, r.is_correction, r.report_tag, r.is_mixed)), fmt(a.as_tuple()), a.reason))
        print_table(["건수", "제목", "코드", "정답(LLM)", "LLM 이유"], rows)
    if unsure:
        print("  unsure 목록:")
        print_table(["건수", "제목", "LLM 이유"],
                    [(counts[a.report_nm], a.report_nm, a.reason) for a in sorted(unsure, key=lambda a: -counts[a.report_nm])])


if __name__ == "__main__":
    main()
