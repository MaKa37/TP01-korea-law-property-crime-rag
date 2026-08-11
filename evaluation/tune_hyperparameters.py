"""검색 파이프라인 하이퍼파라미터 자동 튜닝.

CANDIDATE_K(1차 검색 후보 수), RERANK_POOL_MULTIPLIER(리랭크 풀 크기),
DIVERSITY_SIMILARITY_THRESHOLD(다양성 필터 임계값) 조합을 스윕하며
골든셋 기준 MRR/Recall@k가 가장 좋은 조합을 찾는다.

LLM 생성은 호출하지 않고 검색(retrieve)만 반복하므로 비교적 빠르고
저렴하다. 다만 골든셋이 아주 작으면(지금처럼 3건 미만) 결과가 통계적으로
불안정할 수 있다 — 이 스크립트는 신뢰도 있는 결론이 아니라 "다음에
시도해볼 방향"을 좁혀주는 용도로 쓰는 게 안전하다.

사용법:
    python evaluation/tune_hyperparameters.py
    python evaluation/tune_hyperparameters.py --candidate-k 30 50 80 --pool-multiplier 3 5 --diversity 0.75 0.85 0.95
"""
import argparse
import itertools
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import RAGConfig  # noqa: E402
from rag.bot import LegalRAGBot  # noqa: E402
from evaluation.metrics import aggregate, evaluate_single  # noqa: E402
from evaluation.run_eval import extract_case_id, load_golden_set  # noqa: E402

GOLDEN_SET_PATH = Path(__file__).resolve().parent / "golden_set.jsonl"


def run_combo(bot: LegalRAGBot, combo_config: RAGConfig, golden_set: List[Dict[str, Any]], k_values: List[int]) -> Dict[str, float]:
    # ⚠️ 튜닝 스크립트 전용 트릭: 조합마다 DB풀/세션을 새로 만들지 않고
    # config 객체만 바꿔치기한다 (nim_api_key/DB 접속 정보는 조합 간 동일하므로 안전함).
    bot.config = combo_config

    per_query_results = []
    for item in golden_set:
        relevant_ids = item.get("relevant_case_ids", [])
        if not relevant_ids:
            continue
        docs = bot.retrieve(item["query"])
        retrieved_ids = [extract_case_id(d["title"]) for d in docs]
        per_query_results.append(evaluate_single(retrieved_ids, relevant_ids, k_values))

    return aggregate(per_query_results)


def main() -> None:
    parser = argparse.ArgumentParser(description="검색 하이퍼파라미터 자동 튜닝")
    parser.add_argument("--candidate-k", type=int, nargs="+", default=[30, 50], help="1차 검색 후보 수 (기본: 30 50)")
    parser.add_argument("--pool-multiplier", type=int, nargs="+", default=[3, 5], help="리랭크 풀 배수 (기본: 3 5)")
    parser.add_argument("--diversity", type=float, nargs="+", default=[0.75, 0.85, 0.95], help="다양성 필터 임계값 (기본: 0.75 0.85 0.95)")
    parser.add_argument("--k", type=int, nargs="+", default=[3, 5], help="Recall@k 계산할 k 값들")
    parser.add_argument(
        "--output", type=Path,
        default=Path(__file__).resolve().parent / "reports" / f"tuning_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    args = parser.parse_args()

    golden_set = load_golden_set(GOLDEN_SET_PATH)
    n_labeled = sum(1 for i in golden_set if i.get("relevant_case_ids"))
    print(f"정답이 있는 골든셋: {n_labeled}건")
    if n_labeled < 10:
        print(f"⚠️ {n_labeled}건은 튜닝 결론을 신뢰하기엔 적습니다 (최소 10건 이상 권장). "
              "지금 결과는 참고용으로만 쓰고, 골든셋을 늘린 뒤 다시 돌려보세요.\n")

    base_config = RAGConfig()
    combos = list(itertools.product(args.candidate_k, args.pool_multiplier, args.diversity))
    print(f"총 {len(combos)}개 조합을 테스트합니다 (골든셋 {n_labeled}건 × 조합 {len(combos)}개 = 검색 {n_labeled * len(combos)}회)...\n")

    results = []
    with LegalRAGBot(base_config) as bot:
        for candidate_k, pool_mult, diversity in combos:
            combo_config = replace(
                base_config,
                candidate_k=candidate_k,
                rerank_pool_multiplier=pool_mult,
                diversity_similarity_threshold=diversity,
            )
            summary = run_combo(bot, combo_config, golden_set, args.k)
            results.append({
                "candidate_k": candidate_k,
                "rerank_pool_multiplier": pool_mult,
                "diversity_similarity_threshold": diversity,
                "metrics": summary,
            })
            mrr = summary.get("mrr", float("nan"))
            recall_last = summary.get(f"recall@{args.k[-1]}", float("nan"))
            print(f"  candidate_k={candidate_k:>3}  pool_mult={pool_mult}  diversity={diversity:.2f}  "
                  f"->  MRR={mrr:.3f}  Recall@{args.k[-1]}={recall_last:.3f}")

    def sort_key(r):
        m = r["metrics"]
        mrr = m.get("mrr") or 0
        recall_last = m.get(f"recall@{args.k[-1]}") or 0
        return (mrr, recall_last)

    results.sort(key=sort_key, reverse=True)
    best = results[0]

    print("\n" + "=" * 50)
    print("🏆 최적 조합 (지금 골든셋 기준):")
    print(f"   CANDIDATE_K={best['candidate_k']}")
    print(f"   RERANK_POOL_MULTIPLIER={best['rerank_pool_multiplier']}")
    print(f"   DIVERSITY_SIMILARITY_THRESHOLD={best['diversity_similarity_threshold']}")
    print(f"   MRR={best['metrics'].get('mrr', float('nan')):.3f}  "
          f"Recall@{args.k[-1]}={best['metrics'].get(f'recall@{args.k[-1]}', float('nan')):.3f}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump({
            "run_at": datetime.now(timezone.utc).isoformat(),
            "golden_set_size": n_labeled,
            "results": results,
        }, f, ensure_ascii=False, indent=2)

    print(f"\n💾 전체 결과(모든 조합): {args.output}")
    print(".env에 위 값을 반영한 뒤 python evaluation/run_eval.py로 최종 재확인하세요.")


if __name__ == "__main__":
    main()