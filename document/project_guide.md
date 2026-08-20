# legal-rag-project

## 최상위 파일 (root)

* `docker-compose.yml`
  - **기능**: FastAPI 앱, PostgreSQL+pgvector, Redis 컨테이너를 함께 기동하는 서비스 정의
  - **구문**: YAML, `services` / `volumes` / `networks` 키
  - **의미론**: 코드가 아니라 "배포 토폴로지"를 선언하는 인프라 계층의 명세
  - **문법**: 서비스명은 소문자-하이픈 컨벤션, 환경변수는 `.env.example` 키와 1:1 매칭

* `.env.example`
  - **기능**: 실제 `.env`가 가져야 할 키 목록을 예시값과 함께 제공(비밀값 미포함)
  - **구문**: `KEY=value` 평문
  - **의미론**: `core/config/*`의 각 Settings 클래스 필드와 대응되는 "계약서"
  - **문법**: 계층별 접두어 권장(`RAG_`, `ETL_`, `EVAL_`) — pydantic-settings `env_prefix`와 연동

* `requirements.txt`
  - **기능**: 파이썬 의존성 고정
  - **구문**: `package==version`
  - **의미론**: 재현 가능한 빌드를 보장하는 계약
  - **문법**: 카테고리별 주석 그룹(`# web`, `# db`, `# ml`) 권장

* `pyproject.toml`
  - **기능**: 프로젝트 메타데이터 + import-linter 계층 규칙 정의
  - **구문**: TOML, `[tool.importlinter]` 섹션에 Layers 명시
  - **의미론**: 의존성 규칙을 문서가 아니라 코드로 강제하는 지점 — 이 프로젝트에서 유일하게 "아키텍처가 린트 가능한" 파일
  - **문법**: contract 이름은 규칙의 의도를 드러내는 문장형(`"rag_engine must not import app"`)

---

## directorys

### `app/` — API 및 웹 서버 계층

**모듈화 맥락**: 이 폴더가 바뀌는 이유는 오직 하나 — "외부와의 계약"이 바뀔 때(API 스펙, 라우팅 정책, 미들웨어)다. RAG 로직이 바뀐다고 이 폴더의 파일이 바뀔 필요는 없다는 게 통합의 기준. 내부에서 `api/`와 `schemas/`를 나눈 이유는 라우팅(요청을 어디로 보낼지)과 검증(요청/응답의 모양)의 변경 주기가 다르기 때문 — 스키마는 프론트엔드 계약이 바뀔 때, 라우터는 백엔드 정책이 바뀔 때 수정된다.

**files**
* `main.py`
  - 기능: FastAPI 앱 생성, 라우터 등록, 미들웨어/예외 핸들러 부착
  - 구문: `FastAPI()`, `app.include_router()`, `@app.exception_handler()`
  - 의미론: 다른 모든 계층을 배선(wiring)하는 유일한 합성 지점(composition root)
  - 문법: 비즈니스 로직 0줄 — 조립만 담당
* `api/deps.py`
  - 기능: `Depends()`로 주입할 rag_engine 인스턴스, DB 세션 팩토리 정의
  - 구문: FastAPI `Depends`, 제너레이터 기반 세션 관리(`yield`)
  - 의미론: app이 하위 계층을 "생성"하지 않고 "주입"만 받도록 강제 — 결합도를 낮추는 지점
  - 문법: 함수명은 `get_*` 접두어로 통일
* `api/chat.py`
  - 기능: `/chat` SSE 스트리밍 엔드포인트
  - 구문: `StreamingResponse`, `async def`, `orchestrator.run()` 호출
  - 의미론: HTTP 프로토콜과 RAG 파이프라인 사이의 어댑터
  - 문법: 요청 파싱 → orchestrator 호출 → 스트림 반환의 3단 구조 고정
* `api/health.py`
  - 기능: `/health` 헬스체크
  - 구문: 단순 GET 핸들러, DB/Redis 연결 상태 확인
  - 의미론: 배포 오케스트레이션이 앱 생존 여부를 판단하는 계약
  - 문법: 응답은 항상 200 + 고정 스키마
* `schemas/chat_request.py`, `schemas/chat_response.py`
  - 기능: API 요청/응답 바디 검증
  - 구문: Pydantic `BaseModel`
  - 의미론: `domain.models`와 의도적으로 분리 — API 스키마는 "외부 계약", domain 모델은 "내부 개념"이라 변경 이유가 다름
  - 문법: snake_case 유지, 응답 모델은 `RetrievedChunk` 전체가 아니라 필요한 서브셋만 노출

