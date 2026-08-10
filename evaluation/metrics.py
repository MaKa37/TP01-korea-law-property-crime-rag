"""검색 품질 평가 지표 (Recall@k, MRR).

골든셋의 각 질의는 "정답 판례/법령 식별자(예: 사건번호 99도4923)" 목록을
갖고 있고, 실제 검색 결과에서 뽑아낸 식별자 목록과 비교해 지표를 계산한다.
"""
from typing import Dict, List, Sequence


def recall_at_k(retrieved_ids: Sequence[str], relevant_ids: Sequence[str], k: int) -> float:
    """상위 k개 검색 결과 안에, 정답 중 몇 %가 포함되어 있는지."""
    if not relevant_ids:
        return float("nan")  # 정답이 아직 없는 질의는 집계에서 제외
    top_k = set(retrieved_ids[:k])
    hit = sum(1 for rid in relevant_ids if rid in top_k)
    return hit / len(relevant_ids)


def hit_at_k(retrieved_ids: Sequence[str], relevant_ids: Sequence[str], k: int) -> float:
    """상위 k개 안에 정답이 '하나라도' 있으면 1, 없으면 0."""
    if not relevant_ids:
        return float("nan")
    top_k = set(retrieved_ids[:k])
    return 1.0 if any(rid in top_k for rid in relevant_ids) else 0.0


def mrr(retrieved_ids: Sequence[str], relevant_ids: Sequence[str]) -> float:
    """Mean Reciprocal Rank: 가장 먼저 등장하는 정답의 순위 역수."""
    if not relevant_ids:
        return float("nan")
    relevant_set = set(relevant_ids)
    for rank, rid in enumerate(retrieved_ids, start=1):
        if rid in relevant_set:
            return 1.0 / rank
    return 0.0


def evaluate_single(retrieved_ids: Sequence[str], relevant_ids: Sequence[str], k_values: Sequence[int]) -> Dict[str, float]:
    """질의 하나에 대한 모든 지표를 한 번에 계산."""
    result: Dict[str, float] = {"mrr": mrr(retrieved_ids, relevant_ids)}
    for k in k_values:
        result[f"recall@{k}"] = recall_at_k(retrieved_ids, relevant_ids, k)
        result[f"hit@{k}"] = hit_at_k(retrieved_ids, relevant_ids, k)
    return result


def aggregate(per_query_results: List[Dict[str, float]]) -> Dict[str, float]:
    """질의별 지표를 평균내어 데이터셋 전체 요약을 만든다. NaN(정답 미입력 질의)은 제외."""
    if not per_query_results:
        return {}

    keys = per_query_results[0].keys()
    summary: Dict[str, float] = {}
    for key in keys:
        values = [r[key] for r in per_query_results if r[key] == r[key]]  # NaN 제외 (NaN != NaN)
        summary[key] = sum(values) / len(values) if values else float("nan")
    summary["n_evaluated"] = sum(
        1 for r in per_query_results if r.get("mrr") == r.get("mrr")
    )
    summary["n_total"] = len(per_query_results)
    return summary
