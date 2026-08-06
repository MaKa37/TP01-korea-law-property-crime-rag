-- ============================================================================
-- verify_reembedding.sql
-- reembed_with_title.py 실행 완료 후, DB에 제대로 적재됐는지 검증하는 스크립트입니다.
--
-- 사용 전 준비:
--   1) <BACKUP_TABLE> 을 이번 실행 로그에 찍힌 실제 백업 테이블명으로 전부 치환하세요.
--      예: 💾 기존 임베딩 백업 완료: legal_chunks_embedding_backup_20260806_081234
--          → legal_chunks_embedding_backup_20260806_081234
--   2) [0]은 전체 훑어보기용 요약, [1]~[7]은 각 항목 상세 확인용입니다.
--      psql -f verify_reembedding.sql 로 전체 실행하거나, 필요한 섹션만 골라 실행하세요.
--   3) 1,129,272건 전체 스캔이 들어가는 쿼리가 있어 일부는 수 분 걸릴 수 있습니다.
-- ============================================================================


-- ============================================================================
-- [0] 종합 요약 - 여기부터 먼저 돌려보고, 이상 있는 항목만 아래 상세 쿼리로 drill-down
-- ============================================================================
SELECT '1. 전체 행 수 (재임베딩 전후 동일해야 함, 기대값: 1,129,272)' AS check_item,
       COUNT(*)::text AS result
FROM legal_chunks

UNION ALL

SELECT '2. embedding NULL 잔여 건수 (0 이어야 함 = 실패 없이 전부 채워짐)',
       COUNT(*)::text
FROM legal_chunks
WHERE embedding IS NULL

UNION ALL

SELECT '3. chunk_id 중복 건수 (0 이어야 함 = UPDATE가 행을 안 늘렸음)',
       (COUNT(*) - COUNT(DISTINCT chunk_id))::text
FROM legal_chunks

UNION ALL

SELECT '4. 임베딩 차원 이상 건수 (0 이어야 함, 정상: 2048차원)',
       COUNT(*)::text
FROM legal_chunks
WHERE embedding IS NOT NULL
  AND vector_dims(embedding::vector) <> 2048

UNION ALL

SELECT '5. 노름(norm) 이상 건수 (0.9~1.1 벗어난 것, 극소수/0에 가까워야 함)',
       COUNT(*)::text
FROM legal_chunks
WHERE embedding IS NOT NULL
  AND (vector_norm(embedding::vector) < 0.9 OR vector_norm(embedding::vector) > 1.1)

UNION ALL

SELECT '6. 백업 대비 실제로 값이 바뀐 행 수 (재임베딩 대상 건수와 비슷해야 함)',
       COUNT(*)::text
FROM legal_chunks lc
JOIN <BACKUP_TABLE> b ON lc.chunk_id = b.chunk_id
WHERE lc.embedding IS DISTINCT FROM b.embedding;


-- ============================================================================
-- [1] doc_type별 NULL 잔여 분포
--     특정 doc_type에서만 실패가 몰려있는지 확인 (배치 실패 패턴 파악용)
-- ============================================================================
SELECT doc_type, COUNT(*) AS null_count
FROM legal_chunks
WHERE embedding IS NULL
GROUP BY doc_type
ORDER BY null_count DESC;


-- ============================================================================
-- [2] 차원이 2048이 아닌 행 상세 (있다면 chunk_id 목록 뽑아서 개별 재처리 대상 확보)
-- ============================================================================
SELECT chunk_id, doc_type, vector_dims(embedding::vector) AS dims
FROM legal_chunks
WHERE embedding IS NOT NULL
  AND vector_dims(embedding::vector) <> 2048
LIMIT 100;


-- ============================================================================
-- [3] 노름 이상(제로 벡터 의심 등) 행 상세
-- ============================================================================
SELECT chunk_id, doc_type, vector_norm(embedding::vector) AS norm
FROM legal_chunks
WHERE embedding IS NOT NULL
  AND (vector_norm(embedding::vector) < 0.9 OR vector_norm(embedding::vector) > 1.1)
ORDER BY norm ASC
LIMIT 100;


-- ============================================================================
-- [4] 백업 대비 안 바뀐 행 상세 (재임베딩 대상이었는데 실제로는 그대로인 경우)
--     여기 나오면 배치는 성공 처리됐지만 실제 UPDATE가 안 먹었거나,
--     결합 텍스트가 이전과 우연히 동일했던 경우(title이 원래 비어있었던 등)일 수 있습니다.
-- ============================================================================
SELECT lc.chunk_id, lc.doc_type, lc.title
FROM legal_chunks lc
JOIN <BACKUP_TABLE> b ON lc.chunk_id = b.chunk_id
WHERE lc.embedding IS NOT DISTINCT FROM b.embedding
LIMIT 100;


-- ============================================================================
-- [5] 스팟체크 ①: 10ㆍ27법난 케이스 - 재임베딩 전후 "거리"가 실제로 벌어졌는지
--     law_010719(정답 법) vs law_010831(오검색됐던 다른 법)의 목적조항 간 거리를
--     재임베딩 전(백업) / 후(현재)로 비교합니다. 거리가 커졌으면(=덜 유사해졌으면) 개선.
-- ============================================================================
WITH before AS (
    SELECT b1.embedding <=> b2.embedding AS dist_before
    FROM <BACKUP_TABLE> b1, <BACKUP_TABLE> b2
    WHERE b1.chunk_id = 'law_010719_art_0001001'
      AND b2.chunk_id = 'law_010831_art_0001001'
),
after AS (
    SELECT lc1.embedding <=> lc2.embedding AS dist_after
    FROM legal_chunks lc1, legal_chunks lc2
    WHERE lc1.chunk_id = 'law_010719_art_0001001'
      AND lc2.chunk_id = 'law_010831_art_0001001'
)
SELECT
    before.dist_before AS dist_before_reembed,
    after.dist_after   AS dist_after_reembed,
    (after.dist_after - before.dist_before) AS diff_increase_is_good
FROM before, after;


-- ============================================================================
-- [6] 스팟체크 ②: law_010719_art_0001001 기준 최근접 이웃 Top 10
--     재임베딩 후 상위권에 law_010719(같은 법) 비중이 늘고, law_010831 비중이
--     줄었는지 눈으로 확인. eval 스크립트 재실행 전에 먼저 감을 잡을 수 있습니다.
-- ============================================================================
SELECT chunk_id, doc_id, title,
       embedding <=> (SELECT embedding FROM legal_chunks WHERE chunk_id = 'law_010719_art_0001001') AS dist
FROM legal_chunks
WHERE doc_type = 'law' AND embedding IS NOT NULL
ORDER BY dist ASC
LIMIT 10;


-- ============================================================================
-- [7] doc_type별 최종 건수 재확인 (--doc-type 없이 전체 실행했다면 아래 5개 합이
--     [0]의 총 행 수와 같아야 함)
-- ============================================================================
SELECT doc_type, COUNT(*) AS total,
       COUNT(*) FILTER (WHERE embedding IS NOT NULL) AS embedded,
       COUNT(*) FILTER (WHERE embedding IS NULL) AS still_null
FROM legal_chunks
GROUP BY doc_type
ORDER BY total DESC;