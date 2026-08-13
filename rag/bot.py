"""RAG 파이프라인 오케스트레이터."""
import time
from typing import Any, Dict, Iterator, List

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from core.config import RAGConfig
from core.logging import get_logger
from db.pool import create_db_pool
from rag.embedding import get_embedding
from rag.generator import build_fallback_answer, generate_response, generate_response_stream
from rag.reranker import rerank_candidates, select_diverse_top_k
from rag.retrieval import execute_hybrid_search, execute_keyword_search


class LegalRAGBot:
    def __init__(self, config: RAGConfig):
        self.config = config
        self.logger = get_logger()
        self._validate_config()
        # 임베딩/리랭킹은 응답이 빠르고(수 초) 실패해도 재시도 여러 번이
        # 부담 없어서 관대하게(4회) 재시도한다.
        self.session = self._setup_session(total=4, backoff_factor=1.0)
        # ⚠️ 채팅 생성은 별도 세션을 쓴다. 대형 모델이 응답을 안 주는
        # 상황에서 read timeout(60초)마다 재시도를 4번 반복하면 사용자가
        # 5분 넘게 기다리게 된다(실제로 328초 걸린 사례 있음). 이미
        # rag/bot.py의 ask()/ask_stream()에 애플리케이션 레벨 폴백
        # (검색 원문 대체 답변)이 있으므로, 여기서는 빠르게 실패하고
        # 그 폴백에 맡기는 게 사용자 경험상 낫다.
        self.stream_session = self._setup_session(total=1, backoff_factor=0.5)
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

    def _setup_session(self, total: int = 4, backoff_factor: float = 1.0) -> requests.Session:
        session = requests.Session()
        # ⚠️ urllib3의 Retry는 기본적으로 POST를 재시도 대상에서 제외한다
        # (allowed_methods 기본값이 GET/HEAD/OPTIONS 등 멱등 메서드만 포함).
        # 이 프로젝트의 모든 API 호출(임베딩/리랭킹/생성/판정)이 POST라서,
        # allowed_methods를 명시하지 않으면 status_forcelist가 있어도
        # 재시도가 한 번도 발동하지 않는다. 550B급 모델은 부하 시 503을
        # 자주 반환하므로 이 부분이 특히 중요하다.
        retry = Retry(
            total=total,
            backoff_factor=backoff_factor,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=frozenset(["GET", "HEAD", "OPTIONS", "POST"]),
        )
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

        candidate_pool = rerank_candidates(self.session, self.config, self.logger, user_query, candidates)
        top_docs = select_diverse_top_k(candidate_pool, self.config.top_k, self.config.diversity_similarity_threshold)
        self.logger.info(f"2차 Reranking+다양성 필터 완료: 후보 {len(candidate_pool)}건 중 상위 {len(top_docs)}건 확정")
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
                answer = generate_response(self.stream_session, self.config, self.logger, user_query, top_docs)
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

    def ask_stream(self, user_query: str) -> Iterator[Dict[str, Any]]:
        """API 서비스 계층(SSE)용: 각 단계를 이벤트로 yield한다.

        이벤트 종류:
          - {"type": "sources", "documents": [...]}          검색 완료, 생성 시작 전
          - {"type": "token", "content": "..."}                생성 토큰 (여러 번)
          - {"type": "done", "latency_sec": ..., "llm_available": bool}
          - {"type": "no_results", "message": "..."}
          - {"type": "error", "message": "..."}

        이 메서드 자체는 동기(sync) 제너레이터다. FastAPI 라우트에서
        StreamingResponse에 그대로 넘기면 Starlette가 스레드풀에서
        실행해주므로, DB/HTTP 블로킹 호출이 이벤트 루프를 막지 않는다.
        """
        start_time = time.time()
        self.logger.info(f"[stream] 사용자 질의 접수: '{user_query}'")

        try:
            top_docs = self.retrieve(user_query)
        except Exception as e:
            self.logger.error(f"검색 중 오류 발생: {e}", exc_info=True)
            yield {"type": "error", "message": str(e)}
            return

        if not top_docs:
            self.logger.warning("검색된 참고 자료가 없어 답변 생성을 건너뜁니다.")
            yield {
                "type": "no_results",
                "message": (
                    "죄송합니다. 질문과 관련된 법령이나 판례를 찾지 못했습니다. "
                    "질문을 조금 더 구체적으로 입력하시거나 다른 키워드로 다시 시도해 주세요."
                )
            }
            return

        yield {
            "type": "sources",
            "documents": [
                {"title": d["title"], "doc_type": d.get("doc_type"), "rerank_score": d.get("rerank_score")}
                for d in top_docs
            ]
        }

        llm_available = True
        try:
            for token in generate_response_stream(self.stream_session, self.config, self.logger, user_query, top_docs):
                yield {"type": "token", "content": token}
        except Exception as e:
            self.logger.error(f"🚨 스트리밍 답변 생성 실패, 검색된 원문 자료로 대체합니다: {e}")
            fallback = build_fallback_answer(top_docs)
            yield {"type": "token", "content": fallback}
            llm_available = False

        latency = time.time() - start_time
        self.logger.info(f"[stream] 답변 생성 완료 (총 소요 시간: {latency:.2f}초, LLM 사용: {llm_available})")
        yield {"type": "done", "latency_sec": latency, "llm_available": llm_available}

    def close(self):
        self.session.close()
        self.stream_session.close()
        self.db_pool.closeall()
        self.logger.info("데이터베이스 커넥션 및 세션 종료 완료")