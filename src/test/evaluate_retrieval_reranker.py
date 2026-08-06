import json
import logging
import os
from pathlib import Path
import statistics
import time
from typing import Any, Dict, List, Optional, Tuple, Union

from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool
import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

# =============================================================================
# [1] 환경변수 로드 및 전역 설정 (Configuration)
# =============================================================================
load_dotenv()

DB_CONFIG: Dict[str, Union[str, int]] = {
    "host": os.getenv("DB_HOST", os.getenv("POSTGRES_HOST", "127.0.0.1")),
    "port": int(os.getenv("DB_PORT", os.getenv("POSTGRES_PORT", "5432"))),
    "dbname": os.getenv("DB_NAME", os.getenv("POSTGRES_DB", "postgres")),
    "user": os.getenv("DB_USER", os.getenv("POSTGRES_USER", "postgres")),
    "password": os.getenv("DB_PASSWORD", os.getenv("POSTGRES_PASSWORD", "postgres")),
}

# NVIDIA 임베딩 설정
NVIDIA_NIM_API_KEY: str = os.getenv("NVIDIA_NIM_API_KEY", "")
EMBEDDING_URL: str = os.getenv("EMBEDDING_URL", "https://integrate.api.nvidia.com/v1/embeddings")
EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "nvidia/nv-embedqa-e5-v5")
EXPECTED_EMBEDDING_DIM: int = 2048

# NVIDIA Reranker 설정 추가
RERANK_URL: str = os.getenv("RERANK_URL", "https://ai.api.nvidia.com/v1/retrieval/nvidia/reranking")
RERANK_MODEL: str = os.getenv("RERANK_MODEL", "nvidia/rerank-qa-mistral-4b")

DEFAULT_EVAL_DATASET_PATH: Path = Path("data") / "dataset" / "eval_dataset.json"

logger = logging.getLogger("RetrievalEvaluator")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

VALID_DOC_TYPES: set = {"law", "addendum", "prec", "expc", "lstrm"}


# =============================================================================
# [2] 네트워크 / 세션 헬퍼
# =============================================================================
def get_api_session() -> requests.Session:
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

def preprocess_query(query: str) -> str:
    return " ".join(query.split()) if query else ""


# =============================================================================
# [3] 핵심 연산 모듈 (Core Operations)
# =============================================================================
def fetch_query_embedding(query_text: str, session: Optional[requests.Session] = None) -> List[float]:
    clean_query = preprocess_query(query_text)
    headers = {
        "Authorization": f"Bearer {NVIDIA_NIM_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {
        "input": [clean_query],
        "model": EMBEDDING_MODEL,
        "input_type": "query",
        "encoding_format": "float",
    }
    http_client = session or requests
    response = http_client.post(EMBEDDING_URL, headers=headers, json=payload, timeout=10)
    response.raise_for_status()
    data = response.json()
    return data["data"][0]["embedding"]

