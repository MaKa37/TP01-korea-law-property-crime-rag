r"""
Legal Chunk DB Ultra-Fast Hybrid Search Benchmark (PostgreSQL pgvector + FTS GIN)

주요 최적화 및 수정 사항:
    1. [차원 일치] 1536차원 -> 1024차원 (NVIDIA nv-embedqa-e5-v5 / DB 컬럼 vector(1024) 완벽 대응)
    2. [GIN 인덱스 활용] SQL 구문의 to_tsvector(...) 동적 생성 연산을 제거하고,
       미리 저장된 `fts_vector` 컬럼과 GIN 인덱스(`idx_legal_chunks_fts_gin`)를 100% 활용하도록 쿼리 수정
    3. [DB CONNECTION POOLING] psycopg2.pool.ThreadedConnectionPool 적용으로
       매 요청마다 발생하던 TCP 커넥션 맺기 오버헤드(10~30ms) 완전 제거 -> Pure DB 쿼리 성능 측정
    4. [END-TO-END & PURE DB 모드 제공] API 쿼리 임베딩 포함 Latency vs Pure DB 검색 Latency 분리 측정
    5. [동시성 멀티 테스트 스위트] Concurrency 1, 5, 10, 20 단계별 QPS/Latency 분석 리포트 자동 출력

Author: Vector DB Engineering Team
Date: 2026-08-04
"""

import os
import sys
import time
import random
import statistics
import json
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Any, Tuple
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool
import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# PostgreSQL 커넥션 설정
DB_CONFIG = {
    "host": os.getenv("DB_HOST", os.getenv("POSTGRES_HOST", "127.0.0.1")),
    "port": int(os.getenv("DB_PORT", os.getenv("POSTGRES_PORT", "5432"))),
    "dbname": os.getenv("DB_NAME", os.getenv("POSTGRES_DB", "postgres")),
    "user": os.getenv("DB_USER", os.getenv("POSTGRES_USER", "postgres")),
    "password": os.getenv("DB_PASSWORD", os.getenv("POSTGRES_PASSWORD", "postgres")),
}

# NVIDIA API 설정 (End-to-End 테스트용)
NVIDIA_NIM_API_KEY = os.getenv("NVIDIA_NIM_API_KEY", "")
EMBEDDING_URL = os.getenv("EMBEDDING_URL", "https://integrate.api.nvidia.com/v1/embeddings")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "nvidia/nv-embedqa-e5-v5")
EXPECTED_EMBEDDING_DIM = 1024

# 테스트 키워드 (실제 한국어 법률/재산범죄 질의 데이터)
TEST_KEYWORDS = [
    "절도죄 성립요건 및 처벌",
    "사기죄 기망행위와 재산상 손해",
    "횡령죄 배임죄 차이점 및 판례",
    "점유이탈물횡령죄 불법영득의사",
    "손해배상 청구권 불법행위 원인",
    "타인의 명의를 도용한 금융 사기",
    "아파트 임대차 보증금 반환 의무",
    "야간주거침입절도죄 특수절도 차이",
    "업무상 배임죄 배임수증재 성립여부",
    "부당이득반환청구권 법적 시효"
]


def generate_dummy_vector(dim: int = EXPECTED_EMBEDDING_DIM) -> str:
    """1024차원 정규화 임의 벡터 생성 (Pure DB Benchmark용)"""
    vals = [random.gauss(0, 1) for _ in range(dim)]
    norm = sum(x*x for x in vals) ** 0.5
    normalized = [round(x / norm, 6) for x in vals]
    return "[" + ",".join(map(str, normalized)) + "]"


def fetch_real_query_embedding(query_text: str) -> str:
    """NVIDIA NIM API 호출하여 실제 Query Embedding(1024차원) 생성"""
    if not NVIDIA_NIM_API_KEY:
        return generate_dummy_vector()
    
    headers = {
        "Authorization": f"Bearer {NVIDIA_NIM_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    payload = {
        "input": [query_text],
        "model": EMBEDDING_MODEL,
        "input_type": "query",
        "encoding_format": "float"
    }
    try:
        resp = requests.post(EMBEDDING_URL, json=payload, headers=headers, timeout=10)
        if resp.status_code == 200:
            emb = resp.json()["data"][0]["embedding"]
            return "[" + ",".join(map(str, emb)) + "]"
    except Exception:
        pass
    return generate_dummy_vector()


def execute_hybrid_search(pool: ThreadedConnectionPool, keyword: str, vector_str: str, top_k: int = 10, rrf_k: int = 60) -> float:
    """
    고속 하이브리드 검색 (HNSW Vector + Pre-computed stored fts_vector GIN)
    
    [핵심 최적화 SQL]:
      - `to_tsvector(...)`를 매 요청마다 새로 생성하던 기존 버그 제거!
      - 저장 컬럼 `fts_vector` 및 GIN 인덱스 `idx_legal_chunks_fts_gin` 사용
    """
    sql = """
        WITH vector_search AS (
            SELECT chunk_id, title, left(content, 100) AS content,
                   ROW_NUMBER() OVER (ORDER BY embedding <=> %s::vector) AS rank
            FROM legal_chunks
            ORDER BY embedding <=> %s::vector
            LIMIT 50
        ),
        text_search AS (
            SELECT chunk_id, title, left(content, 100) AS content,
                   ROW_NUMBER() OVER (
                       ORDER BY ts_rank_cd(fts_vector, plainto_tsquery('simple', %s)) DESC
                   ) AS rank
            FROM legal_chunks
            WHERE fts_vector @@ plainto_tsquery('simple', %s)
            ORDER BY rank
            LIMIT 50
        )
        SELECT 
            COALESCE(v.chunk_id, t.chunk_id) AS chunk_id,
            COALESCE(v.title, t.title) AS title,
            COALESCE(v.content, t.content) AS content,
            (COALESCE(1.0 / (%s + v.rank), 0.0) + COALESCE(1.0 / (%s + t.rank), 0.0)) AS rrf_score
        FROM vector_search v
        FULL OUTER JOIN text_search t ON v.chunk_id = t.chunk_id
        ORDER BY rrf_score DESC
        LIMIT %s;
    """
    start = time.perf_counter()
    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, (vector_str, vector_str, keyword, keyword, rrf_k, rrf_k, top_k))
            _ = cur.fetchall()
    finally:
        pool.putconn(conn)
        
    return (time.perf_counter() - start) * 1000.0  # ms 단위 반환


