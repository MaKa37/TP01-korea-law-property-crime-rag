"""오케스트레이션 계층: 세션 관리 + 가드레일 + 라우팅 + 질의 재작성을
RAG 파이프라인(LegalRAGBot) 앞단에 배치한다.

처리 순서 (중요, 이 순서를 바꾸지 말 것):
    1) 위기 신호 감지  — 항상 최우선. 라우팅/재작성보다 먼저 확인해서
       어떤 경우에도 법률 정보로 덮어쓰지 않는다.
    2) 의도 분류        — legal_query가 아니면 RAG 파이프라인 자체를 스킵.
    3) 질의 재작성       — 후속 질문을 독립적인 질문으로 변환.
    4) 기존 RAG 파이프라인(LegalRAGBot.ask_stream)에 위임.
"""
import time
from typing import Any, Dict, Iterator

from orchestration.guardrails import (
    CHITCHAT_RESPONSE,
    CRISIS_RESPONSE,
    OUT_OF_SCOPE_RESPONSE,
    detect_crisis,
)
from orchestration.query_rewriter import rewrite_query
from orchestration.router import classify_intent
from orchestration.session_store import InMemorySessionStore
from rag.bot import LegalRAGBot


class ChatOrchestrator:
    def __init__(self, bot: LegalRAGBot, session_store: InMemorySessionStore):
        self.bot = bot
        self.session_store = session_store

    def ask_stream(self, conversation_id: str, user_query: str) -> Iterator[Dict[str, Any]]:
        start_time = time.time()
        history = self.session_store.get_history(conversation_id)

        # 1) 위기 신호 감지 (최우선, 무조건 통과)
        if detect_crisis(user_query):
            self.bot.logger.warning(f"[orchestrator] 위기 신호 감지 (conversation_id={conversation_id})")
            yield {"type": "token", "content": CRISIS_RESPONSE}
            yield {
                "type": "done",
                "latency_sec": time.time() - start_time,
                "llm_available": None,
                "route": "crisis",
            }
            self.session_store.append(conversation_id, user_query, CRISIS_RESPONSE)
            return

        # 2) 의도 분류
        intent = classify_intent(self.bot.session, self.bot.config, self.bot.logger, user_query, history)
        self.bot.logger.info(f"[orchestrator] 라우팅 결과: {intent} (conversation_id={conversation_id})")

        if intent == "chitchat":
            yield {"type": "token", "content": CHITCHAT_RESPONSE}
            yield {"type": "done", "latency_sec": time.time() - start_time, "llm_available": None, "route": "chitchat"}
            self.session_store.append(conversation_id, user_query, CHITCHAT_RESPONSE)
            return

        if intent == "out_of_scope":
            yield {"type": "token", "content": OUT_OF_SCOPE_RESPONSE}
            yield {"type": "done", "latency_sec": time.time() - start_time, "llm_available": None, "route": "out_of_scope"}
            self.session_store.append(conversation_id, user_query, OUT_OF_SCOPE_RESPONSE)
            return

        # 3) 질의 재작성 (멀티턴 대응)
        standalone_query = rewrite_query(self.bot.session, self.bot.config, self.bot.logger, history, user_query)
        if standalone_query != user_query:
            self.bot.logger.info(f"[orchestrator] 질의 재작성: '{user_query}' -> '{standalone_query}'")
            yield {"type": "rewritten_query", "original": user_query, "query": standalone_query}

        # 4) 기존 RAG 파이프라인에 위임 (로직 변경 없음, 그대로 재사용)
        collected_answer = []
        for event in self.bot.ask_stream(standalone_query):
            if event.get("type") == "token":
                collected_answer.append(event["content"])
            if event.get("type") == "done":
                event = {**event, "route": "legal_query"}
            yield event

        self.session_store.append(conversation_id, user_query, "".join(collected_answer))
