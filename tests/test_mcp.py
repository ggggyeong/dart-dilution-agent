"""MCP 도구 명세 5장 테스트 + 서버 기동 (stdio 클라이언트). DB가 없으면 건너뛴다."""

import json
import sys
from datetime import date

import pytest

from filingscope.db import connect
from filingscope.facts import cb_facts, dilution
from filingscope.mcp import text_search, tools

AS_OF = "2026-10-19"


@pytest.fixture(scope="module")
def conn():
    try:
        c = connect()
    except Exception:
        pytest.skip("DB 없음")
    yield c
    c.close()


@pytest.fixture(scope="module")
def cache(conn):
    return tools.Cache.build()


def test_get_event_same_as_cb_facts(conn):
    r = tools.get_event("20260820000443", conn=conn)
    cs = r.pop("company_signals")                       # 알림과 같은 조합: 카드 + company_signals + dilution_flags
    df = r.pop("dilution_flags")                        # v2 (2026-10-24): 회사 합산 flags(unit_mismatch 등)를 그대로 붙임
    assert r == cb_facts("20260820000443", conn=conn)
    assert cs["repeat_count"] == r["company"]["repeat_count"]
    d = dilution(r["corp_code"], conn=conn)
    assert df == {**d["flags"], "reason": (d["dilution"] or {}).get("reason")}


def test_get_event_not_found(conn):
    assert tools.get_event("00000000000000", conn=conn) == {"error": "not_found", "rcept_no": "00000000000000"}


def test_dilution_entutech(conn):
    r = tools.get_company_dilution("엔투텍", AS_OF, conn=conn)
    assert r == dilution(r["corp_code"], date.fromisoformat(AS_OF), conn=conn)
    assert r["dilution"]["max_pct"] == 30.4


def test_dilution_koiz(conn):
    assert tools.get_company_dilution("코이즈", AS_OF, conn=conn)["dilution"]["max_pct"] == 5.8


def test_dilution_jeil_capital_reduction(conn):
    """calc_criteria v1 C-4 (2026-10-22): 감소로 단위 불일치 → capital_reduction 유지, max_pct는 NULL 대신 가장 최근 공시 1건으로."""
    r = tools.get_company_dilution("제일엠앤에스", AS_OF, conn=conn)
    assert r["flags"]["capital_reduction"] is True and r["flags"]["unit_mismatch"]
    assert r["dilution"]["basis_rcept_no"] == r["flags"]["unit_mismatch"][-1][1] and r["dilution"]["max_pct"] is not None


def test_dilution_samsung(conn):
    r = tools.get_company_dilution("삼성", AS_OF, conn=conn)
    print("삼성 →", json.dumps(r, ensure_ascii=False))
    assert r.get("error") in ("ambiguous", "not_found")


def test_compare_vuno_vs_sql(conn, cache):
    r = tools.compare_event("20260828001315", cache)
    assert r["group"] == "코스닥 주주배정" and r["metric"] == "dilution_pct" and r["value"] == 31.0
    # 손 SQL: 같은 그룹(코스닥 · CI_MTH 1·2·3)의 new_shares ÷ (shares_before + new_shares) 중 31.0 이하 비율
    hand = conn.execute(
        """WITH g AS (SELECT round(v.new_shares::numeric / (v.shares_before + v.new_shares) * 100, 1) AS d
                      FROM rights_values v JOIN events e ON e.event_id = v.event_id
                      WHERE e.event_type = '유상증자' AND e.market_at_filing = 'KOSDAQ' AND e.ci_mth IN (1, 2, 3)
                        AND v.new_shares IS NOT NULL AND v.shares_before IS NOT NULL)
           SELECT count(*) FILTER (WHERE d <= 31.0) * 100.0 / count(*), count(*) FROM g""").fetchone()
    print("뷰노 compare:", r, "| 손 SQL 백분위", round(float(hand[0]), 1), "n", hand[1])
    assert abs(r["percentile"] - float(hand[0])) <= 1


def test_compare_entutech(cache):
    r = tools.compare_event("20260820000443", cache)
    assert (r["metric"], r["value"], r["group"]) == ("total_pct", 17.2, "코스닥 CB")


def test_compare_aptamer_no_value(cache):
    assert tools.compare_event("20250909000002", cache)["error"] == "no_value"


def test_search_cb_refixing(cache):
    r = tools.search_events(cache, type="CB", date_from="2026-01-01", signal="refixing", min_dilution=10)
    print("CB · 2026 · refixing · 10% 이상:", r["n"], r["items"][:3])
    assert r["n"] > 0 and all(i["dilution_pct"] >= 10 and "refixing" in i["signals"] for i in r["items"])


