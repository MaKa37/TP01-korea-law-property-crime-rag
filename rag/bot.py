"""
rag/bot.py
==========
법률 RAG(Retrieval-Augmented Generation) 파이프라인 오케스트레이터.
- 멀티턴 맥락 기반 질의 재작성(Query Rewriting)
- 사건번호 직접 지목 시 DB 다이렉트 바이패스(0-Latency / 0-Token)
- 1차 하이브리드 검색(Dense Vector + Sparse Keyword) + 2차 Reranking
- 부모 원문(legal_documents) 1회 배치 조회를 통한 컨텍스트 오버라이딩
- SSE 실시간 스트리밍 및 엄격한 환각 검증(Grounding Check)
"""

import re
import time
from typing import Any, Dict, Iterator, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from core.config import RAGConfig
from core.logging import get_logger
from db.pool import create_db_pool
from rag.embedding import get_embedding
from rag.generator import build_fallback_answer, generate_response, generate_response_stream
from rag.grounding import check_grounding
from rag.reranker import rerank_candidates, select_diverse_top_k
from rag.retrieval import execute_hybrid_search, execute_keyword_search

# 질의 재작성 및 대화 히스토리 모듈 (선택적 임포트 및 안전 폴백)
try:
    from orchestration.query_rewriter import rewrite_query
except ImportError:
    rewrite_query = None

try:
    from orchestration.history import get_recent_history
except ImportError:
    get_recent_history = None


