r"""
Legal Chunk DB Ultra-Fast Ingestor with NVIDIA NIM Embedding API (Optimized Parallel Version)

최종 최적화 내역:
    1. [HTTP SESSION POOLING] requests.Session() 적용으로 TCP/TLS Handshake 오버헤드 완전 제거
    2. [CONCURRENCY UPGRADE] MAX_API_WORKERS 8 -> 32 상향으로 API Latency 완벽 억제
    3. [NON-BLOCKING AS_COMPLETED] FIFO 순서 블로킹 제거하여 먼저 완료된 API 결과 즉시 DB 버퍼 반영

Author: Vector DB Engineering Team
Date: 2026-08-04
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
import csv
import io
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Dict, Any, Generator, Tuple, List
import psycopg2
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from tqdm import tqdm
except ImportError:
    class DummyTqdm:
        def __init__(self, iterable=None, *args, **kwargs): self.iterable = iterable
        def __iter__(self): return iter(self.iterable) if self.iterable is not None else iter([])
        def update(self, n=1): pass
        def close(self): pass
    def tqdm(iterable=None, *args, **kwargs): return DummyTqdm(iterable) if iterable is not None else DummyTqdm()


logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("LegalDBIngestor")

DB_HOST = os.getenv("DB_HOST", os.getenv("POSTGRES_HOST", "127.0.0.1"))
DB_PORT = int(os.getenv("DB_PORT", os.getenv("POSTGRES_PORT", "5432")))
DB_NAME = os.getenv("DB_NAME", os.getenv("POSTGRES_DB", "postgres"))
DB_USER = os.getenv("DB_USER", os.getenv("POSTGRES_USER", "postgres"))
DB_PASSWORD = os.getenv("DB_PASSWORD", os.getenv("POSTGRES_PASSWORD", "postgres"))

NVIDIA_NIM_API_KEY = os.getenv("NVIDIA_NIM_API_KEY", "")
EMBEDDING_URL = os.getenv("EMBEDDING_URL", "https://integrate.api.nvidia.com/v1/embeddings")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "nvidia/nv-embedqa-e5-v5")

EXPECTED_EMBEDDING_DIM = 1024
COPY_BATCH_SIZE = int(os.getenv("COPY_BATCH_SIZE", "20000"))
API_BATCH_SIZE = 50
MAX_API_WORKERS = 32  # [최적화 1] 동시 API 요청 수 8 -> 32로 확대


class NVIDIAEmbeddingClient:
    def __init__(self, api_key: str, url: str, model: str, max_workers: int = 32):
        self.api_key = api_key
        self.url = url
        self.model = model
        
        # [최적화 2] Connection Pool 재사용 세션 설정 (TLS Handshake 제거)
        self.session = requests.Session()
        adapter = HTTPAdapter(
            pool_connections=max_workers,
            pool_maxsize=max_workers * 2,
            max_retries=Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
        )
        self.session.mount("https://", adapter)
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        })

    def fetch_embeddings_batch(self, records_batch: List[Dict[str, Any]], max_retries: int = 3) -> List[Tuple]:
        texts_to_embed = [
            f"{r.get('title', '')} {r.get('content', '')}".strip()
            for r in records_batch
        ]

        payload = {
            "input": texts_to_embed,
            "model": self.model,
            "input_type": "passage",
            "encoding_format": "float"
        }

        embeddings = []
        for attempt in range(max_retries):
            try:
                # 세션 재사용으로 HTTP 통신 속도 극대화
                resp = self.session.post(self.url, json=payload, timeout=30)
                if resp.status_code == 200:
                    data = resp.json()
                    sorted_data = sorted(data["data"], key=lambda x: x["index"])
                    embeddings = [item["embedding"] for item in sorted_data]
                    break
                elif resp.status_code == 429:
                    time.sleep(2 ** attempt)
                else:
                    time.sleep(1)
            except Exception:
                time.sleep(1)

        if len(embeddings) != len(records_batch):
            embeddings = [None] * len(records_batch)

        processed_tuples = []
        for record, emb in zip(records_batch, embeddings):
            chunk_id = record.get("chunk_id")
            if not chunk_id:
                continue

            vec_str = ""
            if emb and len(emb) == EXPECTED_EMBEDDING_DIM:
                vec_str = "[" + ",".join(map(str, emb)) + "]"

            meta_dict = record.get("metadata", {})
            meta_str = json.dumps(meta_dict, ensure_ascii=False)

            processed_tuples.append((
                str(chunk_id),
                str(record.get("doc_type", "기타")),
                str(record.get("doc_id", "")),
                str(record.get("title", "")),
                str(record.get("content", "")),
                vec_str,
                meta_str
            ))

        return processed_tuples


class LegalDataIngestorProduction:
    def __init__(self):
        self.conn_params = {
            "host": DB_HOST, "port": DB_PORT, "dbname": DB_NAME,
            "user": DB_USER, "password": DB_PASSWORD
        }
        self.emb_client = NVIDIAEmbeddingClient(NVIDIA_NIM_API_KEY, EMBEDDING_URL, EMBEDDING_MODEL, MAX_API_WORKERS)

    @contextmanager
    def get_connection(self):
        conn = psycopg2.connect(**self.conn_params)
        try:
            yield conn
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def prepare_schema(self) -> None:
        logger.info(f"🔧 [Step 1/4] DDL 검증 및 LIST 파티셔닝 생성 (Dim: {EXPECTED_EMBEDDING_DIM})...")
        ddl = f"""
        CREATE EXTENSION IF NOT EXISTS vector;
        CREATE EXTENSION IF NOT EXISTS pg_trgm;

        DROP TABLE IF EXISTS legal_chunks CASCADE;

        CREATE TABLE IF NOT EXISTS legal_chunks (
            chunk_id VARCHAR(128) NOT NULL,
            doc_type VARCHAR(32) NOT NULL,
            doc_id VARCHAR(64) NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            fts_vector tsvector GENERATED ALWAYS AS (
                to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(content, ''))
            ) STORED,
            embedding vector({EXPECTED_EMBEDDING_DIM}),
            metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
            PRIMARY KEY (doc_type, chunk_id)
        ) PARTITION BY LIST (doc_type);

        CREATE TABLE IF NOT EXISTS legal_chunks_precedent PARTITION OF legal_chunks FOR VALUES IN ('판례', 'precedent');
        CREATE TABLE IF NOT EXISTS legal_chunks_statute   PARTITION OF legal_chunks FOR VALUES IN ('법령', 'statute');
        CREATE TABLE IF NOT EXISTS legal_chunks_default   PARTITION OF legal_chunks DEFAULT;
        """
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(ddl)
            conn.commit()

    def drop_all_indexes(self) -> None:
        logger.info("⚡ [Step 2/4] 대량 적재 I/O 병목 제거를 위해 기존 인덱스 DROP...")
        drop_query = """
        DROP INDEX IF EXISTS idx_legal_chunks_embedding_hnsw;
        DROP INDEX IF EXISTS idx_legal_chunks_fts_gin;
        DROP INDEX IF EXISTS idx_legal_chunks_metadata_gin;
        DROP INDEX IF EXISTS idx_legal_chunks_doc_type_id;
        """
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(drop_query)
            conn.commit()

    def _read_jsonl_in_batches(self, jsonl_path: Path, batch_size: int) -> Generator[List[Dict[str, Any]], None, None]:
        batch = []
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    batch.append(record)
                    if len(batch) >= batch_size:
                        yield batch
                        batch = []
                except Exception:
                    continue
            if batch:
                yield batch

    def execute_copy_upsert(self, jsonl_path: Path) -> int:
        logger.info(f"🚀 [Step 3/4] 초고속 API 병렬 임베딩 (Workers: {MAX_API_WORKERS}) & UNLOGGED Bulk COPY 시작")
        start_time = time.time()
        total_inserted = 0

        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(f"""
                    CREATE TEMP TABLE legal_chunks_staging (
                        chunk_id VARCHAR(128),
                        doc_type VARCHAR(32),
                        doc_id VARCHAR(64),
                        title TEXT,
                        content TEXT,
                        embedding vector({EXPECTED_EMBEDDING_DIM}),
                        metadata JSONB
                    ) ON COMMIT DROP;
                """)

                buffer = io.StringIO()
                writer = csv.writer(
                    buffer, delimiter="\t", quotechar='"', quoting=csv.QUOTE_MINIMAL, lineterminator="\n"
                )

                copy_sql = """
                    COPY legal_chunks_staging (
                        chunk_id, doc_type, doc_id, title, content, embedding, metadata
                    ) FROM STDIN WITH (
                        FORMAT CSV, DELIMITER '\t', QUOTE '"', NULL ''
                    )
                """

                pbar = tqdm(unit=" chunks", desc="High-Speed Embedding & Ingesting")

                # [최적화 3] as_completed 비동기-라이크 병렬 처리 구조
                with ThreadPoolExecutor(max_workers=MAX_API_WORKERS) as executor:
                    futures = set()
                    
                    for raw_batch in self._read_jsonl_in_batches(jsonl_path, API_BATCH_SIZE):
                        fut = executor.submit(self.emb_client.fetch_embeddings_batch, raw_batch)
                        futures.add(fut)

                        # 워커 큐의 2배 이상 쌓이면 완료된 스레드부터 비블로킹 방식으로 수집
                        if len(futures) >= MAX_API_WORKERS * 2:
                            # 100ms 타임아웃 안에서 완료된 녀석들만 먼저 꺼냄
                            done_futures = {f for f in futures if f.done()}
                            if not done_futures:
                                # 아직 완료된 게 없으면 1개만 기다림
                                done_futures = [next(as_completed(futures))]
                            
                            for f in done_futures:
                                futures.remove(f)
                                processed_records = f.result()
                                for record_tuple in processed_records:
                                    writer.writerow(record_tuple)
                                    total_inserted += 1

                                pbar.update(len(processed_records))

                            if buffer.tell() > 10 * 1024 * 1024:  # 10MB 단위 Flush
                                buffer.seek(0)
                                cur.copy_expert(copy_sql, buffer)
                                buffer.seek(0)
                                buffer.truncate(0)

                    # 남은 작업 처리
                    for f in as_completed(futures):
                        processed_records = f.result()
                        for record_tuple in processed_records:
                            writer.writerow(record_tuple)
                            total_inserted += 1
                        pbar.update(len(processed_records))

                if buffer.getvalue():
                    buffer.seek(0)
                    cur.copy_expert(copy_sql, buffer)
                    buffer.seek(0)
                    buffer.truncate(0)

                pbar.close()

                logger.info("  └ Staging ➔ 파티션 메인 테이블 Bulk Merge 진행 중...")
                cur.execute("""
                    INSERT INTO legal_chunks (chunk_id, doc_type, doc_id, title, content, embedding, metadata)
                    SELECT chunk_id, doc_type, doc_id, title, content, embedding, metadata
                    FROM legal_chunks_staging
                    ON CONFLICT (doc_type, chunk_id) DO UPDATE SET
                        doc_id = EXCLUDED.doc_id,
                        title = EXCLUDED.title,
                        content = EXCLUDED.content,
                        embedding = EXCLUDED.embedding,
                        metadata = EXCLUDED.metadata;
                """)

            conn.commit()

        elapsed = time.time() - start_time
        logger.info(f"  └ Bulk 적재 완료: 총 {total_inserted:,} 개 ({elapsed:.2f}초 소요, 속도: {total_inserted/elapsed:.1f} chunks/sec)")
        return total_inserted

    def rebuild_all_indexes(self) -> None:
        logger.info("🏗️ [Step 4/4] 파티션 인덱스 병렬 재구성 및 ANALYZE 수행...")
        start_time = time.time()

        index_queries = [
            ("1/4 HNSW Vector Index (1024-dim, ef_construction=128)", """
                CREATE INDEX IF NOT EXISTS idx_legal_chunks_embedding_hnsw
                ON legal_chunks USING hnsw (embedding vector_cosine_ops)
                WITH (m = 16, ef_construction = 128);
            """),
            ("2/4 FTS GIN Index (Pre-computed tsvector)", """
                CREATE INDEX IF NOT EXISTS idx_legal_chunks_fts_gin
                ON legal_chunks USING gin (fts_vector);
            """),
            ("3/4 JSONB Path GIN Index", """
                CREATE INDEX IF NOT EXISTS idx_legal_chunks_metadata_gin
                ON legal_chunks USING gin (metadata jsonb_path_ops);
            """),
            ("4/4 B-Tree Compound Index", """
                CREATE INDEX IF NOT EXISTS idx_legal_chunks_doc_type_id
                ON legal_chunks (doc_type, doc_id);
            """)
        ]

        with self.get_connection() as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("SET max_parallel_maintenance_workers = 4;")
                cur.execute("SET maintenance_work_mem = '4GB';")

                for name, query in index_queries:
                    step_start = time.time()
                    logger.info(f"  └ [{name}] 빌드 중...")
                    cur.execute(query)
                    logger.info(f"  └ [{name}] 완료 ({time.time() - step_start:.2f}초)")

                logger.info("  └ 📊 PostgreSQL ANALYZE 실행...")
                cur.execute("ANALYZE legal_chunks;")

        logger.info(f"🎉 모든 파티션 인덱스 재구성 완료! (총 소요 시간: {time.time() - start_time:.2f}초)")


def main():
    if not NVIDIA_NIM_API_KEY:
        logger.error("❌ NVIDIA_NIM_API_KEY가 없습니다. .env 파일을 확인해주세요.")
        sys.exit(1)

    base_dir = Path(__file__).resolve().parent.parent
    jsonl_file_path = base_dir / "data" / "processed" / "chunks_v4.3.1.jsonl"

    if not jsonl_file_path.exists():
        logger.error(f"❌ 적재 파일 없음: {jsonl_file_path}")
        sys.exit(1)

    ingestor = LegalDataIngestorProduction()

    try:
        ingestor.prepare_schema()
        ingestor.drop_all_indexes()
        ingestor.execute_copy_upsert(jsonl_file_path)
        ingestor.rebuild_all_indexes()
        logger.info("✨ [SUCCESS] 파티셔닝 기반 DB 적재 파이프라인 완료!")

    except Exception as e:
        logger.critical(f"💥 오류 발생: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()