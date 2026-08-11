from typing import Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000, description="사용자 법률 질의")
    conversation_id: Optional[str] = Field(
        default=None,
        description="이전 대화를 이어가려면 서버가 응답 시작 시 보내준 conversation_id를 그대로 전달. 첫 메시지면 생략."
    )