---

### `core/` — 공통 설정 및 유틸리티 (리프 계층)

**모듈화 맥락**: "다른 무엇에도 의존하지 않는다"는 것 자체가 설계 결정이다. 설정/로깅/예외는 어떤 계층에서든 필요하지만, 이 파일들이 rag_engine이나 domain을 알아야 할 이유는 없다 — 이게 의존성 규칙에서 core를 리프로 고정한 근거. `config/`를 4개로 쪼갠 이유는 "config.py가 God Object가 된다"는 문제의 직접적 해결책 — ETL 설정이 바뀐다고 RAG 설정 파일까지 diff에 걸리면 코드 리뷰 시 변경 범위 파악이 어려워지므로, 파일 단위 분리는 "git diff의 최소 단위"를 설계에 반영한 것이다.

**files**
* `config/base.py`
  - 기능: 공통 `.env` 로딩 베이스 클래스
  - 구문: `pydantic_settings.BaseSettings` 상속, `SettingsConfigDict`
  - 의미론: 3개 Settings 클래스의 공통 조상 — "읽는 방식"만 정의, "무엇을 읽는지"는 모름
  - 문법: 실제 환경변수 필드는 두지 않음(하위 클래스 책임)
* `config/etl_settings.py` / `config/rag_settings.py` / `config/eval_settings.py`
  - 기능: 각 파이프라인이 필요로 하는 환경변수 필드 정의
  - 구문: `BaseAppSettings` 상속, 타입 힌트 기반 필드 선언
  - 의미론: 설정값의 소유권을 파이프라인 단위로 명확히 함
  - 문법: API 키류는 기본값 없이 필수 필드로 선언 — 누락 시 기동 실패하도록
* `logging.py`
  - 기능: 공통 로거 팩토리, 요청 ID 자동 삽입
  - 구문: `logging.Logger`, `contextvars.ContextVar`
  - 의미론: 분산된 계층의 로그를 하나의 요청 흐름으로 재구성하는 관측성(observability) 진입점
  - 문법: JSON 구조화 로깅(요청 ID/계층명/레벨 필드 고정) 권장
* `exceptions.py`
  - 기능: `RAGException`을 루트로 한 커스텀 예외 계층
  - 구문: 클래스 상속 체인
  - 의미론: 예외 핸들러가 "어떤 계층에서 실패했는지"를 타입만으로 판별하게 하는 시맨틱 채널
  - 문법: 모든 예외는 `RAGException` 상속, `user_safe: bool` 속성 보유

---

### `domain/` — 공유 도메인 모델 (리프 계층, 신설)

**모듈화 맥락**: 존재 이유는 단 하나 — "data_pipeline이 만든 Chunk와 rag_engine이 소비하는 Chunk가 같은 것이어야 한다." 각 계층이 자체 타입을 쓰면 경계마다 변환 코드(보일러플레이트)가 생기고 스키마가 미묘하게 어긋나는 버그가 생긴다. domain은 "행동(로직)"이 아니라 "개념(형태)"만 정의하므로 리프가 될 수 있다.

**files**
* `enums.py`
  - 기능: `DocumentType`(law/prec/expc/lstrm) 등 열거형 정의
  - 구문: `class X(str, Enum)`
  - 의미론: 도메인 어휘를 코드 타입으로 고정 — 문자열 하드코딩이 여러 파일에 흩어지는 것을 방지
  - 문법: 값은 법제처 API 코드와 동일하게 맞춰 변환 계층 최소화
* `models.py`
  - 기능: `Chunk`, `RetrievedChunk` 등 계층 간 공유 데이터 구조
  - 구문: Pydantic `BaseModel` 상속 체인(`RetrievedChunk(Chunk)`)
  - 의미론: "검색되기 전 청크"와 "점수가 매겨진 청크"를 타입 레벨에서 구분 — 상태를 타입으로 표현
  - 문법: 메서드 최소화, 필드 선언 위주(빈혈 모델 지향 — 로직은 rag_engine에)

---

### `db/` — 데이터베이스 연결 관리

