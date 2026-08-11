"""질의 라우팅 (의도 분류).

모든 메시지에 대해 검색→리랭킹→550B 생성을 다 태우는 건 낭비다.
잡담/범위 밖 질문은 가벼운 모델로 먼저 걸러낸다.
"""
import json
import logging
from typing import Dict, List

import requests

from core.config import RAGConfig

VALID_INTENTS = ("legal_query", "chitchat", "out_of_scope")

ROUTER_SYSTEM_PROMPT = """당신은 법률 상담 챗봇의 라우터입니다. 사용자의 마지막 메시지를 다음 중 하나로 분류하세요.

- legal_query: 사기·재산범죄 관련 법률 질문, 법적 조치/증거/절차 문의. 직전 대화의 후속 질문(예: "그럼 공소시효는요?")도 포함.
- chitchat: 인사, 감사 표현, 서비스 자체에 대한 잡담 등 법률 상담이 아닌 대화.
- out_of_scope: 법률과 무관한 질문(날씨, 요리, 프로그래밍, 다른 나라 법 등).

판단이 애매하면 legal_query로 분류하세요 (실제 법률 질문을 놓치는 것이 더 위험함).

반드시 아래 JSON 형식으로만 답하고, 다른 텍스트는 절대 추가하지 마십시오.
{"intent": "legal_query" 또는 "chitchat" 또는 "out_of_scope"}
"""


def classify_intent(
    session: requests.Session,
    config: RAGConfig,
    logger: logging.Logger,
    query: str,
    history: List[Dict[str, str]]
) -> str:
    messages = [{"role": "system", "content": ROUTER_SYSTEM_PROMPT}]
    for turn in history[-4:]:
        messages.append({"role": "user", "content": turn["user"]})
        messages.append({"role": "assistant", "content": turn["assistant"][:200]})
    messages.append({"role": "user", "content": query})

    payload = {
        "model": config.utility_model,
        "messages": messages,
        "temperature": 0.0,
        "max_tokens": 50,
        "stream": False,
    }

    try:
        resp = session.post(
            config.chat_url,
            json=payload,
            timeout=(config.connect_timeout, config.chat_timeout),
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"].strip().strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
        parsed = json.loads(text)
        intent = parsed.get("intent")
        return intent if intent in VALID_INTENTS else "legal_query"
    except Exception as e:  # noqa: BLE001 - 라우팅 실패는 legal_query로 안전하게 폴백
        logger.warning(f"⚠️ 라우팅 실패, 기본값(legal_query)으로 진행: {e}")
        return "legal_query"
