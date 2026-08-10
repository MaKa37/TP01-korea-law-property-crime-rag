-- ============================================================================
-- housekeeping_cleanup.sql
-- 오늘 작업 중 생성된 백업 테이블들을 정리합니다.
-- 순서: [1]로 전체 목록/크기 확인 -> 내용 검토 -> [2]의 DROP 문 중 필요한 것만 실행
-- ============================================================================

-- [1] 오늘 생성된 백업 테이블 전체 목록과 크기
SELECT
    tablename,
    pg_size_pretty(pg_total_relation_size(quote_ident(tablename))) AS size
FROM pg_tables
WHERE schemaname = 'public'
  AND (
        tablename LIKE 'legal_chunks_embedding_backup_%'
     OR tablename LIKE 'prec_dedup_backup_%'
     OR tablename LIKE 'expc_dedup_backup_%'
     OR tablename LIKE 'lstrm_dedup_backup_%'
  )
ORDER BY tablename;

-- "expc_dedup_backup_20260808"	"73 MB"
-- "legal_chunks_embedding_backup_20260806_075807"	"6040 MB"
-- "legal_chunks_embedding_backup_20260806_081547"	"104 kB"
-- "legal_chunks_embedding_backup_20260806_081628"	"6040 MB"
-- "lstrm_dedup_backup_20260809"	"16 MB"
-- "prec_dedup_backup_20260808"	"108 MB"


-- [2] 정리 대상별 권장 사항 (검토 후 필요한 것만 실행하세요)

-- (A) legal_chunks_embedding_backup_20260806_075807 // 26. 08. 09 DROP 완료
--     -> 재임베딩 "전" 전체 백업(112만 건). 재임베딩 검증 끝났고, 그 후 prec/expc
--        dedup까지 이미 진행되어 현재 스키마와 이미 어긋나 있어 복원 의미 없음.
--        DROP 권장.
-- DROP TABLE legal_chunks_embedding_backup_20260806_075807;

-- (B) legal_chunks_embedding_backup_20260806_081628 // 26. 08. 09 DROP 완료
--     -> 위와 동일 사유. DROP 권장.
-- DROP TABLE legal_chunks_embedding_backup_20260806_081628;

-- (C) prec_dedup_backup_20260808 (11,327건 - case_no 중복 삭제분)
--     -> 삭제 후 여러 차례 eval 재실행으로 부작용 없음 확인됨. DROP 가능.
--        다만 "정말 완전 동일 중복이었는지" 의심되면 좀 더 보관해도 됩니다.
-- DROP TABLE prec_dedup_backup_20260808;

-- (D) expc_dedup_backup_20260808 (7,849건 - 완전 동일 중복 삭제분) // 26. 08. 09 DROP 완료
--     -> content_hash 완전 일치 확인 후 삭제한 것이라 가장 안전. DROP 가능.
-- DROP TABLE expc_dedup_backup_20260808;

-- (E) lstrm_dedup_backup_20260809 (2,438건 - 정의문 완전 일치 중복 삭제분)
--     -> 가장 최근 작업. eval로 부작용 없음은 확인했으나, 범위가 예상보다 넓었으니
--        (200개+ title) 며칠 더 보관 후 DROP 하는 걸 권장합니다.
-- DROP TABLE lstrm_dedup_backup_20260809;


-- [3] (선택) 전부 한 번에 정리하고 싶다면 -- 신중하게, 검토 후에만 실행
-- DROP TABLE IF EXISTS legal_chunks_embedding_backup_20260806_075807;
-- DROP TABLE IF EXISTS legal_chunks_embedding_backup_20260806_081628;
-- DROP TABLE IF EXISTS prec_dedup_backup_20260808;
-- DROP TABLE IF EXISTS expc_dedup_backup_20260808;
-- DROP TABLE IF EXISTS lstrm_dedup_backup_20260809;