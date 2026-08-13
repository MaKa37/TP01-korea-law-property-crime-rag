"""서로 다른 채팅 모델의 답변 품질을 나란히 비교하는 도구.

검색(retrieve)은 질의당 딱 한 번만 수행하고, 그 결과로 여러 모델의
생성 결과만 비교한다 — 검색 품질 차이가 섞이지 않고 순수하게
"생성 모델 차이"만 보기 위함이다.

⚠️ LLM 자동 채점은 하지 않는다. 이전에 골든셋 자동 라벨링에서 확인했듯,
LLM 판정은 완벽하지 않고 법률 답변처럼 미묘한 품질 차이가 중요한
영역에서는 더더욱 그렇다. 대신 사람이 나란히 읽고 판단할 수 있는
마크다운 리포트를 만들어준다.

사용법:
    # 골든셋 앞에서부터 3개 질의로 비교
    python evaluation/compare_models.py --models nvidia/nemotron-3-ultra-550b-a55b meta/llama-3.1-70b-instruct

    # 특정 질의 지정
    python evaluation/compare_models.py --models A B --queries q001 q006

    # 3개 이상 모델도 가능
    python evaluation/compare_models.py --models A B C --n 5
"""
import argparse
import sys
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import RAGConfig  # noqa: E402
from rag.bot import LegalRAGBot  # noqa: E402
from rag.generator import generate_response  # noqa: E402
from evaluation.run_eval import load_golden_set  # noqa: E402

GOLDEN_SET_PATH = Path(__file__).resolve().parent / "golden_set.jsonl"


def pick_queries(golden_set: List[Dict[str, Any]], ids: Optional[List[str]], n: int) -> List[Dict[str, Any]]:
    if ids:
        return [item for item in golden_set if item["id"] in ids]
    labeled = [item for item in golden_set if item.get("relevant_case_ids")]
    return labeled[:n]


def main() -> None:
    parser = argparse.ArgumentParser(description="채팅 모델 답변 품질 비교")
    parser.add_argument("--models", nargs="+", required=True, help="비교할 모델 이름 2개 이상")
    parser.add_argument("--queries", nargs="+", default=None, help="비교할 골든셋 질의 id들 (예: q001 q006)")
    parser.add_argument("--n", type=int, default=3, help="--queries 미지정 시 앞에서부터 몇 개 쓸지 (기본 3)")
    parser.add_argument(
        "--output", type=Path,
        default=Path(__file__).resolve().parent / "reports" / f"model_comparison_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    )
    args = parser.parse_args()

    if len(args.models) < 2:
        print("⚠️ 최소 2개 이상의 모델을 --models로 지정하세요.")
        return

    golden_set = load_golden_set(GOLDEN_SET_PATH)
    queries = pick_queries(golden_set, args.queries, args.n)
    if not queries:
        print("비교할 질의를 찾지 못했습니다 (골든셋이 비어있거나 --queries의 id가 안 맞습니다).")
        return

    print(f"{len(queries)}개 질의 × {len(args.models)}개 모델 비교를 시작합니다...\n")

    base_config = RAGConfig()
    lines = [
        "# 모델 답변 품질 비교 리포트\n",
        f"- 생성 시각: {datetime.now().isoformat()}",
        f"- 비교 모델: {', '.join(args.models)}",
        f"- 질의 수: {len(queries)}\n",
        "⚠️ 자동 채점 없음. 직접 읽고 판단하세요.\n",
    ]

    with LegalRAGBot(base_config) as bot:
        for item in queries:
            query = item["query"]
            print(f"[{item['id']}] {query[:50]}...")

            docs = bot.retrieve(query)  # 검색은 한 번만 - 모델 간 비교 조건을 동일하게 유지
            lines.append(f"\n---\n\n## {item['id']}: {query}\n")

            if not docs:
                lines.append("\n(검색 결과 없음 - 비교 스킵)\n")
                print("  ⚠️ 검색 결과 없음, 스킵")
                continue

            for model in args.models:
                # stream_print 끄고, 대상 모델만 바꿔서 나머지 설정은 고정
                model_config = replace(base_config, chat_model=model, stream_print=False)
                print(f"  → {model} 생성 중...")
                start = time.time()
                try:
                    answer = generate_response(bot.session, model_config, bot.logger, query, docs)
                    elapsed = time.time() - start
                    lines.append(f"\n### {model}  ({elapsed:.1f}초)\n\n{answer}\n")
                    print(f"     완료 ({elapsed:.1f}초)")
                except Exception as e:
                    elapsed = time.time() - start
                    lines.append(f"\n### {model}  (실패, {elapsed:.1f}초)\n\n생성 실패: {e}\n")
                    print(f"     ⚠️ 실패: {e}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"\n💾 비교 리포트 저장: {args.output}")
    print("직접 열어서 두 모델의 답변을 나란히 읽어보시고 판단해보세요.")


if __name__ == "__main__":
    main()