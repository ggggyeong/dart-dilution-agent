# 사실 카드 함수 명세 v1.5

> v1.5 (2026-10-22, 알림 점검 뒤): 표시용 칸 — calc.raised_display (유상증자) · calc.amount_display (CB) · calc.fund_*_display · calc.fund_*_pct (정수 %, ÷ 조달액 · 권면) · allottees[].amount_display. 원 → 억(1억 이상, 소수 1자리, 100억 미만이고 소수 2자리에서 딱 떨어지면 2자리) · 만(1억 미만). 알림 LLM이 나눗셈을 하지 않게 (cards.won_display · share_pct).
> v1.4 (2026-10-22, 희석률 계산 검증 3단계 · docs/calc_criteria.md v1): 우선주만 발행 희석률 NULL + flags.preferred_only · major_holder 정확히 일치 + flags.new_major_holder · flags rehab · price_pending · foreign_currency · dilution 살아 있는 CB = conv_end(없으면 maturity) > as_of · 기준 주식 수 = 가장 최근 살아 있는 공시 · 감자 추정(4-1)을 방향 기준 단위 불일치로 교체. (v1.3의 사람 판정은 ⑨-2에서 없앰 — shares_before_typo는 항상 false)
> v1.3 (2026-10-19): 사람 판정 manual/facts.csv — shares_before 오기(typo)면 card.flags.shares_before_typo = true, 그 카드 희석률 NULL, dilution 감자 판단 · 기준 주식 수에서 뺌 (DB 값은 원문 그대로). 이브이첨단소재 20260914000244.
> v1.2 (2026-10-19): 6장 4-1 감자(주식병합) 추정 규칙.
> v1.1 (2026-10-19): 6장 3-1 같은 날 CB 규칙, major_holder는 '최대주주' 글자 포함 전부 그대로 (사용자), 구현 메모 동의.
> v1 (2026-10-19, 사용자 작성). 구현: `src/filingscope/facts/` (constants.py · cards.py), 테스트 `tests/test_facts.py`.

목적: DB의 원문 값을 읽어 계산하고, 신호를 켜고, JSON 한 장(사실 카드)으로 만든다. 알림(입구 1)과 MCP 도구(입구 2)가 이 함수를 그대로 쓴다. 두 입구가 각자 계산하지 않는다. 원칙: LLM 없음. 새 DB 칸 없음. 새 신호 없음. 판단("괜찮다/나쁘다")은 하지 않고 사실만 담는다.

## 0. 함수 4개

| 함수 | 입력 | 출력 |
|---|---|---|
| rights_facts(rcept_no) | 유상증자 접수번호 | 사실 카드 (유상증자 1건) |
| cb_facts(rcept_no) | CB 접수번호 | 사실 카드 (CB 1건) |
| company_signals(corp_code, as_of=today) | 회사 코드 | 회사 단위 신호 |
| dilution(corp_code, as_of=today) | 회사 코드 | 살아 있는 공시 합산 |

## 1. 사실 카드 JSON 모양 (공통)

```json
{
  "type": "CB", "rcept_no": "20260820000443", "event_id": "...",
  "corp_code": "...", "corp_name": "엔투텍", "rcept_dt": "2026-08-21", "market": "코스닥",
  "values": { ... }, "allottees": [ {"name", "relation", "shares", "amount", "lockup"} ],
  "calc": { ... }, "signals": [ {"name": ..., ...} ],
  "company": { "repeat_count": 4, "repeat": true, "withdrawn": false },
  "flags": { "date_flag": null, "shares_before_missing": false, "shares_before_typo": false, "preferred_only": false,
             "new_major_holder": false, "rehab": false, "price_pending": false, "foreign_currency": false }
}
```
- values는 rights_values / cb_values의 칸을 이름 그대로. 변환하지 않는다. 숫자는 원 · 주 단위 그대로. 비율은 퍼센트 숫자 (5.4), 소수점 1자리.

## 2. values
- 유상증자: rights_values 전 칸 + events.ci_mth · payment_type · payment_evidence · payment_source
- CB: cb_values 전 칸 + events.payment_type · payment_evidence · payment_source

## 3. calc

rights_facts

| 키 | 식 | NULL 조건 |
|---|---|---|
| dilution_pct | new_shares ÷ (shares_before + new_shares) × 100 | 둘 중 하나 NULL |
| raised | new_shares × price (+ 혼합이면 other_new_shares × other_price, fix2 2026-10-24) | price NULL |
| fund_total | fund_* 6칸 합 | – |
| fund_mismatch | abs(raised − fund_total) > raised × 0.01 → true | raised NULL |
| debt_ratio | fund_debt ÷ raised | raised NULL 또는 0 |
| price_pending_display | price_status 예정일 때 조달액 뒤 글자 (fix1, 2026-10-24): 확정일 ≥ as_of "(예정 발행가 기준, YYYY-MM-DD 확정)" · 확정일 없음 "(예정 발행가 기준)" · 확정일 < as_of "(예정 발행가 기준, 확정 공시 미반영)" | 예정 아님 |

