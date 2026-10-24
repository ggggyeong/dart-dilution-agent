"""연결 확인: OpenDART list / document, pykrx 주가, KRX 로그인(지수), PostgreSQL.

각 항목을 독립적으로 실행해 하나가 실패해도 나머지 결과를 볼 수 있다.

    uv run python scripts/check_connections.py
"""

import io
import json
import zipfile
from datetime import date, datetime, timedelta

import httpx

from filingscope.config import DATA_DIR, get_settings

DART_BASE = "https://opendart.fss.or.kr/api"
TARGET_KEYWORDS = ("유상증자결정", "전환사채권발행결정")


def _scrub(text: str) -> str:
    """오류 메시지에 섞인 API 키를 가린다."""
    return text.replace(get_settings().dart_api_key.get_secret_value(), "***")


def check_dart_list() -> list[dict]:
    """B001(주요사항보고서), 최근 7일 목록을 1회 조회한다."""
    end = date.today()
    params = {
        "crtfc_key": get_settings().dart_api_key.get_secret_value(),
        "bgn_de": (end - timedelta(days=7)).strftime("%Y%m%d"),
        "end_de": end.strftime("%Y%m%d"),
        "pblntf_ty": "B",
        "pblntf_detail_ty": "B001",
        "page_count": 100,
    }
    resp = httpx.get(f"{DART_BASE}/list.json", params=params, timeout=30)
    resp.raise_for_status()
    body = resp.json()
    if body["status"] != "000":
        raise RuntimeError(f"DART status {body['status']}: {body['message']}")

    items = body["list"]
    targets = [it for it in items if any(k in it["report_nm"] for k in TARGET_KEYWORDS)]
    print(f"  {params['bgn_de']}~{params['end_de']} B001 총 {body['total_count']}건 "
          f"(이번 페이지 {len(items)}건, 유상증자·CB {len(targets)}건)")
    for it in targets[:5]:
        print(f"    {it['rcept_dt']} {it['corp_name']} | {it['report_nm']} | {it['rcept_no']}")
    return items


def check_dart_document(items: list[dict]) -> None:
    """목록 중 1건(유상증자·CB 우선)의 원본 XML zip을 받아 data/raw/에 저장한다."""
    if not items:
        raise RuntimeError("list 결과가 없어 document를 받을 대상이 없음")
    item = next(
        (it for it in items if any(k in it["report_nm"] for k in TARGET_KEYWORDS)),
        items[0],
    )
    params = {
        "crtfc_key": get_settings().dart_api_key.get_secret_value(),
        "rcept_no": item["rcept_no"],
    }
    resp = httpx.get(f"{DART_BASE}/document.xml", params=params, timeout=60)
    resp.raise_for_status()
    if not resp.content.startswith(b"PK"):  # 오류 시 zip 대신 XML 메시지가 온다
        raise RuntimeError(f"zip이 아닌 응답: {resp.text[:200]}")

    out_dir = DATA_DIR / "raw" / item["rcept_no"]
    out_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = zf.namelist()
        zf.extractall(out_dir)
    meta = {
        "rcept_no": item["rcept_no"],
        "corp_name": item["corp_name"],
        "stock_code": item["stock_code"],
        "report_nm": item["report_nm"],
        "available_date": item["rcept_dt"],  # 공개일(접수일)
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    print(f"  {item['corp_name']} | {item['report_nm']}")
    print(f"  저장: {out_dir.relative_to(DATA_DIR.parent)}/ {names}")


def check_pykrx() -> None:
    """삼성전자(005930) 최근 2주 수정주가. KRX 로그인 없이도 된다."""
    from pykrx import stock

    end = date.today()
    df = stock.get_market_ohlcv(
        (end - timedelta(days=14)).strftime("%Y%m%d"), end.strftime("%Y%m%d"), "005930"
    )
    if df.empty:
        raise RuntimeError("빈 결과")
    print(f"  005930 {len(df)}거래일, 마지막 {df.index[-1].date()} 종가 {df['종가'].iloc[-1]:,}")


def check_krx_login() -> None:
    """코스피 지수(1001) 최근 2주. KRX 로그인이 있어야 된다."""
    from pykrx import stock

    end = date.today()
    df = stock.get_index_ohlcv(
        (end - timedelta(days=14)).strftime("%Y%m%d"), end.strftime("%Y%m%d"), "1001"
    )
    if df.empty:
        raise RuntimeError("빈 결과 — KRX 로그인 실패 가능성")
    print(f"  코스피 {len(df)}거래일, 마지막 {df.index[-1].date()} 종가 {df['종가'].iloc[-1]:,.2f}")


def check_db() -> None:
    """PostgreSQL 접속 후 서버·pgvector 버전 확인."""
    import psycopg

    with psycopg.connect(get_settings().postgres_conninfo, connect_timeout=5) as conn:
        server = conn.execute("SHOW server_version").fetchone()[0]
        row = conn.execute(
            "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
        ).fetchone()
    if row is None:
        raise RuntimeError(f"PostgreSQL {server} 접속은 되지만 pgvector 확장이 없음")
    print(f"  PostgreSQL {server}, pgvector {row[0]}")


def main() -> int:
    get_settings()  # pykrx import 전에 KRX_ID/KRX_PW를 환경 변수로 넘긴다
    results: dict[str, bool] = {}
    items: list[dict] = []

    def run(name, fn, *args):
        print(f"[{name}]")
        try:
            out = fn(*args)
            results[name] = True
            print("  -> OK")
            return out
        except Exception as e:  # 진단용 스크립트이므로 모든 오류를 잡아 보고
            results[name] = False
            print(f"  -> FAIL {type(e).__name__}: {_scrub(str(e))}")
            return None

    items = run("DART list", check_dart_list) or []
    run("DART document", check_dart_document, items)
    run("pykrx 주가", check_pykrx)
    run("KRX 지수(로그인)", check_krx_login)
    run("DB", check_db)

    print("\n요약: " + ", ".join(f"{k} {'OK' if v else 'FAIL'}" for k, v in results.items()))
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
