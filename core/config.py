"""전역 설정 관리.

⚠️ load_dotenv()는 반드시 이 모듈의 최상단, RAGConfig 클래스 정의보다
먼저 호출되어야 한다. dataclass 필드 기본값(os.getenv(...))은 클래스가
정의되는 시점(= 이 모듈이 import될 때)에 평가되기 때문이다.
"""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


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

    # Timeout Settings
    connect_timeout: int = int(os.getenv("CONNECT_TIMEOUT", "10"))
    embed_timeout: int = int(os.getenv("EMBED_TIMEOUT", "15"))
    rerank_timeout: int = int(os.getenv("RERANK_TIMEOUT", "20"))
    chat_timeout: int = int(os.getenv("CHAT_TIMEOUT", "60"))

    # Generation Settings
    max_tokens: int = int(os.getenv("MAX_TOKENS", "4096"))
    stream_print: bool = os.getenv("STREAM_PRINT", "true").lower() == "true"

    # Orchestration Settings (라우팅/질의 재작성용 - 가볍고 빠른 모델 사용)
    utility_model: str = os.getenv("UTILITY_MODEL", "meta/llama-3.1-8b-instruct")
    session_max_turns: int = int(os.getenv("SESSION_MAX_TURNS", "10"))

    # Diversity Settings (5순위 - 거의 동일한 내용의 문서가 top_k를 도배하는 것 방지)
    rerank_pool_multiplier: int = int(os.getenv("RERANK_POOL_MULTIPLIER", "3"))
    diversity_similarity_threshold: float = float(os.getenv("DIVERSITY_SIMILARITY_THRESHOLD", "0.85"))