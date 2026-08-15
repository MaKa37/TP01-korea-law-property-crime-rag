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
    # ⚠️ 실사용 관찰 결과, 무거운 질문은 답변 생성에 170~400초까지 걸릴 수
    # 있다. 이 타임아웃은 "전체 생성 시간"이 아니라 "토큰 사이 대기 시간"에
    # 적용되지만, 그래도 60초는 너무 빡빡해서 정상적인 생성도 폴백으로
    # 넘어가는 경우가 있었다. .env의 CHAT_TIMEOUT 설정이 실수로 사라져도
    # (예: uvicorn --reload는 .env 변경을 자동 반영하지 않음) 안전하도록
    # 코드 기본값 자체를 올려둔다.
    chat_timeout: int = int(os.getenv("CHAT_TIMEOUT", "180"))

    # Generation Settings
    # ⚠️ SYSTEM_PROMPT는 "관련 법령 해설 -> 유사 판례 요약 -> 피해자 조치 절차 ->
    # 면책 조항"까지 4개 섹션을 요구하는 긴 구조라, 답변이 상세할수록
    # max_tokens 한계에 걸려 면책 조항도 못 내놓고 잘릴 수 있다. 실제로
    # nemotron-3-ultra-550b 모델 비교 테스트에서 5건 중 2건이 4096에서
    # 잘렸다(면책 조항 누락 확인됨). 여유 있게 상향.
    max_tokens: int = int(os.getenv("MAX_TOKENS", "8192"))
    stream_print: bool = os.getenv("STREAM_PRINT", "true").lower() == "true"
    # "realtime": NVIDIA에서 오는 토큰을 실시간으로 그대로 릴레이 (원래 방식,
    #             간헐적 한글 깨짐 재현됨)
    # "buffered": 전체 답변을 다 모은 뒤 우리 쪽에서 고정 크기로 재분할해서
    #             의사 스트리밍 (현재 기본값, 깨짐 회피용 임시 조치)
    # 격리 테스트용 토글 - 원인 조사가 끝나면 하나로 정리할 예정
    stream_mode: str = os.getenv("STREAM_MODE", "buffered")

    # Orchestration Settings (라우팅/질의 재작성용 - 가볍고 빠른 모델 사용)
    utility_model: str = os.getenv("UTILITY_MODEL", "meta/llama-3.1-8b-instruct")
    session_max_turns: int = int(os.getenv("SESSION_MAX_TURNS", "10"))

    # Session Store Settings (P2 - 프로덕션 하드닝)
    # "memory": 프로세스 메모리 (재시작 시 유실, 다중 워커 불가)
    # "redis" : Redis 기반 (재시작에도 유지, 다중 워커/프로세스 간 공유 가능)
    session_store_backend: str = os.getenv("SESSION_STORE_BACKEND", "memory")
    redis_host: str = os.getenv("REDIS_HOST", "127.0.0.1")
    redis_port: int = int(os.getenv("REDIS_PORT", "6379"))
    redis_db: int = int(os.getenv("REDIS_DB", "0"))
    redis_password: str = os.getenv("REDIS_PASSWORD", "")
    # 세션 만료 시간(초). 기본 24시간 — 그 시간 동안 후속 질문이 없으면
    # 대화 맥락을 잊어버린다 (Redis가 자동으로 키를 지워줌).
    session_ttl_seconds: int = int(os.getenv("SESSION_TTL_SECONDS", str(24 * 60 * 60)))

    # Diversity Settings (5순위 - 거의 동일한 내용의 문서가 top_k를 도배하는 것 방지)
    rerank_pool_multiplier: int = int(os.getenv("RERANK_POOL_MULTIPLIER", "3"))
    diversity_similarity_threshold: float = float(os.getenv("DIVERSITY_SIMILARITY_THRESHOLD", "0.85"))