def rerank_candidates(
    query_text: str, 
    candidates: List[Dict[str, Any]], 
    session: Optional[requests.Session] = None
) -> List[str]:
    """NVIDIA NIM Reranker API를 호출하여 1차 검색된 후보군을 재정렬합니다."""
    if not candidates:
        return []
        
    headers = {
        "Authorization": f"Bearer {NVIDIA_NIM_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    
    # NVIDIA Reranker API 규격에 맞춘 passages 리스트 구성
    passages = [{"text": f"{c['title']} {c['content']}"} for c in candidates]
    
    # [핵심 수정] query를 단순 문자열이 아닌 {"text": ...} 형태로 감싸야 합니다.
    payload = {
        "model": RERANK_MODEL,
        "query": {"text": query_text},
        "passages": passages
    }

    try:
        http_client = session or requests
        response = http_client.post(RERANK_URL, headers=headers, json=payload, timeout=20)
        
        # 만약 또 다른 에러가 발생할 경우, API가 뱉는 상세 에러 메시지를 확인하기 위한 로그 추가
        if response.status_code != 200:
            logger.error(f"❌ Reranker API 에러 응답: {response.text}")
            
        response.raise_for_status()
        data = response.json()
        
        # API 응답 결과("rankings": [{"index": 0, "logit": 1.2}, ...])
        rankings = data.get("rankings", [])
        
        # Reranker가 매긴 점수 순서대로 원본 청크 ID 재배치
        reranked_ids = [candidates[rank["index"]]["chunk_id"] for rank in rankings]
        return reranked_ids
        
    except Exception as e:
        logger.warning(f"⚠️ Reranker 호출 실패. 1차 하이브리드 검색 순위로 대체합니다. 에러: {e}")
        return [c["chunk_id"] for c in candidates]


def execute_advanced_search(
    pool: ThreadedConnectionPool, 
    query_text: str, 
    target_doc_types: Optional[List[str]] = None,
    candidate_k: int = 30,  # 1차 검색에서 넉넉하게 뽑을 개수
    top_k: int = 5,         # 최종 결과 반환 개수
    session: Optional[requests.Session] = None
) -> List[str]:
    
    clean_query = preprocess_query(query_text)

    # 1. 메타데이터 필터 생성
    doc_type_filter = ""
    if target_doc_types:
        mapped_doc_types = list(dict.fromkeys([dt for dt in target_doc_types if dt in VALID_DOC_TYPES]))
        if mapped_doc_types:
            types_str = ", ".join(f"'{dt}'" for dt in mapped_doc_types)
            doc_type_filter = f"AND doc_type IN ({types_str})"

    # 2. 임베딩 호출
    query_vector = fetch_query_embedding(clean_query, session=session)
    vector_str = str(query_vector)

    # 3. 1차 하이브리드 검색 (내용 텍스트까지 조인하여 추출)
    sql = f"""
        WITH vector_base AS (
            SELECT chunk_id, embedding <=> %s::halfvec AS dist
            FROM legal_chunks
            WHERE embedding IS NOT NULL
              {doc_type_filter}
            ORDER BY embedding <=> %s::halfvec
            LIMIT 50
        ),
        vector_search AS (
            SELECT chunk_id, ROW_NUMBER() OVER (ORDER BY dist ASC) AS rank
            FROM vector_base
        ),
        text_search AS (
            SELECT chunk_id, ROW_NUMBER() OVER (ORDER BY similarity(title || ' ' || content, %s) DESC) AS rank
            FROM legal_chunks
            WHERE (title || ' ' || content) %% %s
              {doc_type_filter}
            ORDER BY rank
            LIMIT 50
        ),
        combined AS (
            SELECT COALESCE(v.chunk_id, t.chunk_id) AS chunk_id,
                   (COALESCE(1.0 / (60 + v.rank), 0.0) + COALESCE(1.0 / (60 + t.rank), 0.0)) AS rrf_score
            FROM vector_search v
            FULL OUTER JOIN text_search t ON v.chunk_id = t.chunk_id
            ORDER BY rrf_score DESC
            LIMIT %s
        )
        -- Reranking을 위해 원본 텍스트 조인
        SELECT c.chunk_id, lc.title, lc.content
        FROM combined c
        JOIN legal_chunks lc ON c.chunk_id = lc.chunk_id
        ORDER BY c.rrf_score DESC;
    """

    conn = pool.getconn()
    candidates = []
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SET local enable_seqscan = off;")
            cur.execute("SET local pg_trgm.similarity_threshold = 0.35;")
            
            cur.execute(sql, (
                vector_str, vector_str,  
                clean_query, clean_query, 
                candidate_k  # 1차 후보군은 candidate_k 만큼 추출
            ))
            candidates = cur.fetchall()
    finally:
        conn.rollback()
        pool.putconn(conn)

    # 4. 2차 Reranking 적용
    reranked_ids = rerank_candidates(clean_query, candidates, session=session)
    
    # 5. 최종 결과 반환
    return reranked_ids[:top_k]

# =============================================================================
# [4] 정량적 평가 파이프라인 (Evaluation Pipeline)
# =============================================================================
def run_evaluation(
    eval_filepath: Union[str, Path] = DEFAULT_EVAL_DATASET_PATH, 
    top_k: int = 5
) -> Tuple[float, float]:
    
    dataset_path = Path(eval_filepath).resolve()
    with open(dataset_path, "r", encoding="utf-8") as f:
        dataset: List[Dict[str, Any]] = json.load(f)

    pool = ThreadedConnectionPool(minconn=1, maxconn=5, **DB_CONFIG)
    api_session = get_api_session()

    hits: int = 0
    reciprocal_ranks: List[float] = []

    print("=" * 75)
    print("🚀 [Retrieval Quality Evaluation - Advanced Two-Stage Phase]")
    print(f" ├─ Target Model    : {EMBEDDING_MODEL}")
    print(f" ├─ Reranker Model  : {RERANK_MODEL}")
    print(f" ├─ Total Test Size : {len(dataset)} Queries")
    print(f" └─ Metric Target   : Hit Rate@{top_k}, MRR@{top_k}")
    print("=" * 75 + "\n")

    try:
        for idx, item in enumerate(dataset, start=1):
            qid = item.get("id", idx)
            query: str = item["query"]
            target_doc_types: Optional[List[str]] = item.get("target_doc_types")
            ground_truth: set = set(item["relevant_chunk_ids"])

            try:
                start_time = time.time()
                # execute_baseline_search -> execute_advanced_search 로 교체
                retrieved_ids = execute_advanced_search(
                    pool, 
                    query, 
                    target_doc_types=target_doc_types, 
                    candidate_k=30,  # 1차에서 30개를 뽑아 Reranker에게 전달
                    top_k=top_k, 
                    session=api_session
                )
                latency_ms = (time.time() - start_time) * 1000

                is_hit = any(cid in ground_truth for cid in retrieved_ids)
                if is_hit:
                    hits += 1

                rr = 0.0
                for rank, cid in enumerate(retrieved_ids, start=1):
                    if cid in ground_truth:
                        rr = 1.0 / rank
                        break
                reciprocal_ranks.append(rr)

                status_mark = "✅ HIT " if is_hit else "❌ MISS"
                print(f"[{idx:02d}/{len(dataset):02d}] Q{qid}. {status_mark} | MRR: {rr:.2f} | Latency: {latency_ms:.1f}ms | 질의: {query}")
                print(f"    ├─ 검색된 Top-{top_k} IDs : {retrieved_ids}")
                print(f"    └─ 정답(Ground Truth) IDs : {list(ground_truth)}")

            except Exception as e:
                print(f"[{idx:02d}/{len(dataset):02d}] Q{qid}. ⚠️ ERROR 발생: {e}")
                reciprocal_ranks.append(0.0)

        total_queries = len(dataset)
        hit_rate = (hits / total_queries) * 100 if total_queries > 0 else 0.0
        mrr = statistics.mean(reciprocal_ranks) if reciprocal_ranks else 0.0

        print("\n" + "=" * 75)
        print("📊 [Final Advanced Evaluation Summary]")
        print(f" ├─ Total Processed : {total_queries} Queries")
        print(f" ├─ Hit Rate@{top_k}      : {hit_rate:.2f}% ({hits}/{total_queries})")
        print(f" └─ MRR@{top_k}           : {mrr:.4f}")
        print("=" * 75)

        return hit_rate, mrr

    finally:
        pool.closeall()
        api_session.close()

if __name__ == "__main__":
    run_evaluation(eval_filepath=DEFAULT_EVAL_DATASET_PATH, top_k=5)

r""" 평가 결과:
$ python src/test/evaluate_retrieval.py 
===========================================================================
🚀 [Retrieval Quality Evaluation - Advanced Two-Stage Phase]
 ├─ Target Model    : nvidia/nemotron-3-embed-1b
 ├─ Reranker Model  : nvidia/rerank-qa-mistral-4b
 ├─ Total Test Size : 6 Queries
 └─ Metric Target   : Hit Rate@5, MRR@5
===========================================================================

[01/06] Q1. ❌ MISS | MRR: 0.00 | Latency: 6118.9ms | 질의: 10ㆍ27법난 피해자의 명예회복 등에 관한 법률상 피해자의 정의와 심의위원회의 설치 목적
    ├─ 검색된 Top-5 IDs : ['law_010719_art_0004001', 'law_010831_art_0007001', 'law_010831_art_0008001', 'law_010831_art_0004001', 'law_010831_art_0006021']
    └─ 정답(Ground Truth) IDs : ['law_010719_art_0001001', 'law_010719_art_0002001', 'law_010719_art_0003001']
[02/06] Q2. ✅ HIT  | MRR: 1.00 | Latency: 4655.2ms | 질의: 10ㆍ27법난 부상자에 대한 의료지원금 지급 대상 기준 및 부정한 수령 시 환수 절차
    ├─ 검색된 Top-5 IDs : ['law_010719_art_0005001', 'law_010719_art_0006001', 'law_009634_art_0012001', 'law_012564_art_0016001', 'law_010540_art_0011001']
    └─ 정답(Ground Truth) IDs : ['law_010719_art_0005001', 'law_010719_art_0006001']
[03/06] Q3. ✅ HIT  | MRR: 0.50 | Latency: 2446.9ms | 질의: 수입식품안전관리 특별법상 준수사항 위반 시 벌칙이 적용되는 영업자에 법인의 종업원이 포함되는지 여부
    ├─ 검색된 Top-5 IDs : ['prec_622249_head_2', 'prec_622249_head_1', 'prec_622249_body_1', 'prec_622249_body_2', 'prec_617159_head']
    └─ 정답(Ground Truth) IDs : ['prec_622249_head_1', 'prec_622249_body_1']
[04/06] Q4. ✅ HIT  | MRR: 1.00 | Latency: 7615.1ms | 질의: 수입식품법 양벌규정에 따라 영업자가 아니면서 해당 업무를 실제로 집행하는 자를 처벌하기 위한 요건
    ├─ 검색된 Top-5 IDs : ['prec_622249_head_2', 'prec_622249_head_1', 'prec_622249_body_2', 'prec_617159_head', 'prec_78726_body_3']
    └─ 정답(Ground Truth) IDs : ['prec_622249_head_2', 'prec_622249_body_2']
[05/06] Q5. ✅ HIT  | MRR: 0.33 | Latency: 1345.3ms | 질의: 1959년 이전 퇴직 군인의 퇴직급여금 재직기간 산정 시 현역병 복무연한 공제와 전투근무기간 3배 가산의 순서
    ├─ 검색된 Top-5 IDs : ['expc_313107_reasoning_3', 'expc_313107_reasoning_2', 'expc_313107_question', 'expc_313032_reasoning_3', 'expc_313032_reasoning_2']
    └─ 정답(Ground Truth) IDs : ['expc_313107_conclusion', 'expc_313107_question', 'expc_313107_reasoning_1']
[06/06] Q6. ✅ HIT  | MRR: 0.50 | Latency: 1342.2ms | 질의: 항공교통업무기준상 계기비행 기상상태(IMC)의 정의
    ├─ 검색된 Top-5 IDs : ['lstrm_3686259', 'lstrm_3945293', 'lstrm_1517210', 'lstrm_13974', 'lstrm_5512443']
    └─ 정답(Ground Truth) IDs : ['lstrm_3945293']

===========================================================================
📊 [Final Advanced Evaluation Summary]
 ├─ Total Processed : 6 Queries
 ├─ Hit Rate@5      : 83.33% (5/6)
 └─ MRR@5           : 0.5556
===========================================================================
(venv) 
"""