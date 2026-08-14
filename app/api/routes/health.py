from fastapi import APIRouter, Depends

from app.api.deps import get_bot, get_orchestrator
from orchestration.orchestrator import ChatOrchestrator
from orchestration.session_store import RedisSessionStore
from rag.bot import LegalRAGBot

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(
    bot: LegalRAGBot = Depends(get_bot),
    orchestrator: ChatOrchestrator = Depends(get_orchestrator),
) -> dict:
    """의존 서비스(DB, 세션 저장소)의 실제 연결 상태까지 확인한다.

    단순히 "서버 프로세스가 살아있다"만 보는 게 아니라, DB 커넥션을
    하나 빌렸다 반납해보고, Redis를 쓰는 경우 ping까지 실제로 날려본다.
    Redis/Docker 전환 시 "정말로 Redis에 붙었는지" 이 엔드포인트
    하나로 바로 확인할 수 있다.

    - database 장애 -> 전체 status="error" (서비스 자체가 불가능하므로)
    - session_store(Redis) 장애 -> status="degraded" (메모리로 자동
      폴백되어 서비스는 계속되지만, 세션이 영속화되지 않는 상태)
    """
    checks: dict = {}

    try:
        conn = bot.db_pool.getconn()
        bot.db_pool.putconn(conn)
        checks["database"] = {"status": "ok"}
    except Exception as e:  # noqa: BLE001 - 헬스체크는 모든 예외를 잡아서 상태로 보여줘야 함
        checks["database"] = {"status": "error", "detail": str(e)}

    store = orchestrator.session_store
    if isinstance(store, RedisSessionStore):
        try:
            store.redis.ping()
            checks["session_store"] = {"status": "ok", "backend": "redis"}
        except Exception as e:  # noqa: BLE001
            checks["session_store"] = {"status": "degraded", "backend": "redis", "detail": str(e)}
    else:
        checks["session_store"] = {"status": "ok", "backend": "memory"}

    database_ok = checks["database"]["status"] == "ok"
    all_ok = all(c["status"] == "ok" for c in checks.values())

    if not database_ok:
        overall = "error"
    elif not all_ok:
        overall = "degraded"
    else:
        overall = "ok"

    return {"status": overall, "checks": checks}