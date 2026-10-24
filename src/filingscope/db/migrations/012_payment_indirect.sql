-- LLM이 납입 방식을 직접 말하는 문장 없이 정황(현물출자 여부, 자금 용도, 납입처 등)으로 판단했는가.
-- 규칙 판정(rule:*)과 LLM 대기는 NULL. Research Agent가 '쓰인 사실'과 '추론'을 구분하는 데 쓴다.
ALTER TABLE events ADD COLUMN payment_indirect boolean;
