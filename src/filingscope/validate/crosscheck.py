"""제목 판정(filings) ↔ DART 전용 API 결과 비교.

API는 사건마다 최종본 1건만 준다. 그래서
1. API → 우리: API 접수번호가 우리 판정에 같은 종류로 있는가 (없으면 놓친 것)
2. 우리 → API: 우리 공시마다 같은 회사·같은 계열(유상/CB)의 API 공시 중 접수번호가 같거나 더 나중인 것이 있는가
   (최종본이 있으면 그 앞의 원본·정정본은 정상. 하나도 없으면 잘못 잡았거나 API 누락)
3. 사건 수: 회사·계열별 우리 원본 수 = API 건수 인가
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from filingscope.collect.ds005 import DS005_APIS


def family(event_type: str) -> str:
    return "rights" if event_type == "rights_issue" else "cb"


@dataclass
class CrosscheckResult:
    api_status: dict[tuple[str, str], str] = field(default_factory=dict)  # (api, rcept_no) → 상태
    our_status: dict[str, str] = field(default_factory=dict)  # rcept_no → exact / superseded / uncovered
    count_diff: list[tuple[str, str, int, int]] = field(default_factory=list)  # (corp, family, 우리 원본, API)
    count_same: int = 0


def compare(ours: list[dict], api_rows: list[dict], b001_rcept_nos: set[str]) -> CrosscheckResult:
    """ours: filings 행 {rcept_no, corp_code, event_type, is_mixed, is_correction}
    api_rows: {api, rcept_no, corp_code}
    b001_rcept_nos: B001 목록에 있는 접수번호 전체 (놓친 것이 제목 판정 탓인지 구분)"""
    r = CrosscheckResult()
    ours_by_no = {o["rcept_no"]: o for o in ours}

    # 1. API → 우리
    for a in api_rows:
        exp_type, exp_mixed = DS005_APIS[a["api"]]
        o = ours_by_no.get(a["rcept_no"])
        if o is None:
            st = "missed_title_none" if a["rcept_no"] in b001_rcept_nos else "missed_not_in_b001"
        elif o["event_type"] != exp_type or (exp_type == "rights_issue" and bool(o["is_mixed"]) != exp_mixed):
            st = "type_diff"
        else:
            st = "match"
        r.api_status[(a["api"], a["rcept_no"])] = st

    # 2. 우리 → API
    api_nos = {a["rcept_no"] for a in api_rows}
    latest: dict[tuple[str, str], str] = {}  # (corp, family) → API 최종본 중 가장 나중 접수번호
    for a in api_rows:
        key = (a["corp_code"], family(DS005_APIS[a["api"]][0]))
        latest[key] = max(latest.get(key, ""), a["rcept_no"])
    for o in ours:
        if o["rcept_no"] in api_nos:
            st = "exact"
        elif latest.get((o["corp_code"], family(o["event_type"])), "") > o["rcept_no"]:
            st = "superseded"
        else:
            st = "uncovered"
        r.our_status[o["rcept_no"]] = st

    # 3. 사건 수
    ours_orig = Counter((o["corp_code"], family(o["event_type"])) for o in ours if not o["is_correction"])
    api_cnt = Counter((a["corp_code"], family(DS005_APIS[a["api"]][0])) for a in api_rows)
    for key in sorted(set(ours_orig) | set(api_cnt)):
        if ours_orig[key] == api_cnt[key]:
            r.count_same += 1
        else:
            r.count_diff.append((*key, ours_orig[key], api_cnt[key]))
    return r


def summarize(r: CrosscheckResult) -> dict[str, Counter]:
    by_api: dict[str, Counter] = defaultdict(Counter)
    for (api, _), st in r.api_status.items():
        by_api[api][st] += 1
    return {"api": dict(by_api), "ours": Counter(r.our_status.values())}
