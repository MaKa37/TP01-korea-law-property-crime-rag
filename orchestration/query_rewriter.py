"""멀티턴 질의 재작성 (Condense Question).

"그럼 공소시효는요?" 같은 후속 질문을 그대로 임베딩하면 검색이 산으로
간다. 대화 히스토리 + 새 질문을 "독립적으로 이해 가능한 질문"으로
재작성한 뒤 검색 파이프라인에 넘긴다.
"""
import json
import logging
from typing import Dict, List

import requests

from core.config import RAGConfig

REWRITE_SYSTEM_PROMPT = """당신은 대화 맥락을 보고 질문을 다시 "쓰는" 보조 도구입니다.
[대화 히스토리]를 참고하여 [마지막 질문]을 "히스토리 없이도 이해 가능한 독립적인 질문"으로 다시 작성하세요.

⚠️ 가장 중요한 규칙: 당신의 역할은 질문을 재구성하는 것이지, 질문에 "답변"하는 것이 절대 아닙니다.
절대로 답을 생성하지 마세요. 결과물은 반드시 질문 형태의 문장이어야 합니다.

기타 규칙:
- 이미 맥락 없이도 이해 가능한 질문이면 원문을 그대로 반환하세요.
- 재작성된 "질문 문장" 하나만 반환하고, 다른 설명/인사말/답변은 절대 추가하지 마세요.
- 히스토리에 없는 사실을 새로 추가하지 마세요.

예시:
[이전 질문] 회사 이사가 담보 없이 회사자금을 대여한 경우 배임죄가 성립하나요?
[마지막 질문] 그럼 공소시효는 몇 년이야?
[올바른 재작성] 업무상배임죄의 공소시효는 몇 년인가요?
[잘못된 예시 - 절대 이렇게 하지 말 것, 이건 재작성이 아니라 답변임] 5년
"""

_MIN_REWRITE_LEN = 8
_QUESTION_MARKERS = ("?", "까요", "나요", "가요", "습니까", "인가요", "인지", "여부", "궁금", "무엇", "어떻게", "어떤")


def _looks_like_answer_not_question(text: str) -> bool:
    """재작성 결과가 질문이 아니라 답변처럼 보이면 True.

    8B 경량 모델이 가끔 "재작성하라"는 지시를 무시하고 질문에 바로
    답변해버리는 실패 사례(예: "5년")를 걸러내기 위한 안전장치.
    완벽한 검증은 아니지만, 지나치게 짧거나 질문의 형태를 전혀
    갖추지 않은 결과는 대부분 이 실패 케이스다.
    """
    stripped = text.strip()
    if len(stripped) < _MIN_REWRITE_LEN:
        return True
    return not any(marker in stripped for marker in _QUESTION_MARKERS)


def rewrite_query(
    session: requests.Session,
    config: RAGConfig,
    logger: logging.Logger,
    history: List[Dict[str, str]],
    query: str
) -> str:
    if not history:
        return query  # 첫 턴은 재작성할 맥락이 없음

    messages = [{"role": "system", "content": REWRITE_SYSTEM_PROMPT}]
    for turn in history[-4:]:  # 최근 4턴만 사용 (프롬프트 비대화 방지)
        messages.append({"role": "user", "content": turn["user"]})
        messages.append({"role": "assistant", "content": turn["assistant"][:300]})
    messages.append({"role": "user", "content": f"[마지막 질문]\n{query}"})

    payload = {
        "model": config.utility_model,
        "messages": messages,
        "temperature": 0.0,
        "max_tokens": 300,
        "stream": False,
    }

    try:
        resp = session.post(
            config.chat_url,
            json=payload,
            timeout=(config.connect_timeout, config.chat_timeout),
        )
        resp.raise_for_status()
        rewritten = resp.json()["choices"][0]["message"]["content"].strip()

        if not rewritten:
            return query

        if _looks_like_answer_not_question(rewritten):
            # 모델이 재작성 대신 답변을 생성해버린 것으로 보임 (예: "5년").
            # 이 값을 그대로 검색 쿼리로 쓰면 완전히 엉뚱한 문서가 검색되므로,
            # 이전 질문과 단순 결합한 안전한 형태로 폴백한다.
            logger.warning(
                f"⚠️ 질의 재작성 결과가 질문이 아닌 답변처럼 보여 폴백합니다: '{rewritten}'"
            )
            return f"{query} ({history[-1]['user']} 관련 후속 질문)"

        return rewritten
    except Exception as e:  # noqa: BLE001 - 재작성 실패는 원본 질문으로 안전하게 폴백
        logger.warning(f"⚠️ 질의 재작성 실패, 원본 질문으로 진행: {e}")
        return query