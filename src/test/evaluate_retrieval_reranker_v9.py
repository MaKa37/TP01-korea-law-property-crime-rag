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
# [1] 환경변수 로드 및 전역 설정
# =============================================================================
load_dotenv()

DB_CONFIG: Dict[str, Union[str, int]] = {
    "host": os.getenv("DB_HOST", os.getenv("POSTGRES_HOST", "127.0.0.1")),
    "port": int(os.getenv("DB_PORT", os.getenv("POSTGRES_PORT", "5432"))),
    "dbname": os.getenv("DB_NAME", os.getenv("POSTGRES_DB", "postgres")),
    "user": os.getenv("DB_USER", os.getenv("POSTGRES_USER", "postgres")),
    "password": os.getenv("DB_PASSWORD", os.getenv("POSTGRES_PASSWORD", "postgres")),
}

# NVIDIA API 설정
NVIDIA_NIM_API_KEY: str = os.getenv("NVIDIA_NIM_API_KEY", "")
EMBEDDING_URL: str = os.getenv("EMBEDDING_URL", "https://integrate.api.nvidia.com/v1/embeddings")
EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "nvidia/nemotron-3-embed-1b")
EXPECTED_EMBEDDING_DIM: int = 2048

RERANK_URL: str = os.getenv("RERANK_URL", "https://ai.api.nvidia.com/v1/retrieval/nvidia/reranking")
RERANK_MODEL: str = os.getenv("RERANK_MODEL", "nvidia/rerank-qa-mistral-4b")
RERANK_MIN_SCORE: Optional[float] = (
    float(os.getenv("RERANK_MIN_SCORE")) if os.getenv("RERANK_MIN_SCORE") else None
)

USE_HYDE: bool = os.getenv("USE_HYDE", "true").lower() == "true"
CHAT_URL: str = os.getenv("CHAT_URL", "https://integrate.api.nvidia.com/v1/chat/completions")
CHAT_MODEL: str = os.getenv("CHAT_MODEL", "meta/llama-3.1-8b-instruct")
HYDE_TEMPERATURE: float = float(os.getenv("HYDE_TEMPERATURE", "0"))

RRF_K: int = int(os.getenv("RRF_K", "60"))
PREC_EMPTY_HEAD_LEN_THRESHOLD: int = int(os.getenv("PREC_EMPTY_HEAD_LEN_THRESHOLD", "150"))
FILTER_LSTRM_BOILERPLATE: bool = os.getenv("FILTER_LSTRM_BOILERPLATE", "true").lower() == "true"
FILTER_LSTRM_EMPTY_SOURCE: bool = os.getenv("FILTER_LSTRM_EMPTY_SOURCE", "true").lower() == "true"

CACHE_DIR: Path = Path("data") / "cache"
EMBEDDING_CACHE_PATH: Path = CACHE_DIR / "embedding_cache.json"

DEFAULT_EVAL_DATASET_PATH: Path = Path("data") / "dataset" / "eval_dataset.json"
DEFAULT_RESULTS_DIR: Path = Path("data") / "eval_results"

logger = logging.getLogger("RetrievalEvaluator")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

VALID_DOC_TYPES: set = {"law", "addendum", "prec", "expc", "lstrm"}


# =============================================================================
# [2] 네트워크 세션 / 캐시 헬퍼
# =============================================================================
def get_api_session() -> requests.Session:
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


_thread_local = threading.local()


def get_thread_session() -> requests.Session:
    if not hasattr(_thread_local, "session"):
        _thread_local.session = get_api_session()
    return _thread_local.session


def preprocess_query(query: str) -> str:
    return " ".join(query.split()) if query else ""


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
            logger.warning(f"⚠️ 임베딩 캐시 로드 실패: {e}")
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
# [3] 핵심 연산 모듈
# =============================================================================
def generate_hyde_passage(query_text: str, session: Optional[requests.Session] = None) -> str:
    """HyDE 생성: 12초 타임아웃으로 빠른 실패(Fail-Fast) 유도"""
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
                "content": "당신은 한국 법률 전문가입니다. 사용자의 질문에 대해 실제 법조문/판례/유권해석에 있을 법한 핵심 답변을 2문장 이내로 간결히 작성하세요.",
            },
            {"role": "user", "content": query_text},
        ],
        "max_tokens": 150,
        "temperature": HYDE_TEMPERATURE,
    }
    try:
        http_client = session or requests
        # 타임아웃을 12초로 줄여 서버 지연 시 빠르게 원본 쿼리로 폴백
        response = http_client.post(CHAT_URL, headers=headers, json=payload, timeout=12)
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.warning(f"⚠️ HyDE 생성 실패/타임아웃, 원본 쿼리로 즉시 폴백: {e}")
        return query_text


