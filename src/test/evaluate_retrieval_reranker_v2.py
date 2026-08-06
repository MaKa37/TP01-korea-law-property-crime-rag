"""
evaluate_retrieval_reranker_v2.py
==================================
기존 evaluate_retrieval_reranker.py 를 아래 관점으로 고도화한 버전입니다.

[변경 요약]
  1. 검색 정확도  : Reranker 패시지에 doc_type 명시, RRF k 값 조정 가능, 재정렬 최소 신뢰도 필터
  2. 고급 기법     : HyDE(가상 답변 임베딩) 옵션 (USE_HYDE=true 로 on/off)
  3. 성능          : 임베딩 로컬 캐싱, ThreadPoolExecutor 기반 병렬 평가
  4. 평가 지표     : Hit Rate/MRR 외 Precision@k, Recall@k, nDCG@k 추가
  5. 진단성        : MISS 시 "정답 출처 vs 검색된 출처"(법령/문서 단위) 자동 비교 출력
  6. 회귀 추적     : 매 실행 결과를 JSON(+CSV)으로 저장 -> 코드/모델 변경 전후 비교 가능

기존 함수 시그니처는 대부분 하위호환(신규 파라미터는 기본값 존재)되도록 유지했습니다.
"""

import argparse
import concurrent.futures
import csv
import hashlib
import json
import logging
import math
import os
import statistics
import threading
import time
from datetime import datetime
from pathlib import Path
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

# NVIDIA Reranker 설정
RERANK_URL: str = os.getenv("RERANK_URL", "https://ai.api.nvidia.com/v1/retrieval/nvidia/reranking")
RERANK_MODEL: str = os.getenv("RERANK_MODEL", "nvidia/rerank-qa-mistral-4b")
# [NEW] 재정렬 점수가 이 값 미만인 후보는 최종 결과에서 제외 (None 이면 미적용)
RERANK_MIN_SCORE: Optional[float] = (
    float(os.getenv("RERANK_MIN_SCORE")) if os.getenv("RERANK_MIN_SCORE") else None
)

# [NEW] HyDE(Hypothetical Document Embedding) 설정 - boilerplate(목적/정의 조항 등) 오검색 완화 목적
USE_HYDE: bool = os.getenv("USE_HYDE", "true").lower() == "true"
CHAT_URL: str = os.getenv("CHAT_URL", "https://integrate.api.nvidia.com/v1/chat/completions")
CHAT_MODEL: str = os.getenv("CHAT_MODEL", "meta/llama-3.1-8b-instruct")

# [NEW] RRF 결합 파라미터 (기존 하드코딩 60 -> 환경변수로 조정 가능)
RRF_K: int = int(os.getenv("RRF_K", "60"))

# [NEW] 임베딩 로컬 캐시 (동일 질의 반복 호출 방지 -> 비용/속도 절감, 특히 반복 평가 시)
CACHE_DIR: Path = Path("data") / "cache"
EMBEDDING_CACHE_PATH: Path = CACHE_DIR / "embedding_cache.json"

DEFAULT_EVAL_DATASET_PATH: Path = Path("data") / "dataset" / "eval_dataset.json"
DEFAULT_RESULTS_DIR: Path = Path("data") / "eval_results"

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


# [NEW] requests.Session은 완전한 스레드 세이프가 보장되지 않으므로,
# 병렬 평가 시 스레드마다 독립적인 세션을 사용하도록 thread-local 로 관리합니다.
_thread_local = threading.local()


def get_thread_session() -> requests.Session:
    if not hasattr(_thread_local, "session"):
        _thread_local.session = get_api_session()
    return _thread_local.session


def preprocess_query(query: str) -> str:
    return " ".join(query.split()) if query else ""


# =============================================================================
# [3] 임베딩 캐시 (NEW)
# =============================================================================
_cache_lock = threading.Lock()
_embedding_cache: Dict[str, List[float]] = {}


def load_embedding_cache() -> None:
    global _embedding_cache
    if EMBEDDING_CACHE_PATH.exists():
        try:
            with open(EMBEDDING_CACHE_PATH, "r", encoding="utf-8") as f:
                _embedding_cache = json.load(f)
            logger.info(f"💾 임베딩 캐시 로드: {len(_embedding_cache)}건")
        except Exception as e:
            logger.warning(f"⚠️ 임베딩 캐시 로드 실패, 빈 캐시로 시작합니다: {e}")
            _embedding_cache = {}


