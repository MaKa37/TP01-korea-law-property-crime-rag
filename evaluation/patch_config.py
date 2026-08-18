"""
evaluation/patch_config.py
==========================
1차 하이브리드 검색의 한계를 극복하기 위해 하이퍼파라미터를 최적화합니다.
"""

import re
from pathlib import Path

CONFIG_PATH = Path("core") / "config.py"

def main():
    if not CONFIG_PATH.exists():
        print(f"❌ {CONFIG_PATH} 파일을 찾을 수 없습니다.")
        return

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config_text = f.read()

    # 1. Trigram 임계값 대폭 완화 (예: 0.15 -> 0.03) : 어휘가 달라도 벡터 유사도로 올라올 수 있게 허용
    config_text = re.sub(r"(trigram_thresh\s*[:=]\s*(?:float\s*=)?\s*)[\d.]+", r"\g<1>0.03", config_text)
    
    # 2. 벡터(의미) 검색 가중치 상향 (예: 0.4 -> 0.6) : 키워드보다 맥락적 의미에 더 큰 비중 부여
    config_text = re.sub(r"(hybrid_alpha\s*[:=]\s*(?:float\s*=)?\s*)[\d.]+", r"\g<1>0.60", config_text)
    
    # 3. 2차 리랭킹으로 넘기는 후보군 수 확대 (예: 15 -> 25)
    config_text = re.sub(r"(candidate_k\s*[:=]\s*(?:int\s*=)?\s*)\d+", r"\g<1>25", config_text)

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write(config_text)

    print("✅ core/config.py 하이퍼파라미터 최적화 완료!")
    print("  - trigram_thresh -> 0.03 (키워드 필터 대폭 완화)")
    print("  - hybrid_alpha -> 0.60 (벡터 검색 비중 강화)")
    print("  - candidate_k -> 25 (리랭킹 후보군 확대)")

if __name__ == "__main__":
    main()