"""FastAPI 앱 진입점.

실행:
    uvicorn app.main:app --reload --port 8000
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.api.routes import chat, health
from app.auth import API_KEYS
from app.rate_limit import limiter
from core.config import RAGConfig
from core.logging import get_logger
from orchestration.orchestrator import ChatOrchestrator
from orchestration.session_store import create_session_store
from rag.bot import LegalRAGBot


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 요청마다 DB 풀/세션을 새로 만들지 않도록, 앱 시작 시 한 번만 생성해서
    # app.state에 보관하고 전체 요청이 공유한다.
    config = RAGConfig()
    bot = LegalRAGBot(config)
    session_store = create_session_store(config, bot.logger)

    app.state.bot = bot
    app.state.orchestrator = ChatOrchestrator(bot, session_store)

    if not API_KEYS:
        get_logger().warning(
            "⚠️ API_KEYS가 설정되지 않아 인증이 비활성화된 상태입니다. "
            "로컬 개발 중에는 괜찮지만, 외부에 배포하기 전에는 반드시 "
            ".env에 API_KEYS를 설정하세요."
        )

    yield
    bot.close()


app = FastAPI(
    title="법률 AI 어시스턴트 API",
    description="사기·재산범죄 피해자를 위한 법률 정보 RAG 챗봇 API",
    version="0.1.0",
    lifespan=lifespan,
)

# 레이트리밋: limiter를 app.state에 연결하고, 초과 시 429를 반환하는
# 핸들러와 미들웨어를 등록한다. 실제 제한 값은 각 라우트에서
# @limiter.limit(...) 데코레이터로 지정한다 (app/rate_limit.py 참고).
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

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