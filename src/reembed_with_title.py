"""
reembed_with_title.py
======================
기존 recover_null_embeddings.py 는 `content` 컬럼만 임베딩했습니다.
법령명(title)이 임베딩 텍스트에서 빠지면서, 보일러플레이트가 유사한
목적/정의/위원회 설치 조항 등이 서로 다른 법령끼리 혼동되는 문제가 있었습니다.

이 스크립트는 title + content 를 결합해 legal_chunks.embedding 을 재생성합니다.
NULL 여부와 무관하게 대상 행 전체(또는 --doc-type/--where/--limit 로 필터링한 부분)를
다시 임베딩합니다.

[안전장치]
  1. 재임베딩 전, 기존 embedding 값을 타임스탬프가 붙은 백업 테이블로 복사합니다.
     (--no-backup 으로 생략 가능. 권장하지 않습니다.)
  2. --dry-run 으로 실제 API 호출 없이 결합된 텍스트 샘플을 먼저 확인할 수 있습니다.
  3. --limit 으로 소규모 스모크 테스트 후 전체 실행하는 것을 권장합니다.

[롤백 방법 예시]
  UPDATE legal_chunks lc
  SET embedding = b.embedding
  FROM legal_chunks_embedding_backup_20260806_120000 b
  WHERE lc.chunk_id = b.chunk_id;
"""

import argparse
import logging
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
import requests
from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor, execute_batch
from psycopg2.pool import ThreadedConnectionPool
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# =============================================================================
# [1] Configuration (설정 관리)
# =============================================================================
@dataclass
class AppConfig:
    """애플리케이션 실행에 필요한 환경 변수 및 상수를 관리하는 데이터 클래스입니다."""

    # Database Settings
    db_host: str = field(default_factory=lambda: os.getenv("DB_HOST", "127.0.0.1"))
    db_port: int = field(default_factory=lambda: int(os.getenv("DB_PORT", "5432")))
    db_name: str = field(default_factory=lambda: os.getenv("DB_NAME", "postgres"))
    db_user: str = field(default_factory=lambda: os.getenv("DB_USER", "postgres"))
    db_pass: str = field(default_factory=lambda: os.getenv("DB_PASSWORD", "postgres"))

    # API Settings
    nim_api_key: str = field(default_factory=lambda: os.getenv("NVIDIA_NIM_API_KEY", ""))
    embedding_url: str = field(
        default_factory=lambda: os.getenv("EMBEDDING_URL", "https://integrate.api.nvidia.com/v1/embeddings")
    )
    embedding_model: str = field(
        default_factory=lambda: os.getenv("EMBEDDING_MODEL", "nvidia/nemotron-3-embed-1b")
    )

    # Worker & Batch Settings
    max_workers: int = 4
    batch_size: int = 20
    max_retries: int = 5

    def get_db_dsn(self) -> Dict[str, Any]:
        return {
            "host": self.db_host,
            "port": self.db_port,
            "dbname": self.db_name,
            "user": self.db_user,
            "password": self.db_pass,
        }

    def validate(self, dry_run: bool) -> None:
        # dry-run 은 API 호출 없이 텍스트만 확인하므로 API 키가 없어도 동작 가능
        if not dry_run and not self.nim_api_key:
            raise ValueError("NVIDIA_NIM_API_KEY 환경 변수가 설정되지 않았습니다. .env 파일을 확인하세요.")


# =============================================================================
# [2] 임베딩 텍스트 결합 로직 (핵심 변경 지점)
# =============================================================================
def build_embedding_text(title: Optional[str], content: Optional[str]) -> str:
    """title 과 content 를 결합해 임베딩할 최종 텍스트를 만듭니다.

    title 예: "10ㆍ27법난 피해자의 명예회복 등에 관한 법률 제1조(목적)"
    content 예: "이 법은 피해자의 명예를 회복시키고..."

    법령명(또는 판례/해석례/용어 식별자)이 본문 앞에 붙어야, 유사한 보일러플레이트를
    가진 다른 문서와 벡터 공간에서 구분될 여지가 생깁니다.
    """
    title = (title or "").strip()
    content = (content or "").strip()
    if title and content:
        return f"{title}\n{content}"
    return title or content


