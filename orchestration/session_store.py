"""대화 세션(히스토리) 저장소.

두 가지 구현을 제공한다:
  - InMemorySessionStore: 프로세스 메모리. 재시작하면 유실되고,
    `uvicorn --workers`로 다중 프로세스를 띄우면 세션이 서로 안 보인다.
  - RedisSessionStore   : Redis. 재시작해도 유지되고, 여러 프로세스/워커가
    같은 세션을 공유할 수 있다.

둘 다 같은 인터페이스(get_history/append/clear)를 따르므로, 어느 쪽을
쓰든 orchestrator.py는 전혀 건드릴 필요가 없다. 어떤 저장소를 쓸지는
core.config.RAGConfig의 session_store_backend("memory" | "redis")로
정해지고, create_session_store()가 그 값을 보고 골라준다.
"""
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Dict, List, Optional


@dataclass
class Turn:
    user: str
    assistant: str
    timestamp: str


class InMemorySessionStore:
    def __init__(self, max_turns: int = 10):
        self._store: Dict[str, List[Turn]] = {}
        self._lock = Lock()
        self.max_turns = max_turns

    def get_history(self, conversation_id: str) -> List[Dict[str, str]]:
        with self._lock:
            turns = self._store.get(conversation_id, [])
            return [{"user": t.user, "assistant": t.assistant} for t in turns]

    def append(self, conversation_id: str, user_message: str, assistant_message: str) -> None:
        with self._lock:
            turns = self._store.setdefault(conversation_id, [])
            turns.append(Turn(
                user=user_message,
                assistant=assistant_message,
                timestamp=datetime.now(timezone.utc).isoformat()
            ))
            # 오래된 턴은 잘라내서 프롬프트가 무한정 길어지는 것을 방지
            if len(turns) > self.max_turns:
                del turns[: len(turns) - self.max_turns]

    def clear(self, conversation_id: str) -> None:
        with self._lock:
            self._store.pop(conversation_id, None)


class RedisSessionStore:
    """Redis 기반 세션 저장소.

    conversation_id별로 대화 턴 목록을 JSON 문자열 하나로 직렬화해서
    저장한다 (key: "session:{conversation_id}"). append()마다 통째로
    읽고-고치고-쓰는 방식이라, 같은 conversation_id에 대한 동시 요청이
    겹치면 레이스 컨디션이 있을 수 있다. 다만 대화 하나를 여러 요청이
    동시에 보내는 경우는 드물어서(보통 한 번에 한 메시지씩 주고받음)
    실사용에서는 위험이 낮다. 완벽한 원자성이 필요하면 Redis 트랜잭션
    (WATCH/MULTI)이나 Lua 스크립트로 바꿀 것.
    """

    def __init__(self, redis_client, max_turns: int = 10, ttl_seconds: Optional[int] = None):
        self.redis = redis_client
        self.max_turns = max_turns
        self.ttl_seconds = ttl_seconds

    @staticmethod
    def _key(conversation_id: str) -> str:
        return f"session:{conversation_id}"

    def get_history(self, conversation_id: str) -> List[Dict[str, str]]:
        raw = self.redis.get(self._key(conversation_id))
        if not raw:
            return []
        turns = json.loads(raw)
        return [{"user": t["user"], "assistant": t["assistant"]} for t in turns]

    def append(self, conversation_id: str, user_message: str, assistant_message: str) -> None:
        key = self._key(conversation_id)
        raw = self.redis.get(key)
        turns = json.loads(raw) if raw else []
        turns.append({
            "user": user_message,
            "assistant": assistant_message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        if len(turns) > self.max_turns:
            turns = turns[-self.max_turns:]

        self.redis.set(key, json.dumps(turns, ensure_ascii=False), ex=self.ttl_seconds)

    def clear(self, conversation_id: str) -> None:
        self.redis.delete(self._key(conversation_id))


def create_session_store(config, logger: logging.Logger):
    """config.session_store_backend에 따라 세션 저장소를 생성한다.

    "redis"로 설정했는데 연결에 실패하면, 서버 전체를 죽이는 대신
    경고를 남기고 메모리 저장소로 폴백한다 (이 프로젝트 전반에서 써온
    "부분 실패는 서비스 중단이 아니라 성능 저하로" 원칙과 동일).
    """
    if config.session_store_backend != "redis":
        return InMemorySessionStore(max_turns=config.session_max_turns)

    try:
        import redis  # 로컬에서 import: redis 미설치 환경에서도 memory 모드는 동작하게

        client = redis.Redis(
            host=config.redis_host,
            port=config.redis_port,
            db=config.redis_db,
            password=config.redis_password or None,
            decode_responses=True,
            socket_connect_timeout=3,
        )
        client.ping()  # 연결 확인 (실패하면 예외 발생)
        logger.info(f"✅ Redis 세션 저장소 연결 성공 ({config.redis_host}:{config.redis_port})")
        return RedisSessionStore(client, max_turns=config.session_max_turns, ttl_seconds=config.session_ttl_seconds)
    except Exception as e:  # noqa: BLE001 - Redis 연결 실패는 폭넓게 잡아서 안전하게 폴백
        logger.error(f"🚨 Redis 연결 실패, 메모리 세션 저장소로 폴백합니다: {e}")
        return InMemorySessionStore(max_turns=config.session_max_turns)