def save_embedding_cache() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = EMBEDDING_CACHE_PATH.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(_embedding_cache, f, ensure_ascii=False)
    tmp_path.replace(EMBEDDING_CACHE_PATH)


def _cache_key(text: str) -> str:
    return hashlib.md5(f"{EMBEDDING_MODEL}:{text}".encode("utf-8")).hexdigest()


# =============================================================================
# [4] 핵심 연산 모듈 (Core Operations)
# =============================================================================
def generate_hyde_passage(query_text: str, session: Optional[requests.Session] = None) -> str:
    """[NEW] HyDE: 질의에 대한 '그럴듯한 가상 답변'을 LLM으로 생성해, 그 답변을 임베딩합니다.
    법률 조문의 목적/정의 조항처럼 boilerplate가 많아 질의-조문 간 어휘 격차가 큰 경우,
    질의를 답변 형태로 변환하면 벡터 검색 정확도가 개선되는 경우가 많습니다.
    실패 시 원본 질의로 자동 폴백합니다.
    """
    headers = {
        "Authorization": f"Bearer {NVIDIA_NIM_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {
        "model": CHAT_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "당신은 한국 법률 전문가입니다. 사용자의 질문에 대해 실제 법조문/판례/유권해석에 "
                    "있을 법한 간결한 답변을 2~3문장으로 작성하세요. 정확한 근거를 모르더라도 "
                    "해당 분야에서 사용될 법한 용어와 문체로 작성하면 됩니다."
                ),
            },
            {"role": "user", "content": query_text},
        ],
        "max_tokens": 200,
        "temperature": 0.3,
    }
    try:
        http_client = session or requests
        response = http_client.post(CHAT_URL, headers=headers, json=payload, timeout=15)
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.warning(f"⚠️ HyDE 생성 실패, 원본 쿼리로 대체합니다: {e}")
        return query_text


def fetch_query_embedding(
    query_text: str,
    session: Optional[requests.Session] = None,
    use_cache: bool = True,
) -> List[float]:
    clean_query = preprocess_query(query_text)

    if use_cache:
        key = _cache_key(clean_query)
        with _cache_lock:
            cached = _embedding_cache.get(key)
        if cached is not None:
            return cached

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
    embedding = data["data"][0]["embedding"]

    if use_cache:
        key = _cache_key(clean_query)
        with _cache_lock:
            _embedding_cache[key] = embedding

    return embedding


