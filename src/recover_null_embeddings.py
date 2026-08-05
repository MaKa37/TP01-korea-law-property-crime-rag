import logging
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
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
    embedding_url: str = field(default_factory=lambda: os.getenv("EMBEDDING_URL", "https://integrate.api.nvidia.com/v1/embeddings"))
    embedding_model: str = "nvidia/nemotron-3-embed-1b"
    
    # Worker & Batch Settings
    max_workers: int = 4
    batch_size: int = 20
    max_retries: int = 5
    text_column: str = "content"

    def get_db_dsn(self) -> Dict[str, Any]:
        """psycopg2 연결에 필요한 DSN 딕셔너리를 반환합니다."""
        return {
            "host": self.db_host,
            "port": self.db_port,
            "dbname": self.db_name,
            "user": self.db_user,
            "password": self.db_pass,
        }

    def validate(self) -> None:
        """필수 환경 변수 누락 여부를 검증합니다."""
        if not self.nim_api_key:
            raise ValueError("NVIDIA_NIM_API_KEY 환경 변수가 설정되지 않았습니다. .env 파일을 확인하세요.")


# =============================================================================
# [2] Pipeline Core (비즈니스 로직)
# =============================================================================
class EmbeddingRecoveryPipeline:
    """누락된(NULL) 임베딩 데이터를 조회하여 생성 및 복구하는 파이프라인 클래스입니다."""

    def __init__(self, config: AppConfig):
        """
        파이프라인 초기화 및 리소스(DB 커넥션 풀, HTTP 세션)를 준비합니다.

        Args:
            config (AppConfig): 애플리케이션 설정 객체
        """
        self.config = config
        self.logger = self._setup_logger()
        
        # 1. DB Connection Pool 초기화 (멀티스레딩 환경 지원)
        self.pool = ThreadedConnectionPool(
            minconn=1, 
            maxconn=self.config.max_workers + 2, 
            **self.config.get_db_dsn()
        )
        
        # 2. HTTP Session 초기화 (Keep-Alive 및 전역 Retry 설정)
        self.http_session = self._create_http_session()

    @staticmethod
    def _setup_logger() -> logging.Logger:
        """파이프라인 전용 로거를 설정합니다."""
        logger = logging.getLogger("EmbeddingRecovery")
        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                fmt="[%(asctime)s][%(levelname)s] %(message)s", 
                datefmt="%Y-%m-%d %H:%M:%S"
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
        return logger

    def _create_http_session(self) -> requests.Session:
        """
        재시도 로직과 커넥션 풀링이 적용된 HTTP 세션을 생성합니다.
        이는 매 요청마다 TCP 핸드쉐이크가 발생하는 것을 방지하여 성능을 향상시킵니다.
        """
        session = requests.Session()
        session.headers.update({
            "Authorization": f"Bearer {self.config.nim_api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        
        # 일시적인 네트워크 장애(500, 502, 503, 504)에 대한 자동 재시도
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["POST"]
        )
        adapter = HTTPAdapter(pool_connections=self.config.max_workers, 
                              pool_maxsize=self.config.max_workers, 
                              max_retries=retry_strategy)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        
        return session

    def _fetch_embeddings(self, texts: List[str]) -> List[List[float]]:
        """
        NVIDIA NIM API를 호출하여 텍스트 배치의 임베딩 벡터를 가져옵니다.

        Args:
            texts (List[str]): 임베딩을 생성할 텍스트 리스트 (최대 배치 사이즈)

        Returns:
            List[List[float]]: 생성된 임베딩 벡터의 리스트. 실패 시 빈 리스트 반환.
        """
        if not texts:
            return []

        payload = {
            "input": texts,
            "model": self.config.embedding_model,
            "input_type": "passage",
            "encoding_format": "float",
            "truncate": "END" # 32K 한도 초과 시 안전하게 뒷부분 절단
        }

        for attempt in range(1, self.config.max_retries + 1):
            try:
                response = self.http_session.post(
                    self.config.embedding_url, 
                    json=payload, 
                    timeout=45
                )
                
                # 성공 처리
                if response.status_code == 200:
                    data = response.json().get("data", [])
                    # API 응답의 인덱스를 보장하여 입력 순서와 일치시킴
                    data_sorted = sorted(data, key=lambda x: x["index"])
                    return [item["embedding"] for item in data_sorted]
                
                # 429 Rate Limit (스로틀링) 처리: 지수 백오프 (Exponential Backoff)
                elif response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    sleep_time = int(retry_after) if retry_after else ((2 ** attempt) + random.uniform(0.1, 1.0))
                    self.logger.warning(
                        f"⚠️ [Rate Limit] API 한도 초과. {sleep_time:.1f}초 대기 후 재시도... "
                        f"(Attempt {attempt}/{self.config.max_retries})"
                    )
                    time.sleep(sleep_time)
                    continue
                
                # 그 외 클라이언트/서버 에러 (400, 401, 403 등)
                else:
                    self.logger.error(f"❌ [API Error] Status: {response.status_code}, Response: {response.text}")
                    break

            except requests.exceptions.RequestException as e:
                self.logger.error(f"❌ [Network Error] 통신 예외 발생: {e} (Attempt {attempt}/{self.config.max_retries})")
                time.sleep((2 ** attempt) + random.uniform(0.1, 1.0))
                
        return []

    def _process_batch(self, batch: List[Dict[str, Any]]) -> Tuple[int, int]:
        """
        단일 데이터 배치를 처리합니다. (임베딩 발급 -> DB 업데이트)

        Args:
            batch (List[Dict[str, Any]]): DB에서 조회한 원본 레코드 리스트

        Returns:
            Tuple[int, int]: (성공 건수, 실패 건수)
        """
        chunk_ids = [row["chunk_id"] for row in batch]
        texts = [row[self.config.text_column] for row in batch]
        
        # 1. 임베딩 벡터 발급
        embeddings = self._fetch_embeddings(texts)
        
        if not embeddings or len(embeddings) != len(batch):
            self.logger.error(f"❌ Batch 실패: ID {chunk_ids[0]} 포함 {len(batch)}건 임베딩 생성 불가")
            return 0, len(batch)
        
        # 2. DB 업데이트를 위한 파라미터 매핑
        update_data = [
            (str(emb), cid) for emb, cid in zip(embeddings, chunk_ids)
        ]
        
        # 3. PostgreSQL 고속 업데이트 (execute_batch)
        conn = self.pool.getconn()
        try:
            with conn.cursor() as cur:
                # pgvector 0.7.0+ halfvec 타입 명시적 캐스팅
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

    def run(self) -> None:
        """전체 임베딩 복구 파이프라인을 실행합니다."""
        try:
            # 1. DB에서 복구 대상(NULL) 조회
            conn = self.pool.getconn()
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(f"SELECT chunk_id, {self.config.text_column} FROM legal_chunks WHERE embedding IS NULL;")
                null_records = cur.fetchall()
            self.pool.putconn(conn)
            
            total_records = len(null_records)
            self.logger.info(f"🔍 복구 대상 (embedding IS NULL) 데이터 조회 완료: 총 {total_records:,}건")
            
            if total_records == 0:
                self.logger.info("✅ 복구할 데이터가 존재하지 않습니다. 파이프라인을 안전하게 종료합니다.")
                return

            # 2. 데이터를 지정된 BATCH_SIZE 단위로 청크 분할
            batches = [
                null_records[i : i + self.config.batch_size] 
                for i in range(0, total_records, self.config.batch_size)
            ]
            self.logger.info(
                f"📦 작업 분할 완료: 총 {len(batches):,}개 배치 (Size: {self.config.batch_size}, Workers: {self.config.max_workers})"
            )
            
            success_count, fail_count = 0, 0
            
            # 3. ThreadPool을 이용한 병렬 처리
            with ThreadPoolExecutor(max_workers=self.config.max_workers) as executor:
                # Future-to-Index 매핑으로 작업 순서 식별
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
                        
                        # 진행 상황 로깅 (매 100번째 배치 또는 마지막 배치)
                        if (batch_index + 1) % 100 == 0 or (batch_index + 1) == len(batches):
                            progress_pct = (success_count + fail_count) / total_records * 100
                            self.logger.info(
                                f"🔄 진행률: {progress_pct:05.2f}% | "
                                f"성공: {success_count:,}건 | 실패: {fail_count:,}건 "
                                f"(Batch {batch_index + 1}/{len(batches)})"
                            )
                    except Exception as exc:
                        self.logger.error(f"💥 배치 {batch_index} 처리 중 치명적인 예외 발생: {exc}", exc_info=True)
                        
            # 4. 결과 요약 리포트
            self.logger.info("=" * 70)
            self.logger.info(f"🎉 복구 파이프라인 실행 완료")
            self.logger.info(f"   ├─ 처리 대상: {total_records:,} 건")
            self.logger.info(f"   ├─ 성공 건수: {success_count:,} 건")
            self.logger.info(f"   └─ 실패 건수: {fail_count:,} 건")
            self.logger.info("=" * 70)

        finally:
            # 리소스 누수(Leak) 방지를 위한 명시적 종료
            self.pool.closeall()
            self.http_session.close()


# =============================================================================
# [3] Entry Point
# =============================================================================
if __name__ == "__main__":
    load_dotenv()
    
    try:
        config = AppConfig()
        config.validate()
        
        pipeline = EmbeddingRecoveryPipeline(config)
        pipeline.run()
        
    except ValueError as ve:
        logging.error(f"설정 오류: {ve}")
    except Exception as e:
        logging.critical(f"애플리케이션 비정상 종료: {e}", exc_info=True)