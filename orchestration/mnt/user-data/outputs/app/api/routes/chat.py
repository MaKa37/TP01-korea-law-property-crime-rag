import json
import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.api.deps import get_orchestrator
from app.schemas.chat import ChatRequest
from orchestration.orchestrator import ChatOrchestrator

router = APIRouter(tags=["chat"])


def _format_sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/chat")
async def chat(payload: ChatRequest, orchestrator: ChatOrchestrator = Depends(get_orchestrator)) -> StreamingResponse:
    """법률 질의에 대한 답변을 SSE로 스트리밍한다.

    ⚠️ 브라우저 네이티브 EventSource는 GET만 지원하므로, 클라이언트는
    fetch() + ReadableStream(또는 SSE 폴리필)으로 이 엔드포인트를 소비해야
    한다. ChatOrchestrator.ask_stream()이 동기 제너레이터이므로,
    StreamingResponse가 이를 스레드풀에서 실행해 이벤트 루프를 막지 않는다.

    이벤트 타입: session / rewritten_query / sources / token / done / no_results / error
    스트림 첫머리에 항상 conversation_id를 담은 session 이벤트를 보낸다 —
    클라이언트는 이 값을 저장해뒀다가 다음 요청에 그대로 실어 보내면
    대화가 이어진다 (없으면 매번 새 대화로 취급됨).
    스트림 마지막에는 항상 "data: [DONE]\\n\\n"을 보낸다.
    """
    conversation_id = payload.conversation_id or str(uuid.uuid4())

    def event_stream():
        yield _format_sse({"type": "session", "conversation_id": conversation_id})
        for event in orchestrator.ask_stream(conversation_id, payload.query):
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
