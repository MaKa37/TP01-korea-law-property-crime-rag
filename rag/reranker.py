"""검색 후보 리랭킹 + 다양성 필터."""
import difflib
import logging
import re
from typing import Any, Dict, List

import requests

from core.config import RAGConfig

_CASE_ID_PATTERN = re.compile(r"\[([^\]]+)\]")


def rerank_candidates(
    session: requests.Session,
    config: RAGConfig,
    logger: logging.Logger,
    query: str,
    candidates: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """리랭크 API를 호출해 관련도 순으로 정렬한 후보 풀을 반환한다.

    ⚠️ 여기서 반환하는 건 최종 top_k가 아니라 top_k의 N배(config.rerank_pool_multiplier)
    크기의 "후보 풀"이다. 최종 top_k 선정은 select_diverse_top_k()에서
    다양성까지 고려해 별도로 수행한다.

    ⚠️ candidates는 이미 하이브리드 검색의 RRF 점수 순으로 정렬되어 들어온다
    (rag/retrieval.py의 SQL이 ORDER BY rrf_score DESC로 정렬함). CANDIDATE_K를
    올려서 candidates가 아무리 커져도, 리랭커에는 항상 상위 rerank_input_cap개
    까지만 넘긴다 - 리랭커에 넘기는 후보 수가 CANDIDATE_K에 비례해서 커지면
    리랭커가 비교해야 할 노이즈도 늘어나 오히려 순위가 흐트러지는 현상이
    실측으로 확인됐다 (하이퍼파라미터 튜닝에서 CANDIDATE_K=50이 30보다
    일관되게 나빴음). CANDIDATE_K는 "1차 검색을 얼마나 넓게 볼지"만 담당하고,
    리랭커가 보는 노이즈 양은 이 상한으로 독립적으로 고정한다.
    """
    if not candidates:
        return []

    candidates = candidates[:config.rerank_input_cap]
    pool_size = min(len(candidates), config.top_k * config.rerank_pool_multiplier)

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
        for rank in rankings[:pool_size]:
            idx = rank["index"]
            doc = candidates[idx]
            doc["rerank_score"] = rank.get("logit")
            reranked_docs.append(doc)
        return reranked_docs
    except Exception as e:
        logger.warning(f"Reranker 실패. 이전 검색 결과 유지: {e}")
        return candidates[:pool_size]


def _extract_case_id(title: str) -> str:
    match = _CASE_ID_PATTERN.search(title)
    return match.group(1) if match else title


def _dedupe_same_case_chunks(docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """같은 사건번호의 여러 청크(문단) 중 리랭크 점수가 가장 높은 것만 남긴다.

    한 판례가 여러 청크로 쪼개져 색인된 경우, 상위 후보군이 "서로 다른
    사건"이 아니라 "같은 사건의 다른 문단"으로 여러 자리를 차지하는
    경우가 있다 (실제로 q002에서 확인된 패턴). docs는 이미 점수 내림차순
    이므로, case_id별로 처음 등장하는 것이 항상 최고 점수다.
    """
    best_by_case: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for doc in docs:
        case_id = _extract_case_id(doc["title"])
        if case_id not in best_by_case:
            best_by_case[case_id] = doc
            order.append(case_id)
    return [best_by_case[cid] for cid in order]


def select_diverse_top_k(
    docs: List[Dict[str, Any]],
    top_k: int,
    similarity_threshold: float = 0.85
) -> List[Dict[str, Any]]:
    """리랭크 점수 순서를 최대한 지키면서 다양성 있는 top_k를 선정한다.

    1단계: 같은 사건번호의 중복 청크 제거 (가장 확실하고 저렴함)
    2단계: 서로 다른 사건이지만 내용이 크게 겹치는 문서(같은 대법원 판례를
           거의 그대로 인용하는 하급심 등) 제거. 전체 내용을 비교해야
           앞부분(사실관계)은 다르고 뒷부분(인용 법리)만 겹치는 경우도 잡아낸다.
           quick_ratio()는 근사치라 빠르다 — 후보 풀이 커도 부담 없다.
    """
    deduped = _dedupe_same_case_chunks(docs)

    selected: List[Dict[str, Any]] = []
    for doc in deduped:
        if len(selected) >= top_k:
            break
        is_duplicate = any(
            difflib.SequenceMatcher(None, doc["content"], s["content"]).quick_ratio() >= similarity_threshold
            for s in selected
        )
        if not is_duplicate:
            selected.append(doc)

    # 후보 전체가 서로 유사해서 top_k를 못 채운 경우, 남은 자리는
    # 중복이어도 리랭크 순서대로 채운다 (빈 자리로 남기지 않음)
    if len(selected) < top_k:
        selected_ids = {id(d) for d in selected}
        remaining = [d for d in deduped if id(d) not in selected_ids]
        selected.extend(remaining[: top_k - len(selected)])

    return selected