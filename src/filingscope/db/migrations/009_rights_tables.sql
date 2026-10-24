-- 유상증자 추출 명세 v1 (docs/rights_issue_spec.md)의 테이블 4개.
-- 공시 당시 코스피·코스닥 상장사의 11306·11308 공시만 넣는다 (비상장·코넥스·80518 제외, 원본 파일은 유지).
-- rights_values · allottees는 공시(버전)마다 한 줄: as_of 조회 때 그 시점까지의 최신 버전을 고른다 (미래 데이터 누수 방지).

-- 사건: 사건 하나에 한 줄. event_id = 사건의 첫 공시(원본) 접수번호
CREATE TABLE events (
    event_id          char(14) PRIMARY KEY,
    corp_code         char(8)  NOT NULL,
    corp_name         text     NOT NULL,
    ci_mth            smallint NOT NULL,      -- 1·2·3·5·6 → 시험지 결정
    first_date        date     NOT NULL,      -- 첫 공시일 = 공개일
    status            text     NOT NULL DEFAULT '진행' CHECK (status IN ('진행', '철회')),
    withdraw_reason   text,
    market_at_filing  text     NOT NULL CHECK (market_at_filing IN ('KOSPI', 'KOSDAQ')),
    halted_at_filing  boolean  NOT NULL,      -- 공시 직전 1거래일 거래량 0
    payment_type      text     CHECK (payment_type IN ('현금', '출자전환', '상계', '현물', '섞임', '불명')),
    payment_evidence  text,                   -- LLM이 판정했을 때만
    payment_source    text                    -- rule:ci_mth / rule:PIN_EXT_YN / rule:no_keyword / pending:llm / llm:<모델>:<프롬프트>
);
CREATE INDEX events_corp_idx ON events (corp_code, first_date);

-- 공시: 원본·정정 한 건에 한 줄
CREATE TABLE filings (
    rcept_no       char(14) PRIMARY KEY,
    event_id       char(14) NOT NULL REFERENCES events ON DELETE CASCADE,
    rcept_dt       date     NOT NULL,         -- 공개일 (as_of 기준)
    is_correction  boolean  NOT NULL,
    version_no     smallint NOT NULL,         -- 원본 = 1
    UNIQUE (event_id, version_no)
);

-- 유상증자 값: 공시(버전)마다 한 줄
CREATE TABLE rights_values (
    rcept_no            char(14) PRIMARY KEY REFERENCES filings ON DELETE CASCADE,
    event_id            char(14) NOT NULL REFERENCES events ON DELETE CASCADE,
    new_shares          bigint,
    shares_before       bigint,
    price               numeric,
    price_status        text CHECK (price_status IN ('예정', '확정')),
    price_confirm_date  date,
    discount_rate       numeric,              -- 할인 양수, 할증 음수
    discount_evidence   text,
    fund_facility       bigint,
    fund_business_acq   bigint,
    fund_operating      bigint,
    fund_debt           bigint,
    fund_other_corp     bigint,
    fund_other          bigint,
    acquire_target      text,
    lead_manager        text,
    participation       text CHECK (participation IN ('우선', '일반', '불가')),
    right_sale          text CHECK (right_sale IN ('시장', '장외', '불가')),
    record_date         date,
    shares_per_share    numeric,
    subscription_start  date,
    subscription_end    date,
    right_sale_start    date,
    right_sale_end      date,
    right_sale_evidence text,
    listing_date        date,
    extractor_version   text        NOT NULL,
    extracted_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX rights_values_event_idx ON rights_values (event_id);

-- 대상자: 제3자배정만, 공시(버전) × 대상자 한 명에 한 줄
CREATE TABLE allottees (
    rcept_no  char(14) REFERENCES filings ON DELETE CASCADE,
    seq       smallint,                       -- 대상자 표 안의 순서
    event_id  char(14) NOT NULL REFERENCES events ON DELETE CASCADE,
    name      text     NOT NULL,
    relation  text,
    shares    bigint,
    lockup    text,                           -- 기간만 (예: '1년')
    PRIMARY KEY (rcept_no, seq)
);