def test_search_entutech(cache):
    assert tools.search_events(cache, company="엔투텍")["n"] == 4


def test_search_public_single(cache):
    r = tools.search_events(cache, signal="public_single")
    assert r["n"] == 1 and r["items"][0]["corp_name"] == "엔투텍"


def test_server_lists_and_calls_tools():
    """stdio로 서버를 띄워 도구 5개가 목록에 뜨고 호출되는지. search_filing_text는 question을 비워 임베딩 없이(원문 순서) 부른다."""
    import anyio
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    async def run():
        params = StdioServerParameters(command=sys.executable, args=["-m", "filingscope.mcp"])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as s:
                await s.initialize()
                listed = await s.list_tools()
                names = sorted(t.name for t in listed.tools)
                descs = {t.name: t.description for t in listed.tools}
                calls = {
                    "get_event": await s.call_tool("get_event", {"rcept_no": "20260820000443"}),
                    "get_company_dilution": await s.call_tool("get_company_dilution", {"company": "코이즈", "as_of": AS_OF}),
                    "compare_event": await s.call_tool("compare_event", {"rcept_no": "20260828001315"}),
                    "search_events": await s.call_tool("search_events", {"signal": "public_single"}),
                    "search_filing_text": await s.call_tool("search_filing_text", {"rcept_no": "20231013000400", "topic": "리픽싱", "question": ""}),
                }
                schemas = {t.name: t.input_schema for t in listed.tools}
                return names, descs, calls, schemas

    try:
        connect().close()
    except Exception:
        pytest.skip("DB 없음")
    names, descs, calls, schemas = anyio.run(run)
    assert schemas["search_filing_text"]["properties"]["topic"]["enum"] == ["청약절차", "리픽싱", "콜", "풋", "전체"]
    assert names == ["compare_event", "get_company_dilution", "get_event", "search_events", "search_filing_text"]
    from filingscope.mcp.server import DESC
    assert descs == DESC
    for n, res in calls.items():
        assert not res.is_error, n
        body = json.loads(res.content[0].text)
        assert "error" not in body, (n, body)
    assert json.loads(calls["get_company_dilution"].content[0].text)["dilution"]["max_pct"] == 5.8


def test_compare_small_sample(cache):
    """코스피 공모(n 11 < SMALL_SAMPLE_N 30)는 small_sample = true, 큰 그룹은 키 없음."""
    no = next(k for k, c in cache.cards.items() if tools.group_name(c) == "코스피 공모" and tools.metric(c)[1] is not None)
    r = tools.compare_event(no, cache)
    print("코스피 공모:", r)
    assert r["group"] == "코스피 공모" and r["n"] < 30 and r["small_sample"] is True
    assert "small_sample" not in tools.compare_event("20260828001315", cache)


def test_company_exact_name_first(conn):
    """정확히 같은 회사명이 1곳이면 그것, 없으면 부분 일치 (여러 곳이면 ambiguous)."""
    for q in ("엔투텍", "삼성", "삼성SDI"):
        print(q, "→", tools.find_company(q, conn))
    assert tools.find_company("엔투텍", conn) == {"corp_code": "01105621"}
    assert tools.find_company("삼성", conn)["error"] == "ambiguous"
    assert tools.find_company("삼성SDI", conn) == {"corp_code": "00126362"}
    assert tools.find_company("삼성SD", conn) == {"corp_code": "00126362"}   # 정확 일치 없음 → 부분 일치 1곳


def test_search_order_by_dilution_first_is_max(conn, cache):
    """order_by='dilution'은 limit 전에 전체를 정렬: 1번이 2024 유상증자 · 희석 20% 이상 중 최대, 건수도 SQL과 같음.
    (⑨-2 전 118건 → 우선주만 발행을 기타주식 칸에서 채운 뒤 119건: 하이로닉 20240906000394
     → calc_criteria v1 B-3 우선주만 발행은 희석률 NULL이라 다시 118건 — 손 SQL도 stock_type '우선'을 뺌)"""
    r = tools.search_events(cache, type="유상증자", date_from="2024-01-01", date_to="2024-12-31", min_dilution=20, order_by="dilution")
    n, top = conn.execute(
        """SELECT count(*), max(round(v.new_shares::numeric / (v.shares_before + v.new_shares) * 100, 1)) FROM rights_values v
           JOIN events e ON e.event_id = v.event_id JOIN filings f ON f.rcept_no = v.rcept_no
           WHERE e.event_type = '유상증자' AND f.rcept_dt BETWEEN '2024-01-01' AND '2024-12-31'
             AND v.stock_type IS DISTINCT FROM '우선'
             AND round(v.new_shares::numeric / (v.shares_before + v.new_shares) * 100, 1) >= 20""").fetchone()
    assert r["n"] == n and r["items"][0]["dilution_pct"] == float(top)
    assert [i["dilution_pct"] for i in r["items"]] == sorted((i["dilution_pct"] for i in r["items"]), reverse=True)
    assert tools.search_events(cache, type="유상증자", date_from="2024-01-01", date_to="2024-12-31", min_dilution=20)["items"][0]["rcept_dt"] >= "2024-12-01"  # 기본은 그대로 최신순



