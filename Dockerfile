# 법률 AI 어시스턴트 API - 프로덕션 이미지
FROM python:3.11-slim

WORKDIR /app

# ⚠️ psycopg2-binary는 대부분의 플랫폼(linux/amd64 등)에서 미리 빌드된
# wheel을 제공하므로 gcc/libpq-dev 없이도 설치된다. 만약 다른 아키텍처
# (예: linux/arm64)에서 빌드 시 psycopg2 설치가 실패한다면, 아래 주석을
# 해제해서 빌드 도구를 설치할 것.
# RUN apt-get update && apt-get install -y --no-install-recommends \
#     gcc libpq-dev \
#     && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# ⚠️ .env는 이미지에 포함하지 않는다 (API 키 등 비밀정보가 이미지 레이어에
# 그대로 남는 것을 방지). 대신 docker-compose.yml의 env_file로 런타임에
# 주입한다. .dockerignore에서 .env를 제외 목록에 넣어 실수로 COPY되는
# 것도 막아뒀다.
#
# 워커 여러 개로 확장하고 싶으면 --workers N을 추가하면 된다. 세션
# 저장소가 이제 Redis 기반이라(P2), 워커 간에 세션이 공유되어 안전하다
# (메모리 저장소였다면 워커마다 세션이 따로 놀아서 다중 워커가 불가능했다).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]