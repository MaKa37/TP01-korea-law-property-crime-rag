import os
import json
import logging
import time
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry
from dotenv import load_dotenv

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

# =====================================================================
# [0] .env 로드 (반드시 파일 최상단, 모든 클래스 정의보다 먼저!)
# =====================================================================
# ⚠️ 중요: dataclass 필드의 기본값(os.getenv(...))은 "클래스가 정의되는
# 시점"(= 모듈이 import 될 때)에 즉시 평가됩니다.
# 기존 코드처럼 load_dotenv()를 `if __name__ == "__main__":` 블록 안에서
# 호출하면, RAGConfig 클래스 정의(파일 상단)가 이미 끝난 뒤에 .env가
# 로드되기 때문에 API 키/URL 등 모든 설정이 .env 값을 반영하지 못하고
# 빈 문자열이나 하드코딩된 기본값으로 고정되어 버립니다.
# 첨부하신 로그의 embedding/chat 500 에러는 대부분 이 문제 때문일
# 가능성이 매우 높습니다. -> load_dotenv()를 클래스 정의보다 먼저 실행.
load_dotenv()


# =====================================================================
# [1] Configuration (설정 관리)
# =====================================================================
@dataclass
class RAGConfig:
    # DB Settings
    db_host: str = os.getenv("DB_HOST", "127.0.0.1")
    db_port: int = int(os.getenv("DB_PORT", "5432"))
    db_name: str = os.getenv("DB_NAME", "postgres")
    db_user: str = os.getenv("DB_USER", "postgres")
    db_pass: str = os.getenv("DB_PASSWORD", "postgres")
    db_pool_min: int = 1
    db_pool_max: int = 10

    # NVIDIA API Settings
    nim_api_key: str = os.getenv("NVIDIA_NIM_API_KEY", "")
    embed_url: str = os.getenv("EMBEDDING_URL", "https://integrate.api.nvidia.com/v1/embeddings")
    rerank_url: str = os.getenv("RERANK_URL", "https://ai.api.nvidia.com/v1/retrieval/nvidia/reranking")
    chat_url: str = os.getenv("CHAT_URL", "https://integrate.api.nvidia.com/v1/chat/completions")

    # Models
    embed_model: str = os.getenv("EMBEDDING_MODEL", "nvidia/nemotron-3-embed-1b")
    rerank_model: str = os.getenv("RERANK_MODEL", "nvidia/rerank-qa-mistral-4b")
    chat_model: str = os.getenv("CHAT_MODEL", "meta/llama-3.1-8b-instruct")

    # Retrieval Settings
    rrf_k: int = int(os.getenv("RRF_K", "60"))
    top_k: int = int(os.getenv("TOP_K", "5"))
    candidate_k: int = int(os.getenv("CANDIDATE_K", "30"))

    # Timeout Settings (초 단위, 하드코딩된 매직넘버 제거)
    # connect_timeout: 서버와 연결이 수립되기까지 대기 시간
    # embed/rerank_timeout: 응답 전체를 기다리는 일반 timeout
    # chat_timeout: 스트리밍 모드에서 "토큰 간 대기 시간"(전체 생성 시간이 아님).
    #   nemotron-3-ultra-550b 같은 대형 모델은 전체 생성에 수십 초~수 분이
    #   걸릴 수 있으므로, 응답을 스트리밍으로 받아 "토큰이 끊기지 않는 한"
    #   타임아웃이 나지 않도록 처리한다.
    connect_timeout: int = int(os.getenv("CONNECT_TIMEOUT", "10"))
    embed_timeout: int = int(os.getenv("EMBED_TIMEOUT", "15"))
    rerank_timeout: int = int(os.getenv("RERANK_TIMEOUT", "20"))
    chat_timeout: int = int(os.getenv("CHAT_TIMEOUT", "60"))

    # Generation Settings
    # ⚠️ SYSTEM_PROMPT는 "관련 법령 해설 -> 유사 판례 요약 -> 피해자 조치 절차 ->
    # 면책 조항"까지 4개 섹션을 요구하는 긴 구조라, max_tokens=2048로는
    # 마지막 섹션(면책 조항 포함)이 완성되기 전에 출력이 잘릴 수 있다.
    # 실제로 이전 실행에서 조치 절차 중간, 면책 조항이 나오기도 전에 답변이
    # 끊긴 사례가 있었다. 여유 있게 상향.
    max_tokens: int = int(os.getenv("MAX_TOKENS", "4096"))
    # 스트리밍 중 콘솔에 토큰을 실시간으로 출력할지 여부 (긴 생성 시간 동안
    # "멈춘 것처럼 보이는" 문제를 완화)
    stream_print: bool = os.getenv("STREAM_PRINT", "true").lower() == "true"


