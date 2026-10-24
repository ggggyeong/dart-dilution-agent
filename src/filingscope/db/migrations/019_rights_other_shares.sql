-- 보통주 · 기타주식(우선주)을 함께 발행한 유상증자(stock_type 혼합)의 기타주식 몫 (2026-10-24 fix2).
-- 조달액 = 보통주 + 기타주식 발행 총액. 희석률은 지금처럼 보통주만 (new_shares).
ALTER TABLE rights_values ADD COLUMN other_new_shares bigint;
ALTER TABLE rights_values ADD COLUMN other_price numeric;
