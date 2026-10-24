# 희석률 계산 정의서 (CALC_CRITERIA) v1 — 2026-10-22

> 목적: 사실 카드(facts) · 회사 합산(dilution)이 **어떤 공시를 넣고 어떤 상황을 어떻게 처리하는지**를 글로 고정한다.
> 식(facts_spec §3)은 그대로 둔다. 이 문서는 식을 **언제 쓰면 안 되는지**를 정한다.
> v0 → v1: 2단계 세기(data/calc_count/calc_count.md)를 보고 사람이 정함. 바뀐 곳은 ★.

## A. 살아 있는 공시 (dilution()이 합산에 넣는 것)

| # | 대상 | 정의 v1 | 이유 |
|---|---|---|---|
| 1 | CB | **conv_end > as_of** (conv_end NULL이면 maturity) | 전환 못 하는 CB는 희석 없음 (2단계: 12건 · 회사 1곳 32%p) |
| 7 | 유상증자 | listing_date > as_of (NULL은 빠짐) | 상장되면 주식 수에 이미 반영 |
| 9 | 정정본 | 같은 event의 공시가 여러 건이면 최신 rcept_no 1건만 | 지금 DB 0건 → 코드 없음 ★ |

## B. 카드 안 계산

| # | 상황 | 정의 v1 | flag |
|---|---|---|---|
| 2 | 잔량표의 옛 CB (old_cb_shares) | 잔량표(TBL_NSB) **줄 합**, 그중 **전환종료일 ≥ 공시일**인 줄만. 종료일 칸이 없거나 비면 포함. **교환사채(EB) 줄은 뺌** — 자기주식 교환이라 희석 아님 (BW는 넣음). **같은 날 공시한 형제 CB 줄도 뺌** — 잔액 · 전환가액이 형제의 발행금액 · 전환가액과 같고, 줄에 회차 숫자가 있으면 형제 회차(SEQ_NO)와도 같은 줄 (형제는 dilution()에서 따로 더해지므로 두 번 세지 않으려고, cb-v4). 수집 때 먼저 들어온 장은 형제가 아직 DB에 없으므로 `scripts/refresh_cb_siblings.py`로 다시 계산. 소계(SUB_TOT_STK) · 잔량표 밖 EXE_STK는 안 씀 ★ (파서 cb-v3) | – |
| 3 | 우선주 | 분모 · 분자 **보통주만** (혼합 · 증자 전 기타주식이 있어도 new_shares = CST_CNT, shares_before = BFR_CST_CNT — rights-v4가 이미 이렇게 읽음). **우선주만 발행(stock_type=우선)은 희석 3개 NULL**, 회사 합산의 새 주식 합에서도 뺌 (살아 있는 목록에는 남김) | `preferred_only` |
| 5 | 회생 출자전환 | 계산은 그대로. 조건은 **exempt_reason에 "회생"** 만 (글 본문은 안 봄 — 2단계: 글까지 보면 254건, exempt_reason은 4건) ★ | `rehab` |
| 8 | 발행가 예정 (price_status=예정) | 계산은 그대로 | `price_pending` |
| 10 | 해외 발행 CB (issue_method=해외) | 원화 환산액으로 그대로 | `foreign_currency` |

## C. 회사 합산

| # | 상황 | 정의 v1 |
|---|---|---|
| 1-3 | 기준 주식 수 ★ | **살아 있는 공시 중 rcept_dt가 가장 최근인 것**(종류 무관, **같은 날이면 rcept_no 큰 것** — 동점 규칙, 예: 케이쓰리아이 20260923000637 · 638 → 638)의 shares_before. basis_rcept_no · basis_date도 그 공시. old_cb_shares는 기준 CB(가장 최근 살아 있는 CB) 것만. 살아 있는 공시가 preferred_only뿐이면 dilution NULL |
| 4 | 단위 불일치 ★ | 살아 있는 공시를 (rcept_dt, rcept_no) 순으로 놓고 인접한 A→B의 shares_before 비교. **감소**: B < A ÷ 1.5. **증가**: B > A + (A 날짜 ≤ d < B 날짜인 그 회사 모든 공시의 new_shares · conv_shares 합, A 자신 포함) × 1.2 **그리고** B ≥ A × 2. 걸리면 합산하지 않고 **가장 최근 공시 1건**으로만 계산 (CB면 conv_shares + old_cb_shares, 같은 날 CB 안 넣음). `flags.unit_mismatch` = [[A, B], …]. 감소로 걸리면 `flags.capital_reduction` = true도 유지 (agent 프롬프트 · 알림 명세가 읽음) |

v0의 "최대/최소 ≥ 3배"는 증자로 늘어난 것과 구분을 못 해서 방향 기준으로 바꿈.

## D. 신호

| # | 신호 | 정의 v1 |
|---|---|---|
| 6 | major_holder | relation을 공백 제거 후 **정확히 "최대주주" 또는 "최대주주본인"** |
| 6 | `new_major_holder` (flag, 신호 아님) | 공백 제거 후 "변경"이 있고, "변경" · "예정" · "후"를 지운 나머지가 정확히 "최대주주" 또는 "최대주주본인" (변경예정최대주주 · 변경후최대주주 · 최대주주변경예정). 새 최대주주의 특수관계인은 아무것도 안 켬 ★ |

"최대주주의특수관계인" · "최대주주의최대주주" 등은 아무것도 켜지 않음.

## E. 하지 않는 것
- 식 변경, 임계값(DEBT_HEAVY 0.5 등) 변경, 새 신호 추가, 감자 공시 수집, 알림 문장 수정, MCP 노출 변경, rcept_no 별 예외 처리.

## F. 남은 것
- 상장예정일 NULL 보통주 유상증자 19건: 확인만 (3단계 보고).
- 9번 정정본: 정정본을 filings에 넣게 되면 다시.