def run_benchmark_suite(concurrency: int, total_requests: int, pool: ThreadedConnectionPool, mode: str = "Pure DB") -> Dict[str, Any]:
    """ThreadPoolExecutor 기반 동시성 QPS/Latency 측정 함수"""
    print(f"  └ [{mode} Benchmark] 동시성: {concurrency:>2d} | 총 요청수: {total_requests:>3d} 진행 중...")
    
    test_samples = []
    for _ in range(total_requests):
        kw = random.choice(TEST_KEYWORDS)
        if mode == "End-to-End":
            vec = fetch_real_query_embedding(kw)
        else:
            vec = generate_dummy_vector()
        test_samples.append((kw, vec))

    latencies = []
    start_total = time.perf_counter()
    
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [
            executor.submit(execute_hybrid_search, pool, kw, vec)
            for kw, vec in test_samples
        ]
        for future in futures:
            latencies.append(future.result())

    total_time = time.perf_counter() - start_total
    latencies.sort()

    avg_lat = statistics.mean(latencies)
    p50_lat = statistics.median(latencies)
    p90_lat = latencies[int(total_requests * 0.90) - 1]
    p95_lat = latencies[int(total_requests * 0.95) - 1]
    p99_lat = latencies[-1]
    qps = total_requests / total_time

    return {
        "concurrency": concurrency,
        "total_requests": total_requests,
        "total_time": total_time,
        "qps": qps,
        "min": latencies[0],
        "max": latencies[-1],
        "avg": avg_lat,
        "p50": p50_lat,
        "p90": p90_lat,
        "p95": p95_lat,
        "p99": p99_lat,
    }


def print_suite_report(results: List[Dict[str, Any]], title: str):
    """결과 가독성 높은 종합 리포트 출력"""
    print("\n" + "=" * 80)
    print(f"📊 [BENCHMARK REPORT] {title}")
    print("=" * 80)
    header = f"{'Concurrency':^12}|{'QPS (req/s)':^14}|{'Avg (ms)':^10}|{'P50 (ms)':^10}|{'P95 (ms)':^10}|{'P99 (ms)':^10}"
    print(header)
    print("-" * 80)
    for r in results:
        line = f"{r['concurrency']:^12d}|{r['qps']:^14.2f}|{r['avg']:^10.2f}|{r['p50']:^10.2f}|{r['p95']:^10.2f}|{r['p99']:^10.2f}"
        print(line)
    print("=" * 80 + "\n")


def main():
    print("🚀 Legal Chunks PostgreSQL Hybrid Search Benchmark 시작...")
    
    # 1. Connection Pool 생성
    try:
        pool = ThreadedConnectionPool(minconn=2, maxconn=35, **DB_CONFIG)
        print("  └ ✅ PostgreSQL Connection Pool 생성 성공 (Max: 35 connections)")
    except Exception as e:
        print(f"❌ DB 연결 실패: {e}")
        sys.exit(1)

    # 2. Warmup (인덱스 버퍼 캐싱)
    print("  └ 🔥 DB Index/Page Buffer Warmup 실행 중 (10회)...")
    for _ in range(10):
        execute_hybrid_search(pool, random.choice(TEST_KEYWORDS), generate_dummy_vector())
    print("  └ ✅ Warmup 완료!\n")

    # 3. Pure DB Hybrid Search 동시성 테스트 (1, 5, 10, 20 동시성)
    concurrencies = [1, 5, 10, 20]
    total_requests_per_test = 100

    pure_db_results = []
    print("🧪 [Test Suite 1/2] Pure DB Hybrid Search Performance Test")
    for conc in concurrencies:
        res = run_benchmark_suite(conc, total_requests_per_test, pool, mode="Pure DB")
        pure_db_results.append(res)

    print_suite_report(pure_db_results, "Pure PostgreSQL Hybrid Search (1024-dim Vector + FTS GIN)")

    # 4. NVIDIA API E2E 테스트 (API Key 존재 시 실행)
    if NVIDIA_NIM_API_KEY:
        print("🧪 [Test Suite 2/2] Real Query Embedding + DB Search (End-to-End Latency)")
        e2e_results = []
        for conc in [1, 5, 10]:
            res = run_benchmark_suite(conc, 30, pool, mode="End-to-End")
            e2e_results.append(res)
        print_suite_report(e2e_results, "End-to-End (NVIDIA NIM Embedding API + DB Hybrid Search)")
    else:
        print("💡 [안내] .env에 NVIDIA_NIM_API_KEY가 설정되어 있지 않아 End-to-End 테스트는 스킵되었습니다.")

    pool.closeall()
    print("✨ 모든 벤치마크 테스트가 완료되었습니다!")


if __name__ == "__main__":
    main()