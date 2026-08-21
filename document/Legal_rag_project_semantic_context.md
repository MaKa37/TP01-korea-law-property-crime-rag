# legal-rag-project — 의미 맥락 문서 (Semantic Context Document)

> 원본: `project_structure_and_code_260820.txt`
> 재구성 기준: 디렉토리 나열 대신 **"데이터가 어떤 순서로 어느 파일을 거쳐가는가"** 를 1차 축으로 삼는다.
> 파이프라인 어디에도 등장하지 않는 파일(`core/`, `domain/`)은 그 자체로 "모든 흐름을 가로지르는 리프 계층"이라는 신호다.

---

## 0. 핵심 통찰 3가지

1. **이 프로젝트에는 서로 독립적인 3개의 실행 흐름이 존재한다.** 실시간 질의응답(A), 배치 적재(B), 수동 평가(C)는 실행 주기·트리거·실패 모드가 완전히 다르므로 코드도 분리되어 있다.
2. **`domain/`과 `core/`는 파이프라인이 아니라 "파이프라인들이 공유하는 계약"이다.** A와 B가 같은 `Chunk` 타입을 쓰지 않으면 경계마다 변환 버그가 생긴다는 것이 존재 이유다.
3. **의존성 방향이 곧 "누가 누구를 몰라도 되는가"의 기록이다.** `rag_engine`이 `app`을 모르는 것, `evaluator`가 `retriever` 내부(RRF 상수)를 모르는 것은 우연이 아니라 리팩토링 비용을 낮추기 위한 설계 결정이다.

---

## 1. 레이어 지도 — 의존성 방향

```
┌───────────┐   HTTP/SSE only    ┌───────────┐
│  client/  │ ─────────────────▶ │   app/    │  ← 합성 지점 (composition root)
└───────────┘                    └─────┬─────┘
                                        │ Depends() 주입 (app이 생성하지 않고 받기만 함)
                                  ┌─────▼─────┐        ┌─────────────────┐
                                  │rag_engine/│ ◀────  │  data_pipeline/  │  (별도 실행, 배치)
                                  └─────┬─────┘  임베딩  └────────┬────────┘
                                        │        함수만  재사용        │
                                        │        (반대 방향 금지)      │
                                  ┌─────▼──────────────────────▼─────┐
                                  │            domain/  (리프)        │  ← 공유 계약(Chunk 등)
                                  └─────┬──────────────────────┬─────┘
                                        │                      │
                                  ┌─────▼─────┐          ┌─────▼─────┐
                                  │    db/    │          │ core/(리프) │  ← 설정/로깅/예외, 전 계층이 참조
                                  └───────────┘          └───────────┘

     evaluation/  ─ ─ ─ (공개 인터페이스만 호출, 블랙박스) ─ ─ ─▶  rag_engine/
```

**`pyproject.toml`의 `[tool.importlinter]`가 강제하는 규칙:**

| 계약 | 강제하는 의미 |
|---|---|
| `rag_engine must not import app` | 비즈니스 로직이 웹 프레임워크(FastAPI)를 몰라야 재사용/테스트가 쉬움 |
| `client → app` 은 HTTP로만 | UI 프로세스와 API 프로세스가 물리적으로 분리 배포 가능해야 함 |
| `core`, `domain`은 상위 계층을 import 금지 | 리프 계층 고정 — 순환 의존 원천 차단 |
| `evaluation → rag_engine` 단방향, 공개 함수만 | 내부 구현(RRF 상수 등)이 바뀌어도 평가 코드는 안 깨짐 |

---

## 2. 파이프라인 A — 온라인 질의응답 (요청마다 실행)

**트리거**: 사용자가 채팅창에 입력 · **목적**: 질문 → 근거 있는 답변 (SSE 스트림)

