-- A 거르기: 공시 당시(공시일 직전 거래일들) 코스피·코스닥에서 거래됐는지. 지금의 corp_cls가 아니라 당시 기준.
CREATE TABLE listing_checks (
    rcept_no       char(14)    PRIMARY KEY,
    stock_code     char(6),
    window_start   date,          -- 확인한 거래일 범위 (공시일 직전 N거래일)
    window_end     date,
    market         text,          -- KOSPI / KOSDAQ / KONEX / NULL
    listed_days    integer,       -- 그중 코스피·코스닥 목록에 있던 날 수
    traded_days    integer,       -- 그중 거래량 > 0인 날 수
    passed         boolean     NOT NULL,
    reason         text        NOT NULL,  -- 통과 / 거래정지 / 코넥스 / 공시 당시 미상장 / 비상장(종목코드 없음)
    checked_at     timestamptz NOT NULL DEFAULT now()
);
