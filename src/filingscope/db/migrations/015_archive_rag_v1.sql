-- RAG v1 보관 (2026-10-23, 재구축 준비). filing_chunks를 지우지 않고 archive_filing_chunks로 이름만 바꾼다.
-- 새 정답셋이 나오면 이 옛 조각 + 옛 hybrid 검색(archive/2026-10-23_rag_v1/)을 같은 정답셋으로 평가해 기준점(0번 줄)으로 쓴다.
-- filings로 가는 FK(ON DELETE CASCADE)는 끊는다 — build 스크립트가 events를 다시 넣을 때 보관본이 같이 지워지지 않게.
-- 백업: data/backups/rag_v1_20261023.dump (pg_dump -Fc -t filing_chunks).

ALTER TABLE filing_chunks DROP CONSTRAINT filing_chunks_rcept_no_fkey;
ALTER TABLE filing_chunks RENAME TO archive_filing_chunks;
ALTER TABLE archive_filing_chunks RENAME CONSTRAINT filing_chunks_pkey TO archive_filing_chunks_pkey;
ALTER TABLE archive_filing_chunks RENAME CONSTRAINT filing_chunks_rcept_no_field_chunk_no_key TO archive_filing_chunks_rcept_no_field_chunk_no_key;
ALTER TABLE archive_filing_chunks RENAME CONSTRAINT filing_chunks_field_check TO archive_filing_chunks_field_check;
ALTER INDEX filing_chunks_embedding_idx RENAME TO archive_filing_chunks_embedding_idx;
ALTER INDEX filing_chunks_tsv_idx RENAME TO archive_filing_chunks_tsv_idx;
ALTER INDEX filing_chunks_rcept_no_idx RENAME TO archive_filing_chunks_rcept_no_idx;
ALTER SEQUENCE filing_chunks_id_seq RENAME TO archive_filing_chunks_id_seq;
