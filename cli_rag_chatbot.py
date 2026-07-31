from __future__ import annotations

import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

import psycopg2
from psycopg2.extensions import connection as PgConnection
from psycopg2.extensions import cursor as PgCursor
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dotenv import load_dotenv


# =========================================================
# 1. 로깅
# =========================================================

LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("legal_rag")


# =========================================================
# 2. 예외 계층
# =========================================================


class LegalRAGError(Exception):
    """Legal RAG 애플리케이션의 최상위 예외입니다."""


class ConfigurationError(LegalRAGError):
    """환경변수 또는 설정값 오류입니다."""


class ExternalAPIError(LegalRAGError):
    """외부 API 요청 또는 응답 오류입니다."""


class DatabaseSearchError(LegalRAGError):
    """데이터베이스 검색 오류입니다."""


class ResponseFormatError(ExternalAPIError):
    """외부 API 응답 형식 오류입니다."""


# =========================================================
# 3. 설정
# =========================================================


@dataclass(frozen=True, slots=True)
class Settings:
    db_name: str
    db_user: str
    db_password: str
    db_host: str
    db_port: int
    api_key: str

    chat_url: str = "https://integrate.api.nvidia.com/v1/chat/completions"
    embedding_url: str = "https://integrate.api.nvidia.com/v1/embeddings"
    chat_model: str = "nvidia/nemotron-3-ultra-550b-a55b"
    embedding_model: str = "nvidia/nv-embedqa-e5-v5"

    top_k: int = 15
    distance_threshold: float = 0.35
    relaxed_distance_threshold: float = 0.42
    request_connect_timeout: float = 10.0
    request_read_timeout: float = 60.0

    max_history_messages: int = 8
    max_context_chars: int = 24_000
    max_chunk_chars: int = 4_000
    max_keywords: int = 3

    @property
    def db_config(self) -> dict[str, Any]:
        return {
            "dbname": self.db_name,
            "user": self.db_user,
            "password": self.db_password,
            "host": self.db_host,
            "port": self.db_port,
        }

    @property
    def timeout(self) -> tuple[float, float]:
        return self.request_connect_timeout, self.request_read_timeout

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()

        required = {
            "DB_NAME": os.getenv("DB_NAME"),
            "DB_USER": os.getenv("DB_USER"),
            "DB_PASSWORD": os.getenv("DB_PASSWORD"),
            "DB_HOST": os.getenv("DB_HOST"),
            "DB_PORT": os.getenv("DB_PORT"),
            "NVIDIA_NIM_API_KEY": os.getenv("NVIDIA_NIM_API_KEY"),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ConfigurationError(
                f"필수 환경변수가 설정되지 않았습니다: {', '.join(missing)}"
            )

        try:
            db_port = int(required["DB_PORT"] or "")
        except ValueError as exc:
            raise ConfigurationError("DB_PORT는 정수여야 합니다.") from exc

        settings = cls(
            db_name=required["DB_NAME"] or "",
            db_user=required["DB_USER"] or "",
            db_password=required["DB_PASSWORD"] or "",
            db_host=required["DB_HOST"] or "",
            db_port=db_port,
            api_key=required["NVIDIA_NIM_API_KEY"] or "",
            top_k=int(os.getenv("TOP_K", "15")),
            distance_threshold=float(os.getenv("DISTANCE_THRESHOLD", "0.48")),
            relaxed_distance_threshold=float(os.getenv("RELAXED_DISTANCE_THRESHOLD", "0.80")),
            max_history_messages=int(os.getenv("MAX_HISTORY_MESSAGES", "8")),
            max_context_chars=int(os.getenv("MAX_CONTEXT_CHARS", "24000")),
            max_chunk_chars=int(os.getenv("MAX_CHUNK_CHARS", "4000")),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.top_k <= 0:
            raise ConfigurationError("TOP_K는 1 이상이어야 합니다.")
        if not 0.0 < self.distance_threshold < 2.0:
            raise ConfigurationError("DISTANCE_THRESHOLD 값이 유효하지 않습니다.")
        if not self.distance_threshold <= self.relaxed_distance_threshold < 2.0:
            raise ConfigurationError(
                "RELAXED_DISTANCE_THRESHOLD는 DISTANCE_THRESHOLD 이상 2.0 미만이어야 합니다."
            )
        if self.max_history_messages < 0:
            raise ConfigurationError("MAX_HISTORY_MESSAGES는 0 이상이어야 합니다.")
        if self.max_context_chars <= 0 or self.max_chunk_chars <= 0:
            raise ConfigurationError("컨텍스트 길이 제한은 1 이상이어야 합니다.")


# =========================================================
# 4. 도메인 모델
# =========================================================


@dataclass(frozen=True, slots=True)
class SearchResult:
    source_type: str
    title: str
    content: str
    distance: float

    @property
    def document_kind(self) -> str:
        return "판례" if self.source_type == "prec" else "법률"


ChatMessage = dict[str, str]


# =========================================================
# 5. 공통 유틸리티
# =========================================================


KEYWORD_STOPWORDS = {
    "거래",
    "사기",
    "법률",
    "조항",
    "관한",
    "위반",
    "사건",
    "경우",
}


def create_http_session() -> requests.Session:
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"POST"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )

    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
    session = requests.Session()
    session.mount("https://", adapter)
    session.headers.update({"Content-Type": "application/json"})
    return session


def escape_like_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def deduplicate_preserving_order(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


def fallback_keywords_from_query(query: str, limit: int) -> list[str]:
    """LLM 구조화 출력이 실패했을 때 사용할 보수적인 로컬 키워드 추출기입니다."""
    normalized = re.sub(r"[^0-9A-Za-z가-힣]+", " ", query)
    candidates = [token.strip() for token in normalized.split()]

    aliases = {
        "중고거래": "중고",
        "인터넷": "온라인",
    }

    cleaned: list[str] = []
    for token in candidates:
        token = aliases.get(token, token)
        if len(token) < 2 or token in KEYWORD_STOPWORDS:
            continue
        cleaned.append(token)

    return deduplicate_preserving_order(cleaned)[:limit]


def compact_history(history: Sequence[ChatMessage], limit: int) -> list[ChatMessage]:
    if limit <= 0:
        return []
    return [dict(message) for message in history[-limit:]]


def parse_json_object(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"```(?:json)?", "", text, flags=re.IGNORECASE).strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for index, char in enumerate(cleaned):
            if char != "{":
                continue
            try:
                parsed, _ = decoder.raw_decode(cleaned[index:])
                break
            except json.JSONDecodeError:
                continue
        else:
            raise ResponseFormatError("응답에서 유효한 JSON 객체를 찾지 못했습니다.")

    if not isinstance(parsed, dict):
        raise ResponseFormatError("응답 JSON의 최상위 값은 객체여야 합니다.")
    return parsed


# =========================================================
# 6. NVIDIA NIM 클라이언트
# =========================================================


class NvidiaNIMClient:
    def __init__(self, settings: Settings, session: requests.Session) -> None:
        self.settings = settings
        self.session = session

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.settings.api_key}"}

    def _post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self.session.post(
                url,
                headers=self.headers,
                json=payload,
                timeout=self.settings.timeout,
            )
            response.raise_for_status()
        except requests.exceptions.RequestException as exc:
            raise ExternalAPIError(f"외부 API 요청 실패: {exc}") from exc

        try:
            body = response.json()
        except ValueError as exc:
            raise ResponseFormatError("외부 API가 JSON이 아닌 응답을 반환했습니다.") from exc

        if not isinstance(body, dict):
            raise ResponseFormatError("외부 API 응답 형식이 올바르지 않습니다.")
        return body

    def chat_completion(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        temperature: float = 0.0,
    ) -> str:
        payload = {
            "model": self.settings.chat_model,
            "messages": list(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        body = self._post_json(self.settings.chat_url, payload)

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ResponseFormatError("채팅 API 응답에서 content를 찾지 못했습니다.") from exc

        if not isinstance(content, str) or not content.strip():
            raise ResponseFormatError("채팅 API가 빈 응답을 반환했습니다.")
        return content.strip()

    def get_embedding(self, text: str) -> list[float]:
        payload = {
            "input": [text],
            "model": self.settings.embedding_model,
            "input_type": "query",
        }
        body = self._post_json(self.settings.embedding_url, payload)

        try:
            embedding = body["data"][0]["embedding"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ResponseFormatError("임베딩 API 응답 형식이 올바르지 않습니다.") from exc

        if not isinstance(embedding, list) or not embedding:
            raise ResponseFormatError("유효한 임베딩 벡터가 반환되지 않았습니다.")

        try:
            return [float(value) for value in embedding]
        except (TypeError, ValueError) as exc:
            raise ResponseFormatError("임베딩 벡터에 숫자가 아닌 값이 포함되어 있습니다.") from exc

    def stream_chat_completion(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        temperature: float = 0.0,
    ) -> Iterable[str]:
        payload = {
            "model": self.settings.chat_model,
            "messages": list(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }

        try:
            with self.session.post(
                self.settings.chat_url,
                headers=self.headers,
                json=payload,
                stream=True,
                timeout=self.settings.timeout,
            ) as response:
                response.raise_for_status()

                for raw_line in response.iter_lines(decode_unicode=True):
                    if not raw_line or not raw_line.startswith("data: "):
                        continue

                    data_str = raw_line[6:].strip()
                    if data_str == "[DONE]":
                        return

                    try:
                        chunk = json.loads(data_str)
                        if "error" in chunk:
                            logger.warning("스트리밍 API 오류: %s", chunk["error"])
                            continue
                        content = chunk["choices"][0]["delta"].get("content")
                    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                        logger.debug("해석할 수 없는 스트리밍 청크를 건너뜁니다.")
                        continue

                    if isinstance(content, str) and content:
                        yield content

        except requests.exceptions.RequestException as exc:
            raise ExternalAPIError(f"스트리밍 API 요청 실패: {exc}") from exc


# =========================================================
# 7. 검색 저장소
# =========================================================


class RagRepository:
    def __init__(self, cursor: PgCursor, settings: Settings) -> None:
        self.cursor = cursor
        self.settings = settings

    def search(
        self,
        query_embedding: Sequence[float],
        dynamic_keywords: Sequence[str],
    ) -> list[SearchResult]:
        embedding_literal = f"[{','.join(map(str, query_embedding))}]"
        keywords = list(dynamic_keywords)

        strategies: list[tuple[str, float | None]] = []
        if keywords:
            strategies.extend(
                [
                    ("all_keywords", self.settings.distance_threshold),
                    ("any_keyword", self.settings.distance_threshold),
                ]
            )

        strategies.extend(
            [
                ("vector_strict", self.settings.distance_threshold),
                ("vector_relaxed", self.settings.relaxed_distance_threshold),
                ("nearest_neighbors", None),
            ]
        )

        for strategy, threshold in strategies:
            query, params = self._build_query(
                embedding_literal,
                keywords,
                strategy,
                threshold,
            )
            results = self._execute(query, params)
            if not results:
                continue

            best_distance = results[0].distance
            if strategy == "nearest_neighbors":
                logger.warning(
                    "임계값 내 결과가 없어 최근접 문서를 반환합니다. 최저 거리=%.4f. "
                    "임베딩 모델/입력 유형/거리 임계값을 점검하세요.",
                    best_distance,
                )
            else:
                logger.info(
                    "검색 전략 '%s'에서 %d건을 찾았습니다. 최저 거리=%.4f",
                    strategy,
                    len(results),
                    best_distance,
                )
            return results

        return []

    def _build_query(
        self,
        embedding_literal: str,
        keywords: Sequence[str],
        strategy: str,
        threshold: float | None,
    ) -> tuple[str, tuple[Any, ...]]:
        where_parts = ["r.source_type IN ('prec', 'law')", "r.embedding IS NOT NULL"]
        params: list[Any] = [embedding_literal]

        if strategy in {"all_keywords", "any_keyword"} and keywords:
            per_keyword_clauses: list[str] = []
            for keyword in keywords:
                per_keyword_clauses.append(
                    "(r.title ILIKE %s ESCAPE '\\' OR r.content_text ILIKE %s ESCAPE '\\')"
                )
                pattern = f"%{escape_like_value(keyword)}%"
                params.extend([pattern, pattern])

            joiner = " AND " if strategy == "all_keywords" else " OR "
            where_parts.append(f"({joiner.join(per_keyword_clauses)})")

        threshold_clause = ""
        if threshold is not None:
            threshold_clause = "WHERE distance < %s"
            params.append(threshold)
        params.append(self.settings.top_k)

        sql = f"""
            WITH q AS (
                SELECT %s::vector AS vec
            ),
            scored AS (
                SELECT
                    r.source_type,
                    r.title,
                    r.content_text,
                    r.embedding <=> q.vec AS distance
                FROM rag_chunks AS r
                CROSS JOIN q
                WHERE {' AND '.join(where_parts)}
            )
            SELECT source_type, title, content_text, distance
            FROM scored
            {threshold_clause}
            ORDER BY distance ASC
            LIMIT %s
        """
        return sql, tuple(params)

    def _execute(self, query: str, params: tuple[Any, ...]) -> list[SearchResult]:
        try:
            self.cursor.execute(query, params)
            rows = self.cursor.fetchall()
        except psycopg2.Error as exc:
            raise DatabaseSearchError(f"검색 쿼리 실행 실패: {exc}") from exc

        return [
            SearchResult(
                source_type=str(row[0]),
                title=str(row[1]),
                content=str(row[2]),
                distance=float(row[3]),
            )
            for row in rows
        ]


# =========================================================
# 8. RAG 서비스
# =========================================================


class LegalRAGService:
    def __init__(
        self,
        settings: Settings,
        client: NvidiaNIMClient,
        repository: RagRepository,
    ) -> None:
        self.settings = settings
        self.client = client
        self.repository = repository

    def extract_keywords(self, user_query: str) -> list[str]:
        prompt = f"""당신은 법률 데이터베이스 검색 전문가입니다.
사용자의 질문에서 검색에 유의미한 고유 키워드를 최대 {self.settings.max_keywords}개 추출하세요.

[사용자 질문]
{user_query}

[규칙]
- 반드시 JSON 객체만 출력합니다.
- 형식: {{"keywords": ["단어1", "단어2"]}}
- 지나치게 일반적인 단어는 제외합니다.
"""
        try:
            content = self.client.chat_completion(
                [{"role": "user", "content": prompt}],
                max_tokens=120,
            )
            parsed = parse_json_object(content)
            raw_keywords = parsed.get("keywords", [])
            if not isinstance(raw_keywords, list):
                return []

            cleaned: list[str] = []
            for value in raw_keywords[: self.settings.max_keywords]:
                keyword = str(value).strip()
                if keyword == "중고거래":
                    keyword = "중고"
                if len(keyword) >= 2 and keyword not in KEYWORD_STOPWORDS:
                    cleaned.append(keyword)

            return deduplicate_preserving_order(cleaned)
        except LegalRAGError as exc:
            fallback = fallback_keywords_from_query(
                user_query,
                self.settings.max_keywords,
            )
            logger.warning(
                "동적 키워드 추출 실패: %s; 로컬 폴백 키워드=%s",
                exc,
                fallback,
            )
            return fallback

    def rewrite_query(self, user_query: str, chat_history: Sequence[ChatMessage]) -> str:
        system_prompt = """당신은 대한민국 법률 정보 검색을 위한 쿼리 변환기입니다.
사용자의 질문을 판례 또는 법률 조문 검색에 유용한 한국어 서술문 한 문장으로 변환하세요.

규칙:
1. 한국어만 사용합니다.
2. 사고 과정, 설명, 인사말을 출력하지 않습니다.
3. 변환된 문장 하나만 출력합니다.
4. 원문의 핵심 사실관계와 법적 쟁점을 보존합니다.
"""
        messages: list[ChatMessage] = [{"role": "system", "content": system_prompt}]
        messages.extend(compact_history(chat_history, 4))
        messages.append(
            {
                "role": "user",
                "content": f"질문: {user_query}\n변환된 법률 검색 문장:",
            }
        )

        try:
            result = self.client.chat_completion(messages, max_tokens=120)
            result = re.sub(r"^[\"']|[\"']$", "", result.strip())

            if len(re.findall(r"[a-zA-Z]", result)) > 10:
                logger.warning("쿼리 재작성 결과에서 과도한 영문을 감지했습니다.")
                return user_query
            if not result:
                return user_query
            return result
        except LegalRAGError as exc:
            logger.warning("쿼리 재작성 실패: %s", exc)
            return user_query

    def retrieve(
        self,
        user_query: str,
        chat_history: Sequence[ChatMessage],
    ) -> tuple[str, list[str], list[SearchResult]]:
        keywords = self.extract_keywords(user_query)
        rewritten_query = self.rewrite_query(user_query, chat_history)
        embedding = self.client.get_embedding(rewritten_query)
        contexts = self.repository.search(embedding, keywords)
        return rewritten_query, keywords, contexts

    def build_context(self, contexts: Sequence[SearchResult]) -> str:
        parts: list[str] = []
        current_length = 0

        for index, context in enumerate(contexts, start=1):
            content = context.content[: self.settings.max_chunk_chars]
            part = (
                f"\n[자료 {index}]\n"
                f"유형: {context.document_kind}\n"
                f"제목: {context.title}\n"
                f"내용:\n{content}\n"
                f"거리: {context.distance:.4f}\n"
                f"{'-' * 40}\n"
            )

            if current_length + len(part) > self.settings.max_context_chars:
                break

            parts.append(part)
            current_length += len(part)

        return "".join(parts)

    def stream_answer(
        self,
        query: str,
        contexts: Sequence[SearchResult],
        chat_history: Sequence[ChatMessage],
    ) -> Iterable[str]:
        if not contexts:
            yield (
                "현재 질문과 관련된 법률 조문이나 판례를 지식 베이스에서 "
                "찾을 수 없습니다. 질문을 더 구체적으로 설명해 주세요."
            )
            return

        context_text = self.build_context(contexts)
        system_prompt = f"""당신은 대한민국 법률 및 대법원 판례를 분석하는 전문 AI 어시스턴트입니다.

[절대 규칙]
1. 아래 검색 자료만을 근거로 답변합니다.
2. 자료에 없는 사실, 사건번호, 날짜, 조문 번호를 만들어내지 않습니다.
3. 핵심 주장 뒤에는 반드시 [자료 N] 형식으로 근거 번호를 표시합니다.
4. 자료만으로 확정할 수 없는 판단은 '제공된 자료만으로는 확정하기 어렵습니다'라고 명시합니다.
5. 검색 자료가 질문과 무관하면 다음 문장만 출력합니다.
   제공된 지식 베이스에 질문과 일치하는 판례 및 법률이 없어 답변할 수 없습니다.
6. 법률 자문을 단정적으로 제공하지 말고, 자료에 근거한 일반적 설명임을 분명히 합니다.

[답변 구조]
1. 관련 자료
2. 사실관계 또는 조문 핵심
3. 법원의 판단 또는 조문의 의미
4. 질문에 대한 결론
5. 불확실성 및 확인이 필요한 사항

[검색 자료]
{context_text}
"""

        messages: list[ChatMessage] = [{"role": "system", "content": system_prompt}]
        messages.extend(compact_history(chat_history, self.settings.max_history_messages))
        messages.append({"role": "user", "content": query})

        yield from self.client.stream_chat_completion(messages, max_tokens=1400)


# =========================================================
# 9. CLI 애플리케이션
# =========================================================


class LegalRAGCLI:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.session = create_http_session()
        self.connection: PgConnection | None = None
        self.cursor: PgCursor | None = None
        self.chat_history: list[ChatMessage] = []

    def __enter__(self) -> "LegalRAGCLI":
        try:
            self.connection = psycopg2.connect(**self.settings.db_config)
            self.connection.autocommit = True
            self.cursor = self.connection.cursor()
        except psycopg2.Error as exc:
            self.close()
            raise ConfigurationError(f"DB 연결 실패: {exc}") from exc
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def close(self) -> None:
        if self.cursor is not None:
            self.cursor.close()
            self.cursor = None
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        self.session.close()

    def run(self) -> None:
        if self.cursor is None:
            raise RuntimeError("CLI가 초기화되지 않았습니다.")

        client = NvidiaNIMClient(self.settings, self.session)
        repository = RagRepository(self.cursor, self.settings)
        service = LegalRAGService(self.settings, client, repository)

        print(f"🚀 통합 법률/판례 RAG 시스템 - {self.settings.chat_model}")
        print("종료: quit / exit / 종료")
        print("대화 초기화: 초기화\n")

        while True:
            try:
                raw_query = input("\n🧑‍⚖️ 질문을 입력하세요: ")
            except (EOFError, KeyboardInterrupt):
                print("\n👋 챗봇을 종료합니다.")
                break

            query = raw_query.strip()
            if not query:
                continue
            if query.lower() in {"quit", "exit", "종료"}:
                print("👋 챗봇을 종료합니다.")
                break
            if query == "초기화":
                self.chat_history.clear()
                print("♻️ 대화 맥락이 초기화되었습니다.")
                continue

            try:
                print("\n🧠 질문 분석 및 검색 쿼리 생성 중...")
                rewritten, keywords, contexts = service.retrieve(query, self.chat_history)
                print(f"   ↳ 동적 키워드: {keywords}")
                print(f"   ↳ 재작성된 쿼리: {rewritten}")
                print(f"   ↳ 검색 결과: {len(contexts)}건")

                for index, result in enumerate(contexts, start=1):
                    print(
                        f"      {index}. [{result.document_kind}] "
                        f"{result.title} (거리: {result.distance:.4f})"
                    )

                print("\n🤖 [Legal RAG 답변]\n")
                answer_parts: list[str] = []
                for piece in service.stream_answer(query, contexts, self.chat_history):
                    print(piece, end="", flush=True)
                    answer_parts.append(piece)
                print("\n")

                full_answer = "".join(answer_parts).strip()
                if full_answer:
                    self.chat_history.extend(
                        [
                            {"role": "user", "content": query},
                            {"role": "assistant", "content": full_answer},
                        ]
                    )
                    self.chat_history = compact_history(
                        self.chat_history,
                        self.settings.max_history_messages,
                    )

            except KeyboardInterrupt:
                print("\n👋 챗봇을 종료합니다.")
                break
            except LegalRAGError as exc:
                logger.error("질문 처리 실패: %s", exc)
                print(f"\n⚠️ 질문 처리 중 오류가 발생했습니다: {exc}")
            except Exception:
                logger.exception("예상하지 못한 오류가 발생했습니다.")
                print("\n⚠️ 예상하지 못한 오류가 발생했습니다. 로그를 확인해 주세요.")

            print("=" * 60)


# =========================================================
# 10. 엔트리포인트
# =========================================================


def main() -> int:
    try:
        settings = Settings.from_env()
        with LegalRAGCLI(settings) as app:
            app.run()
        return 0
    except ConfigurationError as exc:
        logger.error("설정 오류: %s", exc)
        return 1
    except Exception:
        logger.exception("애플리케이션 시작 실패")
        return 1


if __name__ == "__main__":
    sys.exit(main())