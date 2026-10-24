# 알림 (입구 1) 명세 v1.4

> v1.4 (2026-10-22): 금액 · 비중은 코드가 만든 글자(calc.raised_display · amount_display · fund_*_display · fund_*_pct · allottees[].amount_display)를 그대로 씀 — LLM의 원 → 억 환산 삭제 (50건 점검에서 10배 오류 2건).
> v1.3 (2026-10-22, 알림 점검 4번): 지시문에 새 flag 6개 규칙 블록 (preferred_only · price_pending · new_major_holder · rehab · foreign_currency · dilution_flags.unit_mismatch), non_cash의 manual: 분기 삭제.
> v1 (2026-10-19, 사용자 작성 — 이모지 판). 구현: `src/filingscope/alert/` (alert · checks), 지시문 `docs/alert_prompt.md` (코드가 이 파일을 그대로 읽음), 검사 · 10건 `scripts/alert_samples.py` → data/reports/alert/.

> 목적: 공시 1건의 사실 카드(JSON)를 사용자가 읽을 문단 5개로 바꾼다.
> 흐름: rcept_no → events.event_type → rights_facts / cb_facts + company_signals → JSON → LLM → 문장
> 원칙: LLM은 문장만 쓴다. 숫자 · 사실은 JSON에 있는 것만. 판단("괜찮다 · 위험하다 · 사라 · 팔아라") 금지.

## 0. 함수
`alert(rcept_no) -> {rcept_no, corp_name, type, paragraphs: [p1..p5], evidence: [rcept_no], model, card}` — card는 입력 JSON 그대로 (검증용). 위치 `src/filingscope/alert/`. 모델: Sonnet (기본), 환경변수 `FILINGSCOPE_ALERT_MODEL`로 바꿈.

## 1. 문단 5개와 쓰는 키
- 유상증자: 1 📊 얼마나 큰가 (calc.raised · dilution_pct · new_shares · shares_before · price · price_status · discount_rate) / 2 💰 왜 조달하나 (fund_* 6칸 · raised, 0이 아닌 용도 · 금액 · 비중, acquire_target) / 3 👤 누가 사나 (ci_mth · allottees · lead_manager) / 4 🙋 나는 뭘 할 수 있나 · 언제 (participation별) / 5 🔍 이 회사 괜찮나 (signals · company · flags)
- CB: 1 📊 얼마나 큰가 (amount · conv_price · conv_shares · dilution_pct · worst_pct · total_pct · floor_pct · old_cb_shares · coupon · ytm; 리픽싱 있으면 바닥가 · 최악 희석, 예전 사채 있으면 잔량 · 합산) / 2 💰 왜 조달하나 / 3 👤 누가 사나 (issue_method · allottees(amount) · exempt_reason) / 4 📅 언제 풀리나 (pay_date · conv_start · conv_end · maturity, 전환 시작이 발행 1년 뒤면 "사모 1년 잠금") / 5 🔍 이 회사 괜찮나

## 2. 문단 4 (유상증자) participation별
우선: 기준일 · 1주당 배정 · 청약 기간 · 권리 매도(시장이면 기간, 장외/불가면 그렇게) · 상장일 / 일반: "기존 주주 우선권 없음. 누구나 청약" · 청약 기간 · 상장일 / 불가: "기존 주주는 참여 불가" · 상장일만

