-- 새 RAG 조각 (docs/cc_task_rag_build.md 2장, 2026-10-23). 자르기 방법 2가지를 method 칸으로 구분해 한 테이블에.
-- 검색은 공시 1건(또는 같은 날 형제) 안에서만 하므로 벡터 인덱스는 두지 않는다 (후보 = 그 공시 조각 전부, 수십 개).
-- filings로 FK를 걸지 않는다 — build 스크립트가 events를 다시 넣을 때 CASCADE로 지워지지 않게 (RAG v1 보관 때와 같은 이유).

CREATE TABLE rag_chunks (
    id             serial   PRIMARY KEY,
    method         text     NOT NULL CHECK (method IN ('size', 'section')),
    rcept_no       char(14) NOT NULL,
    doc_type       text     NOT NULL CHECK (doc_type IN ('유상증자', 'CB')),
    field          text     NOT NULL,   -- free_text_2978.jsonl의 칸 코드 (FREE_TEXT · EXE_REG · MIN_RSN · OPT_FCT · ISS_FUNC · PRC_PLN)
    section_title  text,                -- section만: 속한 절의 제목 한 줄
    topic          text     CHECK (topic IN ('T1', 'T2', 'T3', 'T4', '기타')),   -- section만
    seq            int      NOT NULL,   -- 같은 method · rcept_no 안 원문 순서 (1부터)
    text           text     NOT NULL,   -- section은 맨 앞에 section_title 한 줄
    embedding      vector(1536),        -- text-embedding-3-small
    tsv            tsvector,            -- to_tsvector('simple', text)
    UNIQUE (method, rcept_no, seq)
);
CREATE INDEX ON rag_chunks (method, rcept_no);
