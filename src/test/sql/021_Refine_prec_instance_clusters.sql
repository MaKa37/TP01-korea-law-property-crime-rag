-- ============================================================================
-- refine_prec_instance_clusters.sql
-- check_prec_instance_clusters.sql의 421개 클러스터 중, 죄명만 같은 무관한
-- 사건(예: "손해배상(기)")과 진짜 같은 분쟁(다른 심급)을 본문 유사도로 분리합니다.
-- ============================================================================

-- [1] 클러스터 내 doc_id 쌍의 head_1 본문 유사도 계산
--     (클러스터가 너무 크면(예: 손해배상 443건) 쌍의 수가 폭발적으로 늘어나므로,
--      먼저 클러스터 크기를 3~10 사이로 제한해서 계산량을 통제합니다 - 진짜 심급
--      클러스터는 보통 2~4개 사건이라 이 범위면 충분히 커버됩니다)
WITH normalized AS (
    SELECT
        chunk_id, doc_id, content,
        regexp_replace(title, '^판례 \[[^\]]+\] (.+?) \(요약.*\)$', '\1') AS charge_names
    FROM legal_chunks
    WHERE doc_type = 'prec'
      AND chunk_id LIKE '%\_head\_1'
),
cluster_size AS (
    SELECT charge_names, COUNT(DISTINCT doc_id) AS cnt
    FROM normalized
    GROUP BY charge_names
    HAVING COUNT(DISTINCT doc_id) BETWEEN 2 AND 10
),
pairs AS (
    SELECT
        a.charge_names,
        a.doc_id AS doc_id_a, a.chunk_id AS chunk_id_a,
        b.doc_id AS doc_id_b, b.chunk_id AS chunk_id_b,
        similarity(a.content, b.content) AS content_sim
    FROM normalized a
    JOIN normalized b
      ON a.charge_names = b.charge_names
     AND a.doc_id < b.doc_id
    JOIN cluster_size cs ON cs.charge_names = a.charge_names
)
SELECT charge_names, doc_id_a, chunk_id_a, doc_id_b, chunk_id_b,
       ROUND(content_sim::numeric, 3) AS content_sim
FROM pairs
WHERE content_sim > 0.5   -- 본문까지 유사해야 "진짜 같은 분쟁" 후보
ORDER BY content_sim DESC
LIMIT 50;

-- "배분이의"	"207527"	"prec_207527_head_1"	"225207"	"prec_225207_head_1"	0.675
-- "석유사업법위반"	"216201"	"prec_216201_head_1"	"216515"	"prec_216515_head_1"	0.673
-- "정보통신망이용촉진및정보보호등에관한법률위반(음란물유포)"	"195388"	"prec_195388_head_1"	"197629"	"prec_197629_head_1"	0.670
-- "모욕"	"230877"	"prec_230877_head_1"	"621901"	"prec_621901_head_1"	0.642
-- "건물철거등"	"147840"	"prec_147840_head_1"	"229797"	"prec_229797_head_1"	0.635
-- "모욕"	"621901"	"prec_621901_head_1"	"622251"	"prec_622251_head_1"	0.622
-- "근로자지위확인등"	"241051"	"prec_241051_head_1"	"619453"	"prec_619453_head_1"	0.622
-- "등록취소(상)"	"618487"	"prec_618487_head_1"	"618497"	"prec_618497_head_1"	0.613
-- "국토의계획및이용에관한법률위반"	"215591"	"prec_215591_head_1"	"84070"	"prec_84070_head_1"	0.596
-- "하자보수보증금"	"216089"	"prec_216089_head_1"	"81265"	"prec_81265_head_1"	0.563
-- "예우법적용대상유족등록결정취소처분취소"	"216425"	"prec_216425_head_1"	"69515"	"prec_69515_head_1"	0.552
-- "정보통신망이용촉진및정보보호등에관한법률위반(음란물유포)"	"204349"	"prec_204349_head_1"	"232511"	"prec_232511_head_1"	0.547
-- "상표등록무효"	"114892"	"prec_114892_head_1"	"195182"	"prec_195182_head_1"	0.545
-- "산업안전보건법위반·업무상과실치사·중대재해처벌등에관한법률위반(산업재해치사)"	"238011"	"prec_238011_head_1"	"238861"	"prec_238861_head_1"	0.542
-- "정보통신망이용촉진및정보보호등에관한법률위반(음란물유포)"	"197629"	"prec_197629_head_1"	"232511"	"prec_232511_head_1"	0.540
-- "교원임용절차이행"	"115097"	"prec_115097_head_1"	"196822"	"prec_196822_head_1"	0.530
-- "모욕"	"230877"	"prec_230877_head_1"	"622251"	"prec_622251_head_1"	0.527
-- "거절사정(상)"	"190316"	"prec_190316_head_1"	"191026"	"prec_191026_head_1"	0.515

-- [2] 규모 확인: 본문 유사도까지 높은(=진짜 같은 분쟁으로 추정되는) 쌍이 몇 개인지
WITH normalized AS (
    SELECT
        chunk_id, doc_id, content,
        regexp_replace(title, '^판례 \[[^\]]+\] (.+?) \(요약.*\)$', '\1') AS charge_names
    FROM legal_chunks
    WHERE doc_type = 'prec'
      AND chunk_id LIKE '%\_head\_1'
),
cluster_size AS (
    SELECT charge_names, COUNT(DISTINCT doc_id) AS cnt
    FROM normalized
    GROUP BY charge_names
    HAVING COUNT(DISTINCT doc_id) BETWEEN 2 AND 10
),
pairs AS (
    SELECT a.charge_names, a.doc_id AS doc_id_a, b.doc_id AS doc_id_b,
           similarity(a.content, b.content) AS content_sim
    FROM normalized a
    JOIN normalized b
      ON a.charge_names = b.charge_names
     AND a.doc_id < b.doc_id
    JOIN cluster_size cs ON cs.charge_names = a.charge_names
)
SELECT
    COUNT(*) FILTER (WHERE content_sim > 0.5) AS likely_same_dispute_pairs,
    COUNT(*) AS total_pairs_checked
FROM pairs;

-- 18	2246