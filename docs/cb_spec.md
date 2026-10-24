# Filingscope 전체 설계 + CB 추출 명세 v1

> v1 (2026-10-17): 사용자 작성. 1장은 전체 설계(기준 공유용), 2장은 CB 추출.
> 원칙: 질문에 쓰이지 않는 칸은 만들지 않는다. 숫자는 코드가 계산하고, LLM은 문장만 쓴다.

## 1. 전체 설계

```
DART 공시 → Event Pipeline → DB (유상증자 1,594 + CB 1,384)
                               ├─ 입구 1: 새 공시 알림   공시 1건 → 질문 5개 → 사실 카드 → LLM 문장
                               └─ 입구 2: Research Agent 사용자 질문 → MCP 도구 → 답 + 근거 접수번호
```

### 1-1. 입구 1: 새 공시 알림 (공시 1건 단위)

| # | 유상증자 | CB |
|---|---|---|
| 1 | 얼마나 큰가 (희석률 · 할인율 · 조달액) | 얼마나 큰가 (예전 CB 잔량 · 리픽싱 최악 포함) |
| 2 | 왜 조달하나 (자금 용도 6칸) | 같음 |
| 3 | 누가 사나 (대상자 · 주관사) | 같음 (대상자 · 관계) |
| 4 | 나는 뭘 할 수 있나 · 언제 | 언제 풀리나 (전환 시작일 · 만기) |
| 5 | 이 회사 괜찮나 (신호 목록) | 이 회사 괜찮나 (신호 목록, CB용) |

처리 순서 (유상증자 · CB 동일):
1. 그 공시 1줄 읽기 (events + 값 테이블 + allottees)
2. event_type에 따라 signals_rights(줄) 또는 signals_cb(줄) 실행
3. signals_company(corp_code): events 줄 수로 반복 조달 · 철회 이력
4. 희석 계산
5. 값 + 켜진 신호 = 사실 카드 → LLM이 5개 문단 작성 (판단 없음, 숫자 추가 없음)

신호 목록:

| 묶음 | 신호 | 재료 |
|---|---|---|
| 공통 (줄) | 거래정지 · 채무상환 50% 이상 | halted_at_filing · fund_debt ÷ 조달액 |
| 유상증자 (줄) | 빚→주식 · 현물 · 최대주주 배정 | payment_type · allottees.relation |
| CB (줄) | 리픽싱 있음(바닥 %) · 공모인데 대상자 1곳 + 소액공모 · 잔량표에 예전 CB · 자금 세부 "미정" | min_price · issue_method + exempt_reason + allottees 수 · old_cb_shares · TBL_FND2 값 |
| 회사 | 반복 조달(2년 내 유상증자 + CB 건수) · 철회 이력 | events |

"괜찮다 / 안 괜찮다"는 말하지 않는다. 신호 0개면 "특별한 신호 없음".

### 1-2. 입구 2: Research Agent (단일 agent, LangGraph)

| # | 질문 | 예시 | 쓰는 도구 |
|---|---|---|---|
| 1 | 최대 얼마나 희석되나 | "엔투텍 희석 얼마나 돼?" | get_company_dilution |
| 2 | 언제 물량이 풀리나 | "CB 언제부터 전환돼?" | get_company_dilution |
| 3 | 이 공시 나쁜 편인가 | "할인율 25%면 심한 거야?" | get_event + compare_event |
| 4 | 리픽싱으로 더 늘 수 있나 | "주가 떨어지면 희석 얼마까지?" | get_company_dilution |
| 5 | 이 회사 자주 조달했나 | "최근 몇 번 했어?" | get_company_dilution · search_events |
| 6 | 이런 조건 있는 공시 찾아줘 | "최대주주 콜 옵션 있는 CB" | search_filing_text (RAG, 마지막) |

MCP 도구 5개:

| 도구 | 입력 | 반환 |
|---|---|---|
| get_event | rcept_no | 공시 1건의 값 + 신호 (입구 1과 같은 코드) |
| get_company_dilution | corp_code | 아래 참조. 질문 1 · 2 · 4 · 5를 이 하나로 |
| compare_event | rcept_no | 같은 종류 공시 전체 중 백분위 (할인율 · 희석률) |
| search_events | 회사 · 기간 · 종류 · 값 범위 | 조건에 맞는 공시 목록 |
| search_filing_text | 자연어 | 원문 글 검색 (RAG) |

get_company_dilution 흐름:
- events에서 그 회사 줄 전부 → signals_company
- 안 끝난 유상증자 (listing_date > 오늘) 줄 전부 → 각각 값 + signals_rights
- 가장 최근 CB 1줄 → 값 + signals_cb + 그 줄 잔량표의 예전 CB (old_cb_shares)
  - CB는 1줄만 보는 이유: 그 공시의 잔량표가 그 시점의 예전 CB 잔량을 적어 줌. DB에 없는 오래된 CB도 잡힘
  - 한계: 잔량은 그 공시 시점 값. 답에 "기준: ○○ 공시"를 붙임. 자기전환사채 취득·매도 공시는 다음 단계
