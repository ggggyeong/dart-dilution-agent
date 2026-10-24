# MCP 도구 명세 v2

> **v2 (2026-10-24, docs/cc_task_mcp_v2.md):** 5번째 도구 `search_filing_text`를 새 RAG(rag_chunks section · topics)로 다시 등록 → 도구 5개 (6장). get_event 출력에 `dilution_flags`(회사 합산 dilution()의 flags + reason, unit_mismatch 노출용 — 새 계산 아님) 추가. 테스트 27개 (5장). 점검 결과 docs/mcp_v2_report.md.
> v1.3 (2026-10-19): search_events에 order_by ("rcept_dt" 기본 | "dilution").
> v1.5 (2026-10-23): search_filing_text를 RAG v1과 함께 보관 → 도구 4개 (코드 · 테스트 3개는 archive/2026-10-23_rag_v1/mcp_search_filing_text.py). 서버 목록 테스트는 4개로. RAG는 T1~T4로 재구축 중.
> v1.4 (2026-10-21): 5번째 도구 search_filing_text (rag_spec 7-5). 테스트 3개 추가, 서버 목록 테스트는 5개로. 아래 원칙의 "도구 수 4개 고정" · 7장의 "search_filing_text 안 함"은 7-5 사용자 결정으로 풀림 (원문은 그대로 둠).
> v1.2 (2026-10-19): 모든 인자에 설명(inputSchema description), signal은 7개 값 목록(enum), get_company_dilution · compare_event 설명 한 줄씩 보강.
> v1.1 (2026-10-19): compare_event small_sample, 회사명 정확히 일치 우선.
> v1 (2026-10-19, 사용자 작성). 구현: `src/filingscope/mcp/` (tools.py 본체 · server.py stdio 서버 · `__main__`), 테스트 `tests/test_mcp.py`.
> 목적: 사실 카드 함수를 agent가 부를 수 있는 도구 4개로 노출한다.
> 원칙: 도구는 함수 껍데기다. 새 계산은 compare_event의 백분위와 search_events의 조회뿐. LLM 없음. (v1 원문: 도구 수 4개 고정 · search_filing_text는 이번에 만들지 않음 → v2에서 5개)

## 0. 서버
Python MCP SDK (설치된 버전 mcp 2.3 — FastMCP가 MCPServer로 이름이 바뀜), stdio 서버 1개. 실행: `uv run python -m filingscope.mcp`. 도구 설명(description)은 아래 문구 그대로.

## 1. 도구 5개 (v2)

| 도구 | 설명 (그대로) | 입력 | 출력 · 오류 |
|---|---|---|---|
| get_event | "공시 1건의 값 · 계산 · 신호. 접수번호로 조회. 유상증자 · CB 모두." | rcept_no | 사실 카드 + company_signals + dilution_flags (v2, 알림과 같은 조합). 없으면 {"error": "not_found", "rcept_no"} |
| get_company_dilution | "회사 1곳의 살아 있는 유상증자 · CB를 모아 최대 희석률 · 일정 · 회사 신호를 계산. 회사명 또는 corp_code. 결과의 live_events가 살아 있는 공시, company.events는 전체 이력." | company (corp_code 8자리 또는 회사명 부분 일치), as_of = today | dilution JSON. 여러 곳이면 {"error": "ambiguous", "candidates" (최대 5)}, 없으면 {"error": "not_found"} |
| compare_event | "공시 1건의 희석률이 같은 종류 · 같은 시장 공시 전체에서 몇 백분위인지. '큰 편인가'에 답할 때. 희석률이 없는 공시는 no_value." | rcept_no | {rcept_no, type, group, metric, value, percentile, n, median}. 값 NULL이면 {"error": "no_value", "reason"} |
| search_filing_text | 6장 문구 그대로 (주제 5개 설명 포함) | rcept_no · topic(청약절차 · 리픽싱 · 콜 · 풋 · 전체, enum) · question | 6장 |
| search_events | "조건으로 공시 목록 찾기. 회사 · 기간 · 종류 · 희석률 범위 · 신호. 최대 20건. 글 내용 검색은 안 됨." | company · type · date_from · date_to · min_dilution · max_dilution · signal · limit=20 · order_by="rcept_dt" (전부 선택). order_by: "rcept_dt"(기본, 접수일 최신순) \| "dilution"(유상증자 dilution_pct · CB total_pct 큰 순, NULL은 뒤, 같으면 접수일 최신순) — 전체를 정렬한 뒤 limit (v1.3) | {n, items: [{rcept_no, corp_name, type, rcept_dt, dilution_pct, signals}], truncated} |

