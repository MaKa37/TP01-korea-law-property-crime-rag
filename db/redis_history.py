"""
db/redis_history.py
===================
도커에 띄워진 Redis를 활용하여 사용자 세션별 대화 기록을 관리합니다.
"""

import json
import redis
import logging

logger = logging.getLogger(__name__)

class RedisChatHistory:
    def __init__(self, host='127.0.0.1', port=6379, db=0, ttl_seconds=3600):
        # 도커의 Redis 기본 포트(6379)로 연결
        self.redis_client = redis.Redis(host=host, port=port, db=db, decode_responses=True)
        self.ttl = ttl_seconds # 1시간(3600초) 동안 대화가 없으면 세션 만료

    def get_history(self, session_id: str):
        """특정 세션의 대화 기록을 리스트 형태로 불러옵니다."""
        try:
            raw_data = self.redis_client.get(f"chat_history:{session_id}")
            if raw_data:
                return json.loads(raw_data)
            return []
        except Exception as e:
            logger.error(f"Redis 읽기 실패: {e}")
            return []

    def add_message(self, session_id: str, role: str, content: str):
        """새로운 메시지(user 또는 assistant)를 기존 대화 기록에 추가하고 저장합니다."""
        try:
            history = self.get_history(session_id)
            history.append({"role": role, "content": content})
            
            # 딕셔너리를 JSON 문자열로 변환하여 저장하고 만료 시간 갱신
            self.redis_client.setex(
                f"chat_history:{session_id}",
                self.ttl,
                json.dumps(history, ensure_ascii=False)
            )
        except Exception as e:
            logger.error(f"Redis 쓰기 실패: {e}")