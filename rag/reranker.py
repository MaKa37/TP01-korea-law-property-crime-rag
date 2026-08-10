"""검색 후보 리랭킹."""
import logging
from typing import Any, Dict, List

import requests

from core.config import RAGConfig


def rerank_candidates(
    session: requests.Session,
    config: RAGConfig,
    logger: logging.Logger,
    query: str,
    candidates: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    if not candidates:
        return []

    passages = [{"text": f"[{c['doc_type']}] {c['title']} {c['content']}"} for c in candidates]
    payload = {
        "model": config.rerank_model,
        "query": {"text": query},
        "passages": passages
    }

    try:
        resp = session.post(
            config.rerank_url,
            json=payload,
            timeout=(config.connect_timeout, config.rerank_timeout)
        )
        resp.raise_for_status()
        rankings = resp.json().get("rankings", [])

        reranked_docs = []
        for rank in rankings[:config.top_k]:
            idx = rank["index"]
            doc = candidates[idx]
            doc["rerank_score"] = rank.get("logit")
            reranked_docs.append(doc)
        return reranked_docs
    except Exception as e:
        logger.warning(f"Reranker 실패. 이전 검색 결과 유지: {e}")
        return candidates[:config.top_k]