## 1-1. 인자 설명 (v1.2, agent가 inputSchema로 받음)
| 인자 | 설명 |
|---|---|
| company | "회사명 또는 corp_code 8자리" |
| rcept_no | "접수번호 14자리" |
| as_of | "기준일 YYYY-MM-DD. 비우면 서버 기준일" |
| date_from / date_to | "YYYY-MM-DD. 연도만 알면 01-01 / 12-31" |
| min_dilution / max_dilution | "희석률 (%)" |
| type | "공시 종류" (유상증자 · CB) |
| signal | 값 7개 목록(enum) + "신호 하나. non_cash=현금 아닌 납입 · major_holder=최대주주 배정 · debt_heavy=채무상환 50% 이상 · halted=거래정지 중 · refixing=리픽싱 있음 · old_bonds=예전 사채 잔량 있음 · public_single=공모인데 한 곳이 전액". 목록 밖 값은 서버가 입력 검증 오류로 거절 |
| limit | "최대 건수" |
| (search_filing_text) rcept_no | "공시 접수번호 14자리. 모르면 search_events나 get_company_dilution으로 먼저 찾는다." |
| (search_filing_text) topic | enum ["청약절차", "리픽싱", "콜", "풋", "전체"] + "아래 설명 참고" |
| (search_filing_text) question | "사용자 질문 원문 그대로. 조각 순서를 정하는 데만 쓴다." |

구현: `Annotated[타입, Field(description=…)]` — mcp 2.3에서 평문 `Annotated[타입, "설명"]`은 스키마에 들어가지 않음 (확인).

## 2. compare_event 계산
1. metric: 유상증자 calc.dilution_pct, CB calc.total_pct (NULL이면 dilution_pct, 그것도 NULL이면 no_value)
2. 그룹: 유상증자 = 같은 시장 · 같은 CI_MTH 계열 (1·2·3 주주배정 / 5 공모 / 6 3자배정), CB = 같은 시장
3. 그룹 분포는 캐시의 사실 카드로 (DB에 저장 안 함)
4. percentile = 그룹 안에서 value 이하 비율 × 100, n = NULL 제외 건수, median
5. group 이름 "코스닥 주주배정" · "코스피 3자배정" · "코스닥 CB"
6. (v1.1) 그룹 n이 SMALL_SAMPLE_N(30, facts/constants.py) 미만이면 반환에 "small_sample": true. 그룹은 합치지 않는다 (지금 코스피 공모 n 11).

## 2-1. 회사명 찾기 (get_company_dilution, v1.1)
corp_code 8자리면 그대로. 회사명이면 corp_name과 정확히 같은 회사가 1곳이면 그것을 쓴다. 없을 때만 부분 일치 → 여러 곳이면 ambiguous (후보 최대 5), 없으면 not_found.

## 3. search_events 조회
company (corp_name 부분 일치 또는 corp_code) · rcept_dt 범위 · 희석률(유상증자 dilution_pct · CB total_pct, 범위를 주면 NULL 제외) · signal (non_cash · major_holder · debt_heavy · halted · refixing · old_bonds · public_single, 그 밖은 {"error": "unknown_signal"}) · rcept_dt 내림차순 · limit 초과면 truncated.

## 4. 캐시
서버 시작 시 사실 카드 전부(2,978건)를 한 번 계산해 메모리에 둔다 (1.7~1.8초). compare · search가 쓴다. DB가 바뀌면 재시작 (무효화 없음).

