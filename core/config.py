"""전역 설정 관리 모듈."""

import os
from dataclasses import dataclass, field
from typing import Literal

from dotenv import load_dotenv

load_dotenv()


def _get_env_str(key: str, default: str) -> str:
    val = os.getenv(key)
    return default if val is None or val.strip() == "" else val.strip()


def _get_env_int(key: str, default: int) -> int:
    val = os.getenv(key)
    if val is None or val.strip() == "":
        return default
    try:
        return int(val.strip())
    except ValueError:
        return default


def _get_env_float(key: str, default: float) -> float:
    val = os.getenv(key)
    if val is None or val.strip() == "":
        return default
    try:
        return float(val.strip())
    except ValueError:
        return default


def _get_env_bool(key: str, default: bool) -> bool:
    val = os.getenv(key)
    if val is None or val.strip() == "":
        return default
    return val.strip().lower() in ("true", "1", "t", "yes", "y")


@dataclass
class RAGConfig:
    # DB Settings
    db_host: str = field(default_factory=lambda: _get_env_str("DB_HOST", "127.0.0.1"))
    db_port: int = field(default_factory=lambda: _get_env_int("DB_PORT", 5432))
    db_name: str = field(default_factory=lambda: _get_env_str("DB_NAME", "postgres"))
    db_user: str = field(default_factory=lambda: _get_env_str("DB_USER", "postgres"))
    db_pass: str = field(default_factory=lambda: _get_env_str("DB_PASSWORD", "postgres"))
    db_pool_min: int = field(default_factory=lambda: _get_env_int("DB_POOL_MIN", 1))
    db_pool_max: int = field(default_factory=lambda: _get_env_int("DB_POOL_MAX", 10))

    # NVIDIA API Settings
    nim_api_key: str = field(default_factory=lambda: _get_env_str("NVIDIA_NIM_API_KEY", ""))
    embed_url: str = field(default_factory=lambda: _get_env_str("EMBEDDING_URL", "https://integrate.api.nvidia.com/v1/embeddings"))
    rerank_url: str = field(default_factory=lambda: _get_env_str("RERANK_URL", "https://ai.api.nvidia.com/v1/retrieval/nvidia/reranking"))
    chat_url: str = field(default_factory=lambda: _get_env_str("CHAT_URL", "https://integrate.api.nvidia.com/v1/chat/completions"))

    # Models
    embed_model: str = field(default_factory=lambda: _get_env_str("EMBEDDING_MODEL", "nvidia/nemotron-3-embed-1b"))
    rerank_model: str = field(default_factory=lambda: _get_env_str("RERANK_MODEL", "nvidia/rerank-qa-mistral-4b"))
    chat_model: str = field(default_factory=lambda: _get_env_str("CHAT_MODEL", "meta/llama-3.1-8b-instruct"))

    # Retrieval Settings
    rrf_k: int = field(default_factory=lambda: _get_env_int("RRF_K", 60))
    top_k: int = field(default_factory=lambda: _get_env_int("TOP_K", 5))
    candidate_k: int = field(default_factory=lambda: _get_env_int("CANDIDATE_K", 30))
    rerank_input_cap: int = field(default_factory=lambda: _get_env_int("RERANK_INPUT_CAP", 30))

    # Diversity Settings
    rerank_pool_multiplier: int = field(default_factory=lambda: _get_env_int("RERANK_POOL_MULTIPLIER", 3))
    diversity_similarity_threshold: float = field(default_factory=lambda: _get_env_float("DIVERSITY_SIMILARITY_THRESHOLD", 0.85))

    # Timeout Settings (초 단위)
    connect_timeout: int = field(default_factory=lambda: _get_env_int("CONNECT_TIMEOUT", 10))
    embed_timeout: int = field(default_factory=lambda: _get_env_int("EMBED_TIMEOUT", 15))
    rerank_timeout: int = field(default_factory=lambda: _get_env_int("RERANK_TIMEOUT", 20))
    chat_timeout: int = field(default_factory=lambda: _get_env_int("CHAT_TIMEOUT", 180))

    # Generation Settings
    max_tokens: int = field(default_factory=lambda: _get_env_int("MAX_TOKENS", 8192))
    stream_print: bool = field(default_factory=lambda: _get_env_bool("STREAM_PRINT", True))
    stream_mode: str = field(default_factory=lambda: _get_env_str("STREAM_MODE", "buffered"))

    # Orchestration Settings
    utility_model: str = field(default_factory=lambda: _get_env_str("UTILITY_MODEL", "meta/llama-3.1-8b-instruct"))
    session_max_turns: int = field(default_factory=lambda: _get_env_int("SESSION_MAX_TURNS", 10))

    # Session Store Settings
    session_store_backend: str = field(default_factory=lambda: _get_env_str("SESSION_STORE_BACKEND", "memory"))
    redis_host: str = field(default_factory=lambda: _get_env_str("REDIS_HOST", "127.0.0.1"))
    redis_port: int = field(default_factory=lambda: _get_env_int("REDIS_PORT", 6379))
    redis_db: int = field(default_factory=lambda: _get_env_int("REDIS_DB", 0))
    redis_password: str = field(default_factory=lambda: _get_env_str("REDIS_PASSWORD", ""))
    session_ttl_seconds: int = field(default_factory=lambda: _get_env_int("SESSION_TTL_SECONDS", 86400))

    def __post_init__(self) -> None:
        """설정 무결성 검증 및 경고."""
        if not self.nim_api_key:
            import warnings
            warnings.warn("NVIDIA_NIM_API_KEY가 비어 있습니다. API 호출 시 오류가 발생할 수 있습니다.", UserWarning)

        if self.session_store_backend not in ("memory", "redis"):
            raise ValueError(f"지원하지 않는 세션 백엔드입니다: {self.session_store_backend} (허용값: 'memory', 'redis')")

        if self.stream_mode not in ("realtime", "buffered"):
            raise ValueError(f"지원하지 않는 스트림 모드입니다: {self.stream_mode} (허용값: 'realtime', 'buffered')")

    @property
    def postgres_dsn(self) -> str:
        """PostgreSQL 연결 URL 생성."""
        return f"postgresql://{self.db_user}:{self.db_pass}@{self.db_host}:{self.db_port}/{self.db_name}"

    @property
    def redis_url(self) -> str:
        """Redis 연결 URL 생성."""
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"


# 싱글톤 형태로 기본 인스턴스를 노출해 매번 RAGConfig()를 호출하지 않고 재사용 가능하게 구성
config = RAGConfig()