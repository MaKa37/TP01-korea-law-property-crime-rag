"""
tests/test_api.py
=================
FastAPI 앱의 헬스체크 및 기본 인증/라우팅 통합 테스트
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
for path in [str(PROJECT_ROOT), str(SRC_DIR)]:
    if path not in sys.path:
        sys.path.insert(0, path)

import pytest
from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture
def client():
    """FastAPI lifespan(startup/shutdown)을 정상 트리거하는 TestClient fixture"""
    with TestClient(app) as test_client:
        yield test_client


def test_health_check(client):
    """DB 및 Redis 상태를 점검하는 /health 엔드포인트 테스트"""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data


def test_chat_unauthorized(client):
    """인증 헤더 누락 시 401/403 차단 테스트"""
    response = client.post(
        "/chat",
        json={"query": "전세사기 고소 방법 알려주세요", "session_id": "test_sess_01"},
    )
    assert response.status_code in (401, 403)