"""
evaluation/check_db_docs.py
===========================
검색이 안 되는 정답 문서 3건이 실제 DB(legal_chunks)에 존재하는지 직접 쿼리하여 확인합니다.
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
    
    print("=" * 80)
    print("🔍 누락 의심 문서 DB 존재 여부 확인")
    print("=" * 80)

    conn = bot.db_pool.getconn()
    try:
        cur = conn.cursor()
        # 조항 번호 띄어쓰기 문제 등을 피하기 위해 법령/판례 번호 핵심 키워드만 검색
        target_docs = ["2020고단10994", "임금채권보장법", "근로기준법"]
        
        for doc_id in target_docs:
            # 안전하게 title과 content(본문) 컬럼만 사용하여 조회
            cur.execute("""
                SELECT title 
                FROM legal_chunks 
                WHERE title LIKE %s OR content LIKE %s
                LIMIT 3
            """, (f"%{doc_id}%", f"%{doc_id}%"))
            
            results = cur.fetchall()
            
            if results:
                print(f"✅ [{doc_id}] DB에 존재함! (검색기 벡터/키워드 매칭 한계)")
                for idx, r in enumerate(results, 1):
                    print(f"   - {idx}. 제목: {r[0]}")
            else:
                print(f"❌ [{doc_id}] DB에 존재하지 않음! (데이터 원천 적재 누락)")
                
        cur.close()
    finally:
        bot.db_pool.putconn(conn)
        if hasattr(bot, "close"): bot.close()
        elif hasattr(bot, "db_pool") and bot.db_pool: bot.db_pool.closeall()

if __name__ == "__main__":
    main()