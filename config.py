import os
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 프로젝트 루트 경로 내 .env 파일 자동 탐색
BASE_DIR = Path(__file__).resolve().parent.parent if Path(__file__).resolve().parent.name == "src" else Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # API Keys
    LAW_OPEN_API_KEY: str = Field(default="foodgoon1")
    NVIDIA_NIM_API_KEY: str = Field(default="")

    # DB Configs
    DB_NAME: str = Field(default="legal_pdrag")
    DB_USER: str = Field(default="postgres")
    DB_PASSWORD: str = Field(default="00000000")
    DB_HOST: str = Field(default="127.0.0.1")
    DB_PORT: int = Field(default=5432)
    COPY_BATCH_SIZE: int = Field(default=20000)

    # NVIDIA Model Configs
    CHAT_URL: str = Field(default="https://integrate.api.nvidia.com/v1/chat/completions")
    EMBEDDING_URL: str = Field(default="https://integrate.api.nvidia.com/v1/embeddings")
    CHAT_MODEL: str = Field(default="nvidia/nemotron-3-ultra-550b-a55b")
    EMBEDDING_MODEL: str = Field(default="nvidia/nv-embedqa-e5-v5")
    NVIDIA_BASE_URL: str = Field(default="https://integrate.api.nvidia.com/v1")

    # Vector RAG Configs
    EMBEDDING_DIM: int = Field(default=1536)
    TOP_K: int = Field(default=5)

    # ------------------------------------------------------------------
    # Dynamic Properties (DB URL 동적 생성으로 불일치 방지)
    # ------------------------------------------------------------------
    @property
    def asyncpg_url(self) -> str:
        """asyncpg 전용 비동기 PostgreSQL 접속 URI"""
        return f"postgres://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

    @property
    def database_url(self) -> str:
        """SQLAlchemy 전용 PostgreSQL 접속 URI"""
        return f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

    # 기존 RAGSystem 호환성을 위한 소문자 속성 래퍼
    @property
    def chat_model(self) -> str:
        return self.CHAT_MODEL

    @property
    def embedding_model(self) -> str:
        return self.EMBEDDING_MODEL

config = Settings()