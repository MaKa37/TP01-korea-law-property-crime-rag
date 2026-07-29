import json
from pathlib import Path

# ===== 설정 =====
INPUT_PATH = Path("Json_Files/DB19_prec_parsed_ALL.json")
OUTPUT_PATH = Path("Json_Files/DB19_prec_filtered.json")

EXCLUDE_SOURCE = "국세법령정보시스템"


def main():
    with open(INPUT_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict):
        for key in ("law", "items", "data", "prec"):
            if key in data and isinstance(data[key], list):
                data = data[key]
                break

    before_count = len(data)

    filtered = [
        item for item in data
        if item.get("데이터출처명") != EXCLUDE_SOURCE
    ]

    after_count = len(filtered)
    removed_count = before_count - after_count

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(filtered, f, ensure_ascii=False, indent=2)

    print(f"전체: {before_count}건")
    print(f"제외됨 ({EXCLUDE_SOURCE}): {removed_count}건")
    print(f"✅ 저장됨: {after_count}건 -> {OUTPUT_PATH}")


if __name__ == "__main__":
    main()