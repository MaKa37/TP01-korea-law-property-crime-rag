"""
evaluation/diagnose_failures.py
===============================
검색 실패 3건(q009, q013, q014)에 대해 정답 문서가 
1차 검색(Top-100) 및 2차 리랭킹에서 각각 몇 위를 기록했는지 추적합니다.
(Monkey Patching 기법을 적용하여 파이프라인 내부 상태를 가로챕니다.)
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rag.bot import LegalRAGBot
import core.config as config_module
import rag.retrieval as ret

GOLDEN_SET_PATH = PROJECT_ROOT / "evaluation" / "golden_set.jsonl"
TARGET_QIDS = {"q009", "q013", "q014"}

def _find_config():
    """core.config 객체 안전 로드"""
    for name in dir(config_module):
        if name.startswith("_"): continue
        obj = getattr(config_module, name)
        if not isinstance(obj, type) and hasattr(obj, "db_host"): return obj
    for name in dir(config_module):
        if name.startswith("_"): continue
        obj = getattr(config_module, name)
        if isinstance(obj, type) and ("Config" in name or "Settings" in name):
            try: return obj()
            except Exception: pass
    raise RuntimeError("설정 객체를 찾을 수 없습니다.")

def main():
    with open(GOLDEN_SET_PATH, "r", encoding="utf-8") as f:
        items = [json.loads(line) for line in f if line.strip()]
    
    target_items = [it for it in items if it.get("id") in TARGET_QIDS]
    config = _find_config()
    
    # 추적 범위를 넓히기 위해 1차 검색 후보군을 100건으로 확대
    config.candidate_k = 100 
    
    # ---------------------------------------------------------
    # [핵심] 하이브리드 검색 함수를 Monkey Patching하여 1차 결과 가로채기
    # ---------------------------------------------------------
    search_fn_name = None
    original_search_fn = None
    for n in dir(ret):
        if ("hybrid" in n.lower() or "search" in n.lower()) and callable(getattr(ret, n)):
            search_fn_name = n
            original_search_fn = getattr(ret, n)
            if "hybrid" in n.lower(): break
            
    captured_candidates = []
    
    def hooked_search(*args, **kwargs):
        """기존 검색 함수 실행 후 결과만 복사해 둡니다."""
        results = original_search_fn(*args, **kwargs)
        captured_candidates.clear()
        captured_candidates.extend(results)
        return results

    if search_fn_name:
        setattr(ret, search_fn_name, hooked_search)

    # ---------------------------------------------------------
    bot = LegalRAGBot(config)

    print("=" * 80)
    print("🔍 검색 실패 3건 정답 유실 구간 추적 (Monkey Patching)")
    print("=" * 80)

    try:
        for item in target_items:
            qid = item["id"]
            query = item["query"]
            gts = item.get("relevant_case_ids", [])
            
            print(f"\n📌 [{qid}] 질의: {query}")
            print(f"🎯 정답 ID: {gts}")
            
            # bot.retrieve 실행 시 내부적으로 hooked_search가 작동하여 1차 결과 확보됨
            reranked = bot.retrieve(query)
            
            # 1차 검색 순위 분석
            c_ranks = {
                (c.get("case_id") or c.get("id") or c.get("metadata", {}).get("case_id") or "미식별").replace(" ", ""): idx 
                for idx, c in enumerate(captured_candidates, 1)
            }
            
            found_in_1st = []
            for gt in gts:
                gt_norm = gt.replace(" ", "")
                if gt_norm in c_ranks:
                    found_in_1st.append(gt)
                    print(f"  ✅ [1차 검색] 통과: 정답 '{gt}' 문서가 Top-100 중 {c_ranks[gt_norm]}위에 있습니다.")
            
            if not found_in_1st:
                print("  ❌ [1차 검색] 실패: Top-100 내에 정답 문서가 없습니다. (키워드/벡터 매칭 한계)")
                continue
            
            # 2차 리랭킹 순위 분석
            r_ranks = {
                (r.get("case_id") or r.get("id") or r.get("metadata", {}).get("case_id") or "미식별").replace(" ", ""): idx 
                for idx, r in enumerate(reranked, 1)
            }
            
            for gt in found_in_1st:
                gt_norm = gt.replace(" ", "")
                final_rank = r_ranks.get(gt_norm, -1)
                if 1 <= final_rank <= 5:
                    print(f"  ✅ [2차 리랭킹] 통과: 최종 {final_rank}위 안착 (성공)")
                else:
                    print(f"  ⚠️ [2차 리랭킹] 실패: 최종 {final_rank}위로 밀려남 (NVIDIA 리랭커 스코어 부족)")

    finally:
        if hasattr(bot, "close"): bot.close()
        elif hasattr(bot, "db_pool") and bot.db_pool: bot.db_pool.closeall()

if __name__ == "__main__":
    main()