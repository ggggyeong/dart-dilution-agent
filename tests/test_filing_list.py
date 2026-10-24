"""수집 흐름: 기간 나누기, 페이지 순회, 013, 재시도, 거르기, 중복. 가짜 응답으로 인터넷 없이 실행."""

from datetime import date, timedelta

import httpx
import pytest

from filingscope.collect.dart_client import DartClient
from filingscope.collect.filing_list import add_months, collect, date_windows


# ---------- 기간 나누기 ----------

@pytest.mark.parametrize("start, end, n", [
    (date(2025, 10, 16), date(2026, 10, 15), 4),   # 1년
    (date(2023, 10, 16), date(2026, 10, 15), 12),  # 3년
    (date(2023, 4, 16), date(2026, 10, 15), 14),   # 3년 6개월 (현재 기본값)
    (date(2026, 1, 1), date(2026, 1, 31), 1),
    (date(2025, 11, 30), date(2026, 6, 30), 3),    # 말일 처리 (2월)
])
def test_date_windows_cover_without_gap(start, end, n):
    windows = date_windows(start, end)
    assert len(windows) == n
    assert windows[0][0] == start and windows[-1][1] == end
    for (_, a_end), (b_start, _) in zip(windows, windows[1:]):
        assert b_start == a_end + timedelta(days=1)  # 겹침·빠짐 없음
    for bgn, last in windows:
        assert bgn <= last < add_months(bgn, 3)  # 3개월 제한 안


def test_date_windows_first_year_boundaries():
    assert date_windows(date(2025, 10, 16), date(2026, 10, 15)) == [
        (date(2025, 10, 16), date(2026, 1, 15)),
        (date(2026, 1, 16), date(2026, 4, 15)),
        (date(2026, 4, 16), date(2026, 7, 15)),
        (date(2026, 7, 16), date(2026, 10, 15)),
    ]


# ---------- 가짜 DART ----------

def item(rcept_no, report_nm, corp_cls="K", corp_code="00000001"):
    return {"corp_code": corp_code, "corp_name": f"회사{rcept_no[-3:]}", "stock_code": "123456",
            "corp_cls": corp_cls, "report_nm": report_nm, "rcept_no": rcept_no,
            "flr_nm": "x", "rcept_dt": rcept_no[:8], "rm": ""}


def fake_client(pages_by_bgn: dict, calls: list, fail_plan: dict | None = None) -> DartClient:
    """pages_by_bgn: {bgn_de: [page1_items, page2_items, ...]} (없으면 013).
    fail_plan: {(bgn_de, page_no): [응답 status들]} — 앞에서부터 하나씩 소모."""
    fail_plan = fail_plan or {}

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.params
        bgn, page_no = p["bgn_de"], int(p["page_no"])
        calls.append((bgn, page_no))
        plan = fail_plan.get((bgn, page_no))
        if plan:
            st = plan.pop(0)
            if st == "NETWORK":
                raise httpx.ConnectError("boom")
            if st.startswith("HTTP_"):
                return httpx.Response(int(st[5:]), text="err")
            return httpx.Response(200, json={"status": st, "message": f"msg {st}"})
        pages = pages_by_bgn.get(bgn)
        if not pages:
            return httpx.Response(200, json={"status": "013", "message": "조회된 데이타가 없습니다."})
        return httpx.Response(200, json={
            "status": "000", "message": "정상", "page_no": page_no, "page_count": 100,
            "total_count": sum(map(len, pages)), "total_page": len(pages), "list": pages[page_no - 1],
        })

    http = httpx.Client(base_url="https://test", transport=httpx.MockTransport(handler))
    return DartClient("SECRET", http=http, max_retries=3, backoff=0, sleep=lambda s: None)


