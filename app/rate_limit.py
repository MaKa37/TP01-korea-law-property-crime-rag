"""레이트리밋 설정.

⚠️ 인증된 요청(X-API-Key 헤더 있음)은 API 키 기준으로, 그렇지 않은
요청은 IP 기준으로 제한한다. API 키가 IP보다 신뢰할 수 있는 식별자다
(같은 IP를 여러 사용자가 공유하는 경우, 프록시 환경에서의 IP 문제
등을 피할 수 있음). 다만 지금은 인증이 선택 사항(API_KEYS 미설정 시
비활성화)이라, 인증 없이 쓰는 경우 여전히 IP 기준 제한만 적용된다.

/chat은 호출 한 번에 임베딩+리랭킹+생성(+라우팅+재작성) 등 유료 LLM API를
여러 번 태우므로, 다른 엔드포인트보다 훨씬 엄격하게 제한한다.
"""
import os

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

# 분당 요청 수. 무거운 엔드포인트(/chat)는 이보다 더 엄격한 값을
# 별도로 적용한다 (CHAT_RATE_LIMIT).
DEFAULT_RATE_LIMIT = os.getenv("DEFAULT_RATE_LIMIT", "60/minute")
CHAT_RATE_LIMIT = os.getenv("CHAT_RATE_LIMIT", "10/minute")


def rate_limit_key(request: Request) -> str:
    api_key = request.headers.get("X-API-Key")
    if api_key:
        return f"key:{api_key}"
    return f"ip:{get_remote_address(request)}"


limiter = Limiter(key_func=rate_limit_key, default_limits=[DEFAULT_RATE_LIMIT])