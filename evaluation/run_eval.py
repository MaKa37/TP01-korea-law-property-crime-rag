"""검색 품질 배치 평가 실행기.

사용법:
    python evaluation/run_eval.py
    python evaluation/run_eval.py --golden-set evaluation/golden_set.jsonl --k 3 5

LLM 생성은 호출하지 않고 검색(임베딩→하이브리드/키워드 검색→리랭킹)만 실행하므로
빠르고 비용이 들지 않는다. `LegalRAGBot.retrieve()`를 재사용한다.
"""
import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

# project_root/legal_rag_bot.py 를 import 하기 위해 상위 디렉터리를 경로에 추가
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import RAGConfig  # noqa: E402
from rag.bot import LegalRAGBot  # noqa: E402
from evaluation.metrics import evaluate_single, aggregate  # noqa: E402

CASE_ID_PATTERN = re.compile(r"\[([^\]]+)\]")


def extract_case_id(title: str) -> str:
    """판례 제목 '판례 [99도4923] ...' 에서 사건번호 '99도4923'을 추출한다.

    형식이 다른 문서(법령용어 등)는 제목 전체를 식별자로 사용한다.
    """
    match = CASE_ID_PATTERN.search(title)
    return match.group(1) if match else title


def load_golden_set(path: Path) -> List[Dict[str, Any]]:
    items = []
    with open(path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{line_no} JSON 파싱 실패: {e}") from e
    return items


def run(golden_set_path: Path, k_values: List[int], output_path: Path) -> None:
    golden_set = load_golden_set(golden_set_path)
    print(f"골든셋 {len(golden_set)}건 로드 완료: {golden_set_path}")

    config = RAGConfig()
    per_query_results = []
    per_query_details = []

    with LegalRAGBot(config) as bot:
        for item in golden_set:
            query = item["query"]
            relevant_ids = item.get("relevant_case_ids", [])

            if not relevant_ids:
                print(f"⏭️  [{item['id']}] 정답(relevant_case_ids)이 없어 건너뜁니다: {query[:40]}...")
                continue

            top_docs = bot.retrieve(query)
            retrieved_ids = [extract_case_id(doc["title"]) for doc in top_docs]

            metrics = evaluate_single(retrieved_ids, relevant_ids, k_values)
            per_query_results.append(metrics)
            per_query_details.append({
                "id": item["id"],
                "query": query,
                "relevant_case_ids": relevant_ids,
                "retrieved_case_ids": retrieved_ids,
                "metrics": metrics,
            })

            print(f"✅ [{item['id']}] MRR={metrics['mrr']:.3f} "
                  + " ".join(f"Recall@{k}={metrics[f'recall@{k}']:.2f}" for k in k_values))

    summary = aggregate(per_query_results)

    report = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "golden_set": str(golden_set_path),
        "k_values": k_values,
        "summary": summary,
        "per_query": per_query_details,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 50)
    print("📊 요약 (정답이 있는 질의 기준)")
    print(f"   평가된 질의: {summary.get('n_evaluated', 0)} / {summary.get('n_total', 0)}")
    print(f"   MRR: {summary.get('mrr', float('nan')):.3f}")
    for k in k_values:
        print(f"   Recall@{k}: {summary.get(f'recall@{k}', float('nan')):.3f}"
              f"   Hit@{k}: {summary.get(f'hit@{k}', float('nan')):.3f}")
    print(f"\n💾 상세 리포트 저장: {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RAG 검색 품질 배치 평가")
    parser.add_argument(
        "--golden-set",
        type=Path,
        default=Path(__file__).resolve().parent / "golden_set.jsonl",
        help="골든셋 JSONL 파일 경로",
    )
    parser.add_argument(
        "--k",
        type=int,
        nargs="+",
        default=[3, 5],
        help="Recall@k / Hit@k 를 계산할 k 값들 (기본: 3 5)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parent / "reports" / f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
        help="결과 리포트를 저장할 경로",
    )
    args = parser.parse_args()

    run(args.golden_set, args.k, args.output)
