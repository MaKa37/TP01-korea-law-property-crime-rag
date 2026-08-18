"""
tests/test_api.py
=================
FastAPI 앱의 헬스체크 및 인증/라우팅 통합 테스트 (Mocking 기반)
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
for path in [str(PROJECT_ROOT), str(SRC_DIR)]:
    if path not in sys.path:
        sys.path.insert(0, path)

import pytest
from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture(autouse=True)
def setup_mock_state(monkeypatch):
    """테스트용 가상 환경 변수 및 의존성 주입"""
    # 인증 활성화 환경 설정
    monkeypatch.setenv("API_KEYS", "test-secret-key-1,test-secret-key-2")

    # DB/Redis 봇 모의 객체 주입
    mock_bot = MagicMock()
    mock_bot.health_check.return_value = {"status": "ok", "db": "healthy", "redis": "healthy"}
    mock_orchestrator = MagicMock()

    app.state.bot = mock_bot
    app.state.orchestrator = mock_orchestrator
    yield
    app.state.bot = None
    app.state.orchestrator = None


@pytest.fixture
def client():
    """FastAPI TestClient fixture"""
    return TestClient(app, raise_server_exceptions=False)


def test_health_check(client):
    """/health 엔드포인트 정상 응답 검증"""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data


def test_chat_unauthorized(client):
    """API Key 없이 요청 시 401/403 차단 검증"""
    response = client.post(
        "/chat",
        json={"query": "전세사기 고소 방법 알려주세요", "session_id": "test_sess_01"},
    )
    # 인증 키가 누락되었으므로 401(Unauthorized) 또는 403(Forbidden) 반환
    assert response.status_code in (401, 403)