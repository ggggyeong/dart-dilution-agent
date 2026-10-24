-- CB 추출 명세 v1 (docs/cb_spec.md 2-2). CB도 events · filings · allottees를 같이 쓰고, 값 테이블은 cb_values 하나.
-- 키는 기존 테이블과 같은 char(14) (명세의 char는 char(1)이라 접수번호가 들어가지 않음).

-- events: CB도 같은 테이블
ALTER TABLE events ADD COLUMN event_type text NOT NULL DEFAULT '유상증자' CHECK (event_type IN ('유상증자', 'CB'));
ALTER TABLE events ALTER COLUMN ci_mth DROP NOT NULL;  -- CB는 NULL

-- cb_values: rights_values와 같은 구조 (공시(버전)마다 한 줄, 정정본 대비)
CREATE TABLE cb_values (
    rcept_no           char(14) PRIMARY KEY REFERENCES filings ON DELETE CASCADE,
    event_id           char(14) NOT NULL REFERENCES events ON DELETE CASCADE,
    amount             bigint,       -- DNM_SUM 권면(전자등록)총액 (원)
    conv_price         numeric,      -- EXE_PRC 전환가액
    conv_shares        bigint,       -- STK_CNT 전환에 따라 발행할 주식수
    shares_before      bigint,       -- TBL_NSB ISSU_TOT 기발행주식 총수 (C). '-'면 NULL
    min_price          numeric,      -- MIN_PRC 최저 조정가액. '-'면 NULL = 리픽싱 없음
    old_cb_shares      bigint,       -- TBL_NSB SUB_TOT_STK 기발행 미상환 사채 전환가능주식 소계 (A). '-'면 0
    fund_facility      bigint,
    fund_business_acq  bigint,
    fund_operating     bigint,
    fund_debt          bigint,
    fund_other_corp    bigint,       -- ANC_ACQ_PRC (유상증자는 ANC_ACQ_AMT)
    fund_other         bigint,
    issue_method       text CHECK (issue_method IN ('사모', '공모', '해외')),
    exempt_reason      text,         -- RPT_SBM 원문 그대로
    conv_start         date,
    conv_end           date,
    maturity           date,
    pay_date           date,
    coupon             numeric,      -- PRFT_RATE 표면이자율 (%)
    ytm                numeric,      -- LST_RTN_RT 만기이자율 (%)
    date_flag          text,         -- 날짜 순서가 어긋난 칸 (예: 'pay_date>=maturity'), 값은 그대로 저장
    extractor_version  text        NOT NULL,
    extracted_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX cb_values_event_idx ON cb_values (event_id);

-- allottees: CB 대상자는 주식이 아니라 금액으로 배정 → 금액 칸. 유상증자는 NULL
ALTER TABLE allottees ADD COLUMN amount bigint;