| # | 단계 | 파일 | 이 단계의 역할 | 데이터 변환 |
|---|---|---|---|---|
| 1 | 요청 발신 | `client/web_ui.py` | `httpx` 스트리밍 클라이언트로 `/chat` 호출, `st.chat_message()`로 렌더링 대기 | 사용자 텍스트 → HTTP 요청 |
| 2 | HTTP 진입 | `app/api/chat.py` | 요청 파싱 → orchestrator 호출 → 스트림 반환 (3단 구조 고정) | `ChatRequest` (schemas) |
| 3 | 의존성 주입 | `app/api/deps.py` | rag_engine 인스턴스·DB 세션을 "생성"이 아니라 "주입" | — |
| 4 | 지휘 | `rag_engine/orchestrator.py` | router→session→rewrite→retrieve→rerank→generate→guardrail 순서 호출, 에러 전파만 담당 | `query, session_id` |
| 5 | 게이트 | `rag_engine/router.py` | 법률 질의 / 잡담 분류 — 불필요한 검색·생성 비용 조기 차단 | → `"legal_query" \| "chitchat"` |
| 6 | 이력 조회 | `db/redis_store.py` | `session:{id}:history` 키로 대화 맥락 조회 | session_id → 이력 |
| 7 | 질의 재작성 | `rag_engine/query_rewriter.py` | 이력 반영 재작성(HyDE 가능), 원문 유실 방지 | → `RewrittenQuery(original, rewritten)` |
| 8 | 병렬 검색 | `rag_engine/retriever.py` | pgvector 벡터 검색 + BM25/Trigram 키워드 검색을 `asyncio.gather`로 동시 실행 (재현율 최적화) | → `list[RetrievedChunk]` × 2 |
| 9 | 융합 | `rag_engine/fusion.py` | RRF로 두 랭킹을 하나로 병합 — 순수 함수, I/O 없음 | 두 랭킹 → 병합 랭킹 |
| 10 | 재정렬 | `rag_engine/reranker.py` | NVIDIA NIM API 호출, 임계값 미달 노이즈 필터링 (정밀도 최적화) | → 축소된 `list[RetrievedChunk]` |
| 11 | 생성 | `rag_engine/generator.py` | `prompts/`에서 템플릿을 가져와 값만 채워 LLM 스트리밍 호출 | chunks+query → 토큰 스트림 |
| 12 | 그라운딩 검증 | `rag_engine/guardrails.py` | 스트리밍 도중 실제 판례번호/법조문 인용 여부 실시간 검증 | 위반 시 `GuardrailViolation` → 스트림 즉시 중단 |
| 13 | 응답 직렬화 | `app/schemas/chat_response.py` | `RetrievedChunk` 전체가 아니라 필요한 서브셋만 노출 | 내부 모델 → 외부 계약 |
| 14 | 렌더링 | `client/web_ui.py` | SSE 토큰을 화면에 순차 표시 | 스트림 → 화면 |

**이 파이프라인을 가로지르는 리프 파일**: `core/logging.py`(요청ID로 6~13단계 로그를 하나의 흐름으로 재구성), `core/exceptions.py`(각 단계 실패를 `RAGException` 계층으로 타입화), `domain/models.py`의 `Chunk`/`RetrievedChunk`(8~10단계를 관통하는 공용 타입 — "검색되기 전"과 "점수가 매겨진" 상태를 타입 레벨에서 구분).

---

## 3. 파이프라인 B — 오프라인 적재 (ETL, 배치/수동 실행)

**트리거**: 스케줄러 또는 개발자 수동 실행 · **목적**: 법령·판례 원문 → 검색 가능한 벡터

| # | 단계 | 파일 | 이 단계의 역할 | 데이터 변환 |
|---|---|---|---|---|
| 1 | 수집 | `data_pipeline/extract.py` | 법제처 Open API 비동기 호출, 세마포어로 동시 요청 제한. **외부 세계와의 유일한 접점** — API 장애 재시도/백오프 로직이 이 파일에만 존재 | API 응답 → Raw 데이터(가공 없이 그대로 저장, 감사 추적용) |
| 2 | 변환 | `data_pipeline/transform.py` | HTML 태그 제거, 법령/조문 단위 청킹(문서타입별 규칙, 공통 인터페이스), 빈 판시사항 필터링 | Raw 텍스트 → `list[domain.Chunk]` |
| 3 | 임베딩+적재 | `data_pipeline/load.py` | `rag_engine`의 임베딩 함수를 **재사용**(단방향 참조), `asyncio.gather`로 배치 임베딩 후 `COPY`/`executemany`로 대량 삽입. **ETL의 유일한 쓰기 지점** | `list[Chunk]` → `db/pool.py`의 pgvector 테이블 |