# =====================================================================
# [2] Prompts (프롬프트 관리)
# =====================================================================
SYSTEM_PROMPT = """당신은 사기 및 재산범죄 피해자를 돕는 전문적이고 객관적인 '법률 AI 어시스턴트'입니다. 
제공된 [검색된 참고 자료]만을 바탕으로 사용자의 질문에 답변해야 하며, 정보가 부족한 경우 임의로 지어내지 마십시오.

답변은 반드시 아래의 3단계 구조(마크다운 형식)를 엄격하게 지켜 작성하십시오.

## 1. 관련 법령 해설
* 사용자의 상황에 적용되는 핵심 법령(형법, 특정경제범죄법 등)과 조문을 명시합니다.
* 범죄 성립 요건(예: 기망행위, 불법영득의사 등)을 일반인이 이해하기 쉽게 풀어서 설명하십시오.
* (주의) "귀하의 경우 ~죄가 성립합니다"라는 단정적 표현은 피하고, "~할 경우 ~죄가 성립할 수 있습니다"와 같이 객관적인 가정법을 사용하십시오.

## 2. 유사 판례 요약
* [검색된 참고 자료] 중 가장 유사한 판례를 찾아 ① 핵심 사실관계, ② 법원의 판단 기준, ③ 최종 결론으로 나누어 요약하십시오.
* (주의) 판례 원문에 등장하는 `(적극)`은 "인정됨/성립함"으로, `(소극)`은 "부정됨/성립하지 않음"으로 반드시 순화하여 일반적인 문장으로 작성하십시오.

## 3. 피해자 조치 절차 (Action Plan)
* 현재 사용자가 취할 수 있는 현실적인 행동 지침을 단계별로 제시합니다.
* 증거 수집 안내 시, 해당 범죄 요건(고의성, 기망 등)을 입증하기 위해 구체적으로 어떤 자료(내용증명, 계좌내역, 회의록 등)가 필요한지 명시하십시오.
* 범죄 성격에 따라 피해 주체가 '개인'인지 '법인(회사)'인지 구분하여 적절한 법적 조치(형사고소, 가압류, 주주대표소송 등)를 안내하십시오.
* 마지막에는 반드시 아래의 면책 조항을 정확히 출력하십시오.
> ⚠️ **면책 조항:** 본 답변은 제공된 법령과 판례를 바탕으로 한 참고용 정보이며, 법적 효력을 갖는 전문적인 법률 감정이 아닙니다. 구체적인 사실관계에 따라 법적 판단이 달라질 수 있으므로, 실제 법적 조치를 취하기 전 반드시 대한법률구조공단(국번없이 132)이나 전문 변호사의 상담을 받으시기 바랍니다.
"""

