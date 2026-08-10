"""텍스트 임베딩."""
import logging
from typing import List, Optional

import requests

from core.config import RAGConfig


def get_embedding(session: requests.Session, config: RAGConfig, logger: logging.Logger, text: str) -> Optional[List[float]]:
    """텍스트를 임베딩 벡터로 변환. 장애 발생 시 None 반환 (호출부에서 키워드 검색으로 폴백)."""
    prefixed_text = f"query: {text}"  # Nemotron-1B 스펙시트 권장 프리픽스

    payload = {
        "input": [prefixed_text],
        "model": config.embed_model,
        "input_type": "query",
        "encoding_format": "float"
    }

    try:
        resp = session.post(
            config.embed_url,
            json=payload,
            timeout=(config.connect_timeout, config.embed_timeout)
        )
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]
    except requests.exceptions.RequestException as e:
        logger.error(f"🚨 NVIDIA 임베딩 API 서버 장애 발생 (Fallback 전환): {e}")
        return None
    except (KeyError, IndexError, ValueError) as e:
        logger.error(f"🚨 임베딩 API 응답 형식 오류 (Fallback 전환): {e}")
        return None
