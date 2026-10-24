-- 유상증자 추출 명세 v1의 `filings` 테이블(공시 한 건에 한 줄, event_id·version_no)과 이름이 겹쳐서,
-- 제목 분류 결과 뷰(B001 중 유상증자·CB만)를 title_filings로 바꾼다.
ALTER VIEW filings RENAME TO title_filings;
