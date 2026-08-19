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
    """NVIDIA Chat Completion API를 SSE로 호출해 토큰을 하나씩 yield하는 저수준 제너레이터."""
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
        for raw_line_bytes in resp.iter_lines():
            if not raw_line_bytes:
                continue
            raw_line = raw_line_bytes.decode("utf-8", errors="strict")
            if "\ufffd" in raw_line:
                idx = raw_line.index("\ufffd")
                context = raw_line[max(0, idx - 20):idx + 20]
                logger.warning(
                    f"⚠️ 업스트림 응답에 U+FFFD(깨진 문자)가 이미 포함되어 있음. "
                    f"주변 텍스트: ...{context}..."
                )
            if not raw_line.startswith("data:"):
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
            
            reasoning_piece = delta.get("reasoning_content") or delta.get("reasoning")
            if reasoning_piece:
                reasoning_chars += len(reasoning_piece)
            reason = choices[0].get("finish_reason")
            if reason:
                finish_reason = reason
    except requests.exceptions.RequestException as e:
        logger.error(f"🚨 스트리밍 중 연결 오류: {e}")
        raise RuntimeError(f"답변 생성 스트리밍 중 연결이 끊겼습니다: {e}") from e

    if not content_emitted and finish_reason == "length":
        logger.warning(
            f"⚠️ content를 하나도 받지 못한 채 max_tokens({config.max_tokens})에 도달했습니다 "
            f"(reasoning 필드 길이: {reasoning_chars}자)."
        )
    elif finish_reason == "length":
        logger.warning(f"⚠️ max_tokens({config.max_tokens})에 도달하여 답변이 중간에 잘렸을 수 있습니다.")


def generate_response_stream(
    session: requests.Session,
    config: RAGConfig,
    logger: logging.Logger,
    query: str,
    retrieved_docs: List[Dict[str, Any]]
) -> Iterator[str]:
    """API 서비스 계층용: 토큰을 그대로 yield한다."""
    yield from _stream_chat_tokens(session, config, logger, query, retrieved_docs)


def generate_response(
    session: requests.Session,
    config: RAGConfig,
    logger: logging.Logger,
    query: str,
    retrieved_docs: List[Dict[str, Any]]
) -> str:
    """CLI/평가 하네스용: 토큰을 전부 모아 완성된 문자열로 반환."""
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
    """LLM 호출 실패 시 검색된 원문 자료를 제공하는 대체 답변."""
    lines = [
        "⚠️ 현재 AI 답변 생성 서비스에 일시적인 장애가 발생하여, "
        "검색된 참고 자료 원문을 대신 안내해 드립니다.\n"
    ]
    for i, doc in enumerate(retrieved_docs, 1):
        content = doc.get("full_text") or doc.get("content", "")
        lines.append(f"### {i}. {doc.get('title', '제목없음')} ({doc.get('doc_type', '판례')})")
        lines.append(content)
        lines.append("")
    lines.append(
        "> ⚠️ **면책 조항:** 위 자료는 AI 요약 없이 제공되는 원문 발췌이며, "
        "법적 조언이 아닙니다. 정확한 판단을 위해 대한법률구조공단(국번없이 132)이나 "
        "전문 변호사의 상담을 받으시기 바랍니다."
    )
    return "\n".join(lines)