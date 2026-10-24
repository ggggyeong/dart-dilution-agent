-- 꼬리표 여러 개 (2026-10-23, "빠뜨리는 것보다 더 넣는 게 낫다"). topic = 머리줄 · 절 규칙으로 붙인 처음 주제(기록용),
-- topics = 처음 주제 + 조각 글 안 내용 규칙으로 더 붙인 주제. rule 검색은 topics를 쓴다.
ALTER TABLE rag_chunks ADD COLUMN topics text[];
UPDATE rag_chunks SET topics = ARRAY[topic] WHERE topic IS NOT NULL;
