"""로컬 단발 테스트용 CLI 스크립트.

사용법:
    python scripts/run_cli.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import RAGConfig  # noqa: E402
from rag.bot import LegalRAGBot  # noqa: E402


def main() -> None:
    config = RAGConfig()

    with LegalRAGBot(config) as rag_bot:
        test_query = (
            "회사의 이사가 채무변제능력을 상실한 타인에게 충분한 담보 없이 만연히 회사자금을 "
            "대여해 준 경우 업무상배임죄가 성립하는지, 그리고 용도가 엄격히 제한된 위탁 자금을 "
            "다른 목적으로 사용한 경우 횡령죄가 성립하는지 다룬 판례의 판시사항은?"
        )

        print("질의를 분석 중입니다. 잠시만 기다려주세요...\n" + "-" * 50)

        result = rag_bot.ask(test_query)

        if result["status"] == "success":
            already_streamed = result.get("llm_available") is True and config.stream_print
            mode_tag = " (원문 대체 모드)" if result.get("llm_available") is False else ""
            if not already_streamed:
                print(f"\n🤖 [AI 어시스턴트 답변]{mode_tag}")
                print(result["answer"])
            print("\n" + "-" * 50)
            print(f"⏱️ 소요 시간: {result['latency_sec']:.2f}초")
            print("📑 [참조된 핵심 문서 Top 3]")
            for i, doc in enumerate(result["retrieved_documents"][:3], 1):
                print(f"  {i}. {doc['title']} (Score: {doc.get('rerank_score', 'N/A')})")
        elif result["status"] == "no_results":
            print("\nℹ️ " + result["answer"])
        else:
            print(f"\n❌ 오류 발생: {result['error_message']}")


if __name__ == "__main__":
    main()
