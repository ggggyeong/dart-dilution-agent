-- CB 권면 통화 (2026-10-24): 원문 AMT_UNIT(권면 통화단위). '-'면 NULL. foreign_currency 문장을 코드가 통화로 만들려고 (사실 카드 calc.foreign_currency_display).
ALTER TABLE cb_values ADD COLUMN currency text;
