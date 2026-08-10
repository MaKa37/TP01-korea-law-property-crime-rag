-- ============================================================================
-- check_prec_citation_boilerplate.sql
-- HARD_PREC_04에서 관찰된 "여러 사건이 같은 상급심 판례를 그대로 인용"하는
-- 패턴이 prec 전체에서 얼마나 흔한지 확인합니다.
-- 이미 본문에서 확인된 두 인용(97도3113, 99도4940)을 표본으로 씁니다.
-- ============================================================================

-- [1] "97도3113" 인용이 등장하는 서로 다른 사건 수
SELECT COUNT(DISTINCT doc_id) AS distinct_cases_citing
FROM legal_chunks
WHERE doc_type = 'prec'
  AND content LIKE '%97도3113%';

-- 14

-- [2] "99도4940" 인용이 등장하는 서로 다른 사건 수
SELECT COUNT(DISTINCT doc_id) AS distinct_cases_citing
FROM legal_chunks
WHERE doc_type = 'prec'
  AND content LIKE '%99도4940%';

-- 36

-- [3] 전체 규모 감 잡기: prec body 청크 중 "대법원 ____. __. __. 선고 ___도____ 판결"
--     형태의 인용구가 포함된 청크 비율 (인용 자체가 얼마나 흔한 서술 방식인지)
SELECT
    COUNT(*) FILTER (WHERE content ~ '대법원 \d{4}\. \d{1,2}\. \d{1,2}\. 선고') AS chunks_with_citation,
    COUNT(*) AS total_body_chunks
FROM legal_chunks
WHERE doc_type = 'prec'
  AND chunk_id LIKE '%\_body\_%';

-- 79235	533079