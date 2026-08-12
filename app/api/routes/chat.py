import json
import uuid
from typing import Any, Dict, Iterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.api.deps import get_orchestrator
from app.auth import require_api_key
from app.rate_limit import CHAT_RATE_LIMIT, limiter
from app.schemas.chat import ChatRequest
from orchestration.orchestrator import ChatOrchestrator

router = APIRouter(tags=["chat"])

# 이 정도 글자 수가 쌓이거나 문장/어절 경계에 도달하면 바로 플러시한다.
# 너무 크면 체감 지연이 늘고, 너무 작으면 원래처럼 쪼개져 보인다.
_TOKEN_BATCH_MIN_CHARS = 20
_FLUSH_ON_SUFFIXES = ("\n", ".", "다.", "요.", "까.", " ")


def _format_sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _batch_token_events(events: Iterator[Dict[str, Any]], min_chars: int = _TOKEN_BATCH_MIN_CHARS) -> Iterator[Dict[str, Any]]:
    """연속된 token 이벤트를 일정 크기로 모아서 다시 내보낸다.

    모델의 서브워드 토큰을 하나씩 그대로 흘려보내면 한국어 특성상 음절이
    어중간하게 쪼개져("는지" "여" "부가" "고" "의") 부자연스럽다. token이
    아닌 다른 이벤트(sources/done 등)가 나오면, 순서가 꼬이지 않도록
    그 전에 버퍼를 무조건 비운다.
    """
    buffer = ""
    for event in events:
        if event.get("type") != "token":
            if buffer:
                yield {"type": "token", "content": buffer}
                buffer = ""
            yield event
            continue

        buffer += event["content"]
        if len(buffer) >= min_chars or buffer.endswith(_FLUSH_ON_SUFFIXES):
            yield {"type": "token", "content": buffer}
            buffer = ""

    if buffer:
        yield {"type": "token", "content": buffer}


@router.post("/chat")
@limiter.limit(CHAT_RATE_LIMIT)
async def chat(
    request: Request,
    payload: ChatRequest,
    orchestrator: ChatOrchestrator = Depends(get_orchestrator),
    api_key: str = Depends(require_api_key),
) -> StreamingResponse:
    """법률 질의에 대한 답변을 SSE로 스트리밍한다.

    ⚠️ 브라우저 네이티브 EventSource는 GET만 지원하므로, 클라이언트는
    fetch() + ReadableStream(또는 SSE 폴리필)으로 이 엔드포인트를 소비해야
    한다. ChatOrchestrator.ask_stream()이 동기 제너레이터이므로,
    StreamingResponse가 이를 스레드풀에서 실행해 이벤트 루프를 막지 않는다.

    ⚠️ X-API-Key 헤더로 인증한다 (.env의 API_KEYS 미설정 시 인증 비활성화).
    요청은 API 키(또는 미인증이면 IP) 기준으로 CHAT_RATE_LIMIT(기본
    10/minute)로 제한된다. 다른 엔드포인트보다 훨씬 엄격한 이유는,
    호출 한 번에 임베딩·리랭킹·생성(+라우팅·재작성)까지 유료 LLM API를
    여러 번 태우기 때문이다. 인증 실패 시 401, 제한 초과 시 429가 반환된다.

    이벤트 타입: session / rewritten_query / sources / token / done / no_results / error
    token 이벤트는 모델 토큰을 그대로 보내지 않고 일정 글자 수 단위로
    묶어서(_batch_token_events) 내보낸다.
    스트림 첫머리에 항상 conversation_id를 담은 session 이벤트를 보낸다 —
    클라이언트는 이 값을 저장해뒀다가 다음 요청에 그대로 실어 보내면
    대화가 이어진다 (없으면 매번 새 대화로 취급됨).
    스트림 마지막에는 항상 "data: [DONE]\\n\\n"을 보낸다.
    """
    conversation_id = payload.conversation_id or str(uuid.uuid4())

    def event_stream():
        yield _format_sse({"type": "session", "conversation_id": conversation_id})
        raw_events = orchestrator.ask_stream(conversation_id, payload.query)
        for event in _batch_token_events(raw_events):
            yield _format_sse(event)
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )