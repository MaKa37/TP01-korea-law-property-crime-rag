"""
evaluation/debug_generation.py
==============================
LLM 답변 생성 중 발생하는 'tuple indices must be integers...' 에러의 
정확한 발생 파일과 라인 번호를 찾기 위해 Traceback을 강제 출력합니다.
"""

import sys
import logging
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import core.config as config_module
from rag.bot import LegalRAGBot

# ---------------------------------------------------------
# [핵심] 로거를 후킹하여 숨겨진 Traceback 강제 출력
# ---------------------------------------------------------
original_error = logging.Logger.error

def patched_error(self, msg, *args, **kwargs):
    exc_type, exc_value, exc_traceback = sys.exc_info()
    if exc_type is not None:
        print("\n" + "🔥" * 40)
        print("🚨 [추적 완료] 숨겨진 에러 발생 지점 상세 로그 🚨")
        traceback.print_exception(exc_type, exc_value, exc_traceback)
        print("🔥" * 40 + "\n")
    original_error(self, msg, *args, **kwargs)

logging.Logger.error = patched_error
# ---------------------------------------------------------

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

    # 디버깅을 위해 단 1개의 쿼리만 빠르게 테스트
    query = "빌려준 돈을 받지 못해 대여금 반환 청구 소송을 진행할 때 필요한 입증 자료는 무엇인가요?"

    print("=" * 80)
    print("🔍 LLM 답변 생성 에러 상세 추적 시작")
    print("=" * 80)

    try:
        print(f"👤 질의: {query}")
        print("-" * 80)
        
        for chunk in bot.ask_stream(query):
            if isinstance(chunk, dict) and chunk.get("type") == "sources":
                print("📑 [문서 검색 완료]")
            elif isinstance(chunk, str):
                pass # 텍스트 스트리밍 중
                
    finally:
        if hasattr(bot, "close"): bot.close()
        elif hasattr(bot, "db_pool") and bot.db_pool: bot.db_pool.closeall()

if __name__ == "__main__":
    main()