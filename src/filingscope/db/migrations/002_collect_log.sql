-- API 호출 이력. 실패한 구간·페이지만 다시 받을 수 있게 호출마다 한 줄.
CREATE TABLE collect_log (
    id          bigserial   PRIMARY KEY,
    run_id      text        NOT NULL,             -- 한 번의 수집 실행
    endpoint    text        NOT NULL,             -- list.json 등
    bgn_de      date,
    end_de      date,
    page_no     integer,
    ok          boolean     NOT NULL,
    status      text,                             -- DART status 또는 HTTP_xxx / NETWORK
    message     text,
    attempts    integer     NOT NULL,
    raw_path    text,                             -- 저장한 원본 JSON (프로젝트 기준 상대 경로)
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX collect_log_run_id_idx ON collect_log (run_id);
CREATE INDEX collect_log_failed_idx ON collect_log (ok) WHERE NOT ok;
