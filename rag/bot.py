"""RAG 파이프라인 오케스트레이터."""
import time
from typing import Any, Dict, List

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from core.config import RAGConfig
from core.logging import get_logger
from db.pool import create_db_pool
from rag.embedding import get_embedding
from rag.generator import build_fallback_answer, generate_response
from rag.reranker import rerank_candidates
from rag.retrieval import execute_hybrid_search, execute_keyword_search


class LegalRAGBot:
    def __init__(self, config: RAGConfig):
        self.config = config
        self.logger = get_logger()
        self._validate_config()
        self.session = self._setup_session()
        self.db_pool = create_db_pool(config, self.logger)

    def __enter__(self) -> "LegalRAGBot":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def _validate_config(self) -> None:
        if not self.config.nim_api_key:
            self.logger.warning(
                "⚠️ NVIDIA_NIM_API_KEY가 비어 있습니다. .env 파일 또는 환경변수를 "
                "확인해주세요. 이 상태로는 임베딩/리랭킹/답변생성 API 호출이 "
                "모두 인증 실패할 수 있습니다."
            )
        if self.config.db_pass == "postgres":
            self.logger.warning(
                "⚠️ DB_PASSWORD가 기본값(postgres)입니다. 운영 환경에서는 "
                "반드시 .env로 별도 설정해주세요."
            )

    def _setup_session(self) -> requests.Session:
        session = requests.Session()
        retry = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.headers.update({
            "Authorization": f"Bearer {self.config.nim_api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        })
        return session

    def retrieve(self, user_query: str) -> List[Dict[str, Any]]:
        """검색(임베딩→하이브리드/키워드 검색→리랭킹)까지만 수행하고 LLM 생성은 하지 않는다.

        evaluation/ 하네스에서 이 메서드를 재사용한다.
        """
        query_vector = get_embedding(self.session, self.config, self.logger, user_query)

        if query_vector:
            candidates = execute_hybrid_search(self.db_pool, self.config, user_query, query_vector)
            self.logger.info(f"1차 하이브리드 검색 완료: {len(candidates)}건 후보 도출")
        else:
            self.logger.warning("⚠️ 임베딩 API 응답 실패. '텍스트 키워드 단독 검색'으로 우회합니다.")
            candidates = execute_keyword_search(self.db_pool, self.config, user_query)
            self.logger.info(f"1차 키워드 검색 완료: {len(candidates)}건 후보 도출")

        top_docs = rerank_candidates(self.session, self.config, self.logger, user_query, candidates)
        self.logger.info(f"2차 Reranking 완료: 상위 {len(top_docs)}건 확정")
        return top_docs

    def ask(self, user_query: str) -> Dict[str, Any]:
        """사용자 질의를 받아 검색부터 답변 생성까지 파이프라인 전체를 실행하는 메인 메서드."""
        start_time = time.time()
        self.logger.info(f"사용자 질의 접수: '{user_query}'")

        try:
            top_docs = self.retrieve(user_query)

            if not top_docs:
                self.logger.warning("검색된 참고 자료가 없어 답변 생성을 건너뜁니다.")
                latency = time.time() - start_time
                return {
                    "status": "no_results",
                    "answer": (
                        "죄송합니다. 질문과 관련된 법령이나 판례를 찾지 못했습니다. "
                        "질문을 조금 더 구체적으로 입력하시거나 다른 키워드로 다시 시도해 주세요."
                    ),
                    "retrieved_documents": [],
                    "llm_available": None,
                    "latency_sec": latency
                }

            try:
                answer = generate_response(self.session, self.config, self.logger, user_query, top_docs)
                llm_available = True
            except Exception as e:
                self.logger.error(f"🚨 답변 생성 실패, 검색된 원문 자료로 대체합니다: {e}")
                answer = build_fallback_answer(top_docs)
                llm_available = False

            latency = time.time() - start_time
            self.logger.info(f"답변 생성 완료 (총 소요 시간: {latency:.2f}초, LLM 사용: {llm_available})")

            return {
                "status": "success",
                "answer": answer,
                "retrieved_documents": top_docs,
                "llm_available": llm_available,
                "latency_sec": latency
            }

        except Exception as e:
            self.logger.error(f"파이프라인 실행 중 오류 발생: {e}", exc_info=True)
            return {
                "status": "error",
                "error_message": str(e)
            }

    def close(self):
        self.session.close()
        self.db_pool.closeall()
        self.logger.info("데이터베이스 커넥션 및 세션 종료 완료")