def rerank_candidates(
    query_text: str,
    candidates: List[Dict[str, Any]],
    session: Optional[requests.Session] = None,
    return_scores: bool = False,
    min_score: Optional[float] = RERANK_MIN_SCORE,
) -> Union[List[str], List[Dict[str, Any]]]:
    """NVIDIA NIM Reranker API를 호출하여 1차 검색된 후보군을 재정렬합니다.

    [NEW] return_scores=True 이면 [{"chunk_id":..., "score":...}, ...] 형태로 반환하고,
    min_score 가 지정되면 해당 점수 미만 후보는 결과에서 제거합니다(저신뢰 결과 억제).
    """
    if not candidates:
        return []

    headers = {
        "Authorization": f"Bearer {NVIDIA_NIM_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    # [NEW] doc_type을 패시지 앞에 명시해 재정렬 시 문맥(법령/판례/유권해석 등) 구분을 돕습니다.
    # (근본적인 해결은 인덱싱 단계에서 title에 정식 법령명을 포함시키는 것이며, 이는 별도 ingestion
    #  파이프라인의 몫입니다. 여기서는 조회 시점에 가능한 신호만 추가로 보강합니다.)
    passages = [
        {"text": f"[{c.get('doc_type', '')}] {c['title']} {c['content']}"} for c in candidates
    ]

    payload = {
        "model": RERANK_MODEL,
        "query": {"text": query_text},
        "passages": passages,
    }

    try:
        http_client = session or requests
        response = http_client.post(RERANK_URL, headers=headers, json=payload, timeout=20)

        if response.status_code != 200:
            logger.error(f"❌ Reranker API 에러 응답: {response.text}")

        response.raise_for_status()
        data = response.json()
        rankings = data.get("rankings", [])

        results = []
        for rank in rankings:
            score = rank.get("logit")
            if min_score is not None and score is not None and score < min_score:
                continue
            results.append({"chunk_id": candidates[rank["index"]]["chunk_id"], "score": score})

        if return_scores:
            return results
        return [r["chunk_id"] for r in results]

    except Exception as e:
        logger.warning(f"⚠️ Reranker 호출 실패. 1차 하이브리드 검색 순위로 대체합니다. 에러: {e}")
        if return_scores:
            return [{"chunk_id": c["chunk_id"], "score": None} for c in candidates]
        return [c["chunk_id"] for c in candidates]


def execute_advanced_search(
    pool: ThreadedConnectionPool,
    query_text: str,
    target_doc_types: Optional[List[str]] = None,
    candidate_k: int = 30,
    top_k: int = 5,
    session: Optional[requests.Session] = None,
    use_cache: bool = True,
    use_hyde: bool = USE_HYDE,
    return_scores: bool = False,
) -> Union[List[str], List[Dict[str, Any]]]:

    clean_query = preprocess_query(query_text)

    # 1. 메타데이터 필터 생성
    doc_type_filter = ""
    if target_doc_types:
        mapped_doc_types = list(dict.fromkeys([dt for dt in target_doc_types if dt in VALID_DOC_TYPES]))
        if mapped_doc_types:
            types_str = ", ".join(f"'{dt}'" for dt in mapped_doc_types)
            doc_type_filter = f"AND doc_type IN ({types_str})"

    # 2. 임베딩 호출 (텍스트 검색은 항상 원본 질의를 사용, 벡터 검색만 HyDE 대상으로 삼음)
    embedding_input = generate_hyde_passage(clean_query, session=session) if use_hyde else clean_query
    query_vector = fetch_query_embedding(embedding_input, session=session, use_cache=use_cache)
    vector_str = str(query_vector)

    # 3. 1차 하이브리드 검색 (내용 텍스트 + doc_type까지 조인하여 추출)
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
                   (COALESCE(1.0 / ({RRF_K} + v.rank), 0.0) + COALESCE(1.0 / ({RRF_K} + t.rank), 0.0)) AS rrf_score
            FROM vector_search v
            FULL OUTER JOIN text_search t ON v.chunk_id = t.chunk_id
            ORDER BY rrf_score DESC
            LIMIT %s
        )
        -- Reranking을 위해 원본 텍스트 + doc_type 조인
        SELECT c.chunk_id, lc.title, lc.content, lc.doc_type
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
                candidate_k,
            ))
            candidates = cur.fetchall()
    finally:
        conn.rollback()
        pool.putconn(conn)

    # 4. 2차 Reranking 적용
    reranked = rerank_candidates(clean_query, candidates, session=session, return_scores=True)

    # 5. 최종 결과 반환
    top_reranked = reranked[:top_k]
    if return_scores:
        return top_reranked
    return [r["chunk_id"] for r in top_reranked]


# =============================================================================
# [5] 평가 지표 (NEW: Hit Rate/MRR 외 Precision/Recall/nDCG 추가)
# =============================================================================
def hit_at_k(retrieved: List[str], relevant: set) -> bool:
    return any(cid in relevant for cid in retrieved)


def reciprocal_rank(retrieved: List[str], relevant: set) -> float:
    for rank, cid in enumerate(retrieved, start=1):
        if cid in relevant:
            return 1.0 / rank
    return 0.0


def precision_at_k(retrieved: List[str], relevant: set, k: int) -> float:
    topk = retrieved[:k]
    if not topk:
        return 0.0
    return sum(1 for cid in topk if cid in relevant) / len(topk)


def recall_at_k(retrieved: List[str], relevant: set, k: int) -> float:
    if not relevant:
        return 0.0
    topk = retrieved[:k]
    return sum(1 for cid in topk if cid in relevant) / len(relevant)


def ndcg_at_k(retrieved: List[str], relevant: set, k: int) -> float:
    dcg = sum(
        (1.0 if cid in relevant else 0.0) / math.log2(i + 1)
        for i, cid in enumerate(retrieved[:k], start=1)
    )
    ideal_hits = min(len(relevant), k)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_hits + 1))
    return dcg / idcg if idcg > 0 else 0.0