**모듈화 맥락**: "연결을 관리하는 것"과 "연결로 무엇을 하는 것"을 분리했다. `pool.py`/`redis_store.py`는 커넥션 라이프사이클만 책임지고, 실제 쿼리 로직은 `rag_engine/retriever.py`에 둔다 — 이렇게 나누면 DB 드라이버를 교체해도 비즈니스 로직은 건드릴 필요가 없다.

**files**
* `pool.py`
  - 기능: PostgreSQL + pgvector 커넥션 풀 생성/종료
  - 구문: `asyncpg.create_pool()` 또는 SQLAlchemy `AsyncEngine`
  - 의미론: 앱 생명주기(startup/shutdown)와 결합된 리소스 관리 지점
  - 문법: 풀 인스턴스는 `app.state`에 저장, 전역 변수 지양
* `redis_store.py`
  - 기능: 세션별 대화 이력 저장/조회
  - 구문: `redis.asyncio` 클라이언트, TTL 기반 키 만료
  - 의미론: "대화의 기억"을 영속화 — query_rewriter가 참조하는 컨텍스트의 소스
  - 문법: 키 네이밍 `session:{session_id}:history` 고정

---

### `rag_engine/` — RAG 핵심 비즈니스 로직

**모듈화 맥락**: v1에서는 `orchestration/`과 `rag/`이 분리되어 있었으나, 두 폴더가 "질의 하나를 처리한다"는 동일한 변경 이유로 묶여 있었기 때문에 통합했다. 반대로 내부에서는 `fusion.py`와 `prompts/`를 신설해 추가로 쪼갰는데, "검색 알고리즘 vs 결과 융합 전략", "생성 로직 vs 프롬프트 문구"가 서로 다른 속도로, 다른 사람(프롬프트는 비개발자도 튜닝 가능해야 함)이 바꿀 가능성이 높기 때문이다.

**files**
* `orchestrator.py`
  - 기능: router→session→rewrite→retrieve→rerank→generate→guardrail 순서 호출
  - 구문: `async def run(query, session_id)`, 서브모듈 함수 조합
  - 의미론: 파이프라인의 지휘자 — 각 단계의 구현은 모르고 인터페이스만 안다
  - 문법: 알고리즘 세부 구현 없음 — 호출 순서와 에러 전파만
* `router.py`
  - 기능: 질의 의도 분류(법률 질의/잡담)
  - 구문: 규칙 기반 또는 경량 분류 모델 호출
  - 의미론: 불필요한 검색/생성 비용을 조기 차단하는 게이트
  - 문법: 반환 타입 `Literal["legal_query", "chitchat"]`로 닫힌 집합 제한
* `query_rewriter.py`
  - 기능: 대화 이력을 반영해 검색 최적화 질의로 재작성(HyDE 적용 가능)
  - 구문: LLM 호출 + 프롬프트 조합
  - 의미론: "사용자가 말한 것"과 "검색기가 이해하는 것" 사이의 번역 계층
  - 문법: 원문 유실 방지 위해 `RewrittenQuery(original, rewritten)` 형태로 함께 반환
* `retriever.py`
  - 기능: 벡터 검색(pgvector) + 키워드 검색(BM25/Trigram) 실행
  - 구문: SQL 조립, `asyncio.gather`로 병렬 실행
  - 의미론: 후보를 넓게 모으는 단계 — 재현율(recall) 최적화
  - 문법: 반환 타입 `list[RetrievedChunk]`, score는 rerank 이전 임시 유사도값
* `fusion.py`
  - 기능: RRF로 벡터/키워드 결과를 하나의 순위로 병합
  - 구문: 순수 함수(입력: 두 랭킹 리스트, 출력: 병합 랭킹) — 외부 I/O 없음
  - 의미론: retriever.py에서 분리된 이유가 여기서 드러남 — I/O 모킹 없이 단위 테스트가 가능해야 한다는 게 분리 기준
  - 문법: 사이드이펙트 없는 함수로만 구성
* `reranker.py`
  - 기능: NVIDIA NIM API로 재정렬, 임계값 미달 노이즈 필터링
  - 구문: `async def rerank()`, 외부 API 호출 + 재시도 로직
  - 의미론: 후보를 좁혀 정밀도(precision)를 높이는 단계 — retriever와 대칭 관계
  - 문법: 임계값은 `RAGSettings`에서만 주입, 하드코딩 금지