- 합산: 유상 신주 + CB 전환 주식 + 잔량 주식 → 최대 희석, min_price로 최악 희석
- 일정: 상장예정일 · 전환 시작일 모음
- 전체를 JSON 하나로 반환. agent가 질문에 맞는 부분만 씀

입구 1과 입구 2는 같은 함수(signals_rights · signals_cb · signals_company · 희석 계산)를 공유한다.

### 1-3. 순서
유상증자 DB (완료) → CB DB (지금) → 신호 함수 + 입구 1 → MCP 5개 + agent → 정정본 → 자기전환사채 · RAG

## 2. CB 추출 명세

### 2-1. 범위
- 대상: 이미 수집한 CB 원본 중 공시 당시 코스피 · 코스닥, 문서코드 11324 → 1,384건
- 제외: 80522(비표준, 전부 비상장) · 코넥스 · 비상장 · 정정본(파일만 보관, 다음 단계)
- 공모 3건은 포함
- LLM 사용: 없음. 모든 칸이 코드 칸. 13번 납입방법이 "현금"이 아닌 공시가 나오면 건수와 값만 보고 (판정은 그때 결정)

### 2-2. 마이그레이션 013 (`013_cb_tables.sql`)
- events: `event_type text NOT NULL DEFAULT '유상증자'`, `ci_mth` NULL 허용
- cb_values 신설 (키 rcept_no, 정정본 대비): amount, conv_price, conv_shares, shares_before, min_price, old_cb_shares, fund_facility · fund_business_acq · fund_operating · fund_debt · fund_other_corp · fund_other, issue_method, exempt_reason, conv_start, conv_end, maturity, pay_date, coupon, ytm, date_flag, extractor_version, extracted_at
- allottees: `amount bigint` (CB 대상자 금액(원), 유상증자는 NULL)
- filings · allottees는 CB가 같이 쓴다. 새 테이블은 cb_values 하나.
- 구현 메모: 키는 기존 테이블과 같은 char(14) (명세 원안의 `char`는 char(1)). FK · CHECK는 rights_values와 같은 방식.

### 2-3. events (CB 줄)

| 칸 | 값 |
|---|---|
| event_type | 'CB' |
| ci_mth | NULL |
| corp_code · corp_name · first_date · status | 유상증자와 같은 방식 |
| market_at_filing · halted_at_filing | listing_checks (A 태그) 그대로 |
| payment_type | 13번 PYM_DET가 "현금"이면 '현금', payment_source = 'rule:pym_det'. 아니면 payment_type NULL로 두고 보고 |

- 13번 PYM_DET는 선택형 칸 (1 = 현금 1,278 / 2 = 기타 106). "기타"는 LLM 판정 (`llm/cb_payment.py`, `scripts/cb_payment_llm.py`, payment_source = 'llm:cb-v2'): 유상증자 납입 방식 판정과 같은 지시문 · 출력(현금 / 빚→주식 / 현물 / 섞임 / 불명 + 근거, 정황 판단), 입력 = 부속 표 TBL_PYM (100건) / 없으면 본문 자유 서술 (6건).
- 규칙 (사용자, cb-v2 · 유상증자 extract-v4 지시문에도): 상계·대용납입이라도 상계되는 채권이 같은 거래에서 자산(주식·부동산 등)을 사며 생긴 매매대금이면 현물, 그 전부터 있던 빚(대여금·차입금·예전 사채 원금)이면 빚→주식.
- 정답표(Opus 원문 전체) 20건 대조: cb-v1 16/20 (불일치 4건 모두 매매대금 상계를 빚→주식 / 현물 중 어디로 볼지) → 규칙 추가 후 cb-v2 20/20.

### 2-4. cb_values 칸 ↔ 원문 (본문 표 CB_PUB, 번호 아닌 ACODE로 읽기)

| 질문 | 칸 | ACODE · 위치 | 규칙 |
|---|---|---|---|
| 1 | amount | DNM_SUM | 원 |
| 1 | conv_price | EXE_PRC | |
| 1 | conv_shares | STK_CNT | STK_RT는 저장하지 않음 (분모가 회사마다 다름) |
| 1 | shares_before | 부속 표 TBL_NSB → ISSU_TOT (C 기발행주식 총수) | "-"면 NULL |
| 1 · 4 | min_price | MIN_PRC | "-"면 NULL = 리픽싱 없음 |
| 1 | old_cb_shares | TBL_NSB 줄 EXE_STK 합 (cb-v3) | 줄의 EXE_DT(전환청구기간) 끝 날짜 < 공시일이면 뺌, 끝 날짜가 없으면 포함. 교환사채(DEB_KND) 줄 뺌. 소계(SUB_TOT_STK) · 잔량표 밖 EXE_STK는 안 씀. 줄이 다 비면 0 |

