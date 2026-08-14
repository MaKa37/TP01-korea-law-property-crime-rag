"""FastAPI 앱 진입점.

실행:
    uvicorn app.main:app --reload --port 8000
"""
import os
from contextlib import asynccontextmanager
from typing import List

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.api.routes import chat, health
from app.auth import API_KEYS
from app.middleware import RequestLoggingMiddleware
from app.rate_limit import limiter
from core.config import RAGConfig
from core.logging import get_logger
from orchestration.orchestrator import ChatOrchestrator
from orchestration.session_store import create_session_store
from rag.bot import LegalRAGBot


def _parse_cors_origins() -> List[str]:
    """CORS_ALLOWED_ORIGINS(콤마 구분)를 파싱한다.

    ⚠️ 미설정 시 빈 리스트를 반환한다 = 브라우저发 크로스 오리진 요청을
    전부 차단하는 게 기본값이다("*" 전체 허용이 기본값이면 안 됨).
    curl/requests 같은 비-브라우저 클라이언트는 CORS 자체의 영향을
    받지 않으므로(브라우저가 강제하는 정책), 지금까지 해온 API 직접
    호출 테스트는 이 설정과 무관하게 계속 동작한다.
    """
    raw = os.getenv("CORS_ALLOWED_ORIGINS", "")
    return [o.strip() for o in raw.split(",") if o.strip()]


CORS_ALLOWED_ORIGINS = _parse_cors_origins()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 요청마다 DB 풀/세션을 새로 만들지 않도록, 앱 시작 시 한 번만 생성해서
    # app.state에 보관하고 전체 요청이 공유한다.
    config = RAGConfig()
    bot = LegalRAGBot(config)
    session_store = create_session_store(config, bot.logger)

    app.state.bot = bot
    app.state.orchestrator = ChatOrchestrator(bot, session_store)

    logger = get_logger()

    if not API_KEYS:
        logger.warning(
            "⚠️ API_KEYS가 설정되지 않아 인증이 비활성화된 상태입니다. "
            "로컬 개발 중에는 괜찮지만, 외부에 배포하기 전에는 반드시 "
            ".env에 API_KEYS를 설정하세요."
        )

    if not CORS_ALLOWED_ORIGINS:
        logger.warning(
            "⚠️ CORS_ALLOWED_ORIGINS가 설정되지 않아 브라우저 기반 크로스 "
            "오리진 요청이 전부 차단됩니다. 프론트엔드 도메인이 정해지면 "
            ".env에 CORS_ALLOWED_ORIGINS=https://your-frontend.com 형태로 "
            "추가하세요. (curl/requests 등 비-브라우저 클라이언트는 영향 없음)"
        )

    yield
    bot.close()


app = FastAPI(
    title="법률 AI 어시스턴트 API",
    description="사기·재산범죄 피해자를 위한 법률 정보 RAG 챗봇 API",
    version="0.1.0",
    lifespan=lifespan,
)

# 요청 ID 발급 + 요청/응답 로깅 (다른 미들웨어보다 먼저 등록해서 가장 바깥에서 감싸도록 함)
app.add_middleware(RequestLoggingMiddleware)

# 레이트리밋: limiter를 app.state에 연결하고, 초과 시 429를 반환하는
# 핸들러와 미들웨어를 등록한다. 실제 제한 값은 각 라우트에서
# @limiter.limit(...) 데코레이터로 지정한다 (app/rate_limit.py 참고).
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# CORS_ALLOWED_ORIGINS를 .env로 설정한 도메인만 허용한다 (기본값: 전부 차단).
# 메서드/헤더도 "*"가 아니라 실제로 쓰는 것만 명시해서 공격 표면을 줄인다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_credentials=False,  # 쿠키 대신 X-API-Key 헤더로 인증하므로 불필요
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-API-Key"],
)

app.include_router(health.router)
app.include_router(chat.router)