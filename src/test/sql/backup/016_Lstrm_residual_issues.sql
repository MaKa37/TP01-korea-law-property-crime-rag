-- ============================================================================
-- lstrm_residual_issues.sql
-- Tier 2-3, 2-4: lstrm 잔여 이슈 두 가지 규모 확인
-- ============================================================================

-- [1] "사망...상이/부상" 류 문구가 겹치는 정의 규모
--     (HARD_LSTRM_01/02에서 "피해자" 그룹 내 confusion으로 관찰된 패턴이
--      lstrm 전체에서 얼마나 흔한지 확인. 절대 규모가 커야 별도 대응 가치가 있음)
SELECT COUNT(*) AS chunk_count, COUNT(DISTINCT title) AS unique_terms
FROM legal_chunks
WHERE doc_type = 'lstrm'
  AND content ~ '사망'
  AND (content ~ '상이' OR content ~ '부상');

-- 205	128

-- [2] 위 패턴이 겹치는 서로 다른 용어(title) 중, 실제로 동일 title 그룹 내에서
--     충돌 위험이 있는 것들만 추림 (동명이의이면서 둘 다 사망/상이·부상 포함)
WITH pattern_chunks AS (
    SELECT title, chunk_id
    FROM legal_chunks
    WHERE doc_type = 'lstrm'
      AND content ~ '사망'
      AND (content ~ '상이' OR content ~ '부상')
)
SELECT title, COUNT(*) AS matching_count,
       array_agg(chunk_id ORDER BY chunk_id) AS chunk_ids
FROM pattern_chunks
GROUP BY title
HAVING COUNT(*) > 1
ORDER BY matching_count DESC
LIMIT 30;

-- "법령용어: 산업재해"	25	"{lstrm_4587460,lstrm_4641095,lstrm_4672574,lstrm_4736514,lstrm_4759202,lstrm_4773652,lstrm_4909730,lstrm_4963913,lstrm_5028675,lstrm_5090358,lstrm_5123642,lstrm_5132786,lstrm_5194722,lstrm_5209297,lstrm_5213676,lstrm_5256718,lstrm_5257857,lstrm_5283962,lstrm_5304659,lstrm_5353308,lstrm_5370945,lstrm_5428229,lstrm_5475482,lstrm_5502726,lstrm_5512242}"
-- "법령용어: 중대재해"	20	"{lstrm_4587461,lstrm_4641096,lstrm_4672575,lstrm_4736352,lstrm_4736515,lstrm_4759209,lstrm_4773653,lstrm_5028676,lstrm_5090359,lstrm_5132787,lstrm_5194723,lstrm_5213677,lstrm_5233060,lstrm_5241768,lstrm_5283963,lstrm_5304660,lstrm_5319426,lstrm_5475483,lstrm_5508883,lstrm_5512243}"
-- "법령용어: 연구실사고"	11	"{lstrm_4834014,lstrm_4843853,lstrm_5101998,lstrm_5207116,lstrm_5212159,lstrm_5222273,lstrm_5303489,lstrm_5319423,lstrm_5374564,lstrm_5479268,lstrm_5508870}"
-- "법령용어: 중대산업재해"	9	"{lstrm_4642711,lstrm_5138254,lstrm_5194724,lstrm_5209299,lstrm_5233061,lstrm_5241769,lstrm_5257859,lstrm_5475484,lstrm_5508884}"
-- "법령용어: 중대시민재해"	6	"{lstrm_4642712,lstrm_5138255,lstrm_5194725,lstrm_5209300,lstrm_5257860,lstrm_5475485}"
-- "법령용어: 중대사고"	4	"{lstrm_4123372,lstrm_4640535,lstrm_5041071,lstrm_5195025}"
-- "법령용어: 관련자"	3	"{lstrm_4419924,lstrm_4737057,lstrm_5502948}"
-- "법령용어: 연구실 사고"	3	"{lstrm_5146108,lstrm_5384502,lstrm_5512383}"
-- "법령용어: 대통령령으로 정하는 규모 이상의 피해"	2	"{lstrm_5423118,lstrm_5452533}"
-- "법령용어: 대통령령으로 정하는 규모 이상의 사고"	2	"{lstrm_5423117,lstrm_5452532}"
-- "법령용어: 피해자"	2	"{lstrm_4081921,lstrm_4956119}"
-- "법령용어: 희생자"	2	"{lstrm_4693456,lstrm_5406567}"

-- [3] 출처 없는 영단어 gloss 항목 규모 (lstrm_16849류: "정의: sexual harassment", 출처 빈칸)
SELECT COUNT(*) AS empty_source_count
FROM legal_chunks
WHERE doc_type = 'lstrm'
  AND content ~ '출처:\s*$';

-- 25781

-- [4] 위 항목들이 실제로 전부 "영단어 gloss"인지, 아니면 다른 이유로 출처가 빈 것도
--     섞여 있는지 샘플 확인
SELECT chunk_id, title, content
FROM legal_chunks
WHERE doc_type = 'lstrm'
  AND content ~ '출처:\s*$'
ORDER BY chunk_id
LIMIT 15;

-- "lstrm_12394"	"법령용어: 긴급구속"	"용어: 긴급구속
-- 정의: emergency confinement/arrest/custody/restraint
-- 출처: "
-- "lstrm_12395"	"법령용어: 긴급구호"	"용어: 긴급구호
-- 정의: emergency relief
-- 출처: "
-- "lstrm_12396"	"법령용어: 긴급명령"	"용어: 긴급명령
-- 정의: emergency order
-- 출처: "
-- "lstrm_12397"	"법령용어: 긴급사태"	"용어: 긴급사태
-- 정의: emergency;state/situation of emergency
-- 출처: "
-- "lstrm_12398"	"법령용어: 긴급성"	"용어: 긴급성
-- 정의: urgency
-- 출처: "
-- "lstrm_12400"	"법령용어: 긴급재정경제처분"	"용어: 긴급재정경제처분
-- 정의: financial and economic emergency action
-- 출처: "
-- "lstrm_12403"	"법령용어: 긴급처분"	"용어: 긴급처분
-- 정의: emergency disposition/measure
-- 출처: "
-- "lstrm_12406"	"법령용어: 긴급한"	"용어: 긴급한
-- 정의: urgent;emergency
-- 출처: "
-- "lstrm_12407"	"법령용어: 긴급히"	"용어: 긴급히
-- 정의: urgently;immediately
-- 출처: "
-- "lstrm_12409"	"법령용어: 나포"	"용어: 나포
-- 정의: arrest;seizure;capture
-- 출처: "
-- "lstrm_12410"	"법령용어: 나용선"	"용어: 나용선
-- 정의: bare boat
-- 출처: "
-- "lstrm_12411"	"법령용어: 나용선계약"	"용어: 나용선계약
-- 정의: bareboat charter
-- 출처: "
-- "lstrm_12412"	"법령용어: 나용선자"	"용어: 나용선자
-- 정의: bareboat charterer
-- 출처: "
-- "lstrm_12413"	"법령용어: 낙부통지의무"	"용어: 낙부통지의무
-- 정의: duty to notify acceptance or rejection;duty to dispatch a notice of acceptance orrejection
-- 출처: "
-- "lstrm_12414"	"법령용어: 낙찰"	"용어: 낙찰
-- 정의: successful bidding/bid
-- 출처: "