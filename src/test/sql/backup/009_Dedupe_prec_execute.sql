-- ============================================================================
-- dedupe_prec_execute.sql
-- 같은 case_no가 여러 doc_id로 중복 적재된 것을 정리합니다.
-- canonical 기준: case_no별 doc_id 오름차순 1등만 남김 (가장 먼저 적재된 것).
-- 순서대로, 한 스텝씩 결과 확인하면서 진행하세요. [3](DELETE) 전에 반드시
-- [1]/[2] 결과를 확인하고, 필요하면 이 파일 그대로 저장해두세요(롤백 참고용).
-- ============================================================================

-- [1] 삭제 대상 doc_id 백업 (실제 행 전체를 별도 테이블로 복사, 삭제 전 필수)
CREATE TABLE prec_dedup_backup_20260808 AS
SELECT lc.*
FROM legal_chunks lc
JOIN (
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
    SELECT doc_id FROM ranked WHERE rn > 1
) dup ON lc.doc_id = dup.doc_id
WHERE lc.doc_type = 'prec';

-- prec_dedup_backup_20260808

-- [2] 백업된 건수 확인 (지난번 조사에서 나온 2,092 doc_id에 해당하는 청크 수와
--     비슷한 규모인지 - 사건당 평균 5.7청크였으니 대략 만 건 안팎이면 정상 범위)
SELECT COUNT(*) AS backed_up_chunks,
       COUNT(DISTINCT doc_id) AS backed_up_doc_ids
FROM prec_dedup_backup_20260808;

-- 11327	2092


-- [3] 실제 삭제 (위 [2] 숫자를 눈으로 확인한 뒤에만 실행하세요)
DELETE FROM legal_chunks
WHERE doc_type = 'prec'
  AND doc_id IN (SELECT DISTINCT doc_id FROM prec_dedup_backup_20260808);

-- DELETE 11327


-- [4] 삭제 후 재검증 - 전체 prec 건수가 (기존 669,619 - [2]의 backed_up_chunks)와 일치해야 함
SELECT doc_type, COUNT(*) AS total
FROM legal_chunks
WHERE doc_type = 'prec'
GROUP BY doc_type;

-- "prec"	658292

-- [5] case_no 중복이 실제로 사라졌는지 최종 확인 (0건이어야 함)
WITH case_groups AS (
    SELECT DISTINCT
        regexp_replace(title, '^판례 \[([^\]]+)\].*$', '\1') AS case_no,
        doc_id
    FROM legal_chunks
    WHERE doc_type = 'prec'
)
SELECT COUNT(*) AS remaining_duplicate_case_no
FROM (
    SELECT case_no FROM case_groups GROUP BY case_no HAVING COUNT(*) > 1
) t;

-- 0


-- ============================================================================
-- 롤백이 필요하면 (삭제를 되돌리려면):
-- INSERT INTO legal_chunks SELECT * FROM prec_dedup_backup_20260808;
-- 이후 검증되면 백업 테이블은 정리:
-- DROP TABLE prec_dedup_backup_20260808;
-- ============================================================================