* `generator.py`
  - 기능: LLM 스트리밍 답변 생성
  - 구문: `async generator`(`yield`), LLM 스트리밍 클라이언트
  - 의미론: 검색된 근거를 사람이 읽는 답변으로 변환하는 최종 단계
  - 문법: 프롬프트 문자열을 직접 조립하지 않고 `prompts/`에서 템플릿을 가져와 값만 채움
* `prompts/system_prompt.py`, `prompts/few_shot_examples.py`
  - 기능: 시스템 프롬프트 및 few-shot 예시 관리
  - 구문: 문자열 상수 또는 Jinja2 템플릿
  - 의미론: 코드가 아니라 "문구"가 자산인 파일 — 버전 관리/A-B 테스트 대상
  - 문법: 템플릿 변수는 `{query}`, `{context}` 등 명시적 플레이스홀더
* `guardrails.py`
  - 기능: 생성된 답변이 실제 존재하는 판례번호/법조문을 인용하는지 실시간 검증
  - 구문: 정규식/DB 존재 확인 쿼리 + 스트리밍 중간 개입
  - 의미론: "모델이 지어낸 것"과 "실제 근거"를 구분하는 신뢰 계층 — RAG에서 가장 사고 위험이 큰 지점
  - 문법: 위반 시 `GuardrailViolation` 예외를 던지고 orchestrator가 스트림 즉시 중단

---

### `data_pipeline/` — 데이터 수집 및 적재 (ETL)

**모듈화 맥락**: Extract-Transform-Load라는 표준 데이터 엔지니어링 3분법을 채택. 세 파일의 경계는 "실패 지점이 다르다"는 실무적 이유로 정당화된다 — extract는 외부 API 장애, transform은 파싱 로직 버그, load는 DB 제약조건 위반으로 각각 다르게 실패하며, 재시도 전략도 파일별로 달라야 한다.

**files**
* `extract.py`
  - 기능: 법제처 Open API(법령/판례/해석례) 비동기 호출, Raw 데이터 저장
  - 구문: `aiohttp.ClientSession`, 세마포어로 동시 요청 수 제한
  - 의미론: 외부 세계와의 유일한 접점 — 이 파일만 API 장애 재시도/백오프 로직을 가짐
  - 문법: 원본 응답은 가공 없이 그대로 저장(감사 추적/재처리 가능성 확보)
* `transform.py`
  - 기능: HTML 태그 제거, 법령/조문 단위 청킹, 빈 판시사항 필터링 → `domain.Chunk` 생성
  - 구문: 순수 함수 위주(입력: Raw 텍스트, 출력: `list[Chunk]`)
  - 의미론: 비정형 텍스트를 도메인 모델이라는 정형 구조로 변환하는 경계
  - 문법: 문서 타입별(law/prec/expc/lstrm) 청킹 규칙은 함수로 분리하되 파일은 통합(공통 인터페이스 공유)
* `load.py`
  - 기능: `rag_engine`의 임베딩 함수 재사용 → Bulk Insert
  - 구문: `asyncio.gather` 배치 임베딩, `COPY`/`executemany` 기반 삽입
  - 의미론: ETL의 유일한 쓰기(write) 지점 — 파괴적 작업이 이 파일 밖에서 일어나지 않는다는 규칙의 근거지
  - 문법: 삽입 전 건수 로그 필수, 파괴적 작업 전 dry-run 옵션 제공

---

### `evaluation/` — 평가 및 벤치마크

**모듈화 맥락**: `evaluator.py`가 rag_engine을 블랙박스로만 호출하도록 설계 — 내부 구현(RRF 상수 등)에 접근하면 리팩토링마다 평가 코드도 같이 깨지기 때문. `metrics.py`를 분리한 이유는 "지표 계산(순수 함수)"과 "평가 실행(I/O, CLI)"의 테스트 방식이 다르기 때문 — metrics.py는 입출력 예시만으로 단위 테스트 가능해야 한다.

**files**
* `golden_set.jsonl`
  - 기능: 질의-정답 쌍의 평가용 데이터셋
  - 구문: JSON Lines
  - 의미론: 코드가 아니라 "리그레션 방지 기준선(baseline)" 역할의 데이터 자산
  - 문법: `{query, expected_chunk_ids, category}` 스키마 고정
* `evaluator.py`
  - 기능: golden_set을 순회하며 orchestrator/retriever 호출, metrics.py로 채점
  - 구문: CLI 인자 파싱, `--use-hyde`, `--top-k` 옵션
  - 의미론: 하이퍼파라미터 변경의 영향을 즉시 수치로 확인시켜주는 피드백 루프
  - 문법: 타임스탬프 포함 리포트 파일명 자동 생성
