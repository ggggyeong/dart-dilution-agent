-- payment_type 값 변경: 출자전환·상계를 '빚→주식' 하나로 합침 (회사도 "출자전환(채무상계)"처럼 섞어 씀).
-- LLM 대기는 payment_type = NULL, payment_source = 'pending:llm'.
ALTER TABLE events DROP CONSTRAINT events_payment_type_check;
ALTER TABLE events ADD CONSTRAINT events_payment_type_check
    CHECK (payment_type IN ('현금', '빚→주식', '현물', '섞임', '불명'));
