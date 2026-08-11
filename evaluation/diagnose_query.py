"""검색 실패 원인 진단 도구.

골든셋 평가에서 특정 질의의 Recall/MRR이 낮게 나올 때, 실제로 어떤
후보들이 검색됐는지 넓게 펼쳐서 보여준다. LLM 생성은 호출하지 않는다.

이 도구로 구분할 수 있는 것:
  - 정답이 DB에 아예 없는 경우 (수집 범위의 문제)
  - 정답은 있는데 순위가 밀린 경우 (검색/리랭킹 튜닝의 문제)

사용법:
    python evaluation/diagnose_query.py --id q002
    python evaluation/diagnose_query.py --query "임의의 질문 텍스트"
    python evaluation/diagnose_query.py --id q002 --n 20
"""
import argparse
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import RAGConfig  # noqa: E402
from rag.bot import LegalRAGBot  # noqa: E402
from evaluation.run_eval import extract_case_id, load_golden_set  # noqa: E402

GOLDEN_SET_PATH = Path(__file__).resolve().parent / "golden_set.jsonl"


def main() -> None:
    parser = argparse.ArgumentParser(description="검색 실패 원인 진단")
    parser.add_argument("--id", type=str, help="golden_set.jsonl 안의 질의 id (예: q002)")
    parser.add_argument("--query", type=str, help="직접 입력할 질의 텍스트 (--id 대신 사용)")
    parser.add_argument("--n", type=int, default=15, help="넓게 볼 후보 개수 (기본 15)")
    args = parser.parse_args()

    relevant_ids = []
    if args.id:
        items = load_golden_set(GOLDEN_SET_PATH)
        match = next((i for i in items if i["id"] == args.id), None)
        if not match:
            print(f"❌ '{args.id}'를 {GOLDEN_SET_PATH}에서 찾을 수 없습니다.")
            return
        query = match["query"]
        relevant_ids = match.get("relevant_case_ids", [])
        print(f"질의 [{args.id}]: {query}")
        print(f"정답으로 등록된 사건번호: {relevant_ids or '(없음)'}\n")
    elif args.query:
        query = args.query
        print(f"질의: {query}\n")
    else:
        parser.print_help()
        return

    base_config = RAGConfig()
    wide_config = replace(base_config, top_k=args.n)  # top_k를 늘려 넓게 본다

    with LegalRAGBot(wide_config) as bot:
        docs = bot.retrieve(query)

    print(f"--- 상위 {len(docs)}건 (넓게 조회) ---\n")
    for i, doc in enumerate(docs, 1):
        case_id = extract_case_id(doc["title"])
        mark = "🎯" if case_id in relevant_ids else "  "
        print(f"{mark} [{i}] ({case_id}) score={doc.get('rerank_score')}")
        print(f"      {doc['title'][:70]}")
        print(f"      {doc['content'][:150].strip()}...")
        print()

    if relevant_ids:
        found_ids = {extract_case_id(d["title"]) for d in docs}
        found = [c for c in relevant_ids if c in found_ids]
        missing = [c for c in relevant_ids if c not in found_ids]
        print(f"✅ 상위 {args.n}건 안에서 찾은 정답: {found or '(없음)'}")
        print(f"❌ 상위 {args.n}건 안에서도 못 찾은 정답: {missing or '(없음)'}")
        if missing:
            print("\n⚠️ 못 찾은 사건번호가 DB에 아예 있는지 직접 확인해보세요:")
            for case_id in missing:
                print(f"   SELECT title, doc_type, length(content) FROM legal_chunks "
                      f"WHERE title LIKE '%{case_id}%';")


if __name__ == "__main__":
    main()