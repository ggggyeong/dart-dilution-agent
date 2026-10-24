"""MCP 5번째 도구 search_filing_text 본체 (docs/cc_task_mcp_v2.md A, docs/mcp_spec.md v2 6장).

공시 1건의 조항 원문 조각(rag_chunks, method='section')을 주제별로 돌려준다. 숫자 계산은 하지 않는다.
- 후보 = 메타데이터 필터: 그 공시 + 주제(topics 배열에 T1~T4). '전체'면 그 공시 조각 전부. 자르지 않고 전부 준다.
- 안전망 = 고른 주제의 조각이 0개면 그 공시 조각 전부 + fallback true.
- 순서 = question 임베딩과 조각 임베딩의 코사인 유사도 내림차순 (실험 exp3 rule_vector와 같은 모델 · 같은 계산 · 같은 동점 처리).
  question이 비거나 임베딩 호출이 실패하면 원문 순서(seq = 칸 순서 → 조각 순서), order = "document".
- 같은 question은 임베딩을 메모리 캐시로 재사용 (같은 입력 → 같은 순서).
"""

import re

from filingscope.config import get_settings
from filingscope.db import connect

EMBED_MODEL = "text-embedding-3-small"   # 조각 임베딩과 같은 모델 (scripts/rag_chunk.py)
TOPICS = {"청약절차": "T1", "리픽싱": "T2", "콜": "T3", "풋": "T4"}
AVAILABLE = {"유상증자": ["청약절차"], "CB": ["리픽싱", "콜", "풋"]}
FIELD_LABEL = {"FREE_TEXT": "기타 투자판단에 참고할 사항", "EXE_REG": "전환가액 조정에 관한 사항", "MIN_RSN": "최저 조정가액 근거",
               "OPT_FCT": "옵션에 관한 사항", "ISS_FUNC": "발행가 산정방법", "PRC_PLN": "실권주 처리계획"}
_QCACHE: dict[str, list[float]] = {}


def embed(question: str) -> list[float]:
    if question not in _QCACHE:
        from openai import OpenAI
        client = OpenAI(api_key=get_settings().openai_api_key.get_secret_value(), timeout=30, max_retries=2)
        _QCACHE[question] = client.embeddings.create(model=EMBED_MODEL, input=[question]).data[0].embedding
    return _QCACHE[question]


def _vec(v) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in v) + "]"   # 실험(scripts/rag_experiments.py)과 같은 표기


def search_filing_text(rcept_no: str, topic: str, question: str, conn=None) -> dict:
    no = (rcept_no or "").strip()
    if not re.fullmatch(r"\d{14}", no):
        return {"error": "bad_rcept_no", "rcept_no": rcept_no}
    if topic != "전체" and topic not in TOPICS:
        return {"error": "bad_topic", "topic": topic, "allowed": [*TOPICS, "전체"]}
    if conn is None:
        with connect() as c:
            return search_filing_text(no, topic, question, conn=c)
    ev = conn.execute("""SELECT e.event_type, e.ci_mth, e.corp_name, f.rcept_dt FROM filings f JOIN events e ON e.event_id = f.event_id
                         WHERE f.rcept_no = %s""", (no,)).fetchone()
    if ev is None:
        return {"error": "not_found", "rcept_no": no}
    doc_type, ci, corp_name, rcept_dt = ev
    if not (doc_type == "CB" or ci in (1, 2, 3)):
        return {"error": "no_text", "reason": "원문 조항 검색은 주주배정 유상증자와 CB만 지원"}
    if topic != "전체" and topic not in AVAILABLE[doc_type]:
        return {"error": "topic_not_applicable", "doc_type": doc_type, "available_topics": AVAILABLE[doc_type]}

    order = "similarity" if (question or "").strip() else "document"
    qv = None
    if order == "similarity":
        try:
            qv = _vec(embed(question))
        except Exception:  # noqa: BLE001 — 임베딩이 안 되면 원문 순서로 (오류로 멈추지 않음)
            order = "document"
    rows = conn.execute(
        """SELECT seq, field, section_title, topics, text,
                  CASE WHEN %(qv)s::text IS NULL THEN NULL ELSE 1 - (embedding <=> %(qv)s::vector) END
           FROM rag_chunks WHERE method = 'section' AND rcept_no = %(no)s ORDER BY seq""", {"qv": qv, "no": no}).fetchall()
    want = TOPICS.get(topic)
    pick = [r for r in rows if want in (r[3] or [])] if want else rows
    fallback = bool(want) and not pick
    if fallback:
        pick = rows
    if order == "similarity":
        pick = sorted(pick, key=lambda r: (-r[5], r[0]))   # 유사도 내림차순, 같으면 원문 순서
    items = [{"rank": i + 1, "seq": s, "field": f, "field_label": FIELD_LABEL.get(f, f), "section_title": st, "topics": ts,
              "score": round(float(sc), 4) if sc is not None and order == "similarity" else None, "text": x}
             for i, (s, f, st, ts, x, sc) in enumerate(pick)]
    return {"rcept_no": no, "corp_name": corp_name, "rcept_dt": rcept_dt.isoformat(), "doc_type": doc_type, "topic": topic,
            "fallback": fallback, "order": order, "n": len(items), "chars": sum(len(i["text"]) for i in items), "items": items}
