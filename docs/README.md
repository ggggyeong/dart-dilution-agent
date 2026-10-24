# 문서 지도

| 보고 싶은 것 | 문서 |
|---|---|
| 실제 질문과 답 · 사실 카드 · 알림 예시 | [examples.md](examples.md) |
| 공시 원문이 어떻게 생겼고 어디를 RAG로 넣었나 | [analysis/](analysis/README.md) |
| agent 최종 평가 (요약표 · 실패 분석 · v8 → v9 → 마무리) | [eval/final_report.md](eval/final_report.md) |
| agent 질문과 답 전문 (학습 178 · 봉인 115) | [eval/answers_train_v9.md](eval/answers_train_v9.md) · [eval/answers_sealed_v9.md](eval/answers_sealed_v9.md) |
| RAG 실험 · 봉인 결과 | [eval/rag_exp_report.md](eval/rag_exp_report.md) · [eval/rag_sealed_report.md](eval/rag_sealed_report.md) |
| 희석률 계산 검증 (독립 재계산 652/654) | [eval/verify_report_calc.md](eval/verify_report_calc.md) |

## 명세
| 문서 | 내용 |
|---|---|
| [facts_spec.md](facts_spec.md) | 사실 카드: 계산식 · 신호 · 특수 상황 표시 |
| [calc_criteria.md](calc_criteria.md) | 희석률이 틀릴 수 있는 상황 10개와 처리 기준 |
| [rights_issue_spec.md](rights_issue_spec.md) · [cb_spec.md](cb_spec.md) | 유상증자 · CB 원문 칸 → DB 칸 |
| [stage1_title_classifier.md](stage1_title_classifier.md) | 공시 제목 분류 |
| [validation.md](validation.md) | 추출값 검증 |
| [mcp_spec.md](mcp_spec.md) | MCP 도구 5개 입력 · 출력 |
| [agent_spec.md](agent_spec.md) · [alert_spec.md](alert_spec.md) | agent · 알림 |

## 프롬프트
| 문서 | 내용 |
|---|---|
| [agent_prompt_v9.md](agent_prompt_v9.md) | agent 프롬프트 (공식 결과 버전, 코드 기본값) |
| [agent_prompt_v10.md](agent_prompt_v10.md) | 다음 후보 (실행 안 함) |
| [alert_prompt.md](alert_prompt.md) | 알림 프롬프트 |
| [../prompts/payment_v4.md](../prompts/payment_v4.md) | 수집 때 납입 방식 판정 프롬프트 |
