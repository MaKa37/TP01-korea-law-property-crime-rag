"""API 키 인증.

⚠️ 지금은 .env에 저장된 고정 키 목록과 대조하는 단순한 방식이다.
키 발급/폐기를 위한 관리 UI나 DB는 없다. 여러 사용자에게 정식으로
서비스하게 되면 키를 DB에 저장하고 폐기/재발급이 가능한 구조로
바꾸는 것을 고려할 것.

.env 설정 예:
    API_KEYS=key-for-alice,key-for-bob

사용법: 요청 헤더에 다음을 포함해야 한다.
    X-API-Key: <발급받은 키>

API_KEYS를 아예 설정하지 않으면(로컬 개발 기본값) 인증이 비활성화된다.
운영 배포 전에는 반드시 API_KEYS를 설정해야 한다 — app/main.py의
lifespan에서 비어 있으면 경고를 띄운다.
"""
import os
from typing import Optional, Set

from fastapi import Header, HTTPException, status


def _load_api_keys() -> Set[str]:
    raw = os.getenv("API_KEYS", "")
    return {k.strip() for k in raw.split(",") if k.strip()}


API_KEYS: Set[str] = _load_api_keys()


async def require_api_key(x_api_key: Optional[str] = Header(default=None, alias="X-API-Key")) -> str:
    """FastAPI 의존성: 유효한 API 키가 없으면 401을 반환한다."""
    if not API_KEYS:
        # 로컬 개발 편의를 위해, API_KEYS가 설정 안 됐으면 인증을 통과시킨다.
        return "anonymous (auth disabled - API_KEYS not set)"

    if not x_api_key or x_api_key not in API_KEYS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="유효하지 않은 API 키입니다. X-API-Key 헤더를 확인하세요.",
        )
    return x_api_key