# =====================================================================
# [3] RAG Engine (핵심 엔진)
# =====================================================================
class LegalRAGBot:
    def __init__(self, config: RAGConfig):
        self.config = config
        self.logger = self._setup_logger()
        self._validate_config()
        self.session = self._setup_session()
        self.db_pool = self._setup_db_pool()

    # -----------------------------------------------------------------
    # Context manager 지원 (with 문으로 사용 시 자동 close())
    # -----------------------------------------------------------------
    def __enter__(self) -> "LegalRAGBot":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def _setup_logger(self) -> logging.Logger:
        logger = logging.getLogger("LegalRAGBot")
        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter('[%(levelname)s] %(asctime)s - %(message)s')
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
        return logger

    def _validate_config(self) -> None:
        """필수 설정값 누락을 초기화 시점에 조기 경고 (원인 불명 500 에러 방지)"""
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
        """API 호출 안정성을 위한 Retry 적용 세션 생성"""
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

    def _setup_db_pool(self) -> ThreadedConnectionPool:
        """PostgreSQL 커넥션 풀 초기화"""
        try:
            pool = ThreadedConnectionPool(
                minconn=self.config.db_pool_min,
                maxconn=self.config.db_pool_max,
                host=self.config.db_host,
                port=self.config.db_port,
                dbname=self.config.db_name,
                user=self.config.db_user,
                password=self.config.db_pass
            )
            self.logger.info("✅ PostgreSQL 커넥션 풀 초기화 완료")
            return pool
        except psycopg2.OperationalError as e:
            self.logger.error(f"🚨 데이터베이스 연결 실패: {e}")
            raise

    def _get_embedding(self, text: str) -> Optional[List[float]]:
        """텍스트를 임베딩 벡터로 변환 (장애 발생 시 None 반환)"""
        # Nemotron-1B 스펙시트 권장에 따라 'query: ' 프리픽스 추가
        prefixed_text = f"query: {text}"

        payload = {
            "input": [prefixed_text],
            "model": self.config.embed_model,
            "input_type": "query",
            "encoding_format": "float"
        }

        try:
            resp = self.session.post(
                self.config.embed_url,
                json=payload,
                timeout=(self.config.connect_timeout, self.config.embed_timeout)
            )
            resp.raise_for_status()
            return resp.json()["data"][0]["embedding"]
        except requests.exceptions.RequestException as e:
            self.logger.error(f"🚨 NVIDIA 임베딩 API 서버 장애 발생 (Fallback 전환): {e}")
            return None
        except (KeyError, IndexError, ValueError) as e:
            self.logger.error(f"🚨 임베딩 API 응답 형식 오류 (Fallback 전환): {e}")
            return None

    def _execute_hybrid_search(self, query: str, vector: List[float]) -> List[Dict[str, Any]]:
        """PGVector 기반 BM25 + Vector 하이브리드 검색 (RRF 적용) 및 노이즈 필터링"""
        vector_str = str(vector)

        filter_conditions = """
            AND NOT (doc_type = 'prec' AND content ~ '\\[판결요지\\]\\s*$' AND length(content) < 150)
            AND NOT (doc_type = 'lstrm' AND title ~ '^법령용어: (대통령령|총리령|부령|[가-힣]+부령|[가-힣]+령)으로 정하는')
            AND NOT (doc_type = 'lstrm' AND content ~ '출처:\\s*$')
        """

        sql = f"""
        WITH vector_search AS (
            SELECT chunk_id, ROW_NUMBER() OVER (ORDER BY embedding <=> %s::halfvec ASC) AS rank
            FROM legal_chunks
            WHERE embedding IS NOT NULL {filter_conditions}
            LIMIT 50
        ),
        text_search AS (
            SELECT chunk_id, ROW_NUMBER() OVER (ORDER BY similarity(title || ' ' || content, %s) DESC) AS rank
            FROM legal_chunks
            WHERE (title || ' ' || content) %% %s {filter_conditions}
            LIMIT 50
        ),
        combined AS (
            SELECT COALESCE(v.chunk_id, t.chunk_id) AS chunk_id,
                   (COALESCE(1.0 / ({self.config.rrf_k} + v.rank), 0.0) + 
                    COALESCE(1.0 / ({self.config.rrf_k} + t.rank), 0.0)) AS rrf_score
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

        conn = self.db_pool.getconn()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SET local enable_seqscan = off;")
                cur.execute("SET local pg_trgm.similarity_threshold = 0.35;")
                cur.execute(sql, (vector_str, query, query, self.config.candidate_k))
                return cur.fetchall()
        finally:
            conn.rollback()
            self.db_pool.putconn(conn)

    def _execute_keyword_search(self, query: str) -> List[Dict[str, Any]]:
        """임베딩 서버 장애 시 대체되는 키워드(Trigram) 단독 검색 로직"""
        filter_conditions = """
            AND NOT (doc_type = 'prec' AND content ~ '\\[판결요지\\]\\s*$' AND length(content) < 150)
            AND NOT (doc_type = 'lstrm' AND title ~ '^법령용어: (대통령령|총리령|부령|[가-힣]+부령|[가-힣]+령)으로 정하는')
            AND NOT (doc_type = 'lstrm' AND content ~ '출처:\\s*$')
        """

        sql = f"""
        WITH text_search AS (
            SELECT chunk_id, ROW_NUMBER() OVER (ORDER BY similarity(title || ' ' || content, %s) DESC) AS rank
            FROM legal_chunks
            WHERE (title || ' ' || content) %% %s {filter_conditions}
            LIMIT %s
        )
        SELECT t.chunk_id, lc.title, lc.content, lc.doc_type
        FROM text_search t
        JOIN legal_chunks lc ON t.chunk_id = lc.chunk_id
        ORDER BY t.rank ASC;
        """

        conn = self.db_pool.getconn()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SET local enable_seqscan = off;")
                cur.execute("SET local pg_trgm.similarity_threshold = 0.35;")
                cur.execute(sql, (query, query, self.config.candidate_k))
                return cur.fetchall()
        finally:
            conn.rollback()
            self.db_pool.putconn(conn)

    def _rerank_candidates(self, query: str, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """검색된 후보들을 Reranker 모델로 재정렬"""
        if not candidates:
            return []

        passages = [{"text": f"[{c['doc_type']}] {c['title']} {c['content']}"} for c in candidates]
        payload = {
            "model": self.config.rerank_model,
            "query": {"text": query},
            "passages": passages
        }

        try:
            resp = self.session.post(
                self.config.rerank_url,
                json=payload,
                timeout=(self.config.connect_timeout, self.config.rerank_timeout)
            )
            resp.raise_for_status()
            rankings = resp.json().get("rankings", [])

            reranked_docs = []
            for rank in rankings[:self.config.top_k]:
                idx = rank["index"]
                doc = candidates[idx]
                doc["rerank_score"] = rank.get("logit")
                reranked_docs.append(doc)
            return reranked_docs
        except Exception as e:
            self.logger.warning(f"Reranker 실패. 이전 검색 결과 유지: {e}")
            return candidates[:self.config.top_k]

    def _generate_response(self, query: str, retrieved_docs: List[Dict[str, Any]]) -> str:
        """검색된 문서를 바탕으로 LLM을 호출하여 최종 답변 생성. 실패 시 예외를 발생시킨다.

        550B급 대형 모델(nemotron-3-ultra 등)은 전체 응답 생성에 수십 초~수 분이
        걸릴 수 있다. 응답을 통째로 기다리면 고정된 read timeout에 쉽게 걸리므로,
        stream=True로 SSE(Server-Sent Events)를 받아 "토큰이 끊기지 않는 한"
        타임아웃이 나지 않도록 처리한다. timeout=(connect, read)에서 read는
        전체 생성 시간이 아니라 "다음 청크가 올 때까지의 대기 시간"이다.
        """
        context_str = "\n\n".join(
            f"[문서 {i+1}] (출처: {doc['title']})\n{doc['content']}"
            for i, doc in enumerate(retrieved_docs)
        )

        user_content = f"[검색된 참고 자료]\n{context_str}\n\n[사용자 질문]\n{query}"

        payload = {
            "model": self.config.chat_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content}
            ],
            "temperature": 0.1,
            "max_tokens": self.config.max_tokens,
            "stream": True
        }

        try:
            resp = self.session.post(
                self.config.chat_url,
                json=payload,
                timeout=(self.config.connect_timeout, self.config.chat_timeout),
                stream=True
            )
            resp.raise_for_status()

            collected: List[str] = []
            finish_reason: Optional[str] = None
            if self.config.stream_print:
                print("\n🤖 [AI 어시스턴트 답변] (실시간 생성 중)\n", flush=True)

            for raw_line in resp.iter_lines(decode_unicode=True):
                if not raw_line or not raw_line.startswith("data:"):
                    continue
                data_str = raw_line[len("data:"):].strip()
                if data_str == "[DONE]":
                    break
                try:
                    event = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                choices = event.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta", {})
                token = delta.get("content")
                if token:
                    collected.append(token)
                    if self.config.stream_print:
                        print(token, end="", flush=True)
                reason = choices[0].get("finish_reason")
                if reason:
                    finish_reason = reason

            if self.config.stream_print:
                print()  # 스트리밍 출력 후 줄바꿈

            answer = "".join(collected).strip()
            if not answer:
                raise RuntimeError("스트리밍 응답에서 콘텐츠를 받지 못했습니다.")

            if finish_reason == "length":
                self.logger.warning(
                    f"⚠️ max_tokens({self.config.max_tokens})에 도달하여 답변이 중간에 "
                    "잘렸을 수 있습니다. MAX_TOKENS 값을 늘리는 것을 고려하세요."
                )

            return answer
        except requests.exceptions.RequestException as e:
            self.logger.error(f"🚨 NVIDIA Chat Completion API 호출 실패: {e}")
            raise RuntimeError(f"답변 생성 LLM 호출에 실패했습니다: {e}") from e
        except (KeyError, IndexError, ValueError) as e:
            self.logger.error(f"🚨 LLM 응답 파싱 실패: {e}")
            raise RuntimeError(f"LLM 응답 형식이 올바르지 않습니다: {e}") from e

    def _build_fallback_answer(self, retrieved_docs: List[Dict[str, Any]]) -> str:
        """LLM 호출이 실패했을 때, 검색된 원문 자료라도 정리해서 보여주는 대체 답변"""
        lines = [
            "⚠️ 현재 AI 답변 생성 서비스에 일시적인 장애가 발생하여, "
            "검색된 참고 자료 원문을 대신 안내해 드립니다.\n"
        ]
        for i, doc in enumerate(retrieved_docs, 1):
            content = doc["content"]
            snippet = content if len(content) <= 500 else content[:500] + "..."
            lines.append(f"### {i}. {doc['title']} ({doc.get('doc_type', '')})")
            lines.append(snippet)
            lines.append("")
        lines.append(
            "> ⚠️ **면책 조항:** 위 자료는 AI 요약 없이 제공되는 원문 발췌이며, "
            "법적 조언이 아닙니다. 정확한 판단을 위해 대한법률구조공단(국번없이 132)이나 "
            "전문 변호사의 상담을 받으시기 바랍니다."
        )
        return "\n".join(lines)

    def retrieve(self, user_query: str) -> List[Dict[str, Any]]:
        """검색(임베딩→하이브리드/키워드 검색→리랭킹)까지만 수행하고 LLM 생성은 하지 않는다.

        평가 하네스(evaluation/)에서 이 메서드를 재사용한다. 골든셋으로 검색
        품질(Recall@k, MRR)을 측정할 때 매번 LLM까지 호출하면 느리고 비용도
        크므로, 검색 단계만 따로 노출한다.
        """
        query_vector = self._get_embedding(user_query)

        if query_vector:
            candidates = self._execute_hybrid_search(user_query, query_vector)
            self.logger.info(f"1차 하이브리드 검색 완료: {len(candidates)}건 후보 도출")
        else:
            self.logger.warning("⚠️ 임베딩 API 응답 실패. '텍스트 키워드 단독 검색'으로 우회합니다.")
            candidates = self._execute_keyword_search(user_query)
            self.logger.info(f"1차 키워드 검색 완료: {len(candidates)}건 후보 도출")

        top_docs = self._rerank_candidates(user_query, candidates)
        self.logger.info(f"2차 Reranking 완료: 상위 {len(top_docs)}건 확정")
        return top_docs

    def ask(self, user_query: str) -> Dict[str, Any]:
        """사용자 질의를 받아 검색부터 답변 생성까지 파이프라인 전체를 실행하는 메인 메서드"""
        start_time = time.time()
        self.logger.info(f"사용자 질의 접수: '{user_query}'")

        try:
            top_docs = self.retrieve(user_query)

            # 후보가 전혀 없으면 LLM을 호출하지 않고 즉시 안내
            #      (근거 없는 자료로 LLM이 답변을 지어내는 것을 방지)
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

            # 4. Generation (LLM 장애 시에도 검색된 원문은 살려서 응답)
            try:
                answer = self._generate_response(user_query, top_docs)
                llm_available = True
            except Exception as e:
                self.logger.error(f"🚨 답변 생성 실패, 검색된 원문 자료로 대체합니다: {e}")
                answer = self._build_fallback_answer(top_docs)
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
        """할당된 리소스 반환"""
        self.session.close()
        self.db_pool.closeall()
        self.logger.info("데이터베이스 커넥션 및 세션 종료 완료")


# =====================================================================
# [4] Usage Example (실행 예시)
# =====================================================================
if __name__ == "__main__":
    # load_dotenv()는 파일 최상단([0] 섹션)에서 이미 호출되었습니다.
    config = RAGConfig()

    # with 문 사용 시 예외가 발생해도 close()가 항상 호출됩니다.
    with LegalRAGBot(config) as rag_bot:
        # 테스트 질의 (구체적 사실관계를 포함한 하드케이스)
        test_query = (
            "회사의 이사가 채무변제능력을 상실한 타인에게 충분한 담보 없이 만연히 회사자금을 "
            "대여해 준 경우 업무상배임죄가 성립하는지, 그리고 용도가 엄격히 제한된 위탁 자금을 "
            "다른 목적으로 사용한 경우 횡령죄가 성립하는지 다룬 판례의 판시사항은?"
        )

        print("질의를 분석 중입니다. 잠시만 기다려주세요...\n" + "-" * 50)

        result = rag_bot.ask(test_query)

        if result["status"] == "success":
            already_streamed = result.get("llm_available") is True and config.stream_print
            mode_tag = " (원문 대체 모드)" if result.get("llm_available") is False else ""
            if not already_streamed:
                print(f"\n🤖 [AI 어시스턴트 답변]{mode_tag}")
                print(result["answer"])
            print("\n" + "-" * 50)
            print(f"⏱️ 소요 시간: {result['latency_sec']:.2f}초")
            print("📑 [참조된 핵심 문서 Top 3]")
            for i, doc in enumerate(result["retrieved_documents"][:3], 1):
                print(f"  {i}. {doc['title']} (Score: {doc.get('rerank_score', 'N/A')})")
        elif result["status"] == "no_results":
            print("\nℹ️ " + result["answer"])
        else:
            print(f"\n❌ 오류 발생: {result['error_message']}")