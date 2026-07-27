import os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base

# 로컬 PostgreSQL 연결 문자열 (asyncpg 드라이버 사용)
# 형식: postgresql+asyncpg://사용자명:비밀번호@호스트:포트/데이터베이스명
DATABASE_URL = os.getenv(
    "DATABASE_URL", 
    "postgresql+asyncpg://postgres:password@localhost:5432/legal_rag"
)

# 비동기 엔진 생성
engine = create_async_engine(DATABASE_URL, echo=False)

# 비동기 세션 팩토리 생성 (외부 스레드/백그라운드 태스크용)
async_session_maker = async_sessionmaker(
    engine, 
    class_=AsyncSession, 
    expire_on_commit=False,
    autoflush=False
)

# ORM 모델의 기본 클래스
Base = declarative_base()

# FastAPI 의존성 주입을 위한 세션 제너레이터 함수
async def get_db():
    async with async_session_maker() as session:
        try:
            yield session
        finally:
            await session.close()