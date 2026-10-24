-- DART 주요사항보고서 전용 API(유상증자·유무상증자·CB 결정)로 제목 판정을 크로스체크한다.

-- 회사별 호출 이력 (이어받기: 이미 성공한 (endpoint, corp_code)는 건너뛴다)
ALTER TABLE collect_log ADD COLUMN corp_code char(8);
CREATE INDEX collect_log_endpoint_corp_idx ON collect_log (endpoint, corp_code) WHERE ok;

-- 크로스체크 대상 회사. A: 우리 판정에서 유상증자·CB가 나온 회사 전부, B: 안 나온 상장사 무작위 표본
CREATE TABLE crosscheck_targets (
    corp_code   char(8)     PRIMARY KEY,
    grp         char(1)     NOT NULL CHECK (grp IN ('A', 'B')),
    note        text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- API가 돌려준 공시 (사건마다 최종본 1건)
CREATE TABLE crosscheck_api_rows (
    api         text        NOT NULL,   -- piicDecsn / pifricDecsn / cvbdIsDecsn
    rcept_no    char(14)    NOT NULL,
    corp_code   char(8)     NOT NULL,
    corp_name   text,
    corp_cls    char(1),
    raw         jsonb       NOT NULL,
    fetched_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (api, rcept_no)
);
CREATE INDEX crosscheck_api_rows_corp_idx ON crosscheck_api_rows (corp_code);
