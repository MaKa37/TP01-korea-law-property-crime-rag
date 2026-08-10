"""PGVector 기반 하이브리드/키워드 검색."""
from typing import Any, Dict, List

from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

from core.config import RAGConfig

# prec(판례)/lstrm(법령용어) 문서 중 내용이 빈약하거나 잘못 파싱된 청크를 걸러내는 필터
_NOISE_FILTER = """
    AND NOT (doc_type = 'prec' AND content ~ '\\[판결요지\\]\\s*$' AND length(content) < 150)
    AND NOT (doc_type = 'lstrm' AND title ~ '^법령용어: (대통령령|총리령|부령|[가-힣]+부령|[가-힣]+령)으로 정하는')
    AND NOT (doc_type = 'lstrm' AND content ~ '출처:\\s*$')
"""


def execute_hybrid_search(db_pool: ThreadedConnectionPool, config: RAGConfig, query: str, vector: List[float]) -> List[Dict[str, Any]]:
    """Vector + BM25(trigram) 하이브리드 검색 (RRF 적용)."""
    vector_str = str(vector)

    sql = f"""
    WITH vector_search AS (
        SELECT chunk_id, ROW_NUMBER() OVER (ORDER BY embedding <=> %s::halfvec ASC) AS rank
        FROM legal_chunks
        WHERE embedding IS NOT NULL {_NOISE_FILTER}
        LIMIT 50
    ),
    text_search AS (
        SELECT chunk_id, ROW_NUMBER() OVER (ORDER BY similarity(title || ' ' || content, %s) DESC) AS rank
        FROM legal_chunks
        WHERE (title || ' ' || content) %% %s {_NOISE_FILTER}
        LIMIT 50
    ),
    combined AS (
        SELECT COALESCE(v.chunk_id, t.chunk_id) AS chunk_id,
               (COALESCE(1.0 / ({config.rrf_k} + v.rank), 0.0) +
                COALESCE(1.0 / ({config.rrf_k} + t.rank), 0.0)) AS rrf_score
        FROM vector_search v
        FULL OUTER JOIN text_search t ON v.chunk_id = t.chunk_id
        ORDER BY rrf_score DESC
        LIMIT %s
    )
    SELECT c.chunk_id, lc.title, lc.content, lc.doc_type
    FROM combined c
    JOIN legal_chunks lc ON c.chunk_id = lc.chunk_id
    ORDER BY c.rrf_score DESC;
    """

    conn = db_pool.getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SET local enable_seqscan = off;")
            cur.execute("SET local pg_trgm.similarity_threshold = 0.35;")
            cur.execute(sql, (vector_str, query, query, config.candidate_k))
            return cur.fetchall()
    finally:
        conn.rollback()
        db_pool.putconn(conn)


def execute_keyword_search(db_pool: ThreadedConnectionPool, config: RAGConfig, query: str) -> List[Dict[str, Any]]:
    """임베딩 서버 장애 시 대체되는 키워드(Trigram) 단독 검색."""
    sql = f"""
    WITH text_search AS (
        SELECT chunk_id, ROW_NUMBER() OVER (ORDER BY similarity(title || ' ' || content, %s) DESC) AS rank
        FROM legal_chunks
        WHERE (title || ' ' || content) %% %s {_NOISE_FILTER}
        LIMIT %s
    )
    SELECT t.chunk_id, lc.title, lc.content, lc.doc_type
    FROM text_search t
    JOIN legal_chunks lc ON t.chunk_id = lc.chunk_id
    ORDER BY t.rank ASC;
    """

    conn = db_pool.getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SET local enable_seqscan = off;")
            cur.execute("SET local pg_trgm.similarity_threshold = 0.35;")
            cur.execute(sql, (query, query, config.candidate_k))
            return cur.fetchall()
    finally:
        conn.rollback()
        db_pool.putconn(conn)
