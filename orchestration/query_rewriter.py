"""멀티턴 질의 재작성 (Condense Question).

대화 히스토리를 기반으로 사용자의 후속 질문(Follow-up Query)을 
문맥이 포함된 '독립적인 단일 질의(Standalone Query)'로 변환하여 검색 품질을 높입니다.
"""
import logging
from typing import Dict, List

import requests

from core.config import RAGConfig

# =========================================================================
# 1. 프롬프트 및 상수 정의 (설정값 분리)
# =========================================================================
REWRITE_SYSTEM_PROMPT = """당신은 사용자의 질문을 검색 엔진용으로 재구성하는(Query Rewriter) AI입니다.
[대화 히스토리]를 참고하여 [마지막 질문]을 맥락 없이도 이해할 수 있는 "독립적인 질문"으로 다시 작성하세요.

[필수 규칙]
1. 질문에 절대 "답변"하지 마세요. 질문을 명확하게 다시 적기만 해야 합니다.
2. 지시대명사(그거, 이 사람 등)나 생략된 주어를 히스토리에서 찾아 명확한 명사로 치환하세요.
3. 어떠한 부가 설명이나 인사말도 포함하지 마세요.

[예시]
이전 질문: 이사가 담보 없이 회사자금을 빌려주면 배임죄인가요?
마지막 질문: 그럼 공소시효는 어떻게 돼?
재작성 결과: 업무상배임죄의 공소시효는 몇 년인가요?
"""

MAX_HISTORY_TURNS = 4          # 컨텍스트에 포함할 최근 대화 턴 수
MAX_ASSISTANT_TEXT_LEN = 300   # 봇 응답 중 프롬프트에 포함할 최대 글자 수
MAX_GENERATION_TOKENS = 100    # 재작성된 질의의 최대 토큰 수 (답변 생성 방지를 위해 짧게 제한)
MIN_VALID_REWRITE_LEN = 8      # 유효한 재작성 결과의 최소 글자 수

# 유효한 질문 형태인지 검증하기 위한 마커 (휴리스틱 필터링 용도)
QUESTION_MARKERS = ("?", "까요", "나요", "가요", "습니까", "인가요", "인지", "여부", "궁금", "무엇", "어떻게", "어떤")


# =========================================================================
# 2. 내부 검증(Guardrail) 함수
# =========================================================================
def _is_invalid_rewrite(text: str) -> bool:
    """LLM의 생성 결과가 올바른 '질문' 형태인지 검증합니다.

    경량화 모델(8B 등)이 지시를 무시하고 단답형 답변(예: "5년입니다")을 
    생성하는 Fail-case를 방지하기 위한 휴리스틱 방어 로직입니다.
    """
    stripped_text = text.strip()
    
    # 조건 1: 너무 짧은 경우 (단답형 답변일 확률이 높음)
    if len(stripped_text) < MIN_VALID_REWRITE_LEN:
        return True
        
    # 조건 2: 질문을 나타내는 어미나 마커가 전혀 없는 경우
    if not any(marker in stripped_text for marker in QUESTION_MARKERS):
        return True
        
    return False


# =========================================================================
# 3. 메인 파이프라인 함수
# =========================================================================
def rewrite_query(
    session: requests.Session,
    config: RAGConfig,
    logger: logging.Logger,
    history: List[Dict[str, str]],
    query: str
) -> str:
    """멀티턴 맥락을 반영하여 질의를 재작성합니다.

    Args:
        session: HTTP 요청을 위한 requests.Session 객체
        config: RAG 설정 객체
        logger: 로깅 객체
        history: 이전 대화 내역 [{"user": "...", "assistant": "..."}, ...]
        query: 사용자의 현재 질문

    Returns:
        str: 재작성된 질의. (오류 또는 필터링 시 원본 질의/안전한 폴백 반환)
    """
    # [Phase 1] Early Exit: 히스토리가 없으면 재작성 불필요
    if not history:
        return query

    # [Phase 2] Context Building: 프롬프트 메시지 구성
    messages = [{"role": "system", "content": REWRITE_SYSTEM_PROMPT}]
    
    # 최근 N번의 턴만 슬라이싱하여 컨텍스트 윈도우 초과 방지
    recent_history = history[-MAX_HISTORY_TURNS:]
    for turn in recent_history:
        messages.append({"role": "user", "content": turn["user"]})
        # 봇의 응답은 맥락만 알면 되므로 길이 제한 적용
        messages.append({"role": "assistant", "content": turn["assistant"][:MAX_ASSISTANT_TEXT_LEN]})
        
    messages.append({"role": "user", "content": f"[마지막 질문]\n{query}"})

    # [Phase 3] Payload & API Request
    payload = {
        "model": config.utility_model,
        "messages": messages,
        "temperature": 0.0,  # 창의성을 배제하고 결정론적(정확한) 결과 유도
        "max_tokens": MAX_GENERATION_TOKENS,
        "stream": False,
    }

    try:
        resp = session.post(
            config.chat_url,
            json=payload,
            timeout=(config.connect_timeout, config.chat_timeout),
        )
        resp.raise_for_status()
        
        rewritten_query = resp.json()["choices"][0]["message"]["content"].strip()

        if not rewritten_query:
            return query

        # [Phase 4] Guardrail: 결과물 품질 검증 및 폴백 처리
        if _is_invalid_rewrite(rewritten_query):
            logger.warning(
                f"⚠️ 질의 재작성 폴백 (답변 생성 의심). "
                f"원문: '{query}' -> 생성결과: '{rewritten_query}'"
            )
            # 이전 질문의 주어를 포함하여 강제로 단순 결합 (가장 안전한 Fallback)
            last_user_query = history[-1]['user']
            return f"{query} ({last_user_query} 관련 후속 질문)"

        return rewritten_query

    # [Phase 5] Exception Handling: 통신 오류 및 기타 장애 발생 시 원본 반환
    except requests.exceptions.RequestException as req_err:
        logger.error(f"🚨 질의 재작성 API 통신 오류: {req_err}")
        return query
    except Exception as e:
        logger.exception(f"🚨 질의 재작성 중 예기치 않은 오류 발생: {e}")
        return query