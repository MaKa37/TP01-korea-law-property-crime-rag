"""FastAPI 앱 진입점.

실행:
    uvicorn app.main:app --reload --port 8000
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import chat, health
from core.config import RAGConfig
from orchestration.orchestrator import ChatOrchestrator
from orchestration.session_store import InMemorySessionStore
from rag.bot import LegalRAGBot


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 요청마다 DB 풀/세션을 새로 만들지 않도록, 앱 시작 시 한 번만 생성해서
    # app.state에 보관하고 전체 요청이 공유한다.
    config = RAGConfig()
    bot = LegalRAGBot(config)
    session_store = InMemorySessionStore(max_turns=config.session_max_turns)

    app.state.bot = bot
    app.state.orchestrator = ChatOrchestrator(bot, session_store)

    yield
    bot.close()


app = FastAPI(
    title="법률 AI 어시스턴트 API",
    description="사기·재산범죄 피해자를 위한 법률 정보 RAG 챗봇 API",
    version="0.1.0",
    lifespan=lifespan,
)

# ⚠️ 개발 편의를 위한 전체 허용 설정. 운영 배포 시 실제 프론트엔드
# 도메인으로 allow_origins를 반드시 제한할 것.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(chat.router)