def extract_source_key(chunk_id: str) -> str:
    """[NEW] chunk_id에서 '문서 단위' 키를 추출합니다. (예: law_010719_art_0004001 -> law_010719)
    MISS 진단 시 '엉뚱한 법령/판례를 끌어왔는지'를 빠르게 파악하는 데 사용합니다.
    """
    parts = chunk_id.split("_")
    return "_".join(parts[:2]) if len(parts) >= 2 else chunk_id


# =============================================================================
# [6] 정량적 평가 파이프라인 (Evaluation Pipeline)
# =============================================================================
def _evaluate_single_item(
    pool: ThreadedConnectionPool,
    item: Dict[str, Any],
    idx: int,
    total: int,
    top_k: int,
    use_cache: bool,
    use_hyde: bool,
) -> Dict[str, Any]:
    qid = item.get("id", idx)
    query: str = item["query"]
    target_doc_types: Optional[List[str]] = item.get("target_doc_types")
    ground_truth: set = set(item["relevant_chunk_ids"])

    session = get_thread_session()
    record: Dict[str, Any] = {"idx": idx, "qid": qid, "query": query, "error": None}

    try:
        start_time = time.time()
        retrieved_ids = execute_advanced_search(
            pool,
            query,
            target_doc_types=target_doc_types,
            candidate_k=30,
            top_k=top_k,
            session=session,
            use_cache=use_cache,
            use_hyde=use_hyde,
        )
        latency_ms = (time.time() - start_time) * 1000

        record.update({
            "retrieved_ids": retrieved_ids,
            "ground_truth_ids": sorted(ground_truth),
            "latency_ms": latency_ms,
            "hit": hit_at_k(retrieved_ids, ground_truth),
            "rr": reciprocal_rank(retrieved_ids, ground_truth),
            "precision": precision_at_k(retrieved_ids, ground_truth, top_k),
            "recall": recall_at_k(retrieved_ids, ground_truth, top_k),
            "ndcg": ndcg_at_k(retrieved_ids, ground_truth, top_k),
            # 진단용: 정답/검색결과의 '문서 단위' 소스 비교 (Q1 케이스처럼 엉뚱한 법령을 끌어온 경우 즉시 확인 가능)
            "expected_sources": sorted({extract_source_key(c) for c in ground_truth}),
            "retrieved_sources": [extract_source_key(c) for c in retrieved_ids],
        })
    except Exception as e:
        record.update({
            "error": str(e),
            "retrieved_ids": [],
            "ground_truth_ids": sorted(ground_truth),
            "latency_ms": None,
            "hit": False,
            "rr": 0.0,
            "precision": 0.0,
            "recall": 0.0,
            "ndcg": 0.0,
            "expected_sources": sorted({extract_source_key(c) for c in ground_truth}),
            "retrieved_sources": [],
        })

    return record


def _print_record(record: Dict[str, Any], total: int, top_k: int) -> None:
    idx, qid = record["idx"], record["qid"]
    if record["error"]:
        print(f"[{idx:02d}/{total:02d}] Q{qid}. ⚠️ ERROR 발생: {record['error']}")
        return

    status_mark = "✅ HIT " if record["hit"] else "❌ MISS"
    print(
        f"[{idx:02d}/{total:02d}] Q{qid}. {status_mark} | MRR: {record['rr']:.2f} | "
        f"nDCG: {record['ndcg']:.2f} | Latency: {record['latency_ms']:.1f}ms | 질의: {record['query']}"
    )
    print(f"    ├─ 검색된 Top-{top_k} IDs : {record['retrieved_ids']}")
    print(f"    └─ 정답(Ground Truth) IDs : {record['ground_truth_ids']}")
    if not record["hit"]:
        # [NEW] MISS일 때 '어느 문서를 잘못 끌어왔는지' 바로 보여줌
        print(f"       ↳ 기대 출처: {record['expected_sources']} / 검색된 출처: {record['retrieved_sources']}")