## 5. 테스트 (tests/test_mcp.py, v2 27개)
get_event 2 · get_company_dilution 4 (엔투텍 30.4 · 코이즈 5.8 · 제일엠앤에스 감자 · 삼성) · compare_event 3 (뷰노 손 SQL ±1 · 엔투텍 · 압타머) · search_events 4 · small_sample 1 · 서버 기동 (stdio 클라이언트로 목록 5개 · topic enum · 5개 호출) · search_filing_text 11 (6장 표).
v2에서 바뀐 기대값: get_event 출력에 dilution_flags (테스트는 떼고 카드와 비교, 붙인 값 == dilution() flags) · 서버 도구 목록 4 → 5. 3단계(2026-10-22)에 바뀐 기대값(제일엠앤에스 max_pct None → 최근 공시 1건 · 2024 희석 20%+ 119 → 118)은 그때 고침. 표: docs/mcp_v2_report.md 4-1.

## 6. search_filing_text (v2, 새 RAG — src/filingscope/mcp/text_search.py)
설명 (그대로):
```
공시 1건의 조항 원문을 주제별로 가져온다. 숫자 계산은 하지 않는다. 주주배정 유상증자와 CB만 지원한다.
주제:
- 청약절차: 주주배정 유상증자에서 확정 발행가를 정하는 방법, 초과청약, 실권주 · 청약 미달 주식 처리
- 리픽싱: 주가가 내려갈 때 CB 전환가액을 낮추는 조건과 조정 주기
- 콜: 회사(또는 회사가 정한 사람)가 투자자에게서 CB를 되사오는 권리
- 풋: 투자자가 만기 전에 회사에 원금을 돌려달라고 하는 권리
- 전체: 어느 주제인지 확실하지 않을 때. 그 공시의 조항 원문 전부
```
동작: rcept_no 14자리 아니면 bad_rcept_no → events에 없으면 not_found → 주주배정(CI_MTH 1 · 2 · 3) · CB가 아니면 no_text → 주제가 공시 종류와 안 맞으면 topic_not_applicable(+ available_topics: 주주배정 [청약절차] · CB [리픽싱 · 콜 · 풋]) → 후보 = 그 공시 section 조각 중 topics에 주제(T1 청약절차 · T2 리픽싱 · T3 콜 · T4 풋)가 있는 것 **전부** ('전체'면 공시 조각 전부) → 0개면 공시 조각 전부 + fallback true → 순서 = question 임베딩(text-embedding-3-small, 메모리 캐시)과의 코사인 유사도 내림차순, 같으면 원문 순서. question이 비거나 임베딩 실패면 원문 순서 · order "document". 자르지 않음.
출력: {rcept_no, corp_name, rcept_dt, doc_type, topic, fallback, order, n, chars, items: [{rank, seq, field, field_label, section_title, topics, score, text}]}.
테스트 11개: 대창솔루션 리픽싱 "매 7개월" · 캐리 콜 "70%" · 태웅로직스 풋 "2028년 4월 30일" · 에스와이스틸텍 청약절차 "1차 발행가액과 2차 발행가액 중 낮은" · CB에 청약절차 → topic_not_applicable · 3자배정 → no_text · not_found · bad_rcept_no · 콜 없는 CB(지투지바이오) → fallback · '전체' n = 공시 조각 수 · 같은 호출 같은 순서 · 빈 질문 → document. 테스트는 OpenAI를 부르지 않음 (질문 임베딩 자리에 DB 조각 임베딩 하나를 고정).
통합 확인 (학습 100, 도구 함수로): 정답 포함 100/100 · Recall@3 0.910 · MRR 0.699 = 실험 exp3_section_rule_vector_v2와 같음.

## 7. 하지 않는 것
agent · 새 신호 · 새 계산 · DB 쓰기. 도구 6개 이상 안 됨 (v2: search_filing_text를 5번째로).

## 구현 메모 (2026-10-19)
- get_event는 "company_signals 붙임"에 따라 카드에 company_signals 키를 더한다. 테스트는 그 키를 뺀 나머지가 cb_facts와 같은지 본다.
- compare_event 그룹 n (2026-10-24, v2): 코스닥 주주배정 172 · 3자배정 1,017 · 공모 50 / 코스피 주주배정 56 · 3자배정 199 · **공모 11 (n < 30)** / 코스닥 CB 1,163 · 코스피 CB 188.
- compare_event 그룹 n (2026-10-19): 코스닥 주주배정 172 · 3자배정 1,016 · 공모 50 / 코스피 주주배정 56 · 3자배정 199 · **공모 11 (n < 30)** / 코스닥 CB 1,163 · 코스피 CB 188.
