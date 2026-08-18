"""
evaluation/inspect_generation.py
================================
RAG 파이프라인의 최종 단계인 '답변 생성(Generation)' 품질을 검증합니다.
스트리밍 청크의 포맷(str, dict 등)과 무관하게 모든 텍스트를 화면에 렌더링합니다.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import core.config as config_module
from rag.bot import LegalRAGBot

def _find_config():
    for name in dir(config_module):
        if name.startswith("_"): continue
        obj = getattr(config_module, name)
        if not isinstance(obj, type) and hasattr(obj, "db_host"): return obj
    for name in dir(config_module):
        if name.startswith("_"): continue
        obj = getattr(config_module, name)
        if isinstance(obj, type) and ("Config" in name or "Settings" in name or "RAGConfig" in name):
            try: return obj()
            except Exception: pass
    raise RuntimeError("설정 객체를 찾을 수 없습니다.")

def main():
    config = _find_config()
    bot = LegalRAGBot(config)

    test_queries = [
        "빌려준 돈을 받지 못해 대여금 반환 청구 소송을 진행할 때 필요한 입증 자료는 무엇인가요?",
        "중고거래로 구매한 물품이 진품이 아닌 가품(짝퉁)으로 밝혀졌을 때 사기죄 고소 요건은?",
        "전세사기 피해를 당한 것 같습니다. 가장 먼저 무엇을 해야 하나요?"
    ]

    print("=" * 80)
    print("🤖 LegalRAGBot 답변 생성(Generation) E2E 최종 테스트")
    print("=" * 80)

    try:
        for idx, query in enumerate(test_queries, 1):
            print(f"\n[{idx}/3] 👤 사용자 질의: {query}")
            print("-" * 80)
            
            try:
                # 스트리밍 청크 수신
                for chunk in bot.ask_stream(query):
                    
                    if isinstance(chunk, dict):
                        # 1. 출처(Sources) 정보일 경우
                        if chunk.get("type") == "sources":
                            print("📑 [참조된 검색 문서]")
                            for doc in chunk.get("documents", []):
                                title = doc.get("title", "제목없음")
                                print(f"  - {title}")
                            print("\n🤖 봇 응답:\n", end="")
                        
                        # 2. 스트리밍 텍스트 토큰이 dict로 감싸져 있을 경우
                        else:
                            text = chunk.get("content") or chunk.get("text") or chunk.get("answer") or chunk.get("message")
                            if text:
                                print(text, end="", flush=True)
                            else:
                                # 예상치 못한 dict 구조라면 그대로 출력
                                print(chunk, end="", flush=True)
                                
                    # 3. 단순 문자열일 경우
                    elif isinstance(chunk, str):
                        print(chunk, end="", flush=True)
                    
                    # 4. 기타 객체일 경우
                    else:
                        print(str(chunk), end="", flush=True)
                        
            except Exception as e:
                print(f"\n[오류 발생] 답변 생성 중 문제 발생: {e}")
            
            print("\n\n" + "-" * 80)

    finally:
        if hasattr(bot, "close"): bot.close()
        elif hasattr(bot, "db_pool") and bot.db_pool: bot.db_pool.closeall()

if __name__ == "__main__":
    main()