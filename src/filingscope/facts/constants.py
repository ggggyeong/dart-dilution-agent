"""사실 카드 임계값 (명세 docs/facts_spec.md 4장). 조정은 여기 한 곳에서만."""

DEBT_HEAVY_RATIO = 0.5   # debt_heavy: 채무상환자금 ÷ 조달액 ≥ 이 값
REPEAT_YEARS = 2         # company.repeat_count: 최근 몇 년 안의 events
REPEAT_MIN = 3           # company.repeat: repeat_count ≥ 이 값
SMALL_SAMPLE_N = 30            # compare_event: 비교 그룹 n이 이 값 미만이면 small_sample = true (그룹은 합치지 않음)
# dilution 단위 불일치 (calc_criteria v1 C-4, 2026-10-22 — 예전 감자 추정 '앞 값의 50% 이하'를 대신함)
UNIT_DECREASE = 1.5      # 감소: 뒤 shares_before < 앞 값 ÷ 이 값
UNIT_INCREASE_SLACK = 1.2  # 증가: 뒤 값 > 앞 값 + 그 사이 공시 new_shares · conv_shares 합 × 이 값
UNIT_INCREASE_MIN = 2    #        그리고 뒤 값 ≥ 앞 값 × 이 값