**주의할 경계**: `load.py → rag_engine`(임베딩 유틸만) 참조는 허용되지만 역방향(`rag_engine → data_pipeline`)은 금지 — 파괴적 쓰기 작업이 ETL 밖으로 새어나가지 않도록 하는 규칙의 근거지. 삽입 전 건수 로그와 dry-run 옵션이 이 규칙을 실무적으로 뒷받침한다.

---

## 4. 파이프라인 C — 평가 피드백 루프 (하이퍼파라미터 변경 시 수동 실행)

**트리거**: 개발자가 RRF 가중치·top-k·HyDE 사용 여부 등을 바꾼 뒤 · **목적**: 변경의 영향을 즉시 수치로 확인

| # | 단계 | 파일 | 이 단계의 역할 | 데이터 변환 |
|---|---|---|---|---|
| 1 | 기준선 | `evaluation/golden_set.jsonl` | `{query, expected_chunk_ids, category}` 스키마의 회귀 방지 기준 데이터셋 | — |
| 2 | 실행 | `evaluation/evaluator.py` | golden_set을 순회하며 `--use-hyde`, `--top-k` 같은 CLI 옵션으로 orchestrator/retriever를 **블랙박스로만** 호출 | query → 실제 검색/생성 결과 |
| 3 | 채점 | `evaluation/metrics.py` | Hit Rate@K, MRR, nDCG — 순수 함수, 입출력 예시만으로 단위 테스트 가능 | (retrieved, expected) → 0~1 점수 |
| 4 | 산출 | `evaluation/reports/` | 타임스탬프 리포트 파일 자동 누적 (`.gitignore` 대상, 요약만 README에 표로 기록) | — |

**설계 의도**: `evaluator.py`가 `rag_engine`의 공개 인터페이스만 호출하므로, 파이프라인 A 내부(RRF 상수, 임계값 등)를 리팩토링해도 평가 코드는 깨지지 않는다.

---

## 5. 계층별 파일 명세 — 존재 이유(변경 트리거) 중심

> 각 행의 "존재 이유"는 "이 파일이 왜 지금의 경계로 쪼개져 있는가"에 대한 답이다. 기능 설명이 아니라 **변경 이유(reason to change)** 기준으로 읽는다.

### `app/` — 외부 계약이 바뀔 때만 변경

| 파일 | 존재 이유 | 참여 파이프라인 |
|---|---|---|
| `main.py` | 합성 지점(composition root) — 비즈니스 로직 0줄, 조립만 | A (진입) |
| `api/deps.py` | app이 하위 계층을 생성하지 않고 주입받게 해 결합도를 낮춤 | A |
| `api/chat.py` | HTTP 프로토콜과 RAG 파이프라인 사이의 어댑터 | A |
| `api/health.py` | 배포 오케스트레이션이 앱 생존을 판단하는 계약 | (모니터링, 파이프라인 외) |
| `schemas/chat_request.py`, `schemas/chat_response.py` | API 계약(외부)과 domain 모델(내부)의 변경 이유가 다르므로 의도적 분리 | A |

### `core/` — 어떤 계층에서든 필요하지만 아무 계층도 몰라도 되는 리프