# =============================================================================
# [3] Pipeline Core (비즈니스 로직)
# =============================================================================
class ReembedWithTitlePipeline:
    """title+content 결합 텍스트로 legal_chunks.embedding 을 재생성하는 파이프라인입니다."""

    def __init__(self, config: AppConfig, dry_run: bool = False):
        self.config = config
        self.dry_run = dry_run
        self.logger = self._setup_logger()

        self.pool = ThreadedConnectionPool(
            minconn=1,
            maxconn=self.config.max_workers + 2,
            **self.config.get_db_dsn(),
        )
        self.http_session = self._create_http_session()

    @staticmethod
    def _setup_logger() -> logging.Logger:
        logger = logging.getLogger("ReembedWithTitle")
        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                fmt="[%(asctime)s][%(levelname)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
        return logger

    def _create_http_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update({
            "Authorization": f"Bearer {self.config.nim_api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["POST"],
        )
        adapter = HTTPAdapter(
            pool_connections=self.config.max_workers,
            pool_maxsize=self.config.max_workers,
            max_retries=retry_strategy,
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    # -------------------------------------------------------------------
    # 대상 조회
    # -------------------------------------------------------------------
    def _build_target_query(
        self,
        doc_type: Optional[str],
        where: Optional[str],
        limit: Optional[int],
    ) -> Tuple[str, Tuple[Any, ...]]:
        conditions = ["(title IS NOT NULL OR content IS NOT NULL)"]
        params: List[Any] = []

        if doc_type:
            conditions.append("doc_type = %s")
            params.append(doc_type)

        if where:
            # 고급 사용자용: 커스텀 WHERE 절을 그대로 삽입합니다.
            # CLI 인자이므로 신뢰 가능한 값(직접 입력)일 때만 사용하세요.
            conditions.append(f"({where})")

        sql = "SELECT chunk_id, title, content FROM legal_chunks WHERE " + " AND ".join(conditions)
        sql += " ORDER BY chunk_id"
        if limit:
            sql += " LIMIT %s"
            params.append(limit)

        return sql, tuple(params)

    def fetch_targets(
        self,
        doc_type: Optional[str],
        where: Optional[str],
        limit: Optional[int],
    ) -> List[Dict[str, Any]]:
        sql, params = self._build_target_query(doc_type, where, limit)
        conn = self.pool.getconn()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, params)
                return cur.fetchall()
        finally:
            self.pool.putconn(conn)

    # -------------------------------------------------------------------
    # 백업
    # -------------------------------------------------------------------
    def backup_embeddings(self, chunk_ids: List[str]) -> Optional[str]:
        """재임베딩 '대상 chunk_id'에 한해서만 기존 embedding을 백업합니다.

        이전 버전은 --where/--limit 필터와 무관하게 항상 테이블 전체를 복사해서,
        소규모 스모크 테스트에서도 백업 테이블이 풀사이즈로 생성되는 문제가 있었습니다.
        이제는 실제로 덮어쓸 chunk_id만 정확히 백업합니다.
        """
        if not chunk_ids:
            return None

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_table = f"legal_chunks_embedding_backup_{ts}"
        conn = self.pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(f"""
                    CREATE TABLE {backup_table} AS
                    SELECT chunk_id, embedding, now() AS backed_up_at
                    FROM legal_chunks
                    WHERE embedding IS NOT NULL
                      AND chunk_id = ANY(%s);
                """, (chunk_ids,))
                cur.execute(f"SELECT COUNT(*) FROM {backup_table};")
                backed_up = cur.fetchone()[0]

                if backed_up == 0:
                    # 백업할 기존 임베딩이 없었던 경우(전부 NULL이었던 대상) 빈 테이블은 정리합니다.
                    cur.execute(f"DROP TABLE {backup_table};")
                    conn.commit()
                    self.logger.info("💾 대상 중 기존 임베딩이 있는 행이 없어 백업을 생략했습니다.")
                    return None

            conn.commit()
            self.logger.info(
                f"💾 기존 임베딩 백업 완료: {backup_table} "
                f"({backed_up:,}건 / 대상 {len(chunk_ids):,}건 중)"
            )
            self.logger.info(
                "   ↳ 롤백 예시: UPDATE legal_chunks lc SET embedding = b.embedding "
                f"FROM {backup_table} b WHERE lc.chunk_id = b.chunk_id;"
            )
            return backup_table
        except psycopg2.DatabaseError as db_err:
            conn.rollback()
            self.logger.error(f"❌ 백업 테이블 생성 실패: {db_err}")
            raise
        finally:
            self.pool.putconn(conn)

    # -------------------------------------------------------------------
    # 임베딩 호출
    # -------------------------------------------------------------------
    def _fetch_embeddings(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []

        payload = {
            "input": texts,
            "model": self.config.embedding_model,
            "input_type": "passage",
            "encoding_format": "float",
            "truncate": "END",
        }

        for attempt in range(1, self.config.max_retries + 1):
            try:
                response = self.http_session.post(self.config.embedding_url, json=payload, timeout=45)

                if response.status_code == 200:
                    data = response.json().get("data", [])
                    data_sorted = sorted(data, key=lambda x: x["index"])
                    return [item["embedding"] for item in data_sorted]

                elif response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    sleep_time = int(retry_after) if retry_after else ((2 ** attempt) + random.uniform(0.1, 1.0))
                    self.logger.warning(
                        f"⚠️ [Rate Limit] API 한도 초과. {sleep_time:.1f}초 대기 후 재시도... "
                        f"(Attempt {attempt}/{self.config.max_retries})"
                    )
                    time.sleep(sleep_time)
                    continue

                else:
                    self.logger.error(f"❌ [API Error] Status: {response.status_code}, Response: {response.text}")
                    break

            except requests.exceptions.RequestException as e:
                self.logger.error(f"❌ [Network Error] 통신 예외 발생: {e} (Attempt {attempt}/{self.config.max_retries})")
                time.sleep((2 ** attempt) + random.uniform(0.1, 1.0))

        return []

    # -------------------------------------------------------------------
    # 배치 처리
    # -------------------------------------------------------------------
    def _process_batch(self, batch: List[Dict[str, Any]]) -> Tuple[int, int]:
        chunk_ids = [row["chunk_id"] for row in batch]
        texts = [build_embedding_text(row["title"], row["content"]) for row in batch]

        embeddings = self._fetch_embeddings(texts)

        if not embeddings or len(embeddings) != len(batch):
            self.logger.error(f"❌ Batch 실패: ID {chunk_ids[0]} 포함 {len(batch)}건 임베딩 생성 불가")
            return 0, len(batch)

        update_data = [(str(emb), cid) for emb, cid in zip(embeddings, chunk_ids)]

        conn = self.pool.getconn()
        try:
            with conn.cursor() as cur:
                sql = "UPDATE legal_chunks SET embedding = %s::halfvec WHERE chunk_id = %s;"
                execute_batch(cur, sql, update_data, page_size=self.config.batch_size)
            conn.commit()
            return len(update_data), 0

        except psycopg2.DatabaseError as db_err:
            conn.rollback()
            self.logger.error(f"❌ [DB Transaction Error] 롤백 처리됨: {db_err}")
            return 0, len(batch)

        finally:
            self.pool.putconn(conn)

    # -------------------------------------------------------------------
    # 실행
    # -------------------------------------------------------------------
    def run_dry_run(self, targets: List[Dict[str, Any]], n: int) -> None:
        self.logger.info(f"🔎 [Dry-run] 결합 텍스트 샘플 {min(n, len(targets))}건 (API 호출 없음)")
        for row in targets[:n]:
            combined = build_embedding_text(row["title"], row["content"])
            preview = combined[:300].replace("\n", " ⏎ ")
            self.logger.info(f"  ── {row['chunk_id']}")
            self.logger.info(f"     {preview}{'...' if len(combined) > 300 else ''}")

    def run(
        self,
        doc_type: Optional[str] = None,
        where: Optional[str] = None,
        limit: Optional[int] = None,
        skip_backup: bool = False,
        dry_run_n: int = 5,
    ) -> None:
        try:
            targets = self.fetch_targets(doc_type, where, limit)
            total_records = len(targets)
            self.logger.info(f"🔍 재임베딩 대상 조회 완료: 총 {total_records:,}건")

            if total_records == 0:
                self.logger.info("✅ 대상 데이터가 없습니다. 파이프라인을 안전하게 종료합니다.")
                return

            if self.dry_run:
                self.run_dry_run(targets, dry_run_n)
                return

            if not skip_backup:
                target_chunk_ids = [row["chunk_id"] for row in targets]
                self.backup_embeddings(target_chunk_ids)
            else:
                self.logger.warning("⚠️ --no-backup 지정됨: 기존 임베딩 백업을 생략합니다.")

            batches = [
                targets[i: i + self.config.batch_size]
                for i in range(0, total_records, self.config.batch_size)
            ]
            self.logger.info(
                f"📦 작업 분할 완료: 총 {len(batches):,}개 배치 "
                f"(Size: {self.config.batch_size}, Workers: {self.config.max_workers})"
            )

            success_count, fail_count = 0, 0

            with ThreadPoolExecutor(max_workers=self.config.max_workers) as executor:
                futures = {
                    executor.submit(self._process_batch, batch): idx
                    for idx, batch in enumerate(batches)
                }

                for future in as_completed(futures):
                    batch_index = futures[future]
                    try:
                        s_count, f_count = future.result()
                        success_count += s_count
                        fail_count += f_count

                        if (batch_index + 1) % 50 == 0 or (batch_index + 1) == len(batches):
                            progress_pct = (success_count + fail_count) / total_records * 100
                            self.logger.info(
                                f"🔄 진행률: {progress_pct:05.2f}% | "
                                f"성공: {success_count:,}건 | 실패: {fail_count:,}건 "
                                f"(Batch {batch_index + 1}/{len(batches)})"
                            )
                    except Exception as exc:
                        self.logger.error(f"💥 배치 {batch_index} 처리 중 치명적인 예외 발생: {exc}", exc_info=True)

            self.logger.info("=" * 70)
            self.logger.info("🎉 재임베딩 파이프라인 실행 완료")
            self.logger.info(f"   ├─ 처리 대상: {total_records:,} 건")
            self.logger.info(f"   ├─ 성공 건수: {success_count:,} 건")
            self.logger.info(f"   └─ 실패 건수: {fail_count:,} 건")
            self.logger.info("=" * 70)

        finally:
            self.pool.closeall()
            self.http_session.close()


# =============================================================================
# [4] Entry Point
# =============================================================================
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="title+content 결합 텍스트로 legal_chunks 임베딩 재생성")
    parser.add_argument("--doc-type", type=str, default=None, help="특정 doc_type만 대상 (law, prec, expc, lstrm, addendum 등)")
    parser.add_argument("--where", type=str, default=None, help="커스텀 WHERE 절 (예: \"law_id = '010719'\")")
    parser.add_argument("--limit", type=int, default=None, help="처리 건수 제한 (스모크 테스트용)")
    parser.add_argument("--dry-run", action="store_true", help="API 호출 없이 결합 텍스트 샘플만 출력하고 종료")
    parser.add_argument("--dry-run-n", type=int, default=5, help="dry-run 시 출력할 샘플 개수")
    parser.add_argument("--no-backup", action="store_true", help="기존 임베딩 백업 생략 (권장하지 않음)")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=20)
    return parser.parse_args()


if __name__ == "__main__":
    load_dotenv()
    args = parse_args()

    try:
        config = AppConfig()
        config.max_workers = args.workers
        config.batch_size = args.batch_size
        config.validate(dry_run=args.dry_run)

        pipeline = ReembedWithTitlePipeline(config, dry_run=args.dry_run)
        pipeline.run(
            doc_type=args.doc_type,
            where=args.where,
            limit=args.limit,
            skip_backup=args.no_backup,
            dry_run_n=args.dry_run_n,
        )

    except ValueError as ve:
        logging.error(f"설정 오류: {ve}")
    except Exception as e:
        logging.critical(f"애플리케이션 비정상 종료: {e}", exc_info=True)