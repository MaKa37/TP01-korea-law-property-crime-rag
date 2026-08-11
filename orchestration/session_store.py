"""대화 세션(히스토리) 저장소.

⚠️ 지금은 프로세스 메모리에만 저장한다. 서버 재시작하거나
`uvicorn --workers`로 다중 프로세스를 띄우면 세션이 유실/불일치할 수 있다.
실사용자 트래픽이 붙기 시작하면(6순위 프로덕션 하드닝) Redis나 이미 쓰고
있는 PostgreSQL의 별도 테이블로 교체할 것. 인터페이스(get_history/append/
clear)만 유지하면 교체 시 orchestrator.py는 건드릴 필요 없다.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Dict, List


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