def run(client, tmp_path, start=date(2026, 1, 1), end=date(2026, 6, 30), store=None):
    store = {} if store is None else store
    logs = []

    def save(filings):
        new = [f for f in filings if f.rcept_no not in store]
        for f in new:
            store[f.rcept_no] = f
        return len(new)

    summary = collect(client, start, end, tmp_path, save, lambda **r: logs.append(r), "test")
    return summary, store, logs


# ---------- 흐름 ----------

def test_pages_store_all_and_013(tmp_path):
    pages = {
        "20260101": [
            [item("20260105000001", "주요사항보고서(유상증자결정)"),
             item("20260105000002", "주요사항보고서(교환사채권발행결정)"),
             item("20260105000003", "[기재정정]주요사항보고서(전환사채권발행결정)", corp_cls="Y")],
            [item("20260210000004", "주요사항보고서(유무상증자결정)"),
             item("20260210000005", "주요사항보고서(유상증자결정)", corp_cls="E"),
             item("20260210000006", "주요사항보고서(자기주식취득결정)", corp_cls="N")],
        ],
        # 20260401 구간은 013
    }
    calls = []
    summary, store, logs = run(fake_client(pages, calls), tmp_path)

    assert calls == [("20260101", 1), ("20260101", 2), ("20260401", 1)]
    assert len(store) == 6  # 제목·시장 상관없이 전부
    assert store["20260210000005"].corp_cls == "E"
    assert summary.fetched == 6 and summary.by_cls == {"K": 3, "Y": 1, "E": 1, "N": 1}
    assert [l["status"] for l in logs] == ["000", "000", "013"]
    assert all(l["ok"] for l in logs)
    assert (tmp_path / "20260101_20260331" / "page_002.json").exists()
    assert (tmp_path / "20260401_20260630" / "page_001.json").exists()  # 013 응답도 보관


def test_retry_then_success(tmp_path):
    pages = {"20260101": [[item("20260105000001", "주요사항보고서(유상증자결정)")]]}
    calls = []
    plan = {("20260101", 1): ["020", "NETWORK", "HTTP_503"]}  # 3번 실패 후 4번째 성공
    summary, store, logs = run(fake_client(pages, calls, plan), tmp_path)
    assert "20260105000001" in store
    assert logs[0]["ok"] and logs[0]["attempts"] == 4


def test_retry_exhausted_is_logged(tmp_path):
    pages = {"20260101": [[item("20260105000001", "주요사항보고서(유상증자결정)")],
                          [item("20260210000002", "주요사항보고서(유상증자결정)")]]}
    plan = {("20260101", 2): ["800"] * 4}
    summary, store, logs = run(fake_client(pages, [], plan), tmp_path)
    failed = [l for l in logs if not l["ok"]]
    assert len(failed) == 1 and failed[0]["status"] == "800" and failed[0]["attempts"] == 4
    assert failed[0]["page_no"] == 2
    assert set(store) == {"20260105000001"}  # 실패 페이지만 빠지고 나머지는 진행


@pytest.mark.parametrize("status", ["010", "100", "HTTP_400"])
def test_non_retryable_fails_immediately(tmp_path, status):
    calls = []
    plan = {("20260101", 1): [status]}
    summary, store, logs = run(fake_client({}, calls, plan), tmp_path)
    assert logs[0]["ok"] is False and logs[0]["attempts"] == 1
    assert calls.count(("20260101", 1)) == 1


def test_api_key_not_in_logs(tmp_path):
    plan = {("20260101", 1): ["NETWORK"] * 4}
    _, _, logs = run(fake_client({}, [], plan), tmp_path)
    assert all("SECRET" not in (l["message"] or "") for l in logs)


def test_duplicate_rcept_no_not_saved_twice(tmp_path):
    pages = {"20260101": [[item("20260105000001", "주요사항보고서(유상증자결정)")]]}
    store = {}
    run(fake_client(pages, []), tmp_path, store=store)
    summary, store, _ = run(fake_client(pages, []), tmp_path, store=store)
    assert len(store) == 1
    assert summary.inserted == 0 and summary.duplicates == 1
