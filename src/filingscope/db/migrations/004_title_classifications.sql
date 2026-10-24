-- 제목 판정 결과 (제목 종류마다 한 줄). 판정은 제목만으로 정해지므로 같은 제목 = 같은 결과.
CREATE TABLE title_classifications (
    report_nm           text        PRIMARY KEY,
    event_type          text        NOT NULL CHECK (event_type IN ('rights_issue', 'cb', 'none')),
    is_correction       boolean     NOT NULL,
    report_tag          text,
    is_mixed            boolean     NOT NULL,
    classifier_version  text        NOT NULL,
    classified_at       timestamptz NOT NULL DEFAULT now()
);

-- filings는 이제 판정 결과로 만들어지는 뷰. 다시 판정하면 자동으로 바뀐다.
DROP TABLE filings;

CREATE VIEW filings AS
SELECT b.rcept_no,
       b.corp_code,
       b.corp_name,
       b.stock_code,
       b.corp_cls,
       b.report_nm,
       b.rcept_dt,
       b.rcept_dt AS available_date,   -- as_of 기준 공개일
       t.event_type,
       t.is_correction,
       t.report_tag,
       t.is_mixed,
       t.classifier_version
FROM b001_filings b
JOIN title_classifications t USING (report_nm)
WHERE t.event_type <> 'none';