| 파일 | 존재 이유 | 참여 파이프라인 |
|---|---|---|
| `config/base.py` | "읽는 방식"만 정의, "무엇을 읽는지"는 모름 | A, B, C 공통 |
| `config/rag_settings.py` / `etl_settings.py` / `eval_settings.py` | God Object 방지 — 설정값 소유권을 파이프라인 단위로 분리(git diff 최소화) | A / B / C 각각 전담 |
| `logging.py` | 분산된 계층의 로그를 요청ID 기준 하나의 흐름으로 재구성 | A, B, C 공통 |
| `exceptions.py` | 예외 타입만으로 "어느 계층에서 실패했는지" 판별 가능하게 함 | A, B, C 공통 |

### `domain/` — 계층 간 타입 계약, 리프

| 파일 | 존재 이유 | 참여 파이프라인 |
|---|---|---|
| `enums.py` | 도메인 어휘(문서타입 등)를 코드 타입으로 고정 — 하드코딩 산개 방지 | B(생성) → A(소비) |
| `models.py` | B가 만든 `Chunk`와 A가 소비하는 `Chunk`가 반드시 같은 것이도록 보장 | B(생성) → A(소비, 8~10단계) |

### `db/` — 연결 관리와 쿼리 로직의 분리

| 파일 | 존재 이유 | 참여 파이프라인 |
|---|---|---|
| `pool.py` | DB 드라이버 교체 시 비즈니스 로직(쿼리)이 안 흔들리도록 커넥션 라이프사이클만 책임 | A(8단계), B(3단계) |
| `redis_store.py` | "대화의 기억"을 영속화 — query_rewriter가 참조하는 컨텍스트의 소스 | A(6단계) |

### `rag_engine/` — 파이프라인 A의 심장부

| 파일 | 존재 이유 | 파이프라인 A 단계 |
|---|---|---|
| `orchestrator.py` | 각 단계의 구현은 모르고 인터페이스만 앎 — 지휘자 역할 | 4 |
| `router.py` | 반환 타입을 닫힌 집합(`Literal`)으로 제한해 게이트 역할을 명확히 | 5 |
| `query_rewriter.py` | "사용자가 말한 것"과 "검색기가 이해하는 것" 사이의 번역 계층 | 7 |
| `retriever.py` | 후보를 넓게 모으는 재현율 단계 | 8 |
| `fusion.py` | retriever에서 굳이 분리한 이유 = I/O 모킹 없이 단위 테스트 가능해야 하므로 | 9 |
| `reranker.py` | 후보를 좁히는 정밀도 단계 — retriever와 대칭 | 10 |
| `generator.py` | 프롬프트 문자열을 직접 조립하지 않고 `prompts/`에서 값만 채움 | 11 |
| `prompts/system_prompt.py`, `prompts/few_shot_examples.py` | 코드가 아니라 "문구"가 자산 — 비개발자도 튜닝 가능해야 해서 별도 분리 | 11 |
| `guardrails.py` | "모델이 지어낸 것"과 "실제 근거"를 구분하는, RAG에서 가장 사고 위험이 큰 지점 | 12 |

### `data_pipeline/` — 파이프라인 B 전담

| 파일 | 존재 이유 | 파이프라인 B 단계 |
|---|---|---|
| `extract.py` | 실패 지점(외부 API 장애)이 달라 재시도 전략도 독립적이어야 함 | 1 |
| `transform.py` | 실패 지점(파싱 버그)이 달라 독립 | 2 |
| `load.py` | 실패 지점(DB 제약 위반)이 달라 독립, 유일한 쓰기 지점 | 3 |

### `evaluation/` — 파이프라인 C 전담

| 파일 | 존재 이유 | 파이프라인 C 단계 |
|---|---|---|
| `golden_set.jsonl` | 코드가 아니라 리그레션 방지 "기준선" 데이터 자산 | 1 |
| `evaluator.py` | 하이퍼파라미터 변경의 영향을 즉시 수치로 확인시키는 피드백 루프 | 2 |
| `metrics.py` | "지표 계산(순수함수)"과 "평가 실행(I/O)"의 테스트 방식이 달라 분리 | 3 |

### `client/`, `tests/`

