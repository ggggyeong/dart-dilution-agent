"""원문(document.xml) 받기 → data/raw/document/{rcept_no}/ + documents 표.

    uv run python scripts/fetch_documents.py rights-2026    # 2026년 유상증자 전부 (원본 + 정정, 유무상 포함)
    uv run python scripts/fetch_documents.py rights-old     # 2023·2024·2025년 연도별 20건 (증자방식이 섞이게)
    uv run python scripts/fetch_documents.py rights-orig-2023-2025   # 2023-04-17 ~ 2025-12-31 유상증자 원본 전부 (정정 제외)
    uv run python scripts/fetch_documents.py cb-2023-2026   # 전환사채(권)발행결정 원본 + 정정 (정정본은 파일만, documents 표에는 원본만)

이미 받은 접수번호는 건너뛰므로 여러 번 돌려도 된다. 하루 한도(020)면 멈춘다.
"""

import argparse
import csv
import random
from collections import defaultdict
from datetime import datetime

from psycopg.types.json import Jsonb

from filingscope.collect.dart_client import DartClient
from filingscope.collect.document import fetch_documents
from filingscope.config import DATA_DIR, get_settings
from filingscope.db import connect
from filingscope.db.filings import insert_collect_log

OLD_YEARS, OLD_PER_YEAR, OLD_SEED = ("2023", "2024", "2025"), 20, 42
OLD_SAMPLE_CSV = DATA_DIR / "reports" / "old_sample_rights_issue.csv"


def targets_2026(conn) -> list[str]:
    return [r[0] for r in conn.execute(
        """SELECT rcept_no FROM title_filings WHERE event_type = 'rights_issue' AND rcept_dt >= '2026-01-01'
           ORDER BY rcept_no""")]


def targets_orig_old(conn) -> list[str]:
    """2026년 이전 유상증자 원본 전부. 지금 시장 구분(corp_cls)으로 거르지 않는다 — 공시 당시 상장사였다가
    지금은 상장폐지된 회사가 빠지지 않게. 상장 여부는 A 태그(check_listing.py)가 공시 당시 시세로 정한다."""
    return [r[0] for r in conn.execute(
        """SELECT rcept_no FROM title_filings WHERE event_type = 'rights_issue' AND NOT is_correction
           AND rcept_dt < '2026-01-01' ORDER BY rcept_no""")]


CB_WHERE = """rcept_dt BETWEEN '2023-04-17' AND '2026-10-15'
               AND (report_nm LIKE '%%전환사채권발행결정%%' OR report_nm LIKE '%%전환사채발행결정%%')"""  # 자기전환사채 · 콜옵션 · BW · EB 제외


def targets_cb(conn) -> list[str]:
    return [r[0] for r in conn.execute(f"SELECT rcept_no FROM b001_filings WHERE {CB_WHERE} ORDER BY rcept_no")]


def cb_originals(conn) -> set[str]:
    """앞머리([기재정정] 등) 없는 원본."""
    return {r[0] for r in conn.execute(f"SELECT rcept_no FROM b001_filings WHERE {CB_WHERE} AND report_nm !~ '^\\s*\\['")}


def targets_old(conn) -> list[str]:
    """크로스체크 때 받은 전용 API 결과의 증자방식(ic_mthn)으로 연도별 고르게 뽑는다. 유무상은 별도 갈래."""
    rows = conn.execute(
        """SELECT left(a.rcept_no, 4), a.rcept_no,
                  CASE WHEN a.api = 'pifricDecsn' THEN '유무상증자' ELSE a.raw->>'ic_mthn' END
           FROM crosscheck_api_rows a JOIN title_filings f USING (rcept_no)
           WHERE a.api IN ('piicDecsn', 'pifricDecsn') AND left(a.rcept_no, 4) = ANY(%s)
           ORDER BY 2""", (list(OLD_YEARS),)).fetchall()
    rng = random.Random(OLD_SEED)
    picked = []
    for year in OLD_YEARS:
        groups = defaultdict(list)
        for y, no, m in rows:
            if y == year:
                groups[m].append(no)
        for g in groups.values():
            rng.shuffle(g)
        order = sorted(groups)
        chosen = []
        while len(chosen) < OLD_PER_YEAR and any(groups.values()):
            for m in order:  # 증자방식마다 돌아가며 하나씩
                if groups[m] and len(chosen) < OLD_PER_YEAR:
                    chosen.append((year, m, groups[m].pop()))
        picked += chosen
    OLD_SAMPLE_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OLD_SAMPLE_CSV.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["year", "ic_mthn", "rcept_no"])
        w.writerows(picked)
    print(f"옛 버전 표본 {len(picked)}건 → {OLD_SAMPLE_CSV.name}")
    return [no for *_, no in picked]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("target", choices=["rights-2026", "rights-old", "rights-orig-2023-2025", "cb-2023-2026"])
    args = ap.parse_args()
    settings = get_settings()
    run_id = datetime.now().strftime("%Y%m%dT%H%M%S")

    with connect() as conn:
        targets = {"rights-2026": targets_2026, "rights-old": targets_old, "rights-orig-2023-2025": targets_orig_old,
                   "cb-2023-2026": targets_cb}[args.target](conn)
        keep = cb_originals(conn) if args.target == "cb-2023-2026" else None  # documents 표에 넣을 것 (None = 전부)
        raw_dir = DATA_DIR / "raw" / "document"
        done = {r[0] for r in conn.execute("SELECT rcept_no FROM documents")}
        done |= {p.name for p in raw_dir.iterdir() if p.is_dir() and any(p.glob("*.xml"))}  # 파일만 둔 정정본
        print(f"{args.target}: 대상 {len(targets)}건, 이미 받음 {len(set(targets) & done)}건 (run_id {run_id})")

        def save_info(info):
            if keep is not None and info.rcept_no not in keep:
                return  # 정정본: 파일만 저장
            conn.execute(
                """INSERT INTO documents (rcept_no, main_file, files, doc_code, doc_name, formula_version, formula_adate)
                   VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (rcept_no) DO NOTHING""",
                (info.rcept_no, info.main_file, Jsonb(info.files), info.doc_code, info.doc_name,
                 info.formula_version, info.formula_adate))
            conn.commit()

        n = [0]

        def log_call(**row):
            insert_collect_log(conn, **row)
            conn.commit()
            n[0] += 1
            if not row["ok"]:
                print(f"  FAIL {row['rcept_no']}: {row['status']} {row['message']}")
            if n[0] % 200 == 0:
                print(f"  ... {n[0]}건")

        client = DartClient(settings.dart_api_key.get_secret_value())
        s = fetch_documents(client, targets, DATA_DIR / "raw" / "document", done, save_info, log_call, run_id)
    print(f"호출 {s.calls}, 저장 {s.saved}, 건너뜀 {s.skipped}, 실패 {len(s.failed)}")
    if s.stopped_on_limit:
        print("하루 한도 초과(020)로 멈춤 — 내일 같은 명령을 다시 실행하면 이어서 받는다")


if __name__ == "__main__":
    main()
