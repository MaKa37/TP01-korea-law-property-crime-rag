"""LLM 답변 생성 (SSE 스트리밍)."""
import json
import logging
from typing import Any, Dict, Iterator, List, Optional

import requests

from core.config import RAGConfig
from rag.prompts import SYSTEM_PROMPT


def _stream_chat_tokens(
    session: requests.Session,
    config: RAGConfig,
    logger: logging.Logger,
    query: str,
    retrieved_docs: List[Dict[str, Any]]
) -> Iterator[str]:
    """NVIDIA Chat Completion API를 SSE로 호출해 토큰을 하나씩 yield하는 저수준 제너레이터.

    550B급 대형 모델은 전체 응답 생성에 수십 초~수 분이 걸릴 수 있다.
    stream=True로 SSE를 받아 "토큰이 끊기지 않는 한" 타임아웃이 나지
    않도록 처리한다. timeout=(connect, read)에서 read는 전체 생성 시간이
    아니라 "다음 청크가 올 때까지의 대기 시간"이다.

    CLI(blocking 수집+콘솔 출력)와 API(그대로 클라이언트로 릴레이) 양쪽에서
    이 제너레이터 하나를 공유한다. 예외는 호출부에서 처리하도록 그대로 전파한다.
    """
    context_str = "\n\n".join(
        f"[문서 {i+1}] (출처: {doc['title']})\n{doc['content']}"
        for i, doc in enumerate(retrieved_docs)
    )
    user_content = f"[검색된 참고 자료]\n{context_str}\n\n[사용자 질문]\n{query}"

    payload = {
        "model": config.chat_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content}
        ],
        "temperature": 0.1,
        "max_tokens": config.max_tokens,
        "stream": True
    }

    try:
        resp = session.post(
            config.chat_url,
            json=payload,
            timeout=(config.connect_timeout, config.chat_timeout),
            stream=True
        )
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error(f"🚨 NVIDIA Chat Completion API 호출 실패: {e}")
        raise RuntimeError(f"답변 생성 LLM 호출에 실패했습니다: {e}") from e

    finish_reason: Optional[str] = None
    content_emitted = False
    reasoning_chars = 0
    try:
        for raw_line in resp.iter_lines(decode_unicode=True):
            if not raw_line or not raw_line.startswith("data:"):
                continue
            data_str = raw_line[len("data:"):].strip()
            if data_str == "[DONE]":
                break
            try:
                event = json.loads(data_str)
            except json.JSONDecodeError:
                continue
            choices = event.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta", {})
            token = delta.get("content")
            if token:
                content_emitted = True
                yield token
            # nemotron-3-ultra 같은 추론(reasoning) 모델은 최종 답변 전에
            # "생각하는" 토큰을 reasoning_content(또는 reasoning) 필드에 먼저
            # 쓴다. content가 하나도 안 나왔는데 max_tokens에 도달했다면,
            # 이 필드가 얼마나 찼는지가 원인 판단에 결정적인 단서가 된다.
            reasoning_piece = delta.get("reasoning_content") or delta.get("reasoning")
            if reasoning_piece:
                reasoning_chars += len(reasoning_piece)

                if config.stream_print:
                    # 터미널에서 구분이 잘 되도록 진한 회색(\033[90m)으로 출력합니다.
                    print(f"\033[90m{reasoning_piece}\033[0m", end="", flush=True)
                    
            reason = choices[0].get("finish_reason")
            if reason:
                finish_reason = reason
    except requests.exceptions.RequestException as e:
        logger.error(f"🚨 스트리밍 중 연결 오류: {e}")
        raise RuntimeError(f"답변 생성 스트리밍 중 연결이 끊겼습니다: {e}") from e

    if not content_emitted and finish_reason == "length":
        # content가 전혀 없는 채로 max_tokens에 도달 -> "반복 루프"보다는
        # 추론 토큰이 전체 예산을 다 써버렸을 가능성이 높은 패턴이다.
        # (진짜 반복 루프였다면 반복되는 텍스트라도 content에 쌓였을 것)
        logger.warning(
            f"⚠️ content를 하나도 받지 못한 채 max_tokens({config.max_tokens})에 도달했습니다 "
            f"(reasoning 필드 길이: {reasoning_chars}자). 추론 토큰이 예산을 전부 소진했을 "
            "가능성이 높습니다. MAX_TOKENS를 더 늘리거나, 이 현상의 재현 빈도를 지켜보세요."
        )
    elif finish_reason == "length":
        logger.warning(
            f"⚠️ max_tokens({config.max_tokens})에 도달하여 답변이 중간에 "
            "잘렸을 수 있습니다. MAX_TOKENS 값을 늘리는 것을 고려하세요."
        )


def generate_response_stream(
    session: requests.Session,
    config: RAGConfig,
    logger: logging.Logger,
    query: str,
    retrieved_docs: List[Dict[str, Any]]
) -> Iterator[str]:
    """API 서비스 계층용: 토큰을 그대로 yield한다. 호출부(FastAPI 라우트)가 SSE로 감싼다."""
    yield from _stream_chat_tokens(session, config, logger, query, retrieved_docs)


def generate_response(
    session: requests.Session,
    config: RAGConfig,
    logger: logging.Logger,
    query: str,
    retrieved_docs: List[Dict[str, Any]]
) -> str:
    """CLI/평가 하네스용: 토큰을 전부 모아 완성된 문자열로 반환. 실패 시 예외를 발생시킨다."""
    collected: List[str] = []
    if config.stream_print:
        print("\n🤖 [AI 어시스턴트 답변] (실시간 생성 중)\n", flush=True)

    try:
        for token in _stream_chat_tokens(session, config, logger, query, retrieved_docs):
            collected.append(token)
            if config.stream_print:
                print(token, end="", flush=True)
    except (KeyError, IndexError, ValueError) as e:
        logger.error(f"🚨 LLM 응답 파싱 실패: {e}")
        raise RuntimeError(f"LLM 응답 형식이 올바르지 않습니다: {e}") from e
    finally:
        if config.stream_print:
            print()

    answer = "".join(collected).strip()
    if not answer:
        raise RuntimeError("스트리밍 응답에서 콘텐츠를 받지 못했습니다.")
    return answer


def build_fallback_answer(retrieved_docs: List[Dict[str, Any]]) -> str:
    """LLM 호출이 실패했을 때, 검색된 원문 자료라도 정리해서 보여주는 대체 답변."""
    lines = [
        "⚠️ 현재 AI 답변 생성 서비스에 일시적인 장애가 발생하여, "
        "검색된 참고 자료 원문을 대신 안내해 드립니다.\n"
    ]
    for i, doc in enumerate(retrieved_docs, 1):
        content = doc["content"]
        snippet = content if len(content) <= 500 else content[:500] + "..."
        lines.append(f"### {i}. {doc['title']} ({doc.get('doc_type', '')})")
        lines.append(snippet)
        lines.append("")
    lines.append(
        "> ⚠️ **면책 조항:** 위 자료는 AI 요약 없이 제공되는 원문 발췌이며, "
        "법적 조언이 아닙니다. 정확한 판단을 위해 대한법률구조공단(국번없이 132)이나 "
        "전문 변호사의 상담을 받으시기 바랍니다."
    )
    return "\n".join(lines)