| 파일 | 존재 이유 |
|---|---|
| `client/web_ui.py` | rag_engine을 import하면 계층 분리가 무의미해짐 — `API_BASE_URL` 환경변수로만 app과 연결 |
| `tests/` 전체 | 소스 트리를 그대로 미러링 — "이 파일을 고쳤을 때 어떤 테스트를 봐야 하는가"를 경로만으로 답할 수 있게 함 |

---

## 6. 설계 결정 로그 (Why-Log, 빠른 참조용)

- **`orchestration/` + `rag/` → `rag_engine/` 통합**: 두 폴더가 "질의 하나를 처리한다"는 동일한 변경 이유로 묶여 있었음
- **`fusion.py` 신설(분리)**: 순수 함수로 두면 I/O 모킹 없이 빠른 단위 테스트 가능
- **`prompts/` 신설(분리)**: 문구는 비개발자도 튜닝 가능해야 하며, 생성 로직과 변경 속도가 다름
- **`config/` 4분할**: config.py가 God Object가 되는 문제의 직접적 해결책 — git diff를 파이프라인 단위로 최소화
- **`domain/` 신설**: data_pipeline과 rag_engine이 같은 `Chunk`를 쓰지 않으면 경계마다 변환 보일러플레이트와 스키마 어긋남 버그가 생김
- **`db/` 분리**: "연결 관리"와 "연결로 무엇을 하는가"를 나눠 DB 드라이버 교체가 비즈니스 로직에 영향을 주지 않게 함
- **`client/`를 `app/` 밖으로**: UI와 API가 물리적으로 다른 서버에 배포될 가능성을 열어둠
- **`evaluator.py`가 rag_engine을 블랙박스로만 호출**: 내부 구현(RRF 상수 등)에 접근하면 리팩토링마다 평가 코드도 같이 깨짐
- **`tests/`가 소스 트리 미러링**: fusion.py가 순수 함수로 분리된 설계가 여기서 보상받음(가장 빠르고 안정적인 테스트)

---

## 7. 변경 영향 매트릭스 (Change Impact Quick Reference)

| 이것을 바꾸면 | 직접 수정 파일 | 함께 확인해야 할 파일 |
|---|---|---|
| 재정렬 임계값 조정 | `core/config/rag_settings.py` | `reranker.py`(값 참조), `evaluation/reports/`(성능 변화 확인) |
| 프롬프트 문구 A/B 테스트 | `rag_engine/prompts/*` | `generator.py` 동작, `evaluation/evaluator.py` 재실행 |
| 임베딩 모델 교체 | `data_pipeline/load.py`, rag_engine 임베딩 유틸 | `retriever.py`(벡터 차원), `reranker.py` 임계값 재조정 |
| 새 문서 타입 추가 | `domain/enums.py`, `data_pipeline/transform.py` | `tests/data_pipeline/test_transform.py`, `evaluation/golden_set.jsonl` 카테고리 |
| DB 드라이버 교체 | `db/pool.py` | `rag_engine/retriever.py`는 **변경 불필요**(쿼리 로직이 분리되어 있으므로) |
| API 응답 필드 추가 | `app/schemas/chat_response.py` | `domain/models.py`는 **불변**, `client/web_ui.py` 렌더링만 추가 |
| RRF 가중치 변경 | `rag_engine/fusion.py` | `tests/rag_engine/test_fusion.py`(DB/네트워크 없이 즉시 검증 가능) |
| 판례 인용 검증 규칙 강화 | `rag_engine/guardrails.py` | `tests/rag_engine/test_guardrails.py`, orchestrator의 예외 전파 경로 |

---

## 8. 한 줄 요약

> **`app`/`client`는 "누가 부르는가"의 문제, `rag_engine`/`data_pipeline`/`evaluation`은 "무엇을 하는가"의 문제, `core`/`domain`은 "모두가 동의해야 하는 것"의 문제다.** 파일이 어느 디렉토리에 있는지보다, 이 세 질문 중 무엇에 답하는 파일인지를 먼저 물으면 새 코드를 어디에 둘지는 대부분 자명해진다.