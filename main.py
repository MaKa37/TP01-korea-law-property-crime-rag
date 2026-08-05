import asyncio
import sys
from rag_system import LegalRAGSystem

async def main():
    rag = LegalRAGSystem()
    print("=" * 60)
    print("⚖️  사기·재산범죄 피해 대응 전문 AI 챗봇")
    print("   (종료하시려면 'exit' 또는 'quit'을 입력하세요)")
    print("=" * 60)

    while True:
        try:
            user_input = input("\n👤 피해 상황을 입력해 주세요: ").strip()
            if not user_input:
                continue
            if user_input.lower() in ["exit", "quit", "종료"]:
                print("👋 챗봇을 종료합니다.")
                break

            print("\n🤖 로컬 DB 검색 및 Nemotron Ultra 550B 답변 생성 중...\n")
            response = await rag.answer(user_input)
            
            print("-" * 60)
            print(response)
            print("-" * 60)

        except (KeyboardInterrupt, EOFError):
            print("\n👋 챗봇을 종료합니다.")
            sys.exit(0)

if __name__ == "__main__":
    asyncio.run(main())