cb_facts

| 키 | 식 | NULL 조건 |
|---|---|---|
| dilution_pct | conv_shares ÷ (shares_before + conv_shares) × 100 | shares_before NULL |
| worst_shares | amount ÷ min_price (정수 내림) | min_price NULL → = conv_shares |
| worst_pct | worst_shares ÷ (shares_before + worst_shares) × 100 | shares_before NULL |
| total_pct | (old_cb_shares + conv_shares) ÷ (shares_before + old_cb_shares + conv_shares) × 100 | shares_before NULL |
| total_worst_pct | (old_cb_shares + worst_shares) ÷ (shares_before + old_cb_shares + worst_shares) × 100 (fix1, 2026-10-24) | shares_before NULL |
| floor_pct | min_price ÷ conv_price × 100 | min_price NULL |
| debt_ratio | fund_debt ÷ amount | amount 0 |

shares_before NULL이면 flags.shares_before_missing = true, 희석 4개 모두 NULL.
같은 기준끼리 짝 (fix1): 이번 공시만 = dilution_pct · worst_pct, 예전 잔량 포함 = total_pct · total_worst_pct. 기준이 다른 둘(예: total_pct와 worst_pct)은 비교하지 않는다.

## 4. signals (켜진 것만)

| 묶음 | name | 조건 | 같이 넣는 값 |
|---|---|---|---|
| 공통 | non_cash | payment_type ≠ '현금' (불명 포함) | payment_type · payment_evidence |
| 공통 | major_holder | allottees.relation을 공백 제거 후 정확히 '최대주주' 또는 '최대주주본인'인 줄 ≥ 1 (v1.4) | 해당 대상자 name 목록 |
| 공통 | debt_heavy | debt_ratio ≥ DEBT_HEAVY_RATIO | debt_ratio · fund_debt |
| 공통 | halted | events.halted_at_filing = true | – |
| CB | refixing | min_price IS NOT NULL | min_price · floor_pct · worst_pct |
| CB | old_bonds | old_cb_shares > 0 | old_cb_shares · total_pct |
| CB | public_single | issue_method = '공모' AND exempt_reason에 '소액공모' AND allottees 1줄 | – |

상수 (한 곳, `facts/constants.py`): DEBT_HEAVY_RATIO = 0.5, REPEAT_YEARS = 2, REPEAT_MIN = 3

## 5. company_signals(corp_code, as_of)
events만 읽는다. repeat_count = first_date가 (as_of − REPEAT_YEARS) 이후인 events 줄 수 (유상증자 + CB), repeat = repeat_count ≥ REPEAT_MIN, withdrawn = status '철회' 줄 존재 (지금은 항상 false), events = 그 회사 events 전부 [{rcept_no, type, first_date, status}] 날짜순.
dilution() 결과 안의 company.events[].status는 live_events 기준으로 덮는다 (fix1 → fix2, 2026-10-24): "진행" = live_events · "잔량 반영" = 전환 기간(coalesce(conv_end, maturity))이 as_of 뒤인데 live가 아닌 이전 CB (기준 CB old_cb_shares로 합산) · "끝남" = 상장 끝난(또는 상장일 없는) 유상증자 · 전환 기간 끝난 CB. 철회는 그대로.

## 6. dilution(corp_code, as_of)
1. company_signals(corp_code)
2. 살아 있는 유상증자: event_type='유상증자' AND listing_date > as_of → 각각 rights_facts
3. 가장 최근 CB: event_type='CB' AND coalesce(conv_end, maturity) > as_of (v1.4 — 전환 기간이 끝난 CB는 희석 없음) 중 rcept_dt 최대 1건 → cb_facts (없으면 CB 부분 없음). 동점이면 접수번호가 큰 것 = 기준 CB
   - 3-1. 같은 날 CB (v1.1): 기준 CB와 rcept_dt가 같은 다른 살아 있는 CB가 있으면 그 conv_shares를 더하고 old_cb_shares는 더하지 않는다. worst도 같은 방식 (그 CB의 worst_shares). 그 CB도 live_events · schedule에 넣는다. (기준 CB의 잔량표에 같은 날 다른 CB가 안 잡힐 수 있음 — 엔투텍 19회 잔량표에 18회 없음. 같은 날 CB 2건 이상: 회사-날짜 106쌍). v1.4: 뒤 회차 잔량표에 같은 날 형제가 들어 있으면 cb-v4가 old_cb_shares에서 그 줄을 빼서 두 번 세지 않음 (아센디오 19.4 → 13.1)
