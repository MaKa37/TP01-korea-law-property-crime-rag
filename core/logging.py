"""공통 로거 설정.

요청 ID 추적: request_id_var(contextvars)에 저장된 값을 모든 로그 라인에
자동으로 붙인다. FastAPI 미들웨어(app/middleware.py)가 요청 시작 시
이 값을 설정하면, 그 요청 처리 도중 호출되는 모든 함수(임베딩→검색→
리랭킹→라우팅→재작성→생성)의 로그가 같은 요청 ID로 묶여서 나온다.
StreamingResponse가 스레드풀에서 실행되는 경우에도 contextvars가
정상적으로 전파되는 것을 확인했다 (Starlette의 anyio 기반 스레드풀은
컨텍스트를 복사해서 실행한다).

로그 포맷: LOG_FORMAT=json 이면 한 줄짜리 JSON, 기본값(text)이면
사람이 읽기 편한 텍스트. 운영/컨테이너 환경에서는 json을 추천한다
(로그 수집기가 파싱하기 쉬움).
"""
import contextvars
import json
import logging
import os
from datetime import datetime, timezone

request_id_var: "contextvars.ContextVar[str]" = contextvars.ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def get_logger(name: str = "LegalRAGBot") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.addFilter(RequestIdFilter())

        if os.getenv("LOG_FORMAT", "text").lower() == "json":
            handler.setFormatter(JsonFormatter())
        else:
            handler.setFormatter(logging.Formatter(
                "[%(levelname)s] %(asctime)s [req=%(request_id)s] - %(message)s"
            ))

        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger