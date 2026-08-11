from fastapi import Request

from orchestration.orchestrator import ChatOrchestrator
from rag.bot import LegalRAGBot


def get_bot(request: Request) -> LegalRAGBot:
    """앱 시작 시 lifespan에서 만든 LegalRAGBot 싱글턴을 반환한다.

    요청마다 DB 풀/세션을 새로 만들지 않도록 앱 전역에서 하나만 유지한다.
    """
    return request.app.state.bot


def get_orchestrator(request: Request) -> ChatOrchestrator:
    """세션/라우팅/재작성/가드레일을 포함하는 오케스트레이터를 반환한다."""
    return request.app.state.orchestrator