def run_evaluation(
    eval_filepath: Union[str, Path] = DEFAULT_EVAL_DATASET_PATH,
    top_k: int = 5,
    workers: int = 1,
    use_cache: bool = True,
    use_hyde: bool = USE_HYDE,
    results_dir: Union[str, Path] = DEFAULT_RESULTS_DIR,
    save_results: bool = True,
) -> Dict[str, Any]:

    dataset_path = Path(eval_filepath).resolve()
    with open(dataset_path, "r", encoding="utf-8") as f:
        dataset: List[Dict[str, Any]] = json.load(f)

    if use_cache:
        load_embedding_cache()

    pool = ThreadedConnectionPool(minconn=1, maxconn=max(5, workers + 1), **DB_CONFIG)

    print("=" * 75)
    print("🚀 [Retrieval Quality Evaluation - Advanced Two-Stage Phase]")
    print(f" ├─ Target Model    : {EMBEDDING_MODEL}")
    print(f" ├─ Reranker Model  : {RERANK_MODEL}")
    print(f" ├─ HyDE            : {'ON' if use_hyde else 'off'}")
    print(f" ├─ Workers         : {workers}")
    print(f" ├─ Total Test Size : {len(dataset)} Queries")
    print(f" └─ Metric Target   : Hit Rate@{top_k}, MRR@{top_k}, nDCG@{top_k}, Precision@{top_k}, Recall@{top_k}")
    print("=" * 75 + "\n")

    records: List[Dict[str, Any]] = []
    total = len(dataset)

    try:
        if workers <= 1:
            for idx, item in enumerate(dataset, start=1):
                record = _evaluate_single_item(pool, item, idx, total, top_k, use_cache, use_hyde)
                records.append(record)
                _print_record(record, total, top_k)
        else:
            # [NEW] 병렬 평가: 완료 순서가 뒤섞이므로 idx 기준으로 재정렬 후 출력합니다.
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(_evaluate_single_item, pool, item, idx, total, top_k, use_cache, use_hyde): idx
                    for idx, item in enumerate(dataset, start=1)
                }
                pending_records: Dict[int, Dict[str, Any]] = {}
                for future in concurrent.futures.as_completed(futures):
                    record = future.result()
                    pending_records[record["idx"]] = record
                for idx in sorted(pending_records):
                    record = pending_records[idx]
                    records.append(record)
                    _print_record(record, total, top_k)

        hits = sum(1 for r in records if r["hit"])
        hit_rate = (hits / total) * 100 if total > 0 else 0.0
        mrr = statistics.mean([r["rr"] for r in records]) if records else 0.0
        mean_precision = statistics.mean([r["precision"] for r in records]) if records else 0.0
        mean_recall = statistics.mean([r["recall"] for r in records]) if records else 0.0
        mean_ndcg = statistics.mean([r["ndcg"] for r in records]) if records else 0.0
        latencies = [r["latency_ms"] for r in records if r["latency_ms"] is not None]
        p50 = statistics.median(latencies) if latencies else 0.0
        p95 = statistics.quantiles(latencies, n=20)[18] if len(latencies) >= 20 else (max(latencies) if latencies else 0.0)

        print("\n" + "=" * 75)
        print("📊 [Final Advanced Evaluation Summary]")
        print(f" ├─ Total Processed : {total} Queries")
        print(f" ├─ Hit Rate@{top_k}      : {hit_rate:.2f}% ({hits}/{total})")
        print(f" ├─ MRR@{top_k}           : {mrr:.4f}")
        print(f" ├─ nDCG@{top_k}          : {mean_ndcg:.4f}")
        print(f" ├─ Precision@{top_k}     : {mean_precision:.4f}")
        print(f" ├─ Recall@{top_k}        : {mean_recall:.4f}")
        print(f" └─ Latency p50/p95 : {p50:.1f}ms / {p95:.1f}ms")
        print("=" * 75)

        # [NEW] doc_type(문서 유형)별 breakdown - 정답의 첫 chunk_id 접두사로 근사 분류
        breakdown: Dict[str, List[Dict[str, Any]]] = {}
        for r in records:
            doc_type = r["expected_sources"][0].split("_")[0] if r["expected_sources"] else "unknown"
            breakdown.setdefault(doc_type, []).append(r)
        if len(breakdown) > 1:
            print("\n📂 [Doc-Type별 Breakdown]")
            for doc_type, recs in sorted(breakdown.items()):
                dt_hit_rate = sum(1 for r in recs if r["hit"]) / len(recs) * 100
                dt_mrr = statistics.mean([r["rr"] for r in recs])
                print(f"  - {doc_type:10s}: N={len(recs):2d} | Hit Rate={dt_hit_rate:6.2f}% | MRR={dt_mrr:.4f}")
            print("=" * 75)

        summary = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "embedding_model": EMBEDDING_MODEL,
            "rerank_model": RERANK_MODEL,
            "use_hyde": use_hyde,
            "top_k": top_k,
            "total_queries": total,
            "hit_rate": hit_rate,
            "mrr": mrr,
            "ndcg": mean_ndcg,
            "precision": mean_precision,
            "recall": mean_recall,
            "latency_p50_ms": p50,
            "latency_p95_ms": p95,
        }

        if save_results:
            _save_results(records, summary, results_dir)

        return {"summary": summary, "records": records}

    finally:
        if use_cache:
            save_embedding_cache()
        pool.closeall()


