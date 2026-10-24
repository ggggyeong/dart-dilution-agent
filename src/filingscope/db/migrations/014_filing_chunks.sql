-- ⑦ RAG 조각 (docs/rag_spec.md 4장). 공시 원문의 글 칸 · 표 밖 문단을 잘라 원문 그대로 저장, 7-2에서 embedding · tsv 채움.
-- 종류 · 날짜 · 회사는 넣지 않는다 (filings · events와 JOIN). rcept_no는 filings와 같은 char(14).

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE filing_chunks (
    id         serial   PRIMARY KEY,
    rcept_no   char(14) NOT NULL REFERENCES filings(rcept_no) ON DELETE CASCADE,
    field      text     NOT NULL CHECK (field IN ('OPT_FCT', 'EXE_REG', 'MOR_OFR', 'OBJ', 'SLT_JDG', 'ETC')),
    chunk_no   int      NOT NULL,   -- 같은 rcept_no · field 안 순서 (1부터)
    text       text     NOT NULL,   -- 원문 그대로 (태그 · 연속 공백만 정리)
    embedding  vector(1536),        -- text-embedding-3-small (7-2)
    tsv        tsvector,            -- to_tsvector('simple', text) (7-2)
    UNIQUE (rcept_no, field, chunk_no)
);
CREATE INDEX ON filing_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON filing_chunks USING gin (tsv);
CREATE INDEX ON filing_chunks (rcept_no);
