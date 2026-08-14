"""요청 단위 로깅 미들웨어.

⚠️ 일부러 BaseHTTPMiddleware를 쓰지 않고 순수 ASGI 미들웨어로 구현했다.
BaseHTTPMiddleware는 스트리밍 응답(StreamingResponse)의 바디를 내부적으로
한 번 더 감싸서 릴레이하는데, 이 과정에서 원래의 청크 경계가 재조정될 수
있다. 실제로 이 프로젝트에서 미들웨어 도입 이후 SSE 스트림의 한글이
클라이언트 단에서 깨지는 문제가 재현됐다 (서버 응답 바디가 재청크되면서,
클라이언트가 `iter_lines(decode_unicode=True)`로 읽을 때 멀티바이트 문자가
청크 경계에서 잘리는 것과 같은 클래스의 버그).

순수 ASGI 미들�스는 http.response.body 메시지를 전혀 손대지 않고 그대로
통과시키므로(응답 헤더에 X-Request-ID만 추가) 이 문제가 원천적으로
발생하지 않는다.

- X-Request-ID 헤더가 있으면 그대로 쓰고, 없으면 새로 발급한다.
- 요청 처리 도중의 모든 로그(core.logging.get_logger로 만든 로거들)에
  이 ID가 자동으로 붙는다 (core/logging.py의 request_id_var 참고).
- 응답 헤더에도 X-Request-ID를 그대로 돌려준다.
- 요청 시작/종료(상태 코드, 소요시간)를 로그로 남긴다.
"""
import time
import uuid
from typing import Optional

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from core.logging import get_logger, request_id_var

_logger = get_logger("http")


def _extract_header(scope: Scope, name: bytes) -> Optional[str]:
    for key, value in scope.get("headers", []):
        if key.lower() == name:
            return value.decode("latin-1")
    return None


class RequestLoggingMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            # 웹소켓/lifespan 등은 그대로 통과
            await self.app(scope, receive, send)
            return

        request_id = _extract_header(scope, b"x-request-id") or str(uuid.uuid4())
        request_id_var.set(request_id)

        method = scope.get("method", "")
        path = scope.get("path", "")
        start = time.time()
        status_holder = {"status": None}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("latin-1")))
                message["headers"] = headers
            # http.response.body는 여기서 전혀 건드리지 않고 그대로 통과시킨다.
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            elapsed_ms = (time.time() - start) * 1000
            _logger.exception(f"{method} {path} -> 예외 발생 ({elapsed_ms:.0f}ms)")
            raise

        elapsed_ms = (time.time() - start) * 1000
        _logger.info(f"{method} {path} -> {status_holder['status']} ({elapsed_ms:.0f}ms)")