def _save_results(records: List[Dict[str, Any]], summary: Dict[str, Any], results_dir: Union[str, Path]) -> None:
    """[NEW] 실행 결과를 JSON(전체)+CSV(요약 이력)로 저장해 버전 간 회귀를 추적할 수 있게 합니다."""
    out_dir = Path(results_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    detail_path = out_dir / f"eval_{ts}.json"
    with open(detail_path, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "records": records}, f, ensure_ascii=False, indent=2)

    history_path = out_dir / "history.csv"
    is_new = not history_path.exists()
    with open(history_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(summary.keys()))
        if is_new:
            writer.writeheader()
        writer.writerow(summary)

    print(f"\n💾 상세 결과 저장 : {detail_path}")
    print(f"💾 이력(history) 누적 : {history_path}  (버전/설정 변경 전후 비교에 사용)")


# =============================================================================
# [7] CLI 엔트리포인트
# =============================================================================
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RAG 검색+재정렬 평가 파이프라인 (고도화 버전)")
    parser.add_argument("--dataset", type=str, default=str(DEFAULT_EVAL_DATASET_PATH))
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--workers", type=int, default=1, help="병렬 평가 스레드 수 (기본 1=순차)")
    parser.add_argument("--use-hyde", action="store_true", default=USE_HYDE)
    parser.add_argument("--no-cache", action="store_true", help="임베딩 캐시 비활성화")
    parser.add_argument("--results-dir", type=str, default=str(DEFAULT_RESULTS_DIR))
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run_evaluation(
        eval_filepath=args.dataset,
        top_k=args.top_k,
        workers=args.workers,
        use_cache=not args.no_cache,
        use_hyde=args.use_hyde,
        results_dir=args.results_dir,
    )


