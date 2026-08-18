"""
evaluation/inspect_unlabeled.py
===============================
미라벨링된 8개 질의에 대해 RAG 파이프라인(하이브리드 검색 + 리랭킹 v9)의
상위 검색 판례와 사건번호, 본문을 출력하여 Ground Truth 선정을 지원합니다.
"""

import json
import sys
from pathlib import Path

# 프로젝트 루트 경로 등록
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import core.config as config_module
from rag.bot import LegalRAGBot

GOLDEN_SET_PATH = PROJECT_ROOT / "evaluation" / "golden_set.jsonl"
UNLABELED_QIDS = {"q008", "q009", "q010", "q013", "q014", "q034", "q036", "q038"}


def _find_config():
    """core.config 내부를 동적으로 탐색하여 유효한 설정 객체를 반환합니다."""
    # 1. 이미 생성된 인스턴스 객체 탐색 (db_host 등 속성을 가지고 있는지 확인)
    for name in dir(config_module):
        if name.startswith("_"): continue
        obj = getattr(config_module, name)
        if not isinstance(obj, type) and (hasattr(obj, "db_host") or hasattr(obj, "candidate_k")):
            return obj
            
    # 2. Config/Settings 류의 클래스를 찾아 직접 인스턴스화 시도
    for name in dir(config_module):
        if name.startswith("_"): continue
        obj = getattr(config_module, name)
        if isinstance(obj, type) and ("Config" in name or "Settings" in name):
            try:
                return obj()
            except Exception:
                pass
                
    # 3. 탐색 실패 시 디버깅을 위해 정의된 항목 목록 출력
    available = [
        n for n in dir(config_module) 
        if not n.startswith("_") and not type(getattr(config_module, n)).__name__ == 'module'
    ]
    raise RuntimeError(
        f"설정 객체를 찾지 못했습니다. 현재 core.config에 정의된 항목들: {available}"
    )


def main():
    if not GOLDEN_SET_PATH.exists():
        print(f"❌ 골든셋 파일을 찾을 수 없습니다: {GOLDEN_SET_PATH}")
        return

    with open(GOLDEN_SET_PATH, "r", encoding="utf-8") as f:
        items = [json.loads(line) for line in f if line.strip()]

    target_items = [
        it for it in items
        if it.get("id") in UNLABELED_QIDS or not it.get("relevant_case_ids")
    ]

    print("=" * 80)
    print(f"🔍 미라벨링 대상 질의: 총 {len(target_items)}건")
    print("=" * 80)

    # 설정 자동 탐색 및 봇 초기화
    config = _find_config()
    bot = LegalRAGBot(config)

    try:
        for idx, item in enumerate(target_items, start=1):
            qid = item.get("id", f"q{idx:03d}")
            query = item.get("query", "")
            category = item.get("category", "미지정")

            print(f"\n[{idx}/{len(target_items)}] 📌 [{qid}] ({category})")
            print(f"질의: {query}")
            print("-" * 80)

            # 검색 및 리랭킹 파이프라인 수행
            top_docs = bot.retrieve(query)

            if not top_docs:
                print("  ⚠️ 검색된 문서가 없습니다.")
                continue

            for rank, doc in enumerate(top_docs, start=1):
                title = doc.get("title", "제목 없음")
                case_id = (
                    doc.get("case_id")
                    or doc.get("id")
                    or doc.get("metadata", {}).get("case_id")
                    or "미식별"
                )
                score = doc.get("score") or doc.get("similarity") or 0.0
                content = doc.get("content", "").replace("\n", " ").strip()
                snippet = content[:150]

                print(f"  [{rank}위] 사건/문서 ID: {case_id} (스코어: {score:.4f})")
                print(f"        제목: {title}")
                print(f"        내용: {snippet}...")
                print()

    finally:
        if hasattr(bot, "close"):
            bot.close()
        elif hasattr(bot, "db_pool") and bot.db_pool:
            bot.db_pool.closeall()


if __name__ == "__main__":
    main()