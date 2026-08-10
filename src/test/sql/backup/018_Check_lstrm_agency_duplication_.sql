-- ============================================================================
-- check_lstrm_agency_duplication.sql
-- 동명이의 title 그룹 안에서, 실제 "정의" 텍스트가 몇 종류나 되는지 확인합니다.
-- (예: "성희롱" 102건 중 실제로 서로 다른 정의는 몇 개인지 - 나머지는 같은
-- 정의를 기관별 훈령이 반복 인용한 것으로 추정)
-- ============================================================================

-- [1] 동명이의 그룹별 총 건수 vs 실제 고유 정의 수 (건수 5개 이상인 그룹만)
WITH dup_titles AS (
    SELECT title
    FROM legal_chunks
    WHERE doc_type = 'lstrm'
    GROUP BY title
    HAVING COUNT(*) > 1
),
def_extract AS (
    SELECT lc.title, lc.chunk_id,
           regexp_replace(lc.content, '^.*?정의: (.*?)(\n출처:.*)?$', '\1', 's') AS definition_text
    FROM legal_chunks lc
    JOIN dup_titles dt ON lc.title = dt.title
    WHERE lc.doc_type = 'lstrm'
)
SELECT title,
       COUNT(*) AS total_entries,
       COUNT(DISTINCT definition_text) AS distinct_definitions,
       ROUND(100.0 * COUNT(DISTINCT definition_text) / COUNT(*), 1) AS diversity_pct
FROM def_extract
GROUP BY title
HAVING COUNT(*) >= 5
ORDER BY total_entries DESC
LIMIT 30;

-- "법령용어: 대통령령으로 정하는 사항"	552	546	98.9
-- "법령용어: 대통령령으로 정하는 경우"	281	281	100.0
-- "법령용어: 대통령령으로 정하는 자"	223	222	99.6
-- "법령용어: 대통령령으로 정하는 기간"	220	191	86.8
-- "법령용어: 대통령령으로 정하는 사업"	211	211	100.0
-- "법령용어: 대통령령으로 정하는 기관"	165	165	100.0
-- "법령용어: 대통령령으로 정하는 기준"	149	147	98.7
-- "법령용어: 대통령령으로 정하는 사유"	137	137	100.0
-- "법령용어: 대통령령으로 정하는 것"	135	134	99.3
-- "법령용어: 대통령령으로 정하는 사람"	128	126	98.4
-- "법령용어: 대통령령으로 정하는 경미한 사항을 변경하는 경우"	121	118	97.5
-- "법령용어: 대통령령으로 정하는 시설"	119	117	98.3
-- "법령용어: 대통령령으로 정하는 금액"	116	107	92.2
-- "법령용어: 대통령령으로 정하는 경미한 사항"	113	112	99.1
-- "법령용어: 대통령령으로 정하는 업무"	110	110	100.0
-- "법령용어: 성희롱"	102	64	62.7
-- "법령용어: 성폭력"	98	32	32.7
-- "법령용어: 대통령령으로 정하는 비율"	97	86	88.7
-- "법령용어: 대통령령으로 정하는 공공기관"	91	88	96.7
-- "법령용어: 대통령령으로 정하는 행위"	89	89	100.0
-- "법령용어: 2차 피해"	82	69	84.1
-- "법령용어: 대통령령으로 정하는 요건"	81	81	100.0
-- "법령용어: 공공기관"	79	65	82.3
-- "법령용어: 적극행정"	78	57	73.1
-- "법령용어: 대통령령으로 정하는 중요한 사항"	76	76	100.0
-- "법령용어: 관리기관"	75	73	97.3
-- "법령용어: 스토킹"	74	24	32.4
-- "법령용어: 불이익조치"	71	9	12.7
-- "법령용어: 대통령령으로 정하는 지역"	68	68	100.0
-- "법령용어: 직무관련자"	64	64	100.0

-- [2] 전체 규모: 동명이의 청크 중 "실제로는 같은 정의의 반복"으로 추정되는 비율
--     (diversity_pct가 낮을수록 = 같은 정의가 많이 반복될수록 = 압축 여지 큼)
WITH dup_titles AS (
    SELECT title
    FROM legal_chunks
    WHERE doc_type = 'lstrm'
    GROUP BY title
    HAVING COUNT(*) > 1
),
def_extract AS (
    SELECT lc.title, lc.chunk_id,
           regexp_replace(lc.content, '^.*?정의: (.*?)(\n출처:.*)?$', '\1', 's') AS definition_text
    FROM legal_chunks lc
    JOIN dup_titles dt ON lc.title = dt.title
    WHERE lc.doc_type = 'lstrm'
)
SELECT
    COUNT(*) AS total_dup_chunks,
    COUNT(DISTINCT (title, definition_text)) AS distinct_title_definition_pairs,
    COUNT(*) - COUNT(DISTINCT (title, definition_text)) AS redundant_chunks_estimate
FROM def_extract;

-- 49540	42141	7399