r"""
===========================================================================
🚀 [Retrieval Quality Evaluation - Advanced Two-Stage Phase]
 ├─ Target Model    : nvidia/nemotron-3-embed-1b
 ├─ Reranker Model  : nvidia/rerank-qa-mistral-4b
 ├─ HyDE            : off
 ├─ Workers         : 1
 ├─ Total Test Size : 6 Queries
 └─ Metric Target   : Hit Rate@5, MRR@5, nDCG@5, Precision@5, Recall@5
===========================================================================

[01/06] Q1. ❌ MISS | MRR: 0.00 | nDCG: 0.00 | Latency: 5822.6ms | 질의: 10ㆍ27법난 피해자의 명예회복 등에 관한 법률상 피해자의 정의와 심의위원회의 설치 목적
    ├─ 검색된 Top-5 IDs : ['law_010719_art_0004001', 'law_010831_art_0007001', 'law_010831_art_0008001', 'law_010831_art_0004001', 'law_010831_art_0006021']
    └─ 정답(Ground Truth) IDs : ['law_010719_art_0001001', 'law_010719_art_0002001', 'law_010719_art_0003001']
       ↳ 기대 출처: ['law_010719'] / 검색된 출처: ['law_010719', 'law_010831', 'law_010831', 'law_010831', 'law_010831']
[02/06] Q2. ✅ HIT  | MRR: 1.00 | nDCG: 1.00 | Latency: 4745.3ms | 질의: 10ㆍ27법난 부상자에 대한 의료지원금 지급 대상 기준 및 부정한 수령 시 환수 절차
    ├─ 검색된 Top-5 IDs : ['law_010719_art_0005001', 'law_010719_art_0006001', 'law_009634_art_0012001', 'law_012564_art_0016001', 'law_010540_art_0011001']
    └─ 정답(Ground Truth) IDs : ['law_010719_art_0005001', 'law_010719_art_0006001']
[03/06] Q3. ✅ HIT  | MRR: 0.50 | nDCG: 0.69 | Latency: 2212.1ms | 질의: 수입식품안전관리 특별법상 준수사항 위반 시 벌칙이 적용되는 영업자에 법인의 종업원이 포함되는지 여부
    ├─ 검색된 Top-5 IDs : ['prec_622249_head_2', 'prec_622249_head_1', 'prec_622249_body_1', 'prec_622249_body_2', 'prec_617159_head']
    └─ 정답(Ground Truth) IDs : ['prec_622249_body_1', 'prec_622249_head_1']
[04/06] Q4. ✅ HIT  | MRR: 1.00 | nDCG: 1.00 | Latency: 7143.6ms | 질의: 수입식품법 양벌규정에 따라 영업자가 아니면서 해당 업무를 실제로 집행하는 자를 처벌하기 위한 요건
    ├─ 검색된 Top-5 IDs : ['prec_622249_head_2', 'prec_622249_body_2', 'prec_622249_head_1', 'prec_617159_head', 'prec_78726_body_3']
    └─ 정답(Ground Truth) IDs : ['prec_622249_body_2', 'prec_622249_head_2']
[05/06] Q5. ✅ HIT  | MRR: 0.33 | nDCG: 0.23 | Latency: 1449.1ms | 질의: 1959년 이전 퇴직 군인의 퇴직급여금 재직기간 산정 시 현역병 복무연한 공제와 전투근무기간 3배 가산의 순서
    ├─ 검색된 Top-5 IDs : ['expc_313107_reasoning_3', 'expc_313107_reasoning_2', 'expc_313107_question', 'expc_313032_reasoning_3', 'expc_313032_reasoning_2']
    └─ 정답(Ground Truth) IDs : ['expc_313107_conclusion', 'expc_313107_question', 'expc_313107_reasoning_1']
[06/06] Q6. ✅ HIT  | MRR: 0.50 | nDCG: 0.63 | Latency: 1309.5ms | 질의: 항공교통업무기준상 계기비행 기상상태(IMC)의 정의
    ├─ 검색된 Top-5 IDs : ['lstrm_3686259', 'lstrm_3945293', 'lstrm_1517210', 'lstrm_5512443', 'lstrm_13974']
    └─ 정답(Ground Truth) IDs : ['lstrm_3945293']

===========================================================================
📊 [Final Advanced Evaluation Summary]
 ├─ Total Processed : 6 Queries
 ├─ Hit Rate@5      : 83.33% (5/6)
 ├─ MRR@5           : 0.5556
 ├─ nDCG@5          : 0.5932
 ├─ Precision@5     : 0.2667
 ├─ Recall@5        : 0.7222
 └─ Latency p50/p95 : 3478.7ms / 7143.6ms
===========================================================================

📂 [Doc-Type별 Breakdown]
  - expc      : N= 1 | Hit Rate=100.00% | MRR=0.3333
  - law       : N= 2 | Hit Rate= 50.00% | MRR=0.5000
  - lstrm     : N= 1 | Hit Rate=100.00% | MRR=0.5000
  - prec      : N= 2 | Hit Rate=100.00% | MRR=0.7500
===========================================================================
"""

