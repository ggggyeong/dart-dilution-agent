-- 원문(document.xml) 수집. ZIP 안 파일을 모두 저장하고 본문 파일과 양식 정보를 기록한다.
ALTER TABLE collect_log ADD COLUMN rcept_no char(14);
CREATE INDEX collect_log_rcept_no_idx ON collect_log (rcept_no) WHERE rcept_no IS NOT NULL;

CREATE TABLE documents (
    rcept_no         char(14)    PRIMARY KEY,
    main_file        text,                    -- 본문 XML 파일 이름
    files            jsonb       NOT NULL,    -- [{name, doc_code, doc_name}] ZIP 안 전체
    doc_code         text,                    -- 본문의 DOCUMENT-NAME ACODE (유상증자 11306, CB 11324)
    doc_name         text,
    formula_version  text,                    -- FORMULA-VERSION (양식 버전)
    formula_adate    date,                    -- FORMULA-VERSION ADATE
    fetched_at       timestamptz NOT NULL DEFAULT now()
);