class LegalRAGBot:
    def __init__(self, config: RAGConfig):
        self.config = config
        self.logger = get_logger()
        self._validate_config()

        # 1. 고속 임베딩/리랭킹 전용 세션 (재시도 4회, 관대한 백오프)
        self.session = self._setup_session(total=4, backoff_factor=1.0)
        
        # 2. 실시간 채팅 생성 전용 세션 (빠른 실패 후 Fallback 유도)
        self.stream_session = self._setup_session(total=1, backoff_factor=0.5)
        
        # 3. PostgreSQL 커넥션 풀 초기화
        self.db_pool = create_db_pool(config, self.logger)

    def __enter__(self) -> "LegalRAGBot":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def _validate_config(self) -> None:
        if not self.config.nim_api_key:
            self.logger.warning("⚠️ NVIDIA_NIM_API_KEY가 비어 있습니다. API 호출이 인증 실패할 수 있습니다.")
        if self.config.db_pass == "postgres":
            self.logger.warning("⚠️ DB_PASSWORD가 기본값입니다. 운영 환경에서는 별도 환경변수를 설정하세요.")

    def _setup_session(self, total: int = 4, backoff_factor: float = 1.0) -> requests.Session:
        session = requests.Session()
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

    @staticmethod
    def _extract_case_number(text: str) -> Optional[str]:
        """
        사건번호 정규식 파서.
        1990년대 이전 2자리(98도231)부터 2000년대 이후 4자리(2024도7082)까지 완벽 추출.
        """
        if not text:
            return None
        match = re.search(r"\b\d{2,4}[가-힣]{1,4}\d+\b", text)
        return match.group(0) if match else None

    def _rewrite_query_if_needed(
        self,
        user_query: str,
        conversation_id: Optional[str] = None,
        history: Optional[List[Dict[str, str]]] = None
    ) -> str:
        """이전 대화 맥락이 있을 경우 LLM을 통해 검색 키워드를 정밀하게 재작성합니다."""
        if not rewrite_query:
            return user_query

        # 전달받은 히스토리가 없으면 저장소(Redis 등)에서 조회 시도
        conv_history = history
        if conv_history is None and conversation_id and get_recent_history:
            try:
                conv_history = get_recent_history(conversation_id)
            except Exception as e:
                self.logger.warning(f"대화 히스토리 로드 실패: {e}")
                conv_history = None

        if not conv_history:
            return user_query

        try:
            rewritten = rewrite_query(self.session, self.config, self.logger, user_query, conv_history)
            if rewritten and rewritten.strip():
                self.logger.info(f"🔄 질의 재작성 완료: '{user_query}' -> '{rewritten.strip()}'")
                return rewritten.strip()
        except Exception as e:
            self.logger.warning(f"질의 재작성 파이프라인 오류 (원본 질의 유지): {e}")

        return user_query

    def _fetch_direct_case_document(self, case_number: str) -> Optional[Dict[str, Any]]:
        """DB에서 특정 사건번호의 판례 원문을 즉시 조회합니다 (1:1 완전 일치)."""
        conn = None
        try:
            conn = self.db_pool.getconn()
            with conn.cursor() as cursor:
                query = """
                    SELECT prec_id, case_number, title, court_name, issue_date, 
                           ref_articles, ref_precedents, full_text
                    FROM legal_documents
                    WHERE case_number = %s
                    LIMIT 1;
                """
                cursor.execute(query, (case_number,))
                row = cursor.fetchone()
                if row:
                    return {
                        "prec_id": row[0],
                        "case_number": row[1],
                        "title": row[2],
                        "court_name": row[3],
                        "issue_date": row[4],
                        "ref_articles": row[5],
                        "ref_precedents": row[6],
                        "full_text": row[7]
                    }
        except Exception as e:
            self.logger.error(f"다이렉트 원문 쿼리 실행 실패 ({case_number}): {e}", exc_info=True)
        finally:
            if conn:
                self.db_pool.putconn(conn)
        return None

    def _enrich_with_full_documents(self, docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        검색된 청크 결과에 부모 테이블(legal_documents)의 판례 원문 전문을 결합하고,
        LLM 생성기가 청크가 아닌 원문 전체를 참조하도록 컨텍스트를 오버라이딩합니다.
        """
        if not docs:
            return docs

        lookup_keys = set()
        for doc in docs:
            case_no = doc.get("case_number") or doc.get("doc_id")
            if case_no:
                lookup_keys.add(str(case_no).strip())
            
            title = doc.get("title", "")
            extracted = self._extract_case_number(title)
            if extracted:
                lookup_keys.add(extracted)

        if not lookup_keys:
            for doc in docs:
                fallback = doc.get("content") or doc.get("chunk_text") or doc.get("text", "")
                doc["full_text"] = fallback
                doc["content"] = fallback
            return docs

        conn = None
        full_doc_map: Dict[str, Dict[str, Any]] = {}
        try:
            conn = self.db_pool.getconn()
            with conn.cursor() as cursor:
                query = """
                    SELECT prec_id, case_number, title, court_name, issue_date, 
                           ref_articles, ref_precedents, full_text
                    FROM legal_documents
                    WHERE case_number = ANY(%s) OR prec_id = ANY(%s);
                """
                keys_list = list(lookup_keys)
                cursor.execute(query, (keys_list, keys_list))
                rows = cursor.fetchall()
                for row in rows:
                    p_id, c_no, t_title, c_court, i_date, r_art, r_prec, f_text = row
                    data = {
                        "prec_id": p_id,
                        "case_number": c_no,
                        "title": t_title,
                        "court_name": c_court,
                        "issue_date": i_date,
                        "ref_articles": r_art,
                        "ref_precedents": r_prec,
                        "full_text": f_text,
                    }
                    if c_no: full_doc_map[c_no] = data
                    if p_id: full_doc_map[p_id] = data
        except Exception as e:
            self.logger.error(f"부모 원문 배치 조회 오류: {e}", exc_info=True)
        finally:
            if conn:
                self.db_pool.putconn(conn)

        for doc in docs:
            case_no = doc.get("case_number") or doc.get("doc_id")
            title = doc.get("title", "")
            matched_key = self._extract_case_number(title)

            matched_data = full_doc_map.get(case_no) or (full_doc_map.get(matched_key) if matched_key else None)
            if matched_data:
                full_text = matched_data["full_text"]
                doc["full_text"] = full_text
                # [핵심] LLM 생성기에 전달되는 컨텍스트를 원문 전문으로 덮어씀 (문맥 짤림 원천 차단)
                doc["content"] = full_text
                doc["chunk_text"] = full_text
                doc["case_number"] = matched_data.get("case_number") or doc.get("case_number", "")
                doc["title"] = matched_data.get("title") or doc.get("title", "")
                doc["court_name"] = matched_data.get("court_name") or doc.get("court_name", "")
                doc["issue_date"] = matched_data.get("issue_date") or doc.get("issue_date", "")
            else:
                fallback = doc.get("content") or doc.get("chunk_text") or doc.get("text", "")
                doc["full_text"] = fallback
                doc["content"] = fallback

        return docs

    def retrieve(self, search_query: str) -> List[Dict[str, Any]]:
        """하이브리드 검색, 리랭킹 및 부모 원문 결합 파이프라인을 실행합니다."""
        query_vector = get_embedding(self.session, self.config, self.logger, search_query)

        if query_vector:
            candidates = execute_hybrid_search(self.db_pool, self.config, search_query, query_vector)
            self.logger.info(f"1차 하이브리드 검색 완료: {len(candidates)}건 후보 도출")
        else:
            self.logger.warning("⚠️ 임베딩 실패로 키워드 단독 검색으로 우회합니다.")
            candidates = execute_keyword_search(self.db_pool, self.config, search_query)
            self.logger.info(f"1차 키워드 검색 완료: {len(candidates)}건 후보 도출")

        candidate_pool = rerank_candidates(self.session, self.config, self.logger, search_query, candidates)
        top_docs = select_diverse_top_k(candidate_pool, self.config.top_k, self.config.diversity_similarity_threshold)
        
        # 검색된 상위 청크들에 부모 원문 매핑
        enriched_docs = self._enrich_with_full_documents(top_docs)
        self.logger.info(f"2차 리랭킹 및 원문 결합 완료: 상위 {len(enriched_docs)}건 확정")
        return enriched_docs

    def ask(
        self,
        user_query: str,
        conversation_id: Optional[str] = None,
        history: Optional[List[Dict[str, str]]] = None
    ) -> Dict[str, Any]:
        """일반 동기식 API 호출용 메인 엔드포인트."""
        start_time = time.time()
        self.logger.info(f"[Sync] 질의 접수: '{user_query}'")

        # 1. 사건번호 직접 지목 시 Direct Bypass
        explicit_case_no = self._extract_case_number(user_query)
        if explicit_case_no and any(k in user_query for k in ["판례", "원문", "전문", "알려줘", "조회"]):
            direct_doc = self._fetch_direct_case_document(explicit_case_no)
            if direct_doc:
                self.logger.info(f"⚡ 특정 판례 다이렉트 반환: {explicit_case_no}")
                ans = f"### 🏛️ 판례 원문 조회 결과: [{direct_doc['case_number']}] {direct_doc['title']}\n\n{direct_doc['full_text']}"
                return {
                    "status": "success",
                    "answer": ans,
                    "retrieved_documents": [direct_doc],
                    "llm_available": False,
                    "latency_sec": time.time() - start_time
                }

        # 2. 질의 재작성 및 검색
        search_query = self._rewrite_query_if_needed(user_query, conversation_id, history)
        top_docs = self.retrieve(search_query)

        if not top_docs:
            return {
                "status": "no_results",
                "answer": "질문과 관련된 법령이나 판례를 찾지 못했습니다. 키워드를 구체화하여 다시 질문해 주세요.",
                "retrieved_documents": [],
                "llm_available": None,
                "latency_sec": time.time() - start_time
            }

        # 3. LLM 답변 생성 및 Grounding 검증
        llm_available = True
        try:
            answer = generate_response(self.stream_session, self.config, self.logger, user_query, top_docs)
        except Exception as e:
            self.logger.error(f"답변 생성 실패, 원문 폴백으로 대체: {e}")
            answer = build_fallback_answer(top_docs)
            llm_available = False

        if llm_available:
            is_grounded, details = check_grounding(answer, top_docs)
            if not is_grounded:
                self.logger.warning(
                    f"🚨 미검증 인용 감지 - 사건: {details.get('ungrounded_cases')}, 법조문: {details.get('ungrounded_statutes')} (원문 대체)"
                )
                answer = build_fallback_answer(top_docs)
                llm_available = False

        latency = time.time() - start_time
        return {
            "status": "success",
            "answer": answer,
            "retrieved_documents": top_docs,
            "llm_available": llm_available,
            "latency_sec": latency
        }

    def ask_stream(
        self,
        user_query: str,
        conversation_id: Optional[str] = None,
        history: Optional[List[Dict[str, str]]] = None
    ) -> Iterator[Dict[str, Any]]:
        """FastAPI SSE 스트리밍 전용 비동기 친화 제너레이터."""
        start_time = time.time()
        self.logger.info(f"[Stream] 질의 접수: '{user_query}' (Session: {conversation_id})")

        # 💡 [Track 1] 특정 사건번호 단독 지목 시 Direct Bypass
        explicit_case_no = self._extract_case_number(user_query)
        if explicit_case_no and any(k in user_query for k in ["판례", "원문", "전문", "알려줘", "조회"]):
            direct_doc = self._fetch_direct_case_document(explicit_case_no)
            if direct_doc:
                self.logger.info(f"⚡ [Bypass] 특정 판례 원문 다이렉트 출력: {explicit_case_no}")
                yield {
                    "type": "sources",
                    "documents": [{
                        "title": direct_doc["title"],
                        "case_number": direct_doc["case_number"],
                        "court_name": direct_doc.get("court_name", ""),
                        "issue_date": direct_doc.get("issue_date", ""),
                        "full_text": direct_doc["full_text"]
                    }]
                }
                direct_ans = f"### 🏛️ 판례 원문 직접 조회 결과: [{direct_doc['case_number']}] {direct_doc['title']}\n\n{direct_doc['full_text']}"
                chunk_size = 15
                for i in range(0, len(direct_ans), chunk_size):
                    yield {"type": "token", "content": direct_ans[i:i + chunk_size]}
                    time.sleep(0.005)
                
                yield {"type": "done", "latency_sec": time.time() - start_time, "llm_available": False}
                return

        # 💡 [Track 2] 일반 RAG 파이프라인 (질의 재작성 -> 검색 -> 원문 매핑 -> LLM 스트리밍)
        search_query = self._rewrite_query_if_needed(user_query, conversation_id, history)

        try:
            top_docs = self.retrieve(search_query)
        except Exception as e:
            self.logger.error(f"검색 단계 중 예외 발생: {e}", exc_info=True)
            yield {"type": "error", "message": str(e)}
            return

        if not top_docs:
            yield {
                "type": "no_results",
                "message": "질문과 관련된 판례나 법령을 찾지 못했습니다. 사건의 사실관계를 조금 더 구체적으로 작성해 주세요."
            }
            return

        # 1. UI 출처 토글/카드 렌더링용 sources 이벤트 발송
        yield {
            "type": "sources",
            "documents": [
                {
                    "title": d.get("title", "제목없음"),
                    "doc_type": d.get("doc_type", "판례"),
                    "case_number": d.get("case_number", ""),
                    "court_name": d.get("court_name", ""),
                    "issue_date": d.get("issue_date", ""),
                    "full_text": d.get("full_text", ""),
                    "rerank_score": d.get("rerank_score")
                }
                for d in top_docs
            ]
        }

        # 2. LLM 실시간 스트리밍 생성
        llm_available = True
        try:
            if self.config.stream_mode == "realtime":
                collected = []
                for token in generate_response_stream(self.stream_session, self.config, self.logger, user_query, top_docs):
                    collected.append(token)
                    yield {"type": "token", "content": token}

                # check_grounding 반환 형태: (bool, dict)
                is_grounded, details = check_grounding("".join(collected), top_docs)
                if not is_grounded:
                    self.logger.warning(
                        f"🚨 [Realtime] 미검증 인용 감지 - 사건: {details.get('ungrounded_cases')}, 법조문: {details.get('ungrounded_statutes')}"
                    )
                    llm_available = False
            else:
                full_answer = generate_response(self.stream_session, self.config, self.logger, user_query, top_docs)
                is_grounded, details = check_grounding(full_answer, top_docs)
                if not is_grounded:
                    self.logger.warning(
                        f"🚨 미검증 인용 감지 - 사건: {details.get('ungrounded_cases')}, 법조문: {details.get('ungrounded_statutes')} (원문 대체)"
                    )
                    full_answer = build_fallback_answer(top_docs)
                    llm_available = False

                chunk_size = 3
                for i in range(0, len(full_answer), chunk_size):
                    yield {"type": "token", "content": full_answer[i:i + chunk_size]}
                    time.sleep(0.015)

        except Exception as e:
            self.logger.error(f"🚨 LLM 답변 생성 오류 발생, 폴백 메시지 출력: {e}", exc_info=True)
            fallback = build_fallback_answer(top_docs)
            yield {"type": "token", "content": fallback}
            llm_available = False

        latency = time.time() - start_time
        self.logger.info(f"[Stream] 완료 (소요시간: {latency:.2f}초, LLM 사용: {llm_available})")
        yield {"type": "done", "latency_sec": latency, "llm_available": llm_available}

    def close(self) -> None:
        """자원 정리: HTTP 세션 및 DB 커넥션 풀 종료"""
        self.session.close()
        self.stream_session.close()
        self.db_pool.closeall()
        self.logger.info("데이터베이스 커넥션 풀 및 세션 정상 종료 완료")