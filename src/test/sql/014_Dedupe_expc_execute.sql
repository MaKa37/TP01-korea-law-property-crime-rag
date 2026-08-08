-- ============================================================================
-- dedupe_expc_execute.sql
-- 같은 안건명(title, conclusion 청크 기준)이 여러 expc_id로 중복 적재된 것을 정리합니다.
-- canonical 기준: title별 doc_id 오름차순 1등만 남김.
-- 21건 샘플 검증 결과 content_hash가 완전히 동일했으므로(순수 재수집 중복),
-- prec 때와 달리 canonical 선택 기준에 대한 추가 고민 없이 진행해도 안전합니다.
-- 순서대로 한 스텝씩 확인하면서 진행하세요.
-- ============================================================================

-- [1] 삭제 대상 doc_id 백업 (전체 행)
CREATE TABLE expc_dedup_backup_20260808 AS
SELECT lc.*
FROM legal_chunks lc
JOIN (
    WITH conclusion_groups AS (
        SELECT title, doc_id
        FROM legal_chunks
        WHERE doc_type = 'expc'
          AND chunk_id LIKE '%\_conclusion'
    ),
    ranked AS (
        SELECT title, doc_id,
               ROW_NUMBER() OVER (PARTITION BY title ORDER BY doc_id ASC) AS rn
        FROM conclusion_groups
    )
    SELECT doc_id FROM ranked WHERE rn > 1
) dup ON lc.doc_id = dup.doc_id
WHERE lc.doc_type = 'expc';

-- SELECT 7849


-- [2] 백업 건수 확인 - backed_up_doc_ids가 1,057과 일치해야 함
SELECT COUNT(*) AS backed_up_chunks,
       COUNT(DISTINCT doc_id) AS backed_up_doc_ids
FROM expc_dedup_backup_20260808;

-- 7849	1057

-- [3] 실제 삭제 ([2] 숫자 확인 후에만 실행)
DELETE FROM legal_chunks
WHERE doc_type = 'expc'
  AND doc_id IN (SELECT DISTINCT doc_id FROM expc_dedup_backup_20260808);

-- DELETE 7849

-- [4] 삭제 후 재검증 - (기존 52,407 - [2]의 backed_up_chunks)와 일치해야 함
SELECT doc_type, COUNT(*) AS total
FROM legal_chunks
WHERE doc_type = 'expc'
GROUP BY doc_type;

-- "expc"	44558

-- [5] 중복이 실제로 사라졌는지 최종 확인 (0건이어야 함)
WITH conclusion_groups AS (
    SELECT title, doc_id
    FROM legal_chunks
    WHERE doc_type = 'expc'
      AND chunk_id LIKE '%\_conclusion'
)
SELECT COUNT(*) AS remaining_duplicate_titles
FROM (
    SELECT title FROM conclusion_groups GROUP BY title HAVING COUNT(*) > 1
) t;

-- 0

-- ============================================================================
-- 롤백:
-- INSERT INTO legal_chunks SELECT * FROM expc_dedup_backup_20260808;
-- 정리:
-- DROP TABLE expc_dedup_backup_20260808;
-- ============================================================================