def combine_vectors(vec_a: List[float], vec_b: List[float], alpha: float = 0.4) -> List[float]:
    """원본 쿼리 벡터(alpha)와 HyDE 벡터(1-alpha) 선형 결합 및 L2 정규화"""
    fused = [alpha * a + (1.0 - alpha) * b for a, b in zip(vec_a, vec_b)]
    norm = math.sqrt(sum(x * x for x in fused))
    return [x / norm for x in fused] if norm > 0 else fused


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
        "input": [f"query: {clean_query}"],
        "model": EMBEDDING_MODEL,
        "input_type": "query",
        "encoding_format": "float",
    }
    http_client = session or requests
    response = http_client.post(EMBEDDING_URL, headers=headers, json=payload, timeout=15)
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
    if not candidates:
        return []

    headers = {
        "Authorization": f"Bearer {NVIDIA_NIM_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    passages = [
        {"text": f"[{c.get('doc_type', '')}] {c['title']} {c['content']}"} for c in candidates
    ]

    payload = {
        "model": RERANK_MODEL,
        "query": {"text": query_text},
        "passages": passages,
    }

    is_purpose_query = any(k in query_text for k in ["목적", "제정", "이유", "취지"])
    is_def_query = any(k in query_text for k in ["정의", "뜻", "의미", "어떤 사람", "대상"])

    try:
        http_client = session or requests
        response = http_client.post(RERANK_URL, headers=headers, json=payload, timeout=25)

        if response.status_code != 200:
            logger.error(f"❌ Reranker API 에러 응답: {response.text}")

        response.raise_for_status()
        data = response.json()
        rankings = data.get("rankings", [])

        results = []
        for rank in rankings:
            score = rank.get("logit")
            candidate = candidates[rank["index"]]
            chunk_id = candidate["chunk_id"]

            boost = 0.0
            if is_purpose_query and "art_0001001" in chunk_id:
                boost += 0.15
            elif is_def_query and "art_0002001" in chunk_id:
                boost += 0.10

            final_score = (score + boost) if score is not None else None

            if min_score is not None and final_score is not None and final_score < min_score:
                continue
            results.append({"chunk_id": chunk_id, "score": final_score})

        results.sort(key=lambda x: (x["score"] is not None, x["score"]), reverse=True)

        if return_scores:
            return results
        return [r["chunk_id"] for r in results]

    except Exception as e:
        logger.warning(f"⚠️ Reranker 호출 실패. 1차 하이브리드 검색 순위로 대체: {e}")
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

    doc_type_filter = ""
    if target_doc_types:
        mapped_doc_types = list(dict.fromkeys([dt for dt in target_doc_types if dt in VALID_DOC_TYPES]))
        if mapped_doc_types:
            types_str = ", ".join(f"'{dt}'" for dt in mapped_doc_types)
            doc_type_filter = f"AND doc_type IN ({types_str})"

    empty_prec_filter = (
        f"AND NOT (content ~ '\\[판결요지\\]\\s*$' AND length(content) < {PREC_EMPTY_HEAD_LEN_THRESHOLD})"
    )

    lstrm_boilerplate_filter = ""
    if FILTER_LSTRM_BOILERPLATE:
        lstrm_boilerplate_filter = (
            "AND NOT (doc_type = 'lstrm' AND title ~ "
            "'^법령용어: (대통령령|총리령|부령|[가-힣]+부령|[가-힣]+령)으로 정하는')"
        )

    lstrm_empty_source_filter = ""
    if FILTER_LSTRM_EMPTY_SOURCE:
        lstrm_empty_source_filter = (
            "AND NOT (doc_type = 'lstrm' AND content ~ '출처:\\s*$')"
        )

    # 쿼리 벡터 결합 (Weighted Fusion)
    orig_vector = fetch_query_embedding(clean_query, session=session, use_cache=use_cache)
    if use_hyde:
        hyde_passage = generate_hyde_passage(clean_query, session=session)
        if hyde_passage != clean_query:
            hyde_vector = fetch_query_embedding(hyde_passage, session=session, use_cache=use_cache)
            query_vector = combine_vectors(orig_vector, hyde_vector, alpha=0.4)
        else:
            query_vector = orig_vector
    else:
        query_vector = orig_vector

    vector_str = str(query_vector)

    sql = f"""
        WITH vector_base AS (
            SELECT chunk_id, embedding <=> %s::halfvec AS dist
            FROM legal_chunks
            WHERE embedding IS NOT NULL
              {doc_type_filter}
              {empty_prec_filter}
              {lstrm_boilerplate_filter}
              {lstrm_empty_source_filter}
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
              {empty_prec_filter}
              {lstrm_boilerplate_filter}
              {lstrm_empty_source_filter}
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
            cur.execute("SET local pg_trgm.similarity_threshold = 0.30;")

            cur.execute(sql, (
                vector_str, vector_str,
                clean_query, clean_query,
                candidate_k,
            ))
            candidates = cur.fetchall()

            if not candidates:
                cur.execute(f"""
                    SELECT COUNT(*) FROM legal_chunks
                    WHERE embedding IS NOT NULL {doc_type_filter} {empty_prec_filter} {lstrm_boilerplate_filter} {lstrm_empty_source_filter}
                """, ())
                base_count = cur.fetchone()["count"]
                logger.warning(
                    f"⚠️ 후보군 0건 발생 - query='{clean_query[:50]}...' "
                    f"doc_type_filter='{doc_type_filter}' "
                    f"필터 통과 대상 행 수={base_count}"
                )
    finally:
        conn.rollback()
        pool.putconn(conn)

    reranked = rerank_candidates(clean_query, candidates, session=session, return_scores=True)
    top_reranked = reranked[:top_k]
    if return_scores:
        return top_reranked
    return [r["chunk_id"] for r in top_reranked]


# =============================================================================
# [4] 평가 지표 함수
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
    parts = chunk_id.split("_")
    return "_".join(parts[:2]) if len(parts) >= 2 else chunk_id


# =============================================================================
# [5] 정량 평가 파이프라인
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
    print(f"💾 이력(history) 누적 : {history_path}")


# =============================================================================
# [6] CLI 엔트리포인트
# =============================================================================
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RAG 검색+재정렬 평가 파이프라인 (고도화 버전)")
    parser.add_argument("--dataset", type=str, default=str(DEFAULT_EVAL_DATASET_PATH))
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--workers", type=int, default=1)
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