# ---------- search_filing_text (docs/cc_task_mcp_v2.md A-5) ----------
# 테스트에서는 OpenAI를 부르지 않는다: 질문 임베딩 자리에 DB의 조각 임베딩 하나를 고정값으로 넣음 (order는 similarity 그대로).

@pytest.fixture
def fixed_embed(conn, monkeypatch):
    row = conn.execute("SELECT embedding::text FROM rag_chunks WHERE method = 'section' AND embedding IS NOT NULL ORDER BY id LIMIT 1").fetchone()
    if row is None:
        pytest.skip("rag_chunks 임베딩 없음")
    v = json.loads(row[0])
    monkeypatch.setattr(text_search, "embed", lambda q: v)
    return v


def _has(out, s):
    return any(s in i["text"] for i in out["items"])


def test_text_refixing_daechang(conn, fixed_embed):
    out = text_search.search_filing_text("20231013000400", "리픽싱", "리픽싱 몇 달마다야?", conn=conn)
    assert _has(out, "매 7개월") and out["fallback"] is False and out["order"] == "similarity"
    assert [i["rank"] for i in out["items"]] == list(range(1, out["n"] + 1)) and all("T2" in i["topics"] for i in out["items"])


def test_text_call_carry(conn, fixed_embed):
    assert _has(text_search.search_filing_text("20230613000438", "콜", "콜옵션 몇 %야?", conn=conn), "70%")


def test_text_put_taewoong(conn, fixed_embed):
    assert _has(text_search.search_filing_text("20260422000229", "풋", "풋 언제부터야?", conn=conn), "2028년 4월 30일")


def test_text_subscription_systeeltec(conn, fixed_embed):
    out = text_search.search_filing_text("20250627000646", "청약절차", "발행가 어떻게 정해?", conn=conn)
    assert _has(out, "1차 발행가액과 2차 발행가액 중 낮은") and out["doc_type"] == "유상증자"


def test_text_topic_not_applicable(conn):
    out = text_search.search_filing_text("20231013000400", "청약절차", "발행가 어떻게 정해?", conn=conn)
    assert out == {"error": "topic_not_applicable", "doc_type": "CB", "available_topics": ["리픽싱", "콜", "풋"]}


def test_text_third_party_no_text(conn):
    assert text_search.search_filing_text("20230418000414", "청약절차", "발행가?", conn=conn)["error"] == "no_text"   # SFA넥셀 3자배정


def test_text_not_found_and_bad_rcept_no(conn):
    assert text_search.search_filing_text("00000000000000", "콜", "콜?", conn=conn) == {"error": "not_found", "rcept_no": "00000000000000"}
    assert text_search.search_filing_text("2023", "콜", "콜?", conn=conn)["error"] == "bad_rcept_no"


def test_text_fallback_no_call(conn, fixed_embed):
    out = text_search.search_filing_text("20260316000004", "콜", "콜옵션 있어?", conn=conn)   # 지투지바이오: T3 조각 0개
    n_all = conn.execute("SELECT count(*) FROM rag_chunks WHERE method = 'section' AND rcept_no = '20260316000004'").fetchone()[0]
    assert out["fallback"] is True and out["n"] == n_all


def test_text_all_topics_count(conn, fixed_embed):
    out = text_search.search_filing_text("20231013000400", "전체", "이 CB 조건 알려줘", conn=conn)
    n_all = conn.execute("SELECT count(*) FROM rag_chunks WHERE method = 'section' AND rcept_no = '20231013000400'").fetchone()[0]
    assert out["n"] == n_all and out["fallback"] is False


def test_text_same_call_same_order(conn, fixed_embed):
    a = text_search.search_filing_text("20260422000229", "풋", "풋 언제부터야?", conn=conn)
    b = text_search.search_filing_text("20260422000229", "풋", "풋 언제부터야?", conn=conn)
    assert [i["seq"] for i in a["items"]] == [i["seq"] for i in b["items"]]


def test_text_empty_question_document_order(conn):
    out = text_search.search_filing_text("20231013000400", "리픽싱", "", conn=conn)
    assert out["order"] == "document" and [i["seq"] for i in out["items"]] == sorted(i["seq"] for i in out["items"])
    assert all(i["score"] is None for i in out["items"])