4. 합산: shares_before = 살아 있는 공시(유상증자 · CB · 같은 날 CB) 중 (rcept_dt, 접수번호)가 가장 최근인 것의 shares_before (v1.4, 종류 무관). new_shares = Σ 유상 new_shares (preferred_only 빼고) + CB conv_shares (+ 같은 날 CB conv_shares, 3-1) + CB old_cb_shares. max_pct = new_shares ÷ (shares_before + new_shares) × 100. worst_pct = CB conv_shares 대신 worst_shares로 다시 계산
   - 4-1. 단위 불일치 (v1.4, 예전 v1.2 감자 추정 '뒤 값 ≤ 앞 값 50%'를 대신함): 살아 있는 공시를 (rcept_dt, 접수번호) 순으로 놓고 인접한 A→B의 shares_before 비교. 감소 B < A ÷ 1.5, 증가 B > A + (A 날짜 ≤ d < B 날짜인 그 회사 모든 공시 new_shares · conv_shares 합) × 1.2 그리고 B ≥ A × 2. 걸리면 합산하지 않고 가장 최근 공시 1건만 (CB면 conv + old, 같은 날 CB 안 넣음), flags.unit_mismatch = [[A, B], …], dilution.reason. 감소로 걸리면 flags.capital_reduction = true도 (agent 프롬프트 · 알림이 읽음). 상수 constants.py UNIT_*. 2026-10-22 기준 15곳.
   - 4-2. 살아 있는 공시가 preferred_only뿐이면 max_pct · worst_pct NULL (basis는 그중 최근 것).
5. 일정: [{type, rcept_no, date, label}] — 유상 listing_date("상장"), CB conv_start("전환 시작"), CB maturity("만기")
6. basis_rcept_no · basis_date = 4번 기준 공시 (v1.4)

출력: {corp_code, corp_name, as_of, company, live_events: [사실 카드], dilution: {shares_before, new_shares, max_pct, worst_pct, basis_rcept_no, basis_date, (단위 불일치 · 우선주만이면 reason)}, schedule, flags: {capital_reduction, unit_mismatch}}. 살아 있는 공시 0건이면 live_events 빈 배열, dilution null, company만 채움.

## 7. 테스트 (tests/test_facts.py, 손 계산값)

| 공시 | 기대값 |
|---|---|
| 코이즈 20260522000636 | dilution_pct 5.8 · refixing 없음 · old_bonds 없음 · signals 빈 배열 |
| 엔투텍 20260820000443 | dilution_pct 5.4 · worst_pct 7.6 · total_pct 17.2 · floor_pct 70 · refixing · old_bonds · public_single · date_flag 있음 |
| 헝셩그룹 20260213002703 | dilution_pct 6.6 · worst_pct 9.1 · total_pct 13.8 · refixing · old_bonds |
| 압타머 20250909000002 | shares_before_missing true · 희석 3개 NULL · major_holder 꺼짐 + new_major_holder ("변경 예정최대주주", v1.4에서 바뀜) |
| 뷰노 20260828001315 | dilution_pct 31.0 · raised 약 313.7억 · debt_heavy (0.64) |
| 뉴온 20260706000282 | dilution_pct 59.1 · major_holder 켜짐 · debt_heavy 꺼짐 |
| 아스트 20240722000152 | non_cash (빚→주식) |
| 카프로 20240328001867 | non_cash (섞임) |
| 엔투텍 dilution() | live CB 19회(기준) + 같은 날 18회 · max_pct 30.4 · basis_rcept_no 20260820000443. v1 기대값 17.2는 19회 한 건만 센 값이라 틀림 — 18회는 19회 잔량표에 없으므로 따로 더해야 하고(3-1), cb-v4 형제 규칙으로도 19회 old_cb(제17회 5,194,805)는 그대로 |
| 코이즈 dilution() | max_pct 5.8 |

## 9. 하지 않는 것
새 DB 칸 · 새 신호 · 임계값 변경 · LLM 호출 · 문장 생성 · MCP 노출.

## 구현 메모 (사용자 동의, 2026-10-19)
- 카드의 company는 company_signals(corp_code) 기본값(as_of = 오늘).
- non_cash: payment_type이 NULL이면 켜지 않음 (지금 DB에 NULL 0건).
- 값은 JSON에 바로 넣을 수 있는 꼴 (Decimal → int/float, date → 'YYYY-MM-DD'). debt_ratio는 비, 소수점 2자리.