- old_cb_shares: 이 칸은 CB뿐 아니라 BW · EB 등 주식연계사채 잔량 전체. cb-v3 (2026-10-22, calc_criteria v1 B-2)부터 잔량표 줄 합 + 전환 끝난 줄 빼기 + 교환사채 줄 빼기 — cb-v2(소계 기준)에서 47건 바뀜 (전환 끝난 줄 22 · 교환사채 12 · 소계 ≠ 줄 합 13, 케이이엠텍 20250203000470 소계 누락도 줄 합 4,203,844로 맞음). 결과 data/calc_fix/report.md.
- **교환사채(EB)는 회사가 가진 자기주식을 주는 것이라 새 주식이 안 생김 → 희석 아님. 잔량표에서 사채 종류(DEB_KND)에 "교환사채" · "EB"가 있는 줄은 뺀다 (BW · CB는 넣음).**
- **같은 날 형제 CB (cb-v4, 2026-10-22):** 같은 회사가 같은 날 CB를 여러 건 공시하면 뒤 회차 잔량표에 앞 회차가 들어 있는 경우가 있음 (아센디오 388에 385 · 386). dilution()은 같은 날 CB를 모두 더하므로 잔량표에서 그 줄을 뺀다 — 줄의 잔액(BAL_AMT) · 전환가액(EXE_MN)이 형제의 amount · conv_price와 같고, 사채 종류에 회차 숫자가 있으면 형제 SEQ_NO와도 같을 때 (회차 조건 없이는 코람코라이프인프라리츠 제1회차 · KR모터스 제54회처럼 우연히 금액 · 가액이 같은 옛 CB를 뺌). 잔량표 줄에는 발행일 칸이 없음 (ISSU_DT는 이번 CB의 전환청구기간). cb-v3 대비 49건 · 회사 37곳. 수집 때는 `scripts/refresh_cb_siblings.py --date`로 그날 형제를 다시 계산.
| 2 | fund_facility … fund_other | FND_USE1 · FND_USE_SQ · FND_USE2 · FND_USE_RD · ANC_ACQ_PRC · FND_USE3 | 유상증자와 같은 칸 이름. 타법인만 코드가 다름(유상증자는 ANC_ACQ_AMT). "-"는 0 |
| 3 | issue_method | 아래 규칙 | 사모 / 공모 / 해외 |
| 3 | exempt_reason | RPT_SBM | 원문 그대로, "-"면 NULL (⑨-2). "소액공모" 신호용 |
| 4 | conv_start · conv_end | SB_BGN_DT · SB_END_DT | |
| 4 | maturity | EXP_DT | |
| 4 | pay_date | PYM_DT | |
| 5 | coupon · ytm | PRFT_RATE · LST_RTN_RT | "-"면 NULL |

issue_method 규칙 (순서대로):
1. PL_KND에 "해외"가 있거나 FRN_AMT가 "-"가 아니면 → 해외
2. 아니면 ISSU_MTH 값 (사모 / 공모)
3. 통화 칸 AMT_UNIT에 "USD"만 있고 FRN_AMT가 "-"인 경우는 해외로 보지 않음

### 2-5. allottees (CB)
부속 표 CRP_ISSU에서 대상자마다 한 줄: ISSU_NM → name, RLT → relation, ISSU_AMT → amount(원). shares · lockup은 NULL. 출자자 · 재무 표(THD_ASN_BAS · FIN)는 저장하지 않음.

### 2-6. 검사 규칙 (date_flag)
날짜 순서 SBSC_DT ≤ PYM_DT < SB_BGN_DT < EXP_DT가 어긋나면 date_flag에 어긋난 칸 이름을 적는다 (예: 'pay_date>conv_start'). 값은 그대로 저장. (사례: 엔투텍 20260820000443 납입일이 만기일과 같음)

### 2-7. 저장하지 않는 것
STK_RT · 정관상 잔여 발행한도 · 사외이사 참석 · 공매도 기간 · 옵션 글(OPT_FCT) · 전환가 조정 글(EXE_REG) · 자금 세부 표 · 출자자 · 재무 표 · 해외 상세(통화 · 환율 · 지역)

### 2-8. 계산 (저장 안 함)
- 잠재 희석 = conv_shares ÷ (shares_before + conv_shares)
- 최악 희석 = (amount ÷ min_price) 기준 재계산
- 예전 CB 포함 = (old_cb_shares + conv_shares) ÷ (shares_before + 둘)
- 알림 5 신호 전부

### 2-9. 보고할 것
- 1,384건 중 저장된 건수, 칸별 채움률 (shares_before · old_cb_shares · min_price는 NULL 건수도)
- PYM_DET 값 종류별 건수
- issue_method별 건수 (사모 / 공모 / 해외)
- date_flag 걸린 건수와 예시 5건
- 대표 4건(코이즈 20260522000636 · 압타머 20250909000002 · 엔투텍 20260820000443 · 헝셩그룹 20260213002703)의 cb_values 줄 전체
- (선택) 본문 9칸을 DART API cvbdIsDecsn과 대조한 불일치 건수

### 2-10. 지킬 것
.env · data/ 커밋 금지. PROGRESS.md 기록. 명세 외 칸 추가 금지(필요하면 먼저 물어보기)
