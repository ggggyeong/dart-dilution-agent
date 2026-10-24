"""크로스체크: 회사별 API 호출(이어받기·020 멈춤)과 비교 로직. 가짜 응답 사용."""

from datetime import date

import httpx

from filingscope.collect.dart_client import DartClient
from filingscope.collect.ds005 import run_ds005
from filingscope.validate.crosscheck import compare


# ---------- 비교 ----------

def o(no, corp="C1", et="rights_issue", mixed=False, corr=False):
    return {"rcept_no": no, "corp_code": corp, "event_type": et, "is_mixed": mixed, "is_correction": corr}


def a(api, no, corp="C1"):
    return {"api": api, "rcept_no": no, "corp_code": corp}


def test_compare_final_version_only():
    # 사건1: 원본 001 → 정정 002 (API는 002만), 사건2: 원본 003 (정정 없음)
    ours = [o("20240101000001"), o("20240102000002", corr=True), o("20240201000003")]
    api = [a("piicDecsn", "20240102000002"), a("piicDecsn", "20240201000003")]
    r = compare(ours, api, b001_rcept_nos=set())
    assert set(r.api_status.values()) == {"match"}
    assert r.our_status == {"20240101000001": "superseded", "20240102000002": "exact", "20240201000003": "exact"}
    assert r.count_diff == [] and r.count_same == 1  # 원본 2 = API 2


def test_compare_missed_type_diff_uncovered():
    ours = [
        o("20240301000010", et="rights_issue", mixed=False),   # API는 유무상 → type_diff
        o("20240401000020", corp="C2", et="cb"),                 # API에 없음 → uncovered
    ]
    api = [
        a("pifricDecsn", "20240301000010"),
        a("cvbdIsDecsn", "20240501000030"),                      # B001에 있으나 우리 판정 none
        a("piicDecsn", "20240601000040"),                        # B001 목록에 없음
    ]
    r = compare(ours, api, b001_rcept_nos={"20240501000030", "20240301000010", "20240401000020"})
    assert r.api_status[("pifricDecsn", "20240301000010")] == "type_diff"
    assert r.api_status[("cvbdIsDecsn", "20240501000030")] == "missed_title_none"
    assert r.api_status[("piicDecsn", "20240601000040")] == "missed_not_in_b001"
    assert r.our_status["20240401000020"] == "uncovered"
    assert ("C2", "cb", 1, 0) in r.count_diff


def test_superseded_needs_same_family():
    # 같은 회사라도 CB 최종본이 유상증자 원본을 덮지는 않는다
    ours = [o("20240101000001", et="rights_issue")]
    api = [a("cvbdIsDecsn", "20240301000005")]
    assert compare(ours, api, set()).our_status["20240101000001"] == "uncovered"


# ---------- 회사별 호출 ----------

def fake_client(plan: dict, calls: list) -> DartClient:
    """plan: {(api, corp): status 또는 list 항목}"""
    def handler(req: httpx.Request) -> httpx.Response:
        api = req.url.path.strip("/").removesuffix(".json")
        corp = req.url.params["corp_code"]
        calls.append((api, corp))
        v = plan.get((api, corp), "013")
        if isinstance(v, list):
            return httpx.Response(200, json={"status": "000", "message": "정상", "list": v})
        return httpx.Response(200, json={"status": v, "message": f"msg {v}"})
    http = httpx.Client(base_url="https://test", transport=httpx.MockTransport(handler))
    return DartClient("SECRET", http=http, max_retries=0, backoff=0, sleep=lambda s: None)


def run(client, corps, tmp_path, done=frozenset()):
    saved, logs = [], []
    s = run_ds005(client, corps, date(2023, 4, 16), date(2026, 10, 15), tmp_path, set(done),
                  lambda api, items: saved.extend((api, i["rcept_no"]) for i in items),
                  lambda **row: logs.append(row), "t", pause=0, sleep=lambda s: None)
    return s, saved, logs


def test_resume_skips_done_and_saves_rows(tmp_path):
    calls = []
    plan = {("cvbdIsDecsn", "C1"): [{"rcept_no": "20240101000001", "corp_code": "C1"}]}
    s, saved, logs = run(fake_client(plan, calls), ["C1", "C2"], tmp_path, done={("piicDecsn", "C1")})
    assert ("piicDecsn", "C1") not in calls and s.skipped == 1
    assert len(calls) == 5 and s.calls == 5
    assert saved == [("cvbdIsDecsn", "20240101000001")]
    assert all(l["ok"] and l["corp_code"] in {"C1", "C2"} for l in logs)  # 013도 성공
    assert (tmp_path / "cvbdIsDecsn" / "C1.json").exists()


def test_stops_on_daily_limit(tmp_path):
    calls = []
    plan = {("pifricDecsn", "C1"): "020"}
    s, _, logs = run(fake_client(plan, calls), ["C1", "C2"], tmp_path)
    assert s.stopped_on_limit
    assert calls == [("piicDecsn", "C1"), ("pifricDecsn", "C1")]  # 바로 멈춤
    assert logs[-1]["ok"] is False and logs[-1]["status"] == "020"
