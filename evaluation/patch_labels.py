"""
evaluation/patch_labels.py
==========================
미라벨링된 8건의 정답(Ground Truth)을 golden_set.jsonl에 일괄 업데이트합니다.
"""

import json
from pathlib import Path

GOLDEN_SET_PATH = Path("evaluation") / "golden_set.jsonl"

updates = {
    "q008": ["85다카1213"],
    "q009": ["2020고단10994"],
    "q010": ["99다62074"],
    "q013": ["임금채권보장법 제7조"],
    "q014": ["근로기준법 제28조"],
    "q034": ["2022노1"],
    "q036": ["96도1081"],
    "q038": ["85도1765"]
}

def main():
    updated_items = []
    with open(GOLDEN_SET_PATH, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip(): continue
            item = json.loads(line)
            if item["id"] in updates:
                item["relevant_case_ids"] = updates[item["id"]]
            updated_items.append(item)

    with open(GOLDEN_SET_PATH, "w", encoding="utf-8") as f:
        for item in updated_items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    print("✅ 8건의 정답(Ground Truth) 라벨링 업데이트가 완료되었습니다.")

if __name__ == "__main__":
    main()