* `metrics.py`
  - 기능: Hit Rate@K, MRR, nDCG 계산
  - 구문: 순수 함수(`def hit_rate_at_k(retrieved, expected, k) -> float`)
  - 의미론: IR 표준 지표를 이 프로젝트의 도메인 타입에 적용하는 어댑터
  - 문법: 0~1 범위 반환, 빈 리스트 등 방어 로직 포함
* `reports/`
  - 기능: 실행 결과 누적 저장 디렉토리(파일 아님)
  - 의미론: 산출물 저장소 — `.gitignore` 대상, 요약만 README에 표로 기록

---

### `client/` — 프론트엔드 UI

**모듈화 맥락**: Streamlit UI는 배포 단위(프로세스)가 FastAPI 서버와 분리될 수 있어야 한다. `app/` 하위에 두지 않은 이유는 "API 계층"과 "UI 계층"이 물리적으로 다른 서버에 배포될 가능성을 열어두기 위함 — client→app을 HTTP로만 연결하도록 강제한 것과 같은 맥락.

**files**
* `web_ui.py`
  - 기능: Streamlit 챗봇 UI, `/chat`을 SSE로 스트리밍 수신해 렌더링
  - 구문: `st.chat_message()`, `httpx` 스트리밍 클라이언트
  - 의미론: 사용자에게 노출되는 유일한 화면 — 이 파일이 rag_engine을 import하면 계층 분리가 전부 무의미해짐
  - 문법: 백엔드 URL은 하드코딩하지 않고 환경변수(`API_BASE_URL`)로 주입

---

### `tests/` — 테스트

**모듈화 맥락**: 소스 트리 구조를 그대로 미러링했다 — `tests/rag_engine/test_fusion.py`처럼 대상 파일과 1:1 대응시키면 "이 파일을 고쳤을 때 어떤 테스트를 봐야 하는가"를 경로만으로 답할 수 있다. `fusion.py`가 순수 함수로 분리된 설계가 여기서 보상받는다 — I/O 모킹 없이 빠르게 도는 단위 테스트가 가능해짐.

**files**
* `conftest.py`
  - 기능: pytest 공통 픽스처(테스트용 DB 세션, 목업 NIM 클라이언트)
  - 구문: `@pytest.fixture`
  - 의미론: 테스트 간 중복 셋업을 제거하는 공유 자원 정의부
  - 문법: 기본 스코프 `function`, 비용 큰 DB 연결만 `session` 스코프
* `test_api.py`
  - 기능: `/health`, `/chat` 통합 테스트(스모크 테스트)
  - 구문: `TestClient` 또는 `httpx.AsyncClient`
  - 의미론: "서버가 최소한 죽지 않고 뜬다"를 보장하는 배포 게이트
  - 문법: 외부 API(NIM, 법제처)는 반드시 목업 처리
* `rag_engine/test_retriever.py`, `test_fusion.py`, `test_guardrails.py`
  - 기능: 하이브리드 검색, RRF 병합, 그라운딩 검증 로직 단위 테스트
  - 구문: `pytest.mark.parametrize`로 다양한 입력 케이스 커버
  - 의미론: `test_fusion.py`는 DB/네트워크 없이 실행 — 셋 중 가장 빠르고 안정적이어야 함
  - 문법: `test_<대상함수명>_<시나리오>` 네이밍(`test_rrf_merge_empty_input`)
* `data_pipeline/test_transform.py`
  - 기능: 4개 문서 타입별 청킹 로직 검증
  - 구문: 실제 법령/판례 원문 샘플을 fixture로 사용
  - 의미론: 데이터 품질 버그(빈 판시사항 등) 재발 방지 회귀선
  - 문법: 타입별 최소 1개 엣지 케이스(빈 필드, 깨진 HTML) 포함
* `evaluation/test_metrics.py`
  - 기능: Hit Rate@K, MRR, nDCG 계산 함수의 수치 정확성 검증
  - 구문: 알려진 입력/출력 쌍에 대한 단순 assert
  - 의미론: 평가 지표 자체가 틀리면 모든 성능 판단이 무의미해지므로 가장 먼저 신뢰가 확보되어야 하는 테스트
  - 문법: 수동 계산한 기대값을 주석으로 함께 남겨 검증 가능성 확보