## 3. 문단 5 규칙
- "주의할 점 N가지예요."로 시작하고 신호마다 ⚠️ 줄 하나. N=0이면 "✅ 특별한 신호는 없어요."
- 세는 신호: non_cash · major_holder · debt_heavy · halted · public_single · company.repeat · company.withdrawn. 세지 않는 신호: CB refixing · old_bonds (문단 1에 숫자로 들어감).
- 문장 재료: non_cash (빚→주식 "현금은 들어오지 않아요. 빚 ○억을 주식(CB)으로 바꿔요" / 현물 "현금 대신 ○○을 받아요" / 섞임 "일부만 현금이에요" / 불명 "납입 방식을 원문에서 확인하지 못했어요", payment_evidence 한 줄 인용) · major_holder (relation 원문 그대로 + 이름 + 주식 수/금액) · debt_heavy ("모은 돈 ○억 중 ○억(○%)을 빚 갚는 데 써요") · halted ("공시 당시 거래정지 중이었어요") · public_single ("공모인데 ○○ 한 곳이 전액 샀고, 30억 미만 소액공모라 증권신고서를 내지 않았어요. 사모와 달리 1년 잠금이 없어요") · repeat ("최근 2년 ○번째 조달이에요 (유상증자 ○ · CB ○)") · withdrawn ("이전에 철회한 공시가 있어요")
- 대상자 여러 명 (v1.2, 사용자): 주식 수(CB는 금액) 상위 3곳만 이름 · 관계 · 수량, 나머지는 "외 N곳도 함께 받아요". 3곳 이하면 전부.
- 오기 (v1.2): card.flags.shares_before_typo면 문단 1에 "기존 주식 수가 원문에 잘못 적혀 있어 이 공시의 희석률은 계산하지 않았어요".
- 감자: 회사 dilution()의 flags.capital_reduction이 true면 문단 1 마지막에 ⚠️ "중간에 감자가 있어 합산 희석률을 계산할 수 없어요" (v1.1, 입력에 dilution_flags 추가).
- flags: date_flag 있으면 문단 4 끝에 ⚠️ "원문의 날짜가 서로 맞지 않아요 (○○)". shares_before_missing이면 문단 1에 "기존 주식 수가 원문에 없어 희석률을 계산할 수 없어요".

## 4. 문체 · 숫자 규칙
존댓말 "~예요 / ~해요", 문단당 2~4문장. 원 → 억 (소수점 1자리, 1억 미만은 만 단위), 주식 수 쉼표, 비율 소수점 1자리, 날짜 YYYY-MM-DD. NULL은 말하지 않거나 "원문에 없어요". 마지막 줄 "📎 근거: 접수번호 ○○○". 이모지는 정해진 것만 (제목 📊 💰 👤 📅/🙋 🔍, 신호 ⚠️, 없음 ✅, 근거 📎). 하지 않는 것: JSON 밖의 숫자 · 회사 설명 · 주가 · 추천 · 비유.

## 5. 예시
엔투텍 20260820000443 (CB) · 뷰노 20260828001315 (유상증자) · 신호 없음(코이즈) — 지시문(docs/alert_prompt.md)에 그대로 넣음.

## 6. 검증
1. 숫자 검사 (코드): 문장의 숫자가 JSON 값 · calc에서 나올 수 있는 숫자(원→억, 반올림 포함) 집합에 있는지. 2. 금지어 검사: 추천 · 매수 · 매도 · 위험하 · 안전하 · 괜찮 · 좋아요 · 나빠요. 3. 10건 생성 (엔투텍 · 코이즈 · 압타머 · 뷰노 · 뉴온 · 아스트 · 카프로 · 코다코 · 제일엠앤에스 · STX). 4. 비용.

## 8. 하지 않는 것
MCP 노출 · agent · DB 쓰기 · 새 계산 · 신호 추가. 문장이 어색해도 JSON을 고치지 말고 프롬프트를 고친다.

## 구현 메모 (2026-10-19)
- 금지어 검사는 명세가 정한 제목 줄("🔍 이 회사 괜찮나" 등)은 빼고 본문만 본다 (제목에 "괜찮"이 있음).
- 근거 줄("📎 근거: 접수번호")은 코드가 붙인다 (LLM은 문단 5개만).
- 검사 추가: 문단 5 신호 수(주의할 점 N · ⚠️ 줄 수 = 세는 신호 수), 반복 조달 괄호 숫자 (작은 정수는 숫자 검사로 못 잡음).
- 5장 예시 2곳을 데이터대로 고침 (사용자, 2026-10-19): 엔투텍 "(유상증자 2 · CB 2)" → "(유상증자 1 · CB 3)", 뷰노 "주의할 점 1가지" → "2가지" + ⚠️ "최근 2년 4번째 조달이에요 (유상증자 1 · CB 3)."
