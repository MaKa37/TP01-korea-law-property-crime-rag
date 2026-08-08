-- ============================================================================
-- dedupe_prec_candidates.sql
-- case_no 기준으로 canonical doc_id(가장 작은 doc_id = 가장 먼저 적재된 것)를
-- 하나만 남기고 나머지를 "삭제 후보"로 표시합니다.
-- 이 파일은 조사용입니다. 실제 DELETE는 [2] 결과를 사람이 검토한 뒤,
-- 별도로 신중하게 실행하세요 (백업 필수).
-- ============================================================================

-- [1] case_no별 canonical doc_id 선정 (doc_id 오름차순 1등을 canonical로 가정)
--     주의: doc_id가 작다고 항상 "더 완전한 버전"이라는 보장은 없습니다.
--     [3]에서 봤듯 뒤에 수집된 버전이 더 정제된 텍스트일 수도 있어서,
--     실제 삭제 전에는 몇 건 표본으로 내용 품질을 비교해보는 걸 권장합니다.
WITH case_groups AS (
    SELECT DISTINCT
        regexp_replace(title, '^판례 \[([^\]]+)\].*$', '\1') AS case_no,
        doc_id
    FROM legal_chunks
    WHERE doc_type = 'prec'
),
ranked AS (
    SELECT case_no, doc_id,
           ROW_NUMBER() OVER (PARTITION BY case_no ORDER BY doc_id ASC) AS rn
    FROM case_groups
)
SELECT case_no, doc_id AS canonical_doc_id
FROM ranked
WHERE rn = 1;


-- [2] 삭제 후보 (canonical이 아닌 doc_id들) - 몇 건인지, 어떤 것들인지 확인용
WITH case_groups AS (
    SELECT DISTINCT
        regexp_replace(title, '^판례 \[([^\]]+)\].*$', '\1') AS case_no,
        doc_id
    FROM legal_chunks
    WHERE doc_type = 'prec'
),
ranked AS (
    SELECT case_no, doc_id,
           ROW_NUMBER() OVER (PARTITION BY case_no ORDER BY doc_id ASC) AS rn
    FROM case_groups
)
SELECT r.case_no, r.doc_id AS duplicate_doc_id,
       COUNT(lc.chunk_id) AS chunk_count_if_deleted
FROM ranked r
JOIN legal_chunks lc ON lc.doc_id = r.doc_id AND lc.doc_type = 'prec'
WHERE r.rn > 1
GROUP BY r.case_no, r.doc_id
ORDER BY r.case_no;


-- [3] (참고, 실행 보류) 실제 삭제는 검토 후 이렇게 - 지금은 실행하지 마세요
-- DELETE FROM legal_chunks
-- WHERE doc_type = 'prec'
--   AND doc_id IN (
--       -- 위 [2] 쿼리의 duplicate_doc_id 목록
--   );