r"""
[INFO] 💾 임베딩 캐시 로드: 6건
===========================================================================
🚀 [Retrieval Quality Evaluation - Advanced Two-Stage Phase]
 ├─ Target Model    : nvidia/nemotron-3-embed-1b
 ├─ Reranker Model  : nvidia/rerank-qa-mistral-4b
 ├─ HyDE            : ON
 ├─ Workers         : 1
 ├─ Total Test Size : 6 Queries
 └─ Metric Target   : Hit Rate@5, MRR@5, nDCG@5, Precision@5, Recall@5
===========================================================================

[01/06] Q1. ✅ HIT  | MRR: 1.00 | nDCG: 0.77 | Latency: 9592.7ms | 질의: 10ㆍ27법난 피해자의 명예회복 등에 관한 법률상 피해자의 정의와 심의위원회의 설치 목적
    ├─ 검색된 Top-5 IDs : ['law_010719_art_0003001', 'law_010719_art_0002001', 'law_010831_art_0001001', 'law_010831_art_0002001', 'law_011871_art_0002001']
    └─ 정답(Ground Truth) IDs : ['law_010719_art_0001001', 'law_010719_art_0002001', 'law_010719_art_0003001']
[02/06] Q2. ❌ MISS | MRR: 0.00 | nDCG: 0.00 | Latency: 10341.5ms | 질의: 10ㆍ27법난 부상자에 대한 의료지원금 지급 대상 기준 및 부정한 수령 시 환수 절차
    ├─ 검색된 Top-5 IDs : []
    └─ 정답(Ground Truth) IDs : ['law_010719_art_0005001', 'law_010719_art_0006001']
       ↳ 기대 출처: ['law_010719'] / 검색된 출처: []
[03/06] Q3. ✅ HIT  | MRR: 0.50 | nDCG: 0.69 | Latency: 6744.9ms | 질의: 수입식품안전관리 특별법상 준수사항 위반 시 벌칙이 적용되는 영업자에 법인의 종업원이 포함되는지 여부
    ├─ 검색된 Top-5 IDs : ['prec_622249_head_2', 'prec_622249_head_1', 'prec_622249_body_1', 'prec_622249_body_2', 'prec_617159_head']
    └─ 정답(Ground Truth) IDs : ['prec_622249_body_1', 'prec_622249_head_1']
[04/06] Q4. ✅ HIT  | MRR: 1.00 | nDCG: 1.00 | Latency: 12994.8ms | 질의: 수입식품법 양벌규정에 따라 영업자가 아니면서 해당 업무를 실제로 집행하는 자를 처벌하기 위한 요건
    ├─ 검색된 Top-5 IDs : ['prec_622249_head_2', 'prec_622249_body_2', 'prec_622249_head_1', 'prec_617159_head', 'prec_78726_body_3']
    └─ 정답(Ground Truth) IDs : ['prec_622249_body_2', 'prec_622249_head_2']
[05/06] Q5. ✅ HIT  | MRR: 0.33 | nDCG: 0.23 | Latency: 7109.2ms | 질의: 1959년 이전 퇴직 군인의 퇴직급여금 재직기간 산정 시 현역병 복무연한 공제와 전투근무기간 3배 가산의 순서
    ├─ 검색된 Top-5 IDs : ['expc_313107_reasoning_3', 'expc_313107_reasoning_2', 'expc_313107_question', 'expc_313032_reasoning_3', 'expc_313032_reasoning_2']
    └─ 정답(Ground Truth) IDs : ['expc_313107_conclusion', 'expc_313107_question', 'expc_313107_reasoning_1']
[06/06] Q6. ✅ HIT  | MRR: 0.50 | nDCG: 0.63 | Latency: 4333.7ms | 질의: 항공교통업무기준상 계기비행 기상상태(IMC)의 정의
    ├─ 검색된 Top-5 IDs : ['lstrm_3686259', 'lstrm_3945293', 'lstrm_1517210', 'lstrm_5512443', 'lstrm_1517246']
    └─ 정답(Ground Truth) IDs : ['lstrm_3945293']

===========================================================================
📊 [Final Advanced Evaluation Summary]
 ├─ Total Processed : 6 Queries
 ├─ Hit Rate@5      : 83.33% (5/6)
 ├─ MRR@5           : 0.5556
 ├─ nDCG@5          : 0.5541
 ├─ Precision@5     : 0.2667
 ├─ Recall@5        : 0.6667
 └─ Latency p50/p95 : 8350.9ms / 12994.8ms
===========================================================================

📂 [Doc-Type별 Breakdown]
  - expc      : N= 1 | Hit Rate=100.00% | MRR=0.3333
  - law       : N= 2 | Hit Rate= 50.00% | MRR=0.5000
  - lstrm     : N= 1 | Hit Rate=100.00% | MRR=0.5000
  - prec      : N= 2 | Hit Rate=100.00% | MRR=0.7500
===========================================================================
"""