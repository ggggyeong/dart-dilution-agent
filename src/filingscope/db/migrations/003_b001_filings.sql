-- B001(주요사항보고서) 공시 목록 전체. 제목·시장 구분 없이 저장하고, 분류는 이 표에서 다시 한다.
CREATE TABLE b001_filings (
    rcept_no      char(14)    PRIMARY KEY,
    corp_code     char(8)     NOT NULL,
    corp_name     text        NOT NULL,
    stock_code    char(6),
    corp_cls      char(1)     NOT NULL,   -- Y 코스피, K 코스닥, N 코넥스, E 기타
    report_nm     text        NOT NULL,
    rcept_dt      date        NOT NULL,
    collected_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX b001_filings_corp_code_rcept_dt_idx ON b001_filings (corp_code, rcept_dt);
CREATE INDEX b001_filings_rcept_dt_idx ON b001_filings (rcept_dt);
CREATE INDEX b001_filings_report_nm_idx ON b001_filings (report_nm);
