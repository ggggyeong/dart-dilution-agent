-- 수집한 공시 목록 (유상증자결정, 전환사채권발행결정)
CREATE TABLE filings (
    rcept_no        char(14)    PRIMARY KEY,           -- 원문 요청, 중복 방지, 출처, 정정본 연결
    corp_code       char(8)     NOT NULL,              -- 회사별 이력
    corp_name       text        NOT NULL,
    stock_code      char(6),                           -- pykrx 주가 조회
    corp_cls        char(1)     NOT NULL CHECK (corp_cls IN ('Y', 'K')),  -- Y 코스피, K 코스닥
    report_nm       text        NOT NULL,              -- 원래 제목
    rcept_dt        date        NOT NULL,              -- 제출일
    available_date  date        NOT NULL,              -- as_of 기준 공개일 (= rcept_dt)
    event_type      text        NOT NULL CHECK (event_type IN ('rights_issue', 'cb')),
    is_correction   boolean     NOT NULL,              -- 제목 앞 [ ] 태그 여부
    report_tag      text,                              -- [ ] 안 글자 (기재정정, 발행조건확정 등)
    is_mixed        boolean     NOT NULL DEFAULT false,  -- 유무상증자결정
    status          text        NOT NULL DEFAULT 'collected',
    collected_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX filings_corp_code_rcept_dt_idx ON filings (corp_code, rcept_dt);
CREATE INDEX filings_available_date